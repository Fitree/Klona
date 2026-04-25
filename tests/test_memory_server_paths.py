import importlib.util
import os
import sys
import tempfile
import types
import unittest
import asyncio
from pathlib import Path
from unittest import mock


SERVER_PATH = Path(__file__).resolve().parents[1] / "memory_server" / "src" / "server.py"


class _FakeFastMCP:
    def __init__(self, *args, **kwargs):
        self.session_manager = types.SimpleNamespace(run=lambda: self._lifespan_context())

    def tool(self):
        def decorator(func):
            return func

        return decorator

    def streamable_http_app(self):
        return object()

    @staticmethod
    def _lifespan_context():
        class _Context:
            async def __aenter__(self):
                return None

            async def __aexit__(self, exc_type, exc, tb):
                return False

        return _Context()


def _install_server_dependency_stubs():
    modules = {
        "mcp": types.ModuleType("mcp"),
        "mcp.server": types.ModuleType("mcp.server"),
        "mcp.server.fastmcp": types.ModuleType("mcp.server.fastmcp"),
        "mcp.server.transport_security": types.ModuleType("mcp.server.transport_security"),
        "starlette": types.ModuleType("starlette"),
        "starlette.applications": types.ModuleType("starlette.applications"),
        "starlette.middleware": types.ModuleType("starlette.middleware"),
        "starlette.requests": types.ModuleType("starlette.requests"),
        "starlette.responses": types.ModuleType("starlette.responses"),
        "starlette.routing": types.ModuleType("starlette.routing"),
    }
    modules["mcp.server.fastmcp"].FastMCP = _FakeFastMCP
    modules["mcp.server.transport_security"].TransportSecuritySettings = lambda **kwargs: kwargs
    modules["starlette.applications"].Starlette = lambda *args, **kwargs: object()
    modules["starlette.middleware"].Middleware = lambda *args, **kwargs: (args, kwargs)
    modules["starlette.requests"].Request = lambda scope, receive=None: types.SimpleNamespace(
        url=types.SimpleNamespace(path=scope.get("path", "")),
        headers={},
    )
    modules["starlette.responses"].JSONResponse = lambda *args, **kwargs: object()
    modules["starlette.responses"].Response = object
    modules["starlette.routing"].Mount = lambda *args, **kwargs: (args, kwargs)
    modules["starlette.routing"].Route = lambda *args, **kwargs: (args, kwargs)
    return mock.patch.dict(sys.modules, modules)


def load_server(vault_dir: Path):
    module_name = "klona_memory_server_under_test"
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(module_name, SERVER_PATH)
    module = importlib.util.module_from_spec(spec)
    with _install_server_dependency_stubs(), mock.patch.dict(os.environ, {"VAULT_DIR": str(vault_dir)}):
        spec.loader.exec_module(module)
    return module


class VaultPathBoundaryTests(unittest.TestCase):
    def test_rejects_absolute_sibling_path_with_same_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "vault"
            vault.mkdir()
            server = load_server(vault)

            sibling = root / "vault_evil" / "file.md"
            with self.assertRaisesRegex(ValueError, "Path escapes vault"):
                server.VaultPath(sibling)

    def test_rejects_relative_traversal_to_sibling_with_same_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "vault"
            vault.mkdir()
            server = load_server(vault)

            with self.assertRaisesRegex(ValueError, "Path escapes vault"):
                server.VaultPath("../vault_evil/file.md")

    def test_accepts_valid_path_inside_vault(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            inside = vault / "notes" / "file.md"
            inside.parent.mkdir(parents=True)
            inside.write_text("content")
            server = load_server(vault)

            vp = server.VaultPath(inside)

            self.assertEqual(vp.filesystem_path, inside.resolve())
            self.assertEqual(vp.vault_path, "/notes/file.md")

    def test_write_rejects_non_markdown_file_path_without_creating_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            server = load_server(vault)

            result = asyncio.run(server.vault_write("/foo", "body"))

            self.assertEqual(result.get("error"), "invalid_path")
            self.assertFalse((vault / "foo").exists())

    def test_write_accepts_markdown_file_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            server = load_server(vault)

            result = asyncio.run(server.vault_write("/foo.md", "body"))

            self.assertEqual(result["status"], "ok")
            self.assertTrue((vault / "foo.md").is_file())

    def test_file_operations_reject_non_markdown_file_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            vault.mkdir()
            (vault / "foo").write_text("body")
            server = load_server(vault)

            read_result = asyncio.run(server.vault_read("/foo"))
            update_result = asyncio.run(server.vault_update("/foo", "new body"))
            move_result = asyncio.run(server.vault_move("/foo", "/bar.md"))

            self.assertEqual(read_result.get("error"), "invalid_path")
            self.assertEqual(update_result.get("error"), "invalid_path")
            self.assertEqual(move_result.get("error"), "invalid_path")
            self.assertEqual((vault / "foo").read_text(), "body")
            self.assertFalse((vault / "bar.md").exists())

    def test_move_to_existing_destination_fails_and_preserves_files_and_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            server = load_server(vault)
            asyncio.run(server.vault_write("/a/foo.md", "source body [[bar]]"))
            asyncio.run(server.vault_write("/b/bar.md", "destination body [[foo]]"))
            source_before = (vault / "a" / "foo.md").read_text()
            dest_before = (vault / "b" / "bar.md").read_text()
            cache_before = {key: set(value) for key, value in server._backlink_map.items()}

            result = asyncio.run(server.vault_move("/a/foo.md", "/b/bar.md"))

            self.assertEqual(result.get("error"), "file_already_exists")
            self.assertEqual((vault / "a" / "foo.md").read_text(), source_before)
            self.assertEqual((vault / "b" / "bar.md").read_text(), dest_before)
            self.assertEqual(server._backlink_map, cache_before)


if __name__ == "__main__":
    unittest.main()
