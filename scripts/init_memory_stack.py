#!/usr/bin/env python3
"""Interactive setup for the server-side KLONA memory stack."""

from __future__ import annotations

import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = REPO_ROOT / ".env"

ENV_ORDER = (
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
)

DEFAULTS = {
    "HOST_VAULT_DIR": "./vault",
    "LOW_LEVEL_MCP_HOST_PORT": "32310",
    "LOW_LEVEL_MCP_AUTH_TOKEN": "",
    "LOW_LEVEL_ALLOWED_HOSTS": "localhost,127.0.0.1,memory-server:8000",
    "HIGH_LEVEL_MCP_HOST_PORT": "32311",
    "HIGH_LEVEL_MCP_AUTH_TOKEN": "",
    "HIGH_LEVEL_ALLOWED_HOSTS": "localhost,127.0.0.1",
    "LOW_LEVEL_MCP_URL": "http://memory-server:8000/mcp",
    "MEMORY_AGENT_QUEUE_DB": "/state/queue.db",
    "MEMORY_AGENT_STATE_DIR": "/state",
    "MEMORY_AGENT_TIMEOUT_SECONDS": "600",
    "MEMORY_AGENT_MAX_RETRIES": "2",
    "OPENCODE_HOST": "127.0.0.1",
    "OPENCODE_PORT": "4096",
}


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


def build_env(values: dict[str, str]) -> str:
    return "\n".join(f"{key}={values[key]}" for key in ENV_ORDER) + "\n"


def collect_values() -> dict[str, str]:
    """Ask non-model setup questions only.

    OpenCode auth, model, and reasoning-effort selection intentionally happen later inside
    the memory-agent container so choices match the final runtime environment.
    """
    low_port = _ask("Low-level admin MCP host port", DEFAULTS["LOW_LEVEL_MCP_HOST_PORT"])
    high_port = _ask("High-level user-agent MCP host port", DEFAULTS["HIGH_LEVEL_MCP_HOST_PORT"])
    return {
        "HOST_VAULT_DIR": _ask("Host markdown vault directory", DEFAULTS["HOST_VAULT_DIR"]),
        "LOW_LEVEL_MCP_HOST_PORT": low_port,
        "LOW_LEVEL_MCP_AUTH_TOKEN": _ask("Low-level admin MCP bearer token (empty disables auth)", DEFAULTS["LOW_LEVEL_MCP_AUTH_TOKEN"]),
        "LOW_LEVEL_ALLOWED_HOSTS": _ask("Low-level allowed hosts (comma-separated)", DEFAULTS["LOW_LEVEL_ALLOWED_HOSTS"]),
        "HIGH_LEVEL_MCP_HOST_PORT": high_port,
        "HIGH_LEVEL_MCP_AUTH_TOKEN": _ask("High-level user-agent MCP bearer token (empty disables auth)", DEFAULTS["HIGH_LEVEL_MCP_AUTH_TOKEN"]),
        "HIGH_LEVEL_ALLOWED_HOSTS": _ask("High-level allowed hosts (comma-separated)", DEFAULTS["HIGH_LEVEL_ALLOWED_HOSTS"]),
        "LOW_LEVEL_MCP_URL": _ask("Low-level MCP URL from memory-agent container", DEFAULTS["LOW_LEVEL_MCP_URL"]),
        "MEMORY_AGENT_QUEUE_DB": _ask("Memory-agent queue DB path in container", DEFAULTS["MEMORY_AGENT_QUEUE_DB"]),
        "MEMORY_AGENT_STATE_DIR": _ask("Memory-agent state dir in container", DEFAULTS["MEMORY_AGENT_STATE_DIR"]),
        "MEMORY_AGENT_TIMEOUT_SECONDS": _ask("Recall timeout seconds", DEFAULTS["MEMORY_AGENT_TIMEOUT_SECONDS"]),
        "MEMORY_AGENT_MAX_RETRIES": _ask("Queue retry attempts", DEFAULTS["MEMORY_AGENT_MAX_RETRIES"]),
        "OPENCODE_HOST": _ask("OpenCode internal host", DEFAULTS["OPENCODE_HOST"]),
        "OPENCODE_PORT": _ask("OpenCode internal port", DEFAULTS["OPENCODE_PORT"]),
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
