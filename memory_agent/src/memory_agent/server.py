"""High-level FastMCP server exposing recall and remember tools."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
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


def _dashboard_host_allowed(request: Request) -> bool:
    if not settings.allowed_hosts:
        return True
    host_header = request.headers.get("host", "")
    if not host_header:
        return False
    normalized_host_header = host_header.strip().lower()
    for allowed_host in settings.allowed_hosts:
        allowed = allowed_host.strip().lower()
        if not allowed:
            continue
        if normalized_host_header == allowed:
            return True
        if allowed.endswith(":*") and normalized_host_header.startswith(allowed[:-1]):
            return True
    return False


def _same_host_dashboard_post_allowed(request: Request) -> bool:
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


DASHBOARD_COOKIE = "klona_dashboard_session"


def _dashboard_session_value() -> str:
    if not settings.auth_token:
        return ""
    return hmac.new(settings.auth_token.encode("utf-8"), b"klona-dashboard-session-v1", hashlib.sha256).hexdigest()


def _cookie_value(request: Request, name: str) -> str:
    cookies = getattr(request, "cookies", None)
    if cookies is not None:
        return str(cookies.get(name, ""))
    cookie_header = request.headers.get("cookie", "")
    for part in cookie_header.split(";"):
        key, sep, value = part.strip().partition("=")
        if sep and key == name:
            return value
    return ""


def _dashboard_session_valid(request: Request) -> bool:
    if not settings.auth_token:
        return True
    return hmac.compare_digest(_cookie_value(request, DASHBOARD_COOKIE), _dashboard_session_value())


def _set_dashboard_cookie(response: Response) -> None:
    value = _dashboard_session_value()
    if hasattr(response, "set_cookie"):
        response.set_cookie(DASHBOARD_COOKIE, value, httponly=True, samesite="lax", path="/dashboard")
    else:
        response.cookie = f"{DASHBOARD_COOKIE}={value}; HttpOnly; SameSite=Lax; Path=/dashboard"


def _delete_dashboard_cookie(response: Response) -> None:
    if hasattr(response, "delete_cookie"):
        response.delete_cookie(DASHBOARD_COOKIE, path="/dashboard")
    else:
        response.cookie = f"{DASHBOARD_COOKIE}=; Max-Age=0; HttpOnly; SameSite=Lax; Path=/dashboard"


async def _read_urlencoded_form(request: Request) -> dict[str, str]:
    body = await request.body()
    parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True)
    return {key: values[-1] if values else "" for key, values in parsed.items()}


def _dashboard_login_page(error: str = "") -> HTMLResponse:
    error_html = f'<p class="error">{html.escape(error)}</p>' if error else ""
    return HTMLResponse(
        """<!doctype html><html><head><title>KLONA Dashboard Login</title><style>
body{margin:0;background:#f6f7f9;color:#17202a;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.login{max-width:360px;margin:14vh auto;padding:28px;background:#fff;border:1px solid #e5e7eb;border-radius:14px;box-shadow:0 10px 30px rgba(15,23,42,.08)}h1{font-size:22px;margin:0 0 8px}p{color:#64748b}label{display:block;font-weight:600;margin:18px 0 8px}input{box-sizing:border-box;width:100%;padding:11px 12px;border:1px solid #cbd5e1;border-radius:10px}button{width:100%;margin-top:16px;padding:11px 14px;border:0;border-radius:10px;background:#2563eb;color:#fff;font-weight:700;cursor:pointer}.error{color:#b42318;background:#fef3f2;border:1px solid #fecdca;border-radius:10px;padding:10px}
</style></head><body><main class="login"><h1>KLONA dashboard</h1><p>Enter the high-level MCP token to continue.</p>"""
        + error_html
        + """<form method="post" action="/dashboard/login"><label for="token">Token</label><input id="token" name="token" type="password" autocomplete="current-password" autofocus><button type="submit">Login</button></form></main></body></html>"""
    )


async def dashboard_login(request: Request) -> Response:
    if not _dashboard_host_allowed(request):
        return JSONResponse({"error": "Dashboard host is not allowed"}, status_code=403)
    if not _same_host_dashboard_post_allowed(request):
        return JSONResponse({"error": "Origin or Referer host mismatch"}, status_code=403)
    if not settings.auth_token:
        return RedirectResponse("/dashboard", status_code=303)
    form = await _read_urlencoded_form(request)
    if not hmac.compare_digest(str(form.get("token") or ""), settings.auth_token):
        return _dashboard_login_page("Invalid token")
    response = RedirectResponse("/dashboard", status_code=303)
    _set_dashboard_cookie(response)
    return response


async def dashboard_logout(request: Request) -> Response:
    if not _dashboard_host_allowed(request):
        return JSONResponse({"error": "Dashboard host is not allowed"}, status_code=403)
    if not _same_host_dashboard_post_allowed(request):
        return JSONResponse({"error": "Origin or Referer host mismatch"}, status_code=403)
    response = RedirectResponse("/dashboard", status_code=303)
    _delete_dashboard_cookie(response)
    return response


async def dashboard(request: Request) -> Response:
    if not _dashboard_host_allowed(request):
        return JSONResponse({"error": "Dashboard host is not allowed"}, status_code=403)
    if not _dashboard_session_valid(request):
        return _dashboard_login_page()
    rows = await asyncio.to_thread(queue.list_items)
    counts = await asyncio.to_thread(queue.status_counts)
    remember_count = await asyncio.to_thread(queue.remember_count_since_rem_sleep)
    total = counts["total"]
    pending = counts["pending"]
    processing = counts["processing"]
    failed = counts["failed"]
    auth_notice = "" if settings.auth_token else '<div class="notice">Dashboard auth is not configured; local/admin access is usable without login.</div>'
    logout = '<form method="post" action="/dashboard/logout"><button class="ghost" type="submit">Logout</button></form>' if settings.auth_token else ""
    body = [
        "<!doctype html><html><head><title>KLONA Dashboard</title><style>",
        "body{margin:0;background:#f6f7f9;color:#17202a;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}main{max-width:1180px;margin:0 auto;padding:24px}header{display:flex;gap:16px;justify-content:space-between;align-items:center;margin-bottom:18px}h1{font-size:24px;margin:0}.muted{color:#64748b}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:16px 0}.card,.panel,.notice{background:#fff;border:1px solid #e5e7eb;border-radius:14px;box-shadow:0 6px 20px rgba(15,23,42,.04)}.card{padding:16px}.num{font-size:26px;font-weight:800}.panel{overflow:hidden}.toolbar{display:flex;gap:12px;justify-content:space-between;align-items:center;padding:16px;border-bottom:1px solid #e5e7eb}.table-wrap{overflow-x:auto}table{width:100%;border-collapse:collapse;min-width:900px}th,td{padding:12px 14px;text-align:left;border-bottom:1px solid #eef2f7;vertical-align:top}th{font-size:12px;text-transform:uppercase;color:#64748b;background:#f8fafc}.badge{display:inline-flex;padding:4px 9px;border-radius:999px;font-size:12px;font-weight:700;background:#e0f2fe;color:#075985}.pending{background:#fef3c7;color:#92400e}.processing{background:#dbeafe;color:#1e40af}.failed{background:#fee2e2;color:#991b1b}.succeeded{background:#dcfce7;color:#166534}button{padding:9px 12px;border:0;border-radius:10px;background:#2563eb;color:#fff;font-weight:700;cursor:pointer}.danger{background:#dc2626}.ghost{background:#fff;color:#334155;border:1px solid #cbd5e1}pre{white-space:pre-wrap;margin:0;max-width:430px}.empty{padding:34px;text-align:center;color:#64748b}.notice{padding:12px 14px;margin-bottom:14px;color:#854d0e;background:#fffbeb}",
        "</style></head><body><main>",
        f"<header><div><h1>KLONA dashboard</h1><div class='muted'>FIFO queue and REM sleep controls</div></div>{logout}</header>",
        auth_notice,
        "<section class='cards'>",
        f"<div class='card'><div class='muted'>Total jobs</div><div class='num'>{total}</div></div>",
        f"<div class='card'><div class='muted'>Pending</div><div class='num'>{pending}</div></div>",
        f"<div class='card'><div class='muted'>Processing</div><div class='num'>{processing}</div></div>",
        f"<div class='card'><div class='muted'>Failed</div><div class='num'>{failed}</div></div>",
        "</section>",
        "<section class='panel'><div class='toolbar'><div><strong>Queue jobs</strong>",
        f"<div class='muted'>Successful remembers since last REM enqueue: {remember_count}. Threshold: {settings.rem_sleep_remember_threshold}. Enabled: {settings.rem_sleep_enabled}.</div></div>",
        '<form method="post" action="/dashboard"><input type="hidden" name="action" value="enqueue_rem_sleep"><button type="submit">Append REM sleep job</button></form></div>',
    ]
    if not rows:
        body.append("<div class='empty'>No queue jobs yet.</div>")
    body.extend([
        "<div class='table-wrap'><table>",
        "<tr><th>ID</th><th>Type</th><th>Input</th><th>Status</th><th>Attempts</th><th>Created</th><th>Updated</th><th>Completed</th><th>Action</th></tr>",
    ])
    for item in rows:
        action = ""
        if item.kind == "rem_sleep" and item.status == "pending" and item.attempts == 0:
            action = (
                f'<form method="post" action="/dashboard"><input type="hidden" name="action" value="delete_rem_sleep">'
                f'<input type="hidden" name="id" value="{item.id}"><button class="danger" type="submit">Delete REM sleep</button></form>'
            )
        body.append(
            "<tr>"
            f"<td>{item.id}</td>"
            f"<td>{html.escape(_display_kind(item.kind))}</td>"
            f"<td><pre>{html.escape(_display_input(item.input))}</pre></td>"
            f"<td><span class='badge {html.escape(item.status)}'>{html.escape(item.status)}</span></td>"
            f"<td>{item.attempts}</td>"
            f"<td>{html.escape(_format_time(item.created_at))}</td>"
            f"<td>{html.escape(_format_time(item.updated_at))}</td>"
            f"<td>{html.escape(_format_time(item.completed_at))}</td>"
            f"<td>{action}</td>"
            "</tr>"
        )
    body.extend(["</table></div></section></main></body></html>"])
    return HTMLResponse("\n".join(body))


async def dashboard_action(request: Request) -> Response:
    if not _dashboard_host_allowed(request):
        return JSONResponse({"error": "Dashboard host is not allowed"}, status_code=403)
    if not _dashboard_session_valid(request):
        return JSONResponse({"error": "Dashboard login required"}, status_code=401)
    if not _same_host_dashboard_post_allowed(request):
        return JSONResponse({"error": "Origin or Referer host mismatch"}, status_code=403)
    form = await _read_urlencoded_form(request)
    action = str(form.get("action") or "")
    if action == "enqueue_rem_sleep":
        await asyncio.to_thread(queue.enqueue_rem_sleep, "Manual REM sleep request from /dashboard")
    elif action == "delete_rem_sleep":
        try:
            item_id = int(str(form.get("id") or "0"))
        except ValueError:
            item_id = 0
        if item_id:
            await asyncio.to_thread(queue.delete_pending_rem_sleep, item_id)
    return RedirectResponse("/dashboard", status_code=303)


class AuthMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            request = Request(scope, receive)
            if request.url.path in {"/health", "/dashboard", "/dashboard/login", "/dashboard/logout"}:
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
        Route("/dashboard", dashboard, methods=["GET"]),
        Route("/dashboard", dashboard_action, methods=["POST"]),
        Route("/dashboard/login", dashboard_login, methods=["POST"]),
        Route("/dashboard/logout", dashboard_logout, methods=["POST"]),
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
