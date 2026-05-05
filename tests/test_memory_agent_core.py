import asyncio
import contextlib
import importlib
import json
import os
import sys
import tempfile
import types
import unittest
import warnings
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MEMORY_AGENT_SRC = ROOT / "memory_agent" / "src"
sys.path.insert(0, str(MEMORY_AGENT_SRC))
warnings.simplefilter("ignore", ResourceWarning)


class MemoryQueueTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)

    def queue(self):
        from memory_agent.queue import MemoryQueue

        return MemoryQueue(Path(self.tempdir.name) / "queue.sqlite3")

    def test_enqueue_and_claim_use_strict_fifo_order(self):
        queue = self.queue()
        first = queue.enqueue("remember", "first")
        second = queue.enqueue("recall", "second")
        third = queue.enqueue("remember", "third")

        first_claim = queue.claim_next()
        queue.mark_succeeded(first_claim.id, "done")
        second_claim = queue.claim_next()
        queue.mark_succeeded(second_claim.id, "done")
        third_claim = queue.claim_next()
        queue.mark_succeeded(third_claim.id, "done")
        claimed = [first_claim, second_claim, third_claim]

        self.assertEqual([item.id for item in claimed], [first, second, third])
        self.assertEqual([item.input for item in claimed], ["first", "second", "third"])
        self.assertEqual([item.attempts for item in claimed], [1, 1, 1])
        self.assertIsNone(queue.claim_next())

    def test_enqueue_records_remember_and_recall_basics(self):
        queue = self.queue()
        remember_id = queue.enqueue("remember", "store this")
        recall_id = queue.enqueue("recall", "find this")

        remember = queue.get(remember_id)
        recall = queue.get(recall_id)

        self.assertEqual(remember.kind, "remember")
        self.assertEqual(remember.input, "store this")
        self.assertEqual(remember.status, "pending")
        self.assertEqual(remember.attempts, 0)
        self.assertEqual(recall.kind, "recall")
        self.assertEqual(recall.input, "find this")
        self.assertEqual(recall.status, "pending")

    def test_retry_twice_then_marks_failed(self):
        queue = self.queue()
        item_id = queue.enqueue("recall", "retry me")

        first = queue.claim_next()
        self.assertEqual(first.attempts, 1)
        self.assertEqual(queue.mark_failed_or_retry(item_id, "boom 1", max_retries=2), "pending")
        self.assertEqual(queue.get(item_id).last_error, "boom 1")

        second = queue.claim_next()
        self.assertEqual(second.id, item_id)
        self.assertEqual(second.attempts, 2)
        self.assertEqual(queue.mark_failed_or_retry(item_id, "boom 2", max_retries=2), "pending")

        third = queue.claim_next()
        self.assertEqual(third.id, item_id)
        self.assertEqual(third.attempts, 3)
        self.assertEqual(queue.mark_failed_or_retry(item_id, "boom 3", max_retries=2), "failed")

        failed = queue.get(item_id)
        self.assertEqual(failed.status, "failed")
        self.assertEqual(failed.last_error, "boom 3")

    def test_stale_processing_row_reclaimed_before_newer_pending_row(self):
        queue = self.queue()
        first_id = queue.enqueue("recall", "old")
        second_id = queue.enqueue("recall", "new")

        first = queue.claim_next(processing_lease_seconds=60)
        self.assertEqual(first.id, first_id)
        self.assertIsNone(queue.claim_next(processing_lease_seconds=60))

        with contextlib.closing(queue._connect()) as conn:
            conn.execute(
                "UPDATE queue_items SET claimed_at = claimed_at - 120 WHERE id = ?",
                (first_id,),
            )

        reclaimed = queue.claim_next(processing_lease_seconds=60)

        self.assertEqual(reclaimed.id, first_id)
        self.assertEqual(reclaimed.attempts, 2)
        self.assertEqual(queue.get(second_id).status, "pending")

    def test_stale_processing_row_fails_after_retry_limit_then_allows_next_item(self):
        queue = self.queue()
        first_id = queue.enqueue("recall", "old")
        second_id = queue.enqueue("recall", "new")

        queue.claim_next(processing_lease_seconds=60, max_retries=2)
        for expected_attempt in [2, 3]:
            with contextlib.closing(queue._connect()) as conn:
                conn.execute(
                    "UPDATE queue_items SET claimed_at = claimed_at - 120 WHERE id = ?",
                    (first_id,),
                )
            reclaimed = queue.claim_next(processing_lease_seconds=60, max_retries=2)
            self.assertEqual(reclaimed.id, first_id)
            self.assertEqual(reclaimed.attempts, expected_attempt)

        with contextlib.closing(queue._connect()) as conn:
            conn.execute(
                "UPDATE queue_items SET claimed_at = claimed_at - 120 WHERE id = ?",
                (first_id,),
            )
        next_item = queue.claim_next(processing_lease_seconds=60, max_retries=2)

        self.assertEqual(queue.get(first_id).status, "failed")
        self.assertEqual(next_item.id, second_id)

    def test_reset_stale_processing_returns_rows_to_pending(self):
        queue = self.queue()
        item_id = queue.enqueue("recall", "old")
        queue.claim_next(processing_lease_seconds=60)
        with contextlib.closing(queue._connect()) as conn:
            conn.execute(
                "UPDATE queue_items SET claimed_at = claimed_at - 120 WHERE id = ?",
                (item_id,),
            )

        self.assertEqual(queue.reset_stale_processing(processing_lease_seconds=60), 1)
        self.assertEqual(queue.get(item_id).status, "pending")


class OpenCodeConfigTests(unittest.TestCase):
    def test_generated_config_limits_permissions_to_low_level_memory_tools(self):
        from memory_agent.config import Settings
        from memory_agent.runtime import ALLOWED_TOOL_PATTERN, generate_opencode_config

        with tempfile.TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "opencode.json"
            settings = Settings(
                low_level_mcp_url="https://low-level.example/mcp",
                low_level_mcp_auth_token="low-secret",
                opencode_config_path=config_path,
            )

            written = generate_opencode_config(settings, model="openai/gpt-5.1", reasoning_effort="high")
            data = json.loads(written.read_text())

        self.assertEqual(data["mcp"]["klona_memory_server"]["type"], "remote")
        self.assertEqual(data["mcp"]["klona_memory_server"]["url"], "https://low-level.example/mcp")
        self.assertEqual(
            data["mcp"]["klona_memory_server"]["headers"],
            {"Authorization": "Bearer low-secret"},
        )
        self.assertEqual(data["permission"], {"*": "deny", ALLOWED_TOOL_PATTERN: "allow"})
        self.assertNotIn("variant", data)
        self.assertNotIn("reasoningEffort", data)
        self.assertEqual(data["agent"]["klona-memory"]["variant"], "high")
        self.assertEqual(
            data["agent"]["klona-memory"]["permission"],
            {"*": "deny", ALLOWED_TOOL_PATTERN: "allow"},
        )
        serialized = json.dumps(data)
        for dangerous in ["bash", "shell", "edit", "write", "filesystem"]:
            self.assertNotIn(dangerous, serialized.lower())

    def test_generated_config_path_uses_xdg_loadable_shape(self):
        from memory_agent.config import Settings
        from memory_agent.runtime import generate_opencode_config, opencode_config_environment

        with tempfile.TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "opencode" / "opencode.json"
            settings = Settings(opencode_config_path=config_path)
            written = generate_opencode_config(settings, model="openai/gpt-5.1")
            env = opencode_config_environment(written)

        self.assertEqual(env["XDG_CONFIG_HOME"], tempdir)
        self.assertEqual(env["OPENCODE_CONFIG"], str(config_path))

    def test_auth_and_model_commands_accept_shared_opencode_environment(self):
        from memory_agent import runtime

        calls = []

        def fake_run(args, **kwargs):
            calls.append((args, kwargs))
            if args[:2] == ["opencode", "models"]:
                return types.SimpleNamespace(returncode=0, stdout="openai/gpt-5.1\n", stderr="")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        with mock.patch("builtins.input", side_effect=["y"]), mock.patch("subprocess.run", side_effect=fake_run):
            runtime.run_auth_prompt_loop({"XDG_CONFIG_HOME": "/tmp/klona-test-config"})
            runtime.discover_models({"XDG_CONFIG_HOME": "/tmp/klona-test-config"})

        self.assertEqual(calls[0][1]["env"], {"XDG_CONFIG_HOME": "/tmp/klona-test-config"})
        self.assertEqual(calls[1][1]["env"], {"XDG_CONFIG_HOME": "/tmp/klona-test-config"})


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload



class FakeHTTPClient:
    def __init__(self):
        self.posts = []

    async def post(self, path, json):
        self.posts.append((path, json))
        return FakeResponse({"parts": [{"type": "text", "text": "ok"}]})


class OpenCodeClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_message_uses_documented_message_body_and_model_split(self):
        sys.modules.setdefault("httpx", types.SimpleNamespace(AsyncClient=object))
        from memory_agent.opencode_client import OpenCodeClient

        client = OpenCodeClient.__new__(OpenCodeClient)
        client.agent = "klona-memory"
        client._client = FakeHTTPClient()

        result = await client.send_message("session-123", "remember this")

        self.assertEqual(result, "ok")
        self.assertEqual(len(client._client.posts), 1)
        path, payload = client._client.posts[0]
        self.assertEqual(path, "/session/session-123/message")
        self.assertEqual(payload["agent"], "klona-memory")
        self.assertEqual(payload["parts"], [{"type": "text", "text": "remember this"}])
        self.assertNotIn("model", payload)


def install_fake_server_dependencies():
    class FakeFastMCP:
        def __init__(self, *args, **kwargs):
            self.session_manager = types.SimpleNamespace(run=lambda: _null_async_context())

        def tool(self):
            return lambda fn: fn

        def streamable_http_app(self):
            return object()

    class FakeTransportSecuritySettings:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeStarlette:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeMiddleware:
        def __init__(self, app):
            self.app = app

    class FakeJSONResponse:
        def __init__(self, body, status_code=200):
            self.body = body
            self.status_code = status_code

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
    modules["mcp.server.fastmcp"].FastMCP = FakeFastMCP
    modules["mcp.server.transport_security"].TransportSecuritySettings = FakeTransportSecuritySettings
    modules["starlette.applications"].Starlette = FakeStarlette
    modules["starlette.middleware"].Middleware = FakeMiddleware
    modules["starlette.requests"].Request = object
    modules["starlette.responses"].JSONResponse = FakeJSONResponse
    modules["starlette.responses"].Response = object
    modules["starlette.routing"].Mount = lambda *args, **kwargs: ("mount", args, kwargs)
    modules["starlette.routing"].Route = lambda *args, **kwargs: ("route", args, kwargs)
    return modules


class _null_async_context:
    async def __aenter__(self):
        return None

    async def __aexit__(self, exc_type, exc, tb):
        return False


class ServerToolBehaviorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.env_patcher = mock.patch.dict(
            os.environ,
            {"MEMORY_AGENT_QUEUE_DB": str(Path(self.tempdir.name) / "import_queue.sqlite3")},
        )
        self.env_patcher.start()
        self.addCleanup(self.env_patcher.stop)
        self.module_patcher = mock.patch.dict(sys.modules, install_fake_server_dependencies())
        self.module_patcher.start()
        self.addCleanup(self.module_patcher.stop)
        sys.modules.pop("memory_agent.server", None)
        self.server = importlib.import_module("memory_agent.server")

    async def test_remember_enqueues_and_returns_ack(self):
        from memory_agent.queue import MemoryQueue

        with tempfile.TemporaryDirectory() as tempdir:
            queue = MemoryQueue(Path(tempdir) / "queue.sqlite3")
            self.server.queue = queue

            response = await self.server.remember("store this")

            self.assertEqual(response, {"status": "request_received", "id": 1})
            item = queue.get(1)
            self.assertEqual(item.kind, "remember")
            self.assertEqual(item.input, "store this")
            self.assertEqual(item.status, "pending")

    async def test_recall_waits_for_successful_result(self):
        from memory_agent.config import Settings
        from memory_agent.queue import MemoryQueue

        with tempfile.TemporaryDirectory() as tempdir:
            queue = MemoryQueue(Path(tempdir) / "queue.sqlite3")
            self.server.queue = queue
            self.server.settings = Settings(
                queue_db_path=Path(tempdir) / "queue.sqlite3",
                recall_timeout_seconds=1,
                poll_interval_seconds=0.01,
            )

            async def complete_recall():
                while True:
                    item = await asyncio.to_thread(queue.claim_next)
                    if item is not None:
                        await asyncio.to_thread(queue.mark_succeeded, item.id, "recalled context")
                        return
                    await asyncio.sleep(0.01)

            worker = asyncio.create_task(complete_recall())
            try:
                response = await self.server.recall("what do I know?")
            finally:
                await worker

            self.assertEqual(response, {"status": "ok", "id": 1, "result": "recalled context"})
            item = queue.get(1)
            self.assertEqual(item.kind, "recall")
            self.assertEqual(item.input, "what do I know?")
            self.assertEqual(item.status, "succeeded")


class PromptTests(unittest.TestCase):
    def test_recall_prompt_preserves_exact_file_content_requests(self):
        from memory_agent.config import Settings
        from memory_agent.prompts import recall_prompt

        prompt = recall_prompt(
            "Return the exact current content of /KLONA_MEMORY_MENTAL_MODEL.md if it exists.",
            Settings(low_level_mcp_url="http://memory-server:8000/mcp"),
        )

        self.assertIn("exact current file content", prompt)
        self.assertIn("content verbatim without semantic summarization", prompt)
        self.assertIn("/KLONA_MEMORY_MENTAL_MODEL.md", prompt)


if __name__ == "__main__":
    unittest.main()
