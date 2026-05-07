"""High-level FastMCP server exposing recall and remember tools."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route

from .config import load_settings
from .constants import MCP_HEALTH_SERVER_NAME, MCP_SERVER_NAME
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


@contextlib.asynccontextmanager
async def lifespan(app: Starlette):
    async with mcp.session_manager.run():
        yield


app = Starlette(
    routes=[Route("/health", health), Mount("/", app=mcp.streamable_http_app())],
    lifespan=lifespan,
    middleware=[Middleware(AuthMiddleware)],
)


def main() -> None:
    import uvicorn

    uvicorn.run("memory_agent.server:app", host="0.0.0.0", port=8080)


if __name__ == "__main__":
    main()
