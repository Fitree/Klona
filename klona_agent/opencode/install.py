"""OpenCode installer for KLONA high-level memory MCP integration."""

from __future__ import annotations

import getpass
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any


BEGIN_MARKER = "<Klona_Memory>"
END_MARKER = "</Klona_Memory>"
LEGACY_BEGIN_MARKER = "<!-- KLONA:BEGIN -->"
LEGACY_END_MARKER = "<!-- KLONA:END -->"
MCP_NAME = "klona_memory"
KLONA_MEMORY_MCP_TIMEOUT_MS = 1_000_000

BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "assets"
SNIPPET_FILE = ASSETS_DIR / "AGENT.md.snippet"
PLUGIN_SOURCE = ASSETS_DIR / "plugins" / "klona-memory-mental-model-injector.js"
PLUGIN_FILENAME = "klona-memory-mental-model-injector.js"
LEGACY_AGENT_FILENAME = "klona-memory.md"


def opencode_dir() -> Path:
    """Return the only supported OpenCode config directory for Phase 1."""
    return Path.home() / ".config" / "opencode"


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise SystemExit(f"failed to parse {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"{path} must contain a JSON object")
    return data


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        dir=path.parent,
        delete=False,
        encoding="utf-8",
    ) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    try:
        tmp_path.replace(path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _write_bytes_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "wb",
        dir=path.parent,
        delete=False,
    ) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    try:
        tmp_path.replace(path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _write_json(path: Path, data: dict[str, Any]) -> None:
    content = json.dumps(data, indent=2, sort_keys=True) + "\n"
    _write_text_atomic(path, content)


def _prompt_mcp_url() -> str:
    try:
        value = input("Klona high-level memory MCP URL: ").strip()
    except (EOFError, KeyboardInterrupt) as exc:
        raise SystemExit("Klona high-level memory MCP URL entry cancelled") from exc
    if not value:
        raise SystemExit("Klona high-level memory MCP URL cannot be empty")
    return value


def _prompt_mcp_token() -> str:
    try:
        value = getpass.getpass("Klona high-level memory bearer token (empty disables auth): ").strip()
    except (EOFError, KeyboardInterrupt) as exc:
        raise SystemExit("Klona high-level memory bearer token entry cancelled") from exc
    return value


def _provided_value(value: str, empty_message: str) -> str:
    value = value.strip()
    if not value:
        raise SystemExit(empty_message)
    return value


def _mcp_entry(url: str, token: str) -> dict[str, Any]:
    entry = {
        "type": "remote",
        "url": url,
        "enabled": True,
        "oauth": False,
        "timeout": KLONA_MEMORY_MCP_TIMEOUT_MS,
    }
    if token:
        entry["headers"] = {"Authorization": f"Bearer {token}"}
    return entry


def _managed_block() -> str:
    if not SNIPPET_FILE.is_file():
        raise SystemExit(f"missing snippet file: {SNIPPET_FILE}")
    snippet = SNIPPET_FILE.read_text().strip()
    return f"{BEGIN_MARKER}\n{snippet}\n{END_MARKER}"


def _check_required_assets() -> None:
    for path in [SNIPPET_FILE, PLUGIN_SOURCE]:
        if not path.is_file():
            raise SystemExit(f"missing required asset: {path}")


def _managed_block_pattern() -> re.Pattern[str]:
    marker_pairs = [
        (BEGIN_MARKER, END_MARKER),
        (LEGACY_BEGIN_MARKER, LEGACY_END_MARKER),
    ]
    alternatives = [
        rf"{re.escape(begin)}.*?{re.escape(end)}" for begin, end in marker_pairs
    ]
    return re.compile(
        "|".join(alternatives),
        re.DOTALL,
    )


def _remove_managed_blocks(text: str) -> str:
    without_blocks = _managed_block_pattern().sub("", text)
    return re.sub(r"\n{3,}", "\n\n", without_blocks)


def _install_marker_block(agent_md: Path) -> None:
    block = _managed_block()
    existing = agent_md.read_text() if agent_md.exists() else ""
    existing = _remove_managed_blocks(existing)
    if existing.strip():
        updated = existing.rstrip("\n") + "\n\n" + block + "\n"
    else:
        updated = block + "\n"
    _write_text_atomic(agent_md, updated)


def _remove_marker_block(agent_md: Path) -> None:
    if not agent_md.exists():
        return
    existing = agent_md.read_text()
    updated = _remove_managed_blocks(existing)
    _write_text_atomic(agent_md, updated if updated.strip() else "")


def _copy_required_asset(src: Path, dst: Path) -> None:
    if not src.is_file():
        raise SystemExit(f"missing required asset: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_name = tempfile.mkstemp(dir=dst.parent)
    tmp_path = Path(tmp_name)
    os.close(tmp_fd)
    try:
        shutil.copy2(src, tmp_path)
        tmp_path.replace(dst)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _snapshot_file(path: Path) -> bytes | None:
    if not path.exists():
        return None
    return path.read_bytes()


def _restore_file(path: Path, snapshot: bytes | None) -> None:
    if snapshot is None:
        if path.exists():
            path.unlink()
        return
    _write_bytes_atomic(path, snapshot)


def _install_mcp_config(
    config_path: Path,
    url: str,
    token: str,
    config: dict[str, Any] | None = None,
) -> None:
    if config is None:
        config = _read_json_object(config_path)
    else:
        config = dict(config)
    mcp = config.get("mcp")
    if not isinstance(mcp, dict):
        mcp = {}
    mcp[MCP_NAME] = _mcp_entry(url, token)
    config["mcp"] = mcp
    _write_json(config_path, config)


def _remove_mcp_config(config_path: Path) -> None:
    if not config_path.exists():
        return
    config = _read_json_object(config_path)
    mcp = config.get("mcp")
    if isinstance(mcp, dict) and MCP_NAME in mcp:
        remaining = dict(mcp)
        remaining.pop(MCP_NAME, None)
        if remaining:
            config["mcp"] = remaining
        else:
            config.pop("mcp", None)
        _write_json(config_path, config)


def install(mcp_url: str | None = None, mcp_token: str | None = None) -> None:
    """Install or refresh KLONA-owned OpenCode files and config."""
    target = opencode_dir()
    config_path = target / "opencode.json"
    agent_md = target / "AGENTS.md"
    legacy_agent_copy = target / "agents" / LEGACY_AGENT_FILENAME
    plugin_copy = target / "plugins" / PLUGIN_FILENAME
    _check_required_assets()
    config = _read_json_object(config_path)
    url = (
        _provided_value(mcp_url, "Klona high-level memory MCP URL cannot be empty")
        if mcp_url is not None
        else _prompt_mcp_url()
    )
    token = (
        mcp_token.strip()
        if mcp_token is not None
        else _prompt_mcp_token()
    )

    snapshots = {
        agent_md: _snapshot_file(agent_md),
        legacy_agent_copy: _snapshot_file(legacy_agent_copy),
        plugin_copy: _snapshot_file(plugin_copy),
        config_path: _snapshot_file(config_path),
    }
    mutation_started = False
    try:
        target.mkdir(parents=True, exist_ok=True)
        mutation_started = True
        _install_marker_block(agent_md)
        legacy_agent_copy.unlink(missing_ok=True)
        _copy_required_asset(PLUGIN_SOURCE, plugin_copy)
        _install_mcp_config(config_path, url, token, config)
    except BaseException:
        if mutation_started:
            for path, snapshot in snapshots.items():
                _restore_file(path, snapshot)
        raise
    print(f"Installed KLONA OpenCode integration in {target}")


def uninstall() -> None:
    """Remove only KLONA-owned OpenCode files and config."""
    target = opencode_dir()
    _remove_marker_block(target / "AGENTS.md")
    for path in [
        target / "agents" / LEGACY_AGENT_FILENAME,
        target / "plugins" / PLUGIN_FILENAME,
    ]:
        if path.exists():
            path.unlink()
    _remove_mcp_config(target / "opencode.json")
    print(f"Uninstalled KLONA OpenCode integration from {target}")


def main() -> int:
    install()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
