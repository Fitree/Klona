#!/usr/bin/env python3
"""Interactive setup for the server-side KLONA memory stack."""

from __future__ import annotations

import secrets
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = REPO_ROOT / ".env"


def _ask(prompt: str, default: str | None = None) -> str:
    label = f"{prompt} [{default}]: " if default is not None else f"{prompt}: "
    try:
        value = input(label).strip()
    except (EOFError, KeyboardInterrupt) as exc:
        raise SystemExit("setup cancelled") from exc
    if value:
        return value
    if default is not None:
        return default
    raise SystemExit(f"{prompt} cannot be empty")


def _token() -> str:
    return secrets.token_urlsafe(32)


def build_env(values: dict[str, str]) -> str:
    ordered = [
        "HOST_VAULT_DIR",
        "LOW_LEVEL_MCP_HOST_PORT",
        "LOW_LEVEL_MCP_AUTH_TOKEN",
        "LOW_LEVEL_ALLOWED_HOSTS",
        "HIGH_LEVEL_MCP_HOST_PORT",
        "HIGH_LEVEL_MCP_AUTH_TOKEN",
        "HIGH_LEVEL_ALLOWED_HOSTS",
        "LOW_LEVEL_MCP_URL",
        "MEMORY_AGENT_QUEUE_DB",
        "MEMORY_AGENT_STATE_DIR",
        "MEMORY_AGENT_TIMEOUT_SECONDS",
        "MEMORY_AGENT_MAX_RETRIES",
        "OPENCODE_HOST",
        "OPENCODE_PORT",
    ]
    return "\n".join(f"{key}={values[key]}" for key in ordered) + "\n"


def collect_values() -> dict[str, str]:
    """Ask non-model setup questions only.

    OpenCode auth, model, and reasoning-effort selection intentionally happen later inside
    the memory-agent container so choices match the final runtime environment.
    """
    low_port = _ask("Low-level admin MCP host port", "32310")
    high_port = _ask("High-level user-agent MCP host port", "32311")
    return {
        "HOST_VAULT_DIR": _ask("Host markdown vault directory", "./vault"),
        "LOW_LEVEL_MCP_HOST_PORT": low_port,
        "LOW_LEVEL_MCP_AUTH_TOKEN": _ask("Low-level admin MCP bearer token", _token()),
        "LOW_LEVEL_ALLOWED_HOSTS": _ask("Low-level allowed hosts (comma-separated)", "localhost,127.0.0.1,memory-server:8000"),
        "HIGH_LEVEL_MCP_HOST_PORT": high_port,
        "HIGH_LEVEL_MCP_AUTH_TOKEN": _ask("High-level user-agent MCP bearer token", _token()),
        "HIGH_LEVEL_ALLOWED_HOSTS": _ask("High-level allowed hosts (comma-separated)", "localhost,127.0.0.1"),
        "LOW_LEVEL_MCP_URL": _ask("Low-level MCP URL from memory-agent container", "http://memory-server:8000/mcp"),
        "MEMORY_AGENT_QUEUE_DB": _ask("Memory-agent queue DB path in container", "/state/queue.db"),
        "MEMORY_AGENT_STATE_DIR": _ask("Memory-agent state dir in container", "/state"),
        "MEMORY_AGENT_TIMEOUT_SECONDS": _ask("Recall timeout seconds", "600"),
        "MEMORY_AGENT_MAX_RETRIES": _ask("Queue retry attempts", "2"),
        "OPENCODE_HOST": _ask("OpenCode internal host", "127.0.0.1"),
        "OPENCODE_PORT": _ask("OpenCode internal port", "4096"),
    }


def main() -> int:
    if ENV_PATH.exists():
        answer = _ask(".env already exists; overwrite? Type yes to continue", "no")
        if answer.lower() != "yes":
            raise SystemExit("leaving existing .env unchanged")
    ENV_PATH.write_text(build_env(collect_values()), encoding="utf-8")
    print(f"Wrote {ENV_PATH}")
    return subprocess.call(["docker", "compose", "up", "--build", "--abort-on-container-exit"], cwd=REPO_ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
