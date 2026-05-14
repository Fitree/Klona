"""Claude Code installer for KLONA high-level memory MCP integration."""

from __future__ import annotations

import getpass
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MCP_NAME = "klona_memory"
PLUGIN_ID = "klona-memory-plugin"
MARKETPLACE_ID = "klona-plugins"
PLUGIN_VERSION = "0.1.0"
INSTALLED_PLUGIN_KEY = f"{PLUGIN_ID}@{MARKETPLACE_ID}"
OWNER_MARKER = ".klona-owned"

BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "assets"
PLUGIN_ASSETS_DIR = ASSETS_DIR / "plugin"
REQUIRED_ASSETS = [
    PLUGIN_ASSETS_DIR / ".claude-plugin" / "plugin.json",
    PLUGIN_ASSETS_DIR / ".claude-plugin" / "marketplace.json",
    PLUGIN_ASSETS_DIR / "hooks" / "hooks.json",
    PLUGIN_ASSETS_DIR / "hooks" / "session-start.js",
    PLUGIN_ASSETS_DIR / "hooks" / "user-prompt-submit.js",
    PLUGIN_ASSETS_DIR / "instructions" / "klona-memory.md",
]


def claude_dir() -> Path:
    return Path.home() / ".claude"


def plugin_dir() -> Path:
    return claude_dir() / "plugins" / PLUGIN_ID


def cache_plugin_dir() -> Path:
    return claude_dir() / "plugins" / "cache" / MARKETPLACE_ID / PLUGIN_ID / PLUGIN_VERSION


def known_marketplaces_file() -> Path:
    return claude_dir() / "plugins" / "known_marketplaces.json"


def installed_plugins_file() -> Path:
    return claude_dir() / "plugins" / "installed_plugins.json"


def settings_file() -> Path:
    return claude_dir() / "settings.json"


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, encoding="utf-8") as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    try:
        tmp_path.replace(path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _write_bytes_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    try:
        tmp_path.replace(path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _write_json(path: Path, data: dict[str, Any]) -> None:
    _write_text_atomic(path, json.dumps(data, indent=2, sort_keys=True) + "\n")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json_object(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return dict(default)
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid JSON in {path}; refusing to overwrite it") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"expected JSON object in {path}; refusing to overwrite it")
    return data


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
        return getpass.getpass("Klona high-level memory bearer token (empty disables auth): ").strip()
    except (EOFError, KeyboardInterrupt) as exc:
        raise SystemExit("Klona high-level memory bearer token entry cancelled") from exc


def _provided_value(value: str, empty_message: str) -> str:
    value = value.strip()
    if not value:
        raise SystemExit(empty_message)
    return value


def _mcp_config(url: str, token: str) -> dict[str, Any]:
    entry: dict[str, Any] = {"type": "http", "url": url}
    if token:
        entry["headers"] = {"Authorization": f"Bearer {token}"}
    return {"mcpServers": {MCP_NAME: entry}}


def _check_required_assets() -> None:
    for path in REQUIRED_ASSETS:
        if not path.is_file():
            raise SystemExit(f"missing required asset: {path}")


def _snapshot_path(path: Path) -> bytes | dict[str, bytes | None] | None:
    if not path.exists():
        return None
    if path.is_file():
        return path.read_bytes()
    snapshot: dict[str, bytes | None] = {}
    for child in path.rglob("*"):
        rel = str(child.relative_to(path))
        snapshot[rel] = None if child.is_dir() else child.read_bytes()
    return snapshot


def _restore_path(path: Path, snapshot: bytes | dict[str, bytes | None] | None) -> None:
    if snapshot is None:
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
        return
    if isinstance(snapshot, bytes):
        if path.is_dir():
            shutil.rmtree(path)
        _write_bytes_atomic(path, snapshot)
        return
    if path.exists():
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    path.mkdir(parents=True, exist_ok=True)
    for rel, content in snapshot.items():
        dst = path / rel
        if content is None:
            dst.mkdir(parents=True, exist_ok=True)
        else:
            _write_bytes_atomic(dst, content)


def _copy_plugin_assets(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for src in PLUGIN_ASSETS_DIR.rglob("*"):
        rel = src.relative_to(PLUGIN_ASSETS_DIR)
        dst = destination / rel
        if src.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_name = tempfile.mkstemp(dir=dst.parent)
        os.close(tmp_fd)
        tmp_path = Path(tmp_name)
        try:
            shutil.copy2(src, tmp_path)
            tmp_path.replace(dst)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise
    _write_text_atomic(destination / OWNER_MARKER, "KLONA Claude Code integration owns this plugin directory.\n")


def _historical_klona_plugin_dir(path: Path) -> bool:
    manifest = path / ".claude-plugin" / "plugin.json"
    if not path.is_dir() or not manifest.is_file():
        return False
    try:
        data = json.loads(manifest.read_text())
    except json.JSONDecodeError:
        return False
    return isinstance(data, dict) and data.get("name") == PLUGIN_ID


def _prepare_plugin_target(path: Path) -> None:
    if not path.exists():
        return
    if not path.is_dir():
        raise SystemExit(f"refusing to replace non-directory plugin path: {path}")
    if _is_owned_plugin_dir(path) or _historical_klona_plugin_dir(path):
        shutil.rmtree(path)
        return
    raise SystemExit(f"refusing to overwrite unrecognized existing Claude plugin directory: {path}")


def _registry_data() -> tuple[dict[str, Any], dict[str, Any]]:
    return _read_json_object(known_marketplaces_file(), {}), _read_json_object(installed_plugins_file(), {})


def _write_known_marketplaces(source_root: Path, data: dict[str, Any]) -> None:
    data[MARKETPLACE_ID] = {
        "source": {"source": "directory", "path": str(source_root)},
        "installLocation": str(source_root),
        "lastUpdated": _now_iso(),
    }
    _write_json(known_marketplaces_file(), data)


def _is_this_installed_record(record: Any, cache_root: Path) -> bool:
    return (
        isinstance(record, dict)
        and record.get("scope") == "user"
        and record.get("installPath") == str(cache_root)
        and record.get("version") == PLUGIN_VERSION
    )


def _write_installed_plugins(cache_root: Path, data: dict[str, Any]) -> None:
    data["version"] = data.get("version", 2)
    plugins = data.get("plugins")
    if not isinstance(plugins, dict):
        if plugins is not None:
            raise SystemExit(f"expected plugins object in {installed_plugins_file()}; refusing to overwrite it")
        plugins = {}
        data["plugins"] = plugins
    now = _now_iso()
    existing = plugins.get(INSTALLED_PLUGIN_KEY)
    if existing is not None and not isinstance(existing, list):
        raise SystemExit(f"expected {INSTALLED_PLUGIN_KEY} records list in {installed_plugins_file()}; refusing to overwrite it")
    records = existing if isinstance(existing, list) else []
    records = [record for record in records if not _is_this_installed_record(record, cache_root)]
    records.append(
        {
            "scope": "user",
            "installPath": str(cache_root),
            "version": PLUGIN_VERSION,
            "installedAt": now,
            "lastUpdated": now,
            "gitCommitSha": "unknown",
        }
    )
    plugins[INSTALLED_PLUGIN_KEY] = records
    _write_json(installed_plugins_file(), data)


def _write_enabled_plugin_settings(data: dict[str, Any]) -> None:
    enabled_plugins = data.get("enabledPlugins")
    if not isinstance(enabled_plugins, dict):
        if enabled_plugins is not None:
            raise SystemExit(f"expected enabledPlugins object in {settings_file()}; refusing to overwrite it")
        enabled_plugins = {}
        data["enabledPlugins"] = enabled_plugins
    enabled_plugins[INSTALLED_PLUGIN_KEY] = True
    _write_json(settings_file(), data)


def _write_registry_files(source_root: Path, cache_root: Path, known_data: dict[str, Any] | None = None, installed_data: dict[str, Any] | None = None) -> None:
    if known_data is None or installed_data is None:
        known_data, installed_data = _registry_data()
    _write_known_marketplaces(source_root, known_data)
    _write_installed_plugins(cache_root, installed_data)


def _is_owned_plugin_dir(path: Path) -> bool:
    return path.is_dir() and (path / OWNER_MARKER).is_file()


def _remove_enabled_plugin_settings(data: dict[str, Any] | None = None) -> None:
    path = settings_file()
    if data is None:
        data = _read_json_object(path, {})
    enabled_plugins = data.get("enabledPlugins")
    if not isinstance(enabled_plugins, dict):
        return
    if INSTALLED_PLUGIN_KEY not in enabled_plugins:
        return
    del enabled_plugins[INSTALLED_PLUGIN_KEY]
    if not enabled_plugins:
        del data["enabledPlugins"]
    _write_json(path, data)


def _remove_owned_marketplace_entry(source_removed: bool) -> bool:
    if not source_removed and plugin_dir().exists():
        return False
    path = known_marketplaces_file()
    data = _read_json_object(path, {})
    entry = data.get(MARKETPLACE_ID)
    source_target = str(plugin_dir())
    source = entry.get("source") if isinstance(entry, dict) else None
    points_to_source = isinstance(entry, dict) and (
        entry.get("installLocation") == source_target
        or (isinstance(source, dict) and source.get("source") == "directory" and source.get("path") == source_target)
    )
    if points_to_source:
        del data[MARKETPLACE_ID]
        _write_json(path, data)
        return True
    return False


def _remove_owned_installed_plugin_entry(cache_removed: bool) -> bool:
    if not cache_removed and cache_plugin_dir().exists():
        return False
    path = installed_plugins_file()
    data = _read_json_object(path, {})
    plugins = data.get("plugins")
    if not isinstance(plugins, dict):
        return
    entry = plugins.get(INSTALLED_PLUGIN_KEY)
    records = entry if isinstance(entry, list) else []
    remaining = [record for record in records if not _is_this_installed_record(record, cache_plugin_dir())]
    if len(remaining) != len(records):
        if remaining:
            plugins[INSTALLED_PLUGIN_KEY] = remaining
        else:
            del plugins[INSTALLED_PLUGIN_KEY]
        _write_json(path, data)
        return True
    return False


def install(mcp_url: str | None = None, mcp_token: str | None = None) -> None:
    """Install or refresh KLONA-owned Claude Code plugin files and config."""
    _check_required_assets()
    url = (_provided_value(mcp_url, "Klona high-level memory MCP URL cannot be empty") if mcp_url is not None else _prompt_mcp_url())
    token = mcp_token.strip() if mcp_token is not None else _prompt_mcp_token()
    source_target = plugin_dir()
    cache_target = cache_plugin_dir()
    known_data, installed_data = _registry_data()
    settings_data = _read_json_object(settings_file(), {})
    snapshots = {
        source_target: _snapshot_path(source_target),
        cache_target: _snapshot_path(cache_target),
        known_marketplaces_file(): _snapshot_path(known_marketplaces_file()),
        installed_plugins_file(): _snapshot_path(installed_plugins_file()),
        settings_file(): _snapshot_path(settings_file()),
    }
    mutation_started = False
    try:
        mutation_started = True
        for target in [source_target, cache_target]:
            _prepare_plugin_target(target)
        for target in [source_target, cache_target]:
            _copy_plugin_assets(target)
            _write_json(target / ".mcp.json", _mcp_config(url, token))
        _write_registry_files(source_target, cache_target, known_data, installed_data)
        _write_enabled_plugin_settings(settings_data)
    except BaseException:
        if mutation_started:
            for path, snapshot in snapshots.items():
                _restore_path(path, snapshot)
        raise
    print(f"Installed KLONA Claude Code integration in {source_target}")


def uninstall() -> None:
    """Remove only KLONA-owned Claude Code plugin files and registry entries."""
    source_target = plugin_dir()
    cache_target = cache_plugin_dir()
    _registry_data()
    settings_data = _read_json_object(settings_file(), {})
    source_removed = False
    cache_removed = False
    if _is_owned_plugin_dir(source_target):
        shutil.rmtree(source_target)
        source_removed = True
    if _is_owned_plugin_dir(cache_target):
        shutil.rmtree(cache_target)
        cache_removed = True
    marketplace_removed = _remove_owned_marketplace_entry(source_removed)
    installed_plugin_removed = _remove_owned_installed_plugin_entry(cache_removed)
    if source_removed or cache_removed or marketplace_removed or installed_plugin_removed:
        _remove_enabled_plugin_settings(settings_data)
    print(f"Uninstalled KLONA Claude Code integration from {source_target}")


def main() -> int:
    install()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
