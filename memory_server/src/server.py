"""Klona Memory MCP Server — markdown knowledge base as a mounted filesystem."""

import asyncio
import contextlib
import logging
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
VAULT_DIR = Path(os.environ.get("VAULT_DIR", "/vault"))
AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "")
# Comma-separated list of allowed Host header values for DNS rebinding protection.
# Supports wildcard ports like "example.com:*". Empty disables protection (Bearer auth still applies).
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h.strip()]

SERVER_INSTRUCTIONS = """\
You are connected to the Klona Memory Server — a markdown knowledge base.

The vault is a directory of markdown files organized hierarchically.
Navigate it like a filesystem: tree → ls → read → follow [[wikilinks]].

Tools:
  vault_tree      — get the directory structure (start here)
  vault_ls        — list files in a directory
  vault_read      — read a file
  vault_write     — create a new file (fails if exists)
  vault_update    — update an existing file (fails if not exists)
  vault_delete    — delete a file
  vault_move      — move or rename a file
  vault_mkdir     — create a directory
  vault_grep      — search file contents by keyword
  vault_backlinks — find files that link to a given file

Wikilinks use the filename stem only — no path, no extension.
  Correct:   [[profile]]
  Wrong:     [[leo/profile]], [[profile.md]]
  Aliased:   [[profile|Leo's Profile]]
The server auto-rewrites malformed wikilinks to stem-only form.

The server auto-manages the `updated` frontmatter field on writes.
Filenames must be globally unique across the vault (wikilinks resolve by stem, so duplicates would be ambiguous).
All paths start with / (vault root) and directories end with /.
"""

# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------
if ALLOWED_HOSTS:
    _transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=ALLOWED_HOSTS,
    )
else:
    _transport_security = TransportSecuritySettings(enable_dns_rebinding_protection=False)

mcp = FastMCP(
    "klona-memory-server",
    instructions=SERVER_INSTRUCTIONS,
    json_response=True,
    transport_security=_transport_security,
)


# ---------------------------------------------------------------------------
# Read-Write Lock
# ---------------------------------------------------------------------------
class RWLock:
    """Async read-write lock. Multiple readers, exclusive writer."""

    def __init__(self):
        self._readers = 0
        self._readers_lock = asyncio.Lock()
        self._writer_lock = asyncio.Lock()

    @contextlib.asynccontextmanager
    async def read(self):
        async with self._readers_lock:
            self._readers += 1
            if self._readers == 1:
                await self._writer_lock.acquire()
        try:
            yield
        finally:
            async with self._readers_lock:
                self._readers -= 1
                if self._readers == 0:
                    self._writer_lock.release()

    @contextlib.asynccontextmanager
    async def write(self):
        await self._writer_lock.acquire()
        try:
            yield
        finally:
            self._writer_lock.release()


vault_lock = RWLock()
_maintenance_locked = False


# ---------------------------------------------------------------------------
# VaultPath — single object, two views:
#   .vault_path      — normalized string: "/projects/klona.md" or "/projects/"
#   .filesystem_path — filesystem Path:   Path("/vault/projects/klona.md")
# ---------------------------------------------------------------------------

class VaultPath:
    """Unified vault path. Accepts any format, provides .vault_path and .filesystem_path views."""

    def __init__(self, path: str | Path):
        # 1. Convert to Path
        p = Path(path)
        vault_resolved = VAULT_DIR.resolve()

        # 2. Already under vault? Use directly. Otherwise, treat as relative to vault.
        resolved = p.resolve()
        if str(resolved).startswith(str(vault_resolved)):
            abs_path = resolved
        else:
            # Strip leading / so Path("/projects") becomes relative "projects"
            abs_path = (VAULT_DIR / Path(str(p).strip("/"))).resolve()

        # 3. Safety check
        if not str(abs_path).startswith(str(vault_resolved)):
            raise ValueError(f"Path escapes vault: {path}")

        self.filesystem_path: Path = abs_path

        rel = str(abs_path.relative_to(VAULT_DIR.resolve())).strip("/")
        if not rel or rel == ".":
            self.vault_path = "/"
            return

        # Determine dir vs file: check filesystem first, fall back to extension
        if abs_path.exists():
            is_dir = abs_path.is_dir()
        else:
            is_dir = not abs_path.suffix

        if is_dir:
            self.vault_path = "/" + rel + "/"
        else:
            self.vault_path = "/" + rel

    @property
    def stem(self) -> str:
        return self.filesystem_path.stem

    @property
    def name(self) -> str:
        return self.filesystem_path.name

    @property
    def parent(self) -> "VaultPath":
        return VaultPath(self.filesystem_path.parent, is_dir=True)

    def exists(self) -> bool:
        return self.filesystem_path.exists()

    def is_file(self) -> bool:
        return self.filesystem_path.is_file()

    def is_dir(self) -> bool:
        return self.filesystem_path.is_dir()

    def __str__(self) -> str:
        return self.vault_path

    def __repr__(self) -> str:
        return f"VaultPath({self.vault_path!r})"


# ---------------------------------------------------------------------------
# Backlink Cache — all paths stored in normalized format
# ---------------------------------------------------------------------------
# Maps: link_target_stem -> set of normalized source file paths
_backlink_map: dict[str, set[str]] = {}

WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]")


def _parse_wikilinks(content: str) -> list[str]:
    """Extract wikilink targets (normalized to stem) from markdown content."""
    return [Path(t).stem for t in WIKILINK_RE.findall(content)]


def _sanitize_wikilinks(content: str) -> tuple[str, list[dict]]:
    """Rewrite malformed wikilinks to stem-only form. Returns (fixed_content, warnings)."""
    warnings: list[dict] = []

    def _fix(m: re.Match) -> str:
        raw_target = m.group(1)
        stem = Path(raw_target).stem
        alias = m.group(2) or ""  # "|display" part or ""
        if raw_target != stem:
            warnings.append({
                "type": "wikilink_rewritten",
                "original": raw_target,
                "rewritten": stem,
            })
            return f"[[{stem}{alias}]]"
        return m.group(0)

    # Regex captures: group(1)=target, group(2)=|alias (optional, includes the |)
    fixed = re.sub(r"\[\[([^\]|]+)(\|[^\]]*)?\]\]", _fix, content)
    return fixed, warnings


def _build_backlink_cache():
    """Full scan: build backlink map from all .md files."""
    _backlink_map.clear()
    for md_file in VAULT_DIR.rglob("*.md"):
        source = VaultPath(md_file).vault_path
        for target in _parse_wikilinks(md_file.read_text(encoding="utf-8")):
            _backlink_map.setdefault(target, set()).add(source)
    logger.info("Backlink cache built: %d targets", len(_backlink_map))


def _update_backlinks(source_vault_path: str, old_content: str | None, new_content: str):
    """Update backlink map when a file is written/updated."""
    if old_content:
        for target in _parse_wikilinks(old_content):
            if target in _backlink_map:
                _backlink_map[target].discard(source_vault_path)
                if not _backlink_map[target]:
                    del _backlink_map[target]
    for target in _parse_wikilinks(new_content):
        _backlink_map.setdefault(target, set()).add(source_vault_path)


def _remove_backlinks(source_vault_path: str, content: str):
    """Remove all outgoing backlink entries for a deleted/moved file."""
    for target in _parse_wikilinks(content):
        if target in _backlink_map:
            _backlink_map[target].discard(source_vault_path)
            if not _backlink_map[target]:
                del _backlink_map[target]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ensure_vault() -> Path:
    VAULT_DIR.mkdir(parents=True, exist_ok=True)
    return VAULT_DIR


def _atomic_write(filepath: Path, content: str) -> None:
    """Write file atomically via temp file + rename."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=filepath.parent, suffix=".tmp", prefix=".klona_"
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.rename(tmp_path, filepath)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _stamp_frontmatter(content: str) -> str:
    """Add or update the `updated` field in YAML frontmatter."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    fm_match = re.match(r"^---\n(.*?)\n---\n(.*)", content, re.DOTALL)
    if fm_match:
        fm_text, body = fm_match.group(1), fm_match.group(2)
        if re.search(r"^updated:", fm_text, re.MULTILINE):
            fm_text = re.sub(r"^updated:.*$", f"updated: {now}", fm_text, flags=re.MULTILINE)
        else:
            fm_text += f"\nupdated: {now}"
        return f"---\n{fm_text}\n---\n{body}"
    else:
        return f"---\nupdated: {now}\n---\n\n{content}"


def _get_updated(content: str) -> str | None:
    """Extract the updated field from frontmatter."""
    fm_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if fm_match:
        for line in fm_match.group(1).split("\n"):
            if line.startswith("updated:"):
                return line.split(":", 1)[1].strip()
    return None


def _find_duplicate(vp: VaultPath, exclude: VaultPath | None = None) -> dict | None:
    """Check if a file with the same name exists at a different path."""
    for md_file in VAULT_DIR.rglob(vp.name):
        if md_file.resolve() == vp.filesystem_path.resolve():
            continue
        if exclude and md_file.resolve() == exclude.filesystem_path.resolve():
            continue
        return {
            "error": "duplicate_filename",
            "message": f"Filename '{vp.name}' already exists at '{VaultPath(md_file).vault_path}'",
        }
    return None


def _find_broken_links(content: str) -> list[dict]:
    """Check wikilinks in content for broken targets."""
    warnings = []
    for i, line in enumerate(content.split("\n"), 1):
        for target in WIKILINK_RE.findall(line):
            stem = Path(target).stem
            found = any(VAULT_DIR.rglob(f"{stem}.md"))
            if not found:
                warnings.append({"type": "broken_link", "target": stem, "line": i})
    return warnings


def _check_maintenance() -> dict | None:
    """Return error dict if maintenance lock is active."""
    if _maintenance_locked:
        return {"error": "locked", "message": "Vault is locked for maintenance"}
    return None


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def vault_tree() -> dict:
    """Returns the directory structure of the vault as a nested dictionary. Directories only, no files.
    Use this to orient yourself at the start of a session. Empty dict {} means a leaf directory.

    Returns: {"a": {"b": {"c": {}}}, "d": {}}
    """
    async with vault_lock.read():
        vault = _ensure_vault()
        tree: dict = {}
        for d in sorted(vault.rglob("*")):
            if d.is_dir() and not d.name.startswith("."):
                parts = d.relative_to(vault).parts
                node = tree
                for part in parts:
                    node = node.setdefault(part, {})
        return tree


@mcp.tool()
async def vault_ls(path: str) -> dict:
    """Lists files and immediate subdirectories at the given path.

    Args:
        path: Directory path relative to vault root (e.g. "/" or "/a/").

    Returns: {"path": "/a/", "dirs": ["/a/b/"], "files": [{"name": "foo.md", "updated": "2026-01-01T00:00:00Z"}]}
    """
    async with vault_lock.read():
        try:
            vp = VaultPath(path)
        except ValueError as e:
            return {"error": "invalid_path", "message": str(e)}

        if not vp.exists() or not vp.is_dir():
            return {"error": "path_not_found", "message": f"Directory not found: {path}"}

        dirs = sorted(
            VaultPath(d).vault_path
            for d in vp.filesystem_path.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        )
        files = []
        for f in sorted(vp.filesystem_path.iterdir()):
            if f.is_file() and f.suffix == ".md":
                content = f.read_text(encoding="utf-8")
                files.append({"name": f.name, "updated": _get_updated(content)})

        return {"path": vp.vault_path, "dirs": dirs, "files": files}


@mcp.tool()
async def vault_read(path: str) -> dict:
    """Reads the full content of a markdown file.

    Args:
        path: File path relative to vault root (e.g. "/a/foo.md").

    Returns: {"path": "/a/foo.md", "content": "# Foo\n...", "links": ["bar", "baz"]}
        links contains the stem of each [[wikilink]] found in the file.
    """
    async with vault_lock.read():
        try:
            vp = VaultPath(path)
        except ValueError as e:
            return {"error": "invalid_path", "message": str(e)}

        if not vp.exists() or not vp.is_file():
            return {"error": "file_not_found", "message": f"File not found: {path}"}

        content = vp.filesystem_path.read_text(encoding="utf-8")
        return {
            "path": vp.vault_path,
            "content": content,
            "links": _parse_wikilinks(content),
        }


@mcp.tool()
async def vault_write(path: str, content: str) -> dict:
    """Creates a new file. Fails if the file already exists — use vault_update instead.
    Parent directories are created automatically. Malformed [[wikilinks]] are auto-rewritten to stem-only form.

    Args:
        path: File path relative to vault root (e.g. "/a/foo.md").
        content: Markdown content. May include [[wikilinks]]. Do not include `updated` frontmatter — server manages it.

    Returns: {"status": "ok", "path": "/a/foo.md", "updated": "2026-01-01T00:00:00Z", "warnings": [...]}
        warnings may include {"type": "wikilink_rewritten", "original": "a/foo", "rewritten": "foo"}
        or {"type": "broken_link", "target": "bar", "line": 5}.
    """
    lock_err = _check_maintenance()
    if lock_err:
        return lock_err

    async with vault_lock.write():
        try:
            vp = VaultPath(path)
        except ValueError as e:
            return {"error": "invalid_path", "message": str(e)}

        if vp.exists():
            return {"error": "file_already_exists", "message": f"File already exists: {path}. Use vault_update instead."}

        vp.filesystem_path.parent.mkdir(parents=True, exist_ok=True)

        dup = _find_duplicate(vp)
        if dup:
            return dup

        sanitized, link_warnings = _sanitize_wikilinks(content)
        stamped = _stamp_frontmatter(sanitized)
        broken_warnings = _find_broken_links(stamped)
        _atomic_write(vp.filesystem_path, stamped)
        _update_backlinks(vp.vault_path, None, stamped)

        return {"status": "ok", "path": vp.vault_path, "updated": _get_updated(stamped), "warnings": link_warnings + broken_warnings}


@mcp.tool()
async def vault_update(path: str, content: str) -> dict:
    """Updates an existing file. Fails if the file does not exist — use vault_write instead.
    Replaces the entire file content. Malformed [[wikilinks]] are auto-rewritten to stem-only form.

    Args:
        path: File path relative to vault root (e.g. "/a/foo.md").
        content: New markdown content (full replacement). Do not include `updated` frontmatter — server manages it.

    Returns: {"status": "ok", "path": "/a/foo.md", "updated": "2026-01-01T00:00:00Z", "warnings": [...]}
        warnings may include {"type": "wikilink_rewritten", "original": "a/foo", "rewritten": "foo"}
        or {"type": "broken_link", "target": "bar", "line": 5}.
    """
    lock_err = _check_maintenance()
    if lock_err:
        return lock_err

    async with vault_lock.write():
        try:
            vp = VaultPath(path)
        except ValueError as e:
            return {"error": "invalid_path", "message": str(e)}

        if not vp.exists() or not vp.is_file():
            return {"error": "file_not_found", "message": f"File not found: {path}. Use vault_write instead."}

        old_content = vp.filesystem_path.read_text(encoding="utf-8")
        sanitized, link_warnings = _sanitize_wikilinks(content)
        stamped = _stamp_frontmatter(sanitized)
        broken_warnings = _find_broken_links(stamped)
        _atomic_write(vp.filesystem_path, stamped)
        _update_backlinks(vp.vault_path, old_content, stamped)

        return {"status": "ok", "path": vp.vault_path, "updated": _get_updated(stamped), "warnings": link_warnings + broken_warnings}


@mcp.tool()
async def vault_delete(path: str) -> dict:
    """Deletes a file from the vault.

    Args:
        path: File path relative to vault root (e.g. "/a/foo.md").

    Returns: {"status": "ok", "deleted": "/a/foo.md", "orphaned_backlinks": ["/b/bar.md"]}
        orphaned_backlinks lists files that still contain [[wikilinks]] pointing to the deleted file.
    """
    lock_err = _check_maintenance()
    if lock_err:
        return lock_err

    async with vault_lock.write():
        try:
            vp = VaultPath(path)
        except ValueError as e:
            return {"error": "invalid_path", "message": str(e)}

        if not vp.exists() or not vp.is_file():
            return {"error": "file_not_found", "message": f"File not found: {path}"}

        content = vp.filesystem_path.read_text(encoding="utf-8")
        orphaned = sorted(_backlink_map.get(vp.stem, set()))

        _remove_backlinks(vp.vault_path, content)
        vp.filesystem_path.unlink()

        return {"status": "ok", "deleted": vp.vault_path, "orphaned_backlinks": orphaned}


@mcp.tool()
async def vault_move(src: str, dst: str) -> dict:
    """Moves or renames a file. Parent directories for dst are created automatically.
    Wikilinks in other files are NOT auto-updated — check referencing_files in the response.

    Args:
        src: Current file path (e.g. "/a/foo.md").
        dst: New file path (e.g. "/b/foo.md").

    Returns: {"status": "ok", "from": "/a/foo.md", "to": "/b/foo.md", "referencing_files": ["/c/bar.md"]}
        referencing_files lists files that contain [[wikilinks]] pointing to the old filename (may need manual update).
    """
    lock_err = _check_maintenance()
    if lock_err:
        return lock_err

    async with vault_lock.write():
        try:
            src_vp = VaultPath(src)
            dst_vp = VaultPath(dst)
        except ValueError as e:
            return {"error": "invalid_path", "message": str(e)}

        if not src_vp.exists() or not src_vp.is_file():
            return {"error": "file_not_found", "message": f"Source file not found: {src}"}

        dst_vp.filesystem_path.parent.mkdir(parents=True, exist_ok=True)

        dup = _find_duplicate(dst_vp, exclude=src_vp)
        if dup:
            return dup

        content = src_vp.filesystem_path.read_text(encoding="utf-8")
        referencing = sorted(_backlink_map.get(src_vp.stem, set()))

        _remove_backlinks(src_vp.vault_path, content)
        src_vp.filesystem_path.rename(dst_vp.filesystem_path)
        _update_backlinks(dst_vp.vault_path, None, content)

        return {
            "status": "ok",
            "from": src_vp.vault_path,
            "to": dst_vp.vault_path,
            "referencing_files": referencing,
        }


@mcp.tool()
async def vault_mkdir(path: str) -> dict:
    """Creates a directory. Parent directories are created automatically.

    Args:
        path: Directory path relative to vault root (e.g. "/a/b/").

    Returns: {"status": "ok", "path": "/a/b/"}
    """
    lock_err = _check_maintenance()
    if lock_err:
        return lock_err

    async with vault_lock.write():
        try:
            vp = VaultPath(path)
        except ValueError as e:
            return {"error": "invalid_path", "message": str(e)}

        vp.filesystem_path.mkdir(parents=True, exist_ok=True)
        return {"status": "ok", "path": vp.vault_path}


@mcp.tool()
async def vault_grep(pattern: str, path: str = "/") -> dict:
    """Searches file contents for a plain text pattern. Case-insensitive.

    Args:
        pattern: Search string (plain text, not regex).
        path: Directory to scope the search (e.g. "/a/"). Defaults to "/" (entire vault).

    Returns: {"pattern": "xyz", "results": [{"path": "/a/foo.md", "matches": [{"line": 5, "text": "...xyz..."}]}], "total_matches": 1}
        total_matches is the number of files matched, not total lines.
    """
    async with vault_lock.read():
        try:
            vp = VaultPath(path)
        except ValueError as e:
            return {"error": "invalid_path", "message": str(e)}

        if not vp.exists() or not vp.is_dir():
            return {"error": "path_not_found", "message": f"Directory not found: {path}"}

        pattern_lower = pattern.lower()
        results = []

        for md_file in sorted(vp.filesystem_path.rglob("*.md")):
            content = md_file.read_text(encoding="utf-8")
            matches = []
            for i, line in enumerate(content.split("\n"), 1):
                if pattern_lower in line.lower():
                    matches.append({"line": i, "text": line.strip()})
            if matches:
                results.append({"path": VaultPath(md_file).vault_path, "matches": matches})

        return {"pattern": pattern, "results": results, "total_matches": len(results)}


@mcp.tool()
async def vault_backlinks(path: str) -> dict:
    """Returns all files that contain a [[wikilink]] pointing to the given file.
    Use this to discover how a note is connected within the knowledge graph.

    Args:
        path: File path relative to vault root (e.g. "/a/foo.md").

    Returns: {"path": "/a/foo.md", "backlinks": [{"path": "/b/bar.md", "line": 3, "text": "See [[foo]]."}], "count": 1}
    """
    async with vault_lock.read():
        try:
            vp = VaultPath(path)
        except ValueError as e:
            return {"error": "invalid_path", "message": str(e)}

        if not vp.exists() or not vp.is_file():
            return {"error": "file_not_found", "message": f"File not found: {path}"}

        sources = sorted(_backlink_map.get(vp.stem, set()))

        backlinks = []
        for source_vault_path in sources:
            source_vp = VaultPath(source_vault_path)
            if source_vp.exists():
                content = source_vp.filesystem_path.read_text(encoding="utf-8")
                for i, line in enumerate(content.split("\n"), 1):
                    if f"[[{vp.stem}]]" in line or f"[[{vp.stem}|" in line:
                        backlinks.append({
                            "path": source_vault_path,
                            "line": i,
                            "text": line.strip(),
                        })

        return {"path": vp.vault_path, "backlinks": backlinks, "count": len(backlinks)}


# ---------------------------------------------------------------------------
# Auth Middleware
# ---------------------------------------------------------------------------

class AuthMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            request = Request(scope, receive)
            if request.url.path == "/health":
                await self.app(scope, receive, send)
                return
            if AUTH_TOKEN:
                auth_header = request.headers.get("authorization", "")
                if auth_header != f"Bearer {AUTH_TOKEN}":
                    response = JSONResponse({"error": "Unauthorized"}, status_code=401)
                    await response(scope, receive, send)
                    return
        await self.app(scope, receive, send)


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------

async def health(request: Request) -> Response:
    return JSONResponse({"status": "ok", "server": "klona-memory-server", "version": "0.2.0"})


# ---------------------------------------------------------------------------
# App Assembly
# ---------------------------------------------------------------------------

@contextlib.asynccontextmanager
async def lifespan(app: Starlette):
    _ensure_vault()
    _build_backlink_cache()
    async with mcp.session_manager.run():
        yield


app = Starlette(
    routes=[
        Route("/health", health),
        Mount("/", app=mcp.streamable_http_app()),
    ],
    lifespan=lifespan,
    middleware=[Middleware(AuthMiddleware)],
)
