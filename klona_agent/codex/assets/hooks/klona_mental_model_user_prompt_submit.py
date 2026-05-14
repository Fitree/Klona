#!/usr/bin/env python3
"""Codex UserPromptSubmit hook that adds Klona mental-model context."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Codex currently requires Python 3.11+ here.
    tomllib = None  # type: ignore[assignment]


OPEN = "<Klona_memory_mental_model>\n"
CLOSE = "\n</Klona_memory_mental_model>"
MCP_NAME = "klona_memory"
INTERNAL_PATH = "/internal/mental-model"


def codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".codex"


def mental_model_url(mcp_url: str) -> str:
    parts = urlsplit(mcp_url)
    path = parts.path.rstrip("/")
    if path.endswith("/mcp"):
        path = path[:-4]
        return urlunsplit((parts.scheme, parts.netloc, path + INTERNAL_PATH, "", ""))
    return urlunsplit((parts.scheme, parts.netloc, INTERNAL_PATH, "", ""))


def load_mcp_config() -> tuple[str, dict[str, str]] | None:
    if tomllib is None:
        return None
    config_path = codex_home() / "config.toml"
    if not config_path.exists():
        return None
    data = tomllib.loads(config_path.read_text())
    mcp_servers = data.get("mcp_servers", {})
    if not isinstance(mcp_servers, dict):
        return None
    server = mcp_servers.get(MCP_NAME, {})
    if not isinstance(server, dict) or server.get("enabled") is False:
        return None
    url = server.get("url")
    if not isinstance(url, str) or not url.strip():
        return None
    if "http_headers" not in server:
        return url.strip(), {}
    headers = server["http_headers"]
    if not isinstance(headers, dict):
        return None
    if not all(isinstance(key, str) and isinstance(value, str) for key, value in headers.items()):
        return None
    authorization = headers.get("Authorization")
    if authorization is not None and not authorization.startswith("Bearer "):
        return None
    return url.strip(), headers


def fetch_mental_model(url: str, headers: dict[str, str]) -> str:
    request = urllib.request.Request(
        mental_model_url(url),
        headers={"Accept": "application/json", **headers},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8") or "{}")
    if payload.get("status") == "ok" and isinstance(payload.get("content"), str):
        return payload["content"]
    return ""


def output(additional_context: str = "") -> None:
    payload: dict[str, object] = {"continue": True, "suppressOutput": True}
    if additional_context:
        payload["hookSpecificOutput"] = {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": additional_context,
        }
    print(json.dumps(payload))


def main() -> int:
    try:
        config = load_mcp_config()
        if not config:
            output()
            return 0
        content = fetch_mental_model(*config).strip()
        if not content:
            output()
            return 0
        output(f"{OPEN}{content}{CLOSE}")
    except (OSError, TypeError, ValueError, urllib.error.URLError):
        output()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
