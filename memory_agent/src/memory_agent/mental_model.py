"""Exact mental-model file reader for the high-level memory agent."""

from __future__ import annotations

import json
import re
from typing import Any

from .config import Settings

MCP_PROTOCOL_VERSION = "2025-03-26"
MCP_ACCEPT_HEADER = "application/json, text/event-stream"
MENTAL_MODEL_PATH = "/KLONA_MEMORY_MENTAL_MODEL.md"
MENTAL_MODEL_TOOL_NAME = "vault_read"
MENTAL_MODEL_TOOL_FALLBACK_NAME = "klona_memory_server_vault_read"


class MentalModelMissingError(Exception):
    """Raised when the mental-model file is not present in the vault."""


class LowLevelMcpMentalModelClient:
    """Small streamable-HTTP MCP client for exact mental-model reads."""

    def __init__(self, settings: Settings):
        self.url = settings.low_level_mcp_url
        self.auth_token = settings.low_level_mcp_auth_token
        self.timeout = settings.recall_timeout_seconds

    async def read(self) -> str:
        if not self.url:
            raise RuntimeError("LOW_LEVEL_MCP_URL is not configured")
        import httpx

        headers = {
            "Accept": MCP_ACCEPT_HEADER,
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {self.auth_token}"} if self.auth_token else {}),
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            session_id = await self._initialize(client, headers)
            await self._send_initialized(client, headers, session_id)
            try:
                return await self._call_read_tool(client, headers, session_id, MENTAL_MODEL_TOOL_NAME)
            except RuntimeError as error:
                lowered_error = str(error).lower()
                if "method not found" not in lowered_error and "tool not found" not in lowered_error and "unknown tool" not in lowered_error:
                    raise
                return await self._call_read_tool(client, headers, session_id, MENTAL_MODEL_TOOL_FALLBACK_NAME)

    async def _post(self, client: Any, headers: dict[str, str], body: dict[str, Any], session_id: str | None = None) -> tuple[dict[str, Any] | None, str | None]:
        request_headers = dict(headers)
        if session_id:
            request_headers["Mcp-Session-Id"] = session_id
        response = await client.post(self.url, headers=request_headers, json=body)
        payload = await _parse_mcp_response(response)
        return payload, response.headers.get("Mcp-Session-Id") or session_id

    async def _initialize(self, client: Any, headers: dict[str, str]) -> str | None:
        payload, session_id = await self._post(
            client,
            headers,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "klona-memory-agent-mental-model-reader", "version": "1.0.0"},
                },
            },
        )
        if payload and payload.get("error"):
            raise RuntimeError(payload["error"].get("message") or "MCP initialize failed")
        return session_id

    async def _send_initialized(self, client: Any, headers: dict[str, str], session_id: str | None) -> None:
        await self._post(
            client,
            headers,
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            session_id=session_id,
        )

    async def _call_read_tool(self, client: Any, headers: dict[str, str], session_id: str | None, tool_name: str) -> str:
        payload, _ = await self._post(
            client,
            headers,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": {"path": MENTAL_MODEL_PATH}},
            },
            session_id=session_id,
        )
        if not payload:
            raise RuntimeError("empty MCP tool response")
        if payload.get("error"):
            raise RuntimeError(payload["error"].get("message") or "MCP tool call failed")

        result = payload.get("result")
        if _is_missing_result(result):
            raise MentalModelMissingError()
        if isinstance(result, dict) and result.get("isError"):
            raise RuntimeError(json.dumps(result))
        content = _extract_exact_content(result)
        if content is None:
            raise RuntimeError("MCP vault_read response did not contain content")
        return content


async def _parse_mcp_response(response: Any) -> dict[str, Any] | None:
    raw = response.text
    response.raise_for_status()
    if not raw.strip():
        return None

    content_type = response.headers.get("content-type", "")
    if "text/event-stream" not in content_type:
        return json.loads(raw)

    for chunk in re.split(r"\r?\n\r?\n", raw):
        data_lines = []
        for line in chunk.splitlines():
            normalized = line.rstrip("\r")
            if normalized.startswith("data:"):
                data_lines.append(normalized[5:].strip())
        if not data_lines:
            continue
        parsed = json.loads("\n".join(data_lines))
        if parsed.get("id") is not None or parsed.get("result") is not None or parsed.get("error") is not None:
            return parsed
    return None


def _extract_exact_content(result: Any) -> str | None:
    candidate = _extract_result_object(result)
    if isinstance(candidate, dict):
        if candidate.get("error") == "file_not_found":
            raise MentalModelMissingError()
        if isinstance(candidate.get("content"), str):
            return candidate["content"]
    return None


def _extract_result_object(value: Any) -> Any:
    if isinstance(value, dict):
        if isinstance(value.get("structuredContent"), dict):
            return value["structuredContent"]
        if isinstance(value.get("content"), list):
            for item in value["content"]:
                if item.get("type") == "text" and isinstance(item.get("text"), str):
                    try:
                        return json.loads(item["text"])
                    except json.JSONDecodeError:
                        return {"content": item["text"]}
        return value
    return value


def _is_missing_result(result: Any) -> bool:
    candidate = _extract_result_object(result)
    if isinstance(candidate, dict) and candidate.get("error") == "file_not_found":
        return True
    if isinstance(result, dict) and result.get("isError"):
        text = json.dumps(result).lower()
        return "not found" in text or "does not exist" in text or "no such file" in text
    return False
