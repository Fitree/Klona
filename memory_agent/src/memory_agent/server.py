"""High-level FastMCP server exposing recall and remember tools."""

from __future__ import annotations

import asyncio
import contextlib
import html
import logging
from urllib.parse import parse_qs, urlparse

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route

from .config import load_settings
from .constants import MCP_HEALTH_SERVER_NAME, MCP_SERVER_NAME
from .mental_model import LowLevelMcpMentalModelClient, MentalModelMissingError
from .queue import MemoryQueue

logger = logging.getLogger(__name__)
settings = load_settings()

if settings.allowed_hosts:
    transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=list(settings.allowed_hosts),
    )
else:
    transport_security = TransportSecuritySettings(enable_dns_rebinding_protection=False)

mcp = FastMCP(
    MCP_SERVER_NAME,
    instructions=(
        "High-level KLONA memory tools. Use recall(input) to retrieve relevant memory context "
        "and remember(input) to request durable memory storage."
    ),
    json_response=True,
    transport_security=transport_security,
)
queue = MemoryQueue(settings.queue_db_path)


def _is_authorized(auth_header: str, auth_token: str) -> bool:
    """Return whether a request is authorized for the configured token.

    An empty token intentionally disables auth for this MCP server.
    """
    return not auth_token or auth_header == f"Bearer {auth_token}"


@mcp.tool()
async def remember(input: str) -> dict:
    """Queue information for durable memory storage.

    Args:
        input: Complete context the memory agent should consider storing.

    Returns immediately after durable queue insertion. Processing is silent.
    """
    item_id = await asyncio.to_thread(queue.enqueue, "remember", input)
    return {"status": "request_received", "id": item_id}


@mcp.tool()
async def recall(input: str) -> dict:
    """Retrieve relevant memory context via the server-side memory agent.

    Args:
        input: Complete question/context needed to perform memory recall.
    """
    item_id = await asyncio.to_thread(queue.enqueue, "recall", input)
    item = await asyncio.to_thread(
        queue.wait_for_terminal,
        item_id,
        settings.recall_timeout_seconds,
        settings.poll_interval_seconds,
    )
    if item is None:
        return {"status": "failed", "id": item_id, "error": "queue_item_missing"}
    if item.status == "succeeded":
        return {"status": "ok", "id": item_id, "result": item.result or ""}
    if item.status == "failed":
        return {"status": "failed", "id": item_id, "error": item.last_error or "unknown_error"}
    return {"status": "timeout", "id": item_id, "timeout_seconds": settings.recall_timeout_seconds}


def _format_time(timestamp: float | None) -> str:
    if timestamp is None:
        return ""
    import datetime as _datetime

    return _datetime.datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


def _display_kind(kind: str) -> str:
    return "rem-sleep" if kind == "rem_sleep" else kind


def _display_input(input_text: str, limit: int = 300) -> str:
    if len(input_text) <= limit:
        return input_text
    return input_text[:limit] + f"... [truncated {len(input_text) - limit} chars]"


def _hostname_from_host_header(host_header: str) -> str:
    host = host_header.strip().lower()
    if host.startswith("["):
        end = host.find("]")
        if end != -1:
            return host[1:end]
    if ":" in host:
        return host.rsplit(":", 1)[0]
    return host


def _host_entry_includes_port(host_entry: str) -> bool:
    host = host_entry.strip().lower()
    if host.startswith("["):
        end = host.find("]")
        return end != -1 and host[end + 1 :].startswith(":")
    return ":" in host


def _queue_host_allowed(request: Request) -> bool:
    host_header = request.headers.get("host", "")
    if not host_header:
        return False
    normalized_host_header = host_header.strip().lower()
    hostname = _hostname_from_host_header(normalized_host_header)
    if hostname in {"localhost", "127.0.0.1", "::1"}:
        return True
    for allowed_host in settings.allowed_hosts:
        allowed = allowed_host.strip().lower()
        if not allowed:
            continue
        if normalized_host_header == allowed:
            return True
        if not _host_entry_includes_port(allowed) and hostname == _hostname_from_host_header(allowed):
            return True
    return False


def _same_host_queue_post_allowed(request: Request) -> bool:
    host = request.headers.get("host", "")
    if not host:
        return True
    for header_name in ("origin", "referer"):
        value = request.headers.get(header_name, "")
        if not value:
            continue
        parsed = urlparse(value)
        if parsed.netloc != host:
            return False
    return True


async def _read_urlencoded_form(request: Request) -> dict[str, str]:
    body = await request.body()
    parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True)
    return {key: values[-1] if values else "" for key, values in parsed.items()}


async def queue_dashboard(request: Request) -> Response:
    if not _queue_host_allowed(request):
        return JSONResponse({"error": "Queue dashboard host is not allowed"}, status_code=403)
    rows = await asyncio.to_thread(queue.list_items)
    remember_count = await asyncio.to_thread(queue.remember_count_since_rem_sleep)
    body = [
        "<!doctype html><html><head><title>KLONA FIFO Queue</title></head><body>",
        "<h1>KLONA FIFO Queue</h1>",
        "<p>REM sleep automatic enqueue is disabled when KLONA_REM_SLEEP_ENABLED is false or KLONA_REM_SLEEP_REMEMBER_THRESHOLD <= 0. Manual enqueue still works.</p>",
        f"<p>Successful remembers since last REM enqueue: {remember_count}. Threshold: {settings.rem_sleep_remember_threshold}. Enabled: {settings.rem_sleep_enabled}.</p>",
        '<form method="post" action="/queue"><input type="hidden" name="action" value="enqueue_rem_sleep"><button type="submit">Append REM sleep job</button></form>',
        "<table border='1' cellpadding='4' cellspacing='0'>",
        "<tr><th>ID</th><th>Type</th><th>Input</th><th>Status</th><th>Attempts</th><th>Created</th><th>Updated</th><th>Completed</th><th>Action</th></tr>",
    ]
    for item in rows:
        action = ""
        if item.kind == "rem_sleep" and item.status == "pending" and item.attempts == 0:
            action = (
                f'<form method="post" action="/queue"><input type="hidden" name="action" value="delete_rem_sleep">'
                f'<input type="hidden" name="id" value="{item.id}"><button type="submit">Delete pending REM sleep</button></form>'
            )
        body.append(
            "<tr>"
            f"<td>{item.id}</td>"
            f"<td>{html.escape(_display_kind(item.kind))}</td>"
            f"<td><pre>{html.escape(_display_input(item.input))}</pre></td>"
            f"<td>{html.escape(item.status)}</td>"
            f"<td>{item.attempts}</td>"
            f"<td>{html.escape(_format_time(item.created_at))}</td>"
            f"<td>{html.escape(_format_time(item.updated_at))}</td>"
            f"<td>{html.escape(_format_time(item.completed_at))}</td>"
            f"<td>{action}</td>"
            "</tr>"
        )
    body.extend(["</table>", "</body></html>"])
    return HTMLResponse("\n".join(body))


async def queue_action(request: Request) -> Response:
    if not _queue_host_allowed(request):
        return JSONResponse({"error": "Queue dashboard host is not allowed"}, status_code=403)
    if not _same_host_queue_post_allowed(request):
        return JSONResponse({"error": "Origin or Referer host mismatch"}, status_code=403)
    form = await _read_urlencoded_form(request)
    action = str(form.get("action") or "")
    if action == "enqueue_rem_sleep":
        await asyncio.to_thread(queue.enqueue_rem_sleep, "Manual REM sleep request from /queue dashboard")
    elif action == "delete_rem_sleep":
        try:
            item_id = int(str(form.get("id") or "0"))
        except ValueError:
            item_id = 0
        if item_id:
            await asyncio.to_thread(queue.delete_pending_rem_sleep, item_id)
    return RedirectResponse("/queue", status_code=303)


class AuthMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            request = Request(scope, receive)
            if request.url.path == "/health":
                await self.app(scope, receive, send)
                return
            auth_header = request.headers.get("authorization", "")
            if not _is_authorized(auth_header, settings.auth_token):
                response = JSONResponse({"error": "Unauthorized"}, status_code=401)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


async def health(request: Request) -> Response:
    return JSONResponse({"status": "ok", "server": MCP_HEALTH_SERVER_NAME, "version": "0.1.0"})


async def internal_mental_model(request: Request) -> Response:
    """Return exact mental-model markdown through a private non-MCP route."""
    try:
        content = await LowLevelMcpMentalModelClient(settings).read()
    except MentalModelMissingError:
        return JSONResponse({"status": "missing", "content": ""}, status_code=404)
    except Exception as error:
        logger.warning("Failed to exact-read KLONA_MEMORY_MENTAL_MODEL.md", exc_info=True)
        return JSONResponse({"status": "error", "error": str(error)}, status_code=502)
    return JSONResponse({"status": "ok", "content": content})


@contextlib.asynccontextmanager
async def lifespan(app: Starlette):
    async with mcp.session_manager.run():
        yield


app = Starlette(
    routes=[
        Route("/health", health),
        Route("/queue", queue_dashboard, methods=["GET"]),
        Route("/queue", queue_action, methods=["POST"]),
        Route("/internal/mental-model", internal_mental_model),
        Mount("/", app=mcp.streamable_http_app()),
    ],
    lifespan=lifespan,
    middleware=[Middleware(AuthMiddleware)],
)


def main() -> None:
    import uvicorn

    uvicorn.run("memory_agent.server:app", host="0.0.0.0", port=8080)


if __name__ == "__main__":
    main()
