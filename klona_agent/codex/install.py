"""Codex installer for KLONA high-level memory MCP integration."""

from __future__ import annotations

import getpass
import json
import os
import re
import shlex
import shutil
import tempfile
import tomllib
from pathlib import Path
from typing import Any


BEGIN_MARKER = "<Klona_Memory>"
END_MARKER = "</Klona_Memory>"
LEGACY_BEGIN_MARKER = "<!-- KLONA:BEGIN -->"
LEGACY_END_MARKER = "<!-- KLONA:END -->"
MCP_NAME = "klona_memory"

BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "assets"
SNIPPET_FILE = ASSETS_DIR / "AGENT.md.snippet"
HOOK_SOURCE = ASSETS_DIR / "hooks" / "klona_mental_model_user_prompt_submit.py"
HOOK_FILENAME = "klona_mental_model_user_prompt_submit.py"
MCP_BEGIN = "# KLONA:BEGIN mcp_servers.klona_memory"
MCP_END = "# KLONA:END mcp_servers.klona_memory"
FEATURE_ADDED_MARKER = "# KLONA:CODEX_HOOKS_ADDED"
FEATURE_PREVIOUS_MARKER_PREFIX = "# KLONA:CODEX_HOOKS_PREVIOUS="
FEATURES_TABLE_ADDED_MARKER = "# KLONA:FEATURES_TABLE_ADDED"
CODEX_HOOKS_KEY_RE = re.compile(r"^\s*codex_hooks\s*=")


def codex_dir() -> Path:
    """Return Codex home, respecting CODEX_HOME and defaulting to ~/.codex."""
    configured = os.environ.get("CODEX_HOME")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".codex"


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


def _toml_string(value: str) -> str:
    return json.dumps(value)


def _managed_block() -> str:
    if not SNIPPET_FILE.is_file():
        raise SystemExit(f"missing snippet file: {SNIPPET_FILE}")
    snippet = SNIPPET_FILE.read_text().strip()
    return f"{BEGIN_MARKER}\n{snippet}\n{END_MARKER}"


def _check_required_assets() -> None:
    for path in [SNIPPET_FILE, HOOK_SOURCE]:
        if not path.is_file():
            raise SystemExit(f"missing required asset: {path}")


def _managed_block_pattern() -> re.Pattern[str]:
    return re.compile(
        "|".join(
            [
                rf"{re.escape(BEGIN_MARKER)}.*?{re.escape(END_MARKER)}",
                rf"{re.escape(LEGACY_BEGIN_MARKER)}.*?{re.escape(LEGACY_END_MARKER)}",
            ]
        ),
        re.DOTALL,
    )


def _remove_managed_blocks(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", _managed_block_pattern().sub("", text))


def _install_marker_block(agent_md: Path) -> None:
    existing = _remove_managed_blocks(agent_md.read_text() if agent_md.exists() else "")
    block = _managed_block()
    updated = existing.rstrip("\n") + "\n\n" + block + "\n" if existing.strip() else block + "\n"
    _write_text_atomic(agent_md, updated)


def _remove_marker_block(agent_md: Path) -> None:
    if not agent_md.exists():
        return
    updated = _remove_managed_blocks(agent_md.read_text())
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
    return path.read_bytes() if path.exists() else None


def _restore_file(path: Path, snapshot: bytes | None) -> None:
    if snapshot is None:
        path.unlink(missing_ok=True)
    else:
        _write_bytes_atomic(path, snapshot)


def _mcp_block(url: str, token: str) -> str:
    lines = [
        MCP_BEGIN,
        f"[mcp_servers.{MCP_NAME}]",
        f"url = {_toml_string(url)}",
        "enabled = true",
    ]
    if token:
        lines.append(f"http_headers = {{ \"Authorization\" = {_toml_string('Bearer ' + token)} }}")
    lines.append(MCP_END)
    return "\n".join(lines)


def _remove_mcp_block(text: str) -> str:
    pattern = re.compile(rf"\n?{re.escape(MCP_BEGIN)}.*?{re.escape(MCP_END)}\n?", re.DOTALL)
    return re.sub(r"\n{3,}", "\n\n", pattern.sub("\n", text))


def _table_name(line: str) -> str | None:
    stripped = line.lstrip()
    if stripped.startswith("[["):
        opener_len = 2
        closer = "]]"
    elif stripped.startswith("["):
        opener_len = 1
        closer = "]"
    else:
        return None

    quote: str | None = None
    escaped = False
    i = opener_len
    while i < len(stripped):
        char = stripped[i]
        if quote:
            if quote == '"' and escaped:
                escaped = False
            elif quote == '"' and char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            i += 1
            continue
        if char in {'"', "'"}:
            quote = char
            i += 1
            continue
        if stripped.startswith(closer, i):
            name = stripped[opener_len:i].strip()
            rest = stripped[i + len(closer):].strip()
            if name and (not rest or rest.startswith("#")):
                return name
            return None
        i += 1
    return None


def _is_table_header(line: str) -> bool:
    return _table_name(line) is not None


def _table_path_parts(table_name: str) -> list[str] | None:
    parts: list[str] = []
    current: list[str] = []
    quote: str | None = None
    escaped = False
    quoted_part = False
    i = 0
    while i < len(table_name):
        char = table_name[i]
        if quote:
            current.append(char)
            if quote == '"' and escaped:
                escaped = False
            elif quote == '"' and char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            i += 1
            continue
        if char in {'"', "'"}:
            if current and "".join(current).strip():
                return None
            quote = char
            quoted_part = True
            current.append(char)
            i += 1
            continue
        if char == ".":
            part = _decode_table_path_part("".join(current).strip(), quoted_part)
            if part is None:
                return None
            parts.append(part)
            current = []
            quoted_part = False
            i += 1
            continue
        current.append(char)
        i += 1

    if quote:
        return None
    part = _decode_table_path_part("".join(current).strip(), quoted_part)
    if part is None:
        return None
    parts.append(part)
    return parts


def _decode_table_path_part(part: str, quoted: bool) -> str | None:
    if not part:
        return None
    if quoted:
        if part.startswith('"') and part.endswith('"'):
            try:
                decoded = json.loads(part)
            except json.JSONDecodeError:
                return None
            return decoded if isinstance(decoded, str) else None
        if part.startswith("'") and part.endswith("'"):
            return part[1:-1]
        return None
    return part


def _table_path_equals(line: str, expected: list[str]) -> bool:
    table_name = _table_name(line)
    if table_name is None:
        return False
    return _table_path_parts(table_name) == expected


def _find_table(lines: list[str], table_name: str) -> tuple[int | None, int]:
    section_start = None
    section_end = len(lines)
    for i, line in enumerate(lines):
        expected = table_name.split(".")
        if _table_path_equals(line, expected):
            section_start = i
            for j in range(i + 1, len(lines)):
                if _is_table_header(lines[j]):
                    section_end = j
                    break
            break
    return section_start, section_end


def _line_is_blank_or_comment(line: str) -> bool:
    stripped = line.strip()
    return not stripped or stripped.startswith("#")


def _remove_mcp_table(text: str) -> str:
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        if _table_path_equals(lines[i], ["mcp_servers", MCP_NAME]):
            i += 1
            while i < len(lines):
                if _is_table_header(lines[i]):
                    break
                i += 1
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out) + ("\n" if text.endswith("\n") and out else "")


def _install_codex_hooks_feature(text: str) -> str:
    lines = text.splitlines()
    section_start, section_end = _find_table(lines, "features")

    if section_start is None:
        suffix = "" if not text or text.endswith("\n") else "\n"
        return text + suffix + FEATURES_TABLE_ADDED_MARKER + "\n[features]\n" + FEATURE_ADDED_MARKER + "\ncodex_hooks = true\n"

    for i in range(section_start + 1, section_end):
        stripped = lines[i].strip()
        if CODEX_HOOKS_KEY_RE.match(stripped):
            value = stripped.split("=", 1)[1].strip().split("#", 1)[0].strip()
            if value == "true":
                return text
            lines.insert(i, f"{FEATURE_PREVIOUS_MARKER_PREFIX}{value}")
            lines[i + 1] = "codex_hooks = true"
            return "\n".join(lines) + ("\n" if text.endswith("\n") else "")

    lines.insert(section_start + 1, FEATURE_ADDED_MARKER)
    lines.insert(section_start + 2, "codex_hooks = true")
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def _remove_codex_hooks_feature(text: str) -> str:
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip() == FEATURE_ADDED_MARKER and i + 1 < len(lines) and CODEX_HOOKS_KEY_RE.match(lines[i + 1].strip()):
            i += 2
            continue
        if line.strip().startswith(FEATURE_PREVIOUS_MARKER_PREFIX) and i + 1 < len(lines) and CODEX_HOOKS_KEY_RE.match(lines[i + 1].strip()):
            previous = line.strip()[len(FEATURE_PREVIOUS_MARKER_PREFIX):]
            out.append(f"codex_hooks = {previous}")
            i += 2
            continue
        out.append(line)
        i += 1
    return "\n".join(out) + ("\n" if text.endswith("\n") and out else "")


def _remove_klona_created_empty_features_table(text: str) -> str:
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        if lines[i].strip() != FEATURES_TABLE_ADDED_MARKER:
            out.append(lines[i])
            i += 1
            continue

        if i + 1 >= len(lines) or not _table_path_equals(lines[i + 1], ["features"]):
            out.append(lines[i])
            i += 1
            continue

        j = i + 2
        body: list[str] = []
        while j < len(lines) and not _is_table_header(lines[j]):
            body.append(lines[j])
            j += 1
        if any(not _line_is_blank_or_comment(line) for line in body) or any(line.strip().startswith("#") for line in body):
            out.append(lines[i + 1])
            out.extend(body)
        i = j
    return "\n".join(out) + ("\n" if text.endswith("\n") and out else "")


def _validate_toml(text: str, path: Path) -> None:
    try:
        tomllib.loads(text or "")
    except tomllib.TOMLDecodeError as exc:
        raise SystemExit(f"failed to parse {path}: {exc}") from exc


def _install_mcp_config(config_path: Path, url: str, token: str) -> None:
    existing = config_path.read_text() if config_path.exists() else ""
    _validate_toml(existing, config_path)
    without_owned = _remove_mcp_table(_remove_mcp_block(existing)).rstrip()
    with_feature = _install_codex_hooks_feature(without_owned + ("\n" if without_owned else ""))
    separator = "" if not with_feature or with_feature.endswith("\n") else "\n"
    updated = with_feature + separator + _mcp_block(url, token) + "\n"
    _validate_toml(updated, config_path)
    _write_text_atomic(config_path, updated)


def _remove_mcp_config(config_path: Path) -> None:
    if not config_path.exists():
        return
    _validate_toml(config_path.read_text(), config_path)
    updated = _remove_klona_created_empty_features_table(
        _remove_codex_hooks_feature(_remove_mcp_block(config_path.read_text()))
    )
    _validate_toml(updated, config_path)
    if updated.strip():
        _write_text_atomic(config_path, updated)
    else:
        config_path.unlink()


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


def _hook_command(hook_path: Path) -> str:
    return f"python3 {shlex.quote(str(hook_path))}"


def _remove_owned_hook_entries(data: dict[str, Any], hook_path: Path) -> dict[str, Any]:
    owned_command = _hook_command(hook_path)
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return data
    for event, entries in list(hooks.items()):
        if not isinstance(entries, list):
            continue
        kept_entries = []
        for entry in entries:
            entry_hooks = entry.get("hooks") if isinstance(entry, dict) else None
            if not isinstance(entry_hooks, list):
                kept_entries.append(entry)
                continue
            kept_hooks = [
                h for h in entry_hooks
                if not (isinstance(h, dict) and h.get("command") == owned_command)
            ]
            if kept_hooks:
                cloned = dict(entry)
                cloned["hooks"] = kept_hooks
                kept_entries.append(cloned)
        if kept_entries:
            hooks[event] = kept_entries
        else:
            hooks.pop(event, None)
    if not hooks:
        data.pop("hooks", None)
    return data


def _install_hooks_config(hooks_path: Path, hook_path: Path) -> None:
    data = _remove_owned_hook_entries(_read_json_object(hooks_path), hook_path)
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
    prompt_entries = hooks.get("UserPromptSubmit")
    if not isinstance(prompt_entries, list):
        prompt_entries = []
    prompt_entries.append(
        {
            "hooks": [
                {
                    "type": "command",
                    "command": _hook_command(hook_path),
                    "statusMessage": "Loading Klona memory mental model",
                }
            ]
        }
    )
    hooks["UserPromptSubmit"] = prompt_entries
    data["hooks"] = hooks
    _write_json(hooks_path, data)


def _remove_hooks_config(hooks_path: Path, hook_path: Path) -> None:
    if not hooks_path.exists():
        return
    data = _remove_owned_hook_entries(_read_json_object(hooks_path), hook_path)
    if data:
        _write_json(hooks_path, data)
    else:
        hooks_path.unlink()


def _remove_empty_file(path: Path) -> None:
    if path.exists() and path.is_file() and not path.read_text().strip():
        path.unlink()


def _remove_empty_dir(path: Path) -> None:
    try:
        path.rmdir()
    except OSError:
        pass


def install(mcp_url: str | None = None, mcp_token: str | None = None) -> None:
    """Install or refresh KLONA-owned Codex files and config."""
    target = codex_dir()
    config_path = target / "config.toml"
    agent_md = target / "AGENTS.md"
    hook_copy = target / "hooks" / HOOK_FILENAME
    hooks_json = target / "hooks.json"
    _check_required_assets()
    url = _provided_value(mcp_url, "Klona high-level memory MCP URL cannot be empty") if mcp_url is not None else _prompt_mcp_url()
    token = mcp_token.strip() if mcp_token is not None else _prompt_mcp_token()

    snapshots = {path: _snapshot_file(path) for path in [agent_md, hook_copy, config_path, hooks_json]}
    target_existed = target.exists()
    hooks_dir = target / "hooks"
    hooks_dir_existed = hooks_dir.exists()
    mutation_started = False
    try:
        target.mkdir(parents=True, exist_ok=True)
        mutation_started = True
        _install_marker_block(agent_md)
        _copy_required_asset(HOOK_SOURCE, hook_copy)
        _install_hooks_config(hooks_json, hook_copy)
        _install_mcp_config(config_path, url, token)
    except BaseException:
        if mutation_started:
            for path, snapshot in snapshots.items():
                _restore_file(path, snapshot)
            if not hooks_dir_existed:
                _remove_empty_dir(hooks_dir)
            if not target_existed:
                _remove_empty_dir(target)
        raise
    print(f"Installed KLONA Codex integration in {target}")


def uninstall() -> None:
    """Remove only KLONA-owned Codex files and config."""
    target = codex_dir()
    agent_md = target / "AGENTS.md"
    _remove_marker_block(agent_md)
    _remove_empty_file(agent_md)
    hook_copy = target / "hooks" / HOOK_FILENAME
    hook_copy.unlink(missing_ok=True)
    _remove_empty_dir(target / "hooks")
    _remove_hooks_config(target / "hooks.json", hook_copy)
    _remove_mcp_config(target / "config.toml")
    print(f"Uninstalled KLONA Codex integration from {target}")


def main() -> int:
    install()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
