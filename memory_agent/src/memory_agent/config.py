"""Environment-backed configuration for the memory agent service."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return float(raw)


def _first_env(names: tuple[str, ...], default: str = "") -> str:
    for name in names:
        value = os.environ.get(name)
        if value not in (None, ""):
            return value
    return default


def _memory_timeout_seconds() -> float:
    return float(_first_env(("MEMORY_AGENT_TIMEOUT_SECONDS", "MEMORY_AGENT_RECALL_TIMEOUT_SECONDS"), "600"))


@dataclass(frozen=True)
class Settings:
    queue_db_path: Path = field(default_factory=lambda: Path(os.environ.get("MEMORY_AGENT_QUEUE_DB", "/state/memory_agent_queue.sqlite3")))
    recall_timeout_seconds: float = field(default_factory=_memory_timeout_seconds)
    processing_lease_seconds: float = field(default_factory=lambda: _float_env("MEMORY_AGENT_PROCESSING_LEASE_SECONDS", _memory_timeout_seconds()))
    max_retries: int = field(default_factory=lambda: _int_env("MEMORY_AGENT_MAX_RETRIES", 2))
    poll_interval_seconds: float = field(default_factory=lambda: _float_env("MEMORY_AGENT_POLL_INTERVAL_SECONDS", 0.5))
    worker_idle_sleep_seconds: float = field(default_factory=lambda: _float_env("MEMORY_AGENT_WORKER_IDLE_SLEEP_SECONDS", 1.0))
    auth_token: str = field(default_factory=lambda: _first_env(("HIGH_LEVEL_MCP_AUTH_TOKEN", "MEMORY_AGENT_AUTH_TOKEN")))
    allowed_hosts: tuple[str, ...] = field(default_factory=lambda: tuple(
        h.strip() for h in _first_env(("HIGH_LEVEL_ALLOWED_HOSTS", "MEMORY_AGENT_ALLOWED_HOSTS")).split(",") if h.strip()
    ))
    opencode_base_url: str = field(default_factory=lambda: os.environ.get("OPENCODE_BASE_URL", "http://127.0.0.1:4096").rstrip("/"))
    opencode_model: str = field(default_factory=lambda: _first_env(("MEMORY_AGENT_MODEL", "OPENCODE_MODEL")))
    opencode_reasoning_effort: str = field(default_factory=lambda: _first_env(("MEMORY_AGENT_REASONING_EFFORT", "OPENCODE_REASONING_EFFORT")))
    low_level_mcp_url: str = field(default_factory=lambda: os.environ.get("LOW_LEVEL_MCP_URL", ""))
    low_level_mcp_auth_token: str = field(default_factory=lambda: os.environ.get("LOW_LEVEL_MCP_AUTH_TOKEN", ""))
    opencode_config_path: Path = field(default_factory=lambda: Path(os.environ.get("OPENCODE_CONFIG_PATH", "/tmp/klona-opencode-config/opencode/opencode.json")))


def load_settings() -> Settings:
    """Return a fresh settings object using the current environment."""
    return Settings()
