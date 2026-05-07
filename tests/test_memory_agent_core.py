import asyncio
import contextlib
import io
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
MEMORY_SERVER_SRC = ROOT / "memory_server" / "src"
sys.path.insert(0, str(MEMORY_AGENT_SRC))
sys.path.insert(0, str(MEMORY_SERVER_SRC))
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
    def test_shared_constants_keep_public_opencode_names_stable(self):
        from memory_agent.constants import (
            DEFAULT_QUEUE_DB_PATH,
            LOW_LEVEL_MCP_ALLOWED_TOOL_PATTERN,
            LOW_LEVEL_MCP_NAME,
            MEMORY_AGENT_NAME,
            MEMORY_AGENT_SESSION_TITLE,
        )

        self.assertEqual(MEMORY_AGENT_NAME, "klona-memory")
        self.assertEqual(MEMORY_AGENT_SESSION_TITLE, "KLONA memory agent")
        self.assertEqual(LOW_LEVEL_MCP_NAME, "klona_memory_server")
        self.assertEqual(LOW_LEVEL_MCP_ALLOWED_TOOL_PATTERN, "klona_memory_server_*")
        self.assertEqual(DEFAULT_QUEUE_DB_PATH, "/state/queue.db")

    def test_settings_defaults_align_with_compose_queue_db_path(self):
        from memory_agent.config import Settings
        from memory_agent.constants import DEFAULT_QUEUE_DB_PATH

        with mock.patch.dict(os.environ, {"MEMORY_AGENT_QUEUE_DB": ""}, clear=True):
            settings = Settings()

        self.assertEqual(settings.queue_db_path, Path(DEFAULT_QUEUE_DB_PATH))

    def test_opencode_base_url_defaults_derive_from_host_and_port(self):
        from memory_agent.config import Settings

        with mock.patch.dict(os.environ, {"OPENCODE_HOST": "0.0.0.0", "OPENCODE_PORT": "5099"}, clear=True):
            settings = Settings()

        self.assertEqual(settings.opencode_base_url, "http://0.0.0.0:5099")

    def test_explicit_empty_opencode_base_url_uses_host_port_defaults(self):
        from memory_agent.config import Settings

        with mock.patch.dict(os.environ, {"OPENCODE_BASE_URL": "", "OPENCODE_HOST": "", "OPENCODE_PORT": ""}, clear=True):
            settings = Settings()

        self.assertEqual(settings.opencode_base_url, "http://127.0.0.1:4096")

    def test_explicit_opencode_base_url_still_takes_precedence(self):
        from memory_agent.config import Settings

        with mock.patch.dict(
            os.environ,
            {"OPENCODE_BASE_URL": "http://opencode.example:7000/", "OPENCODE_HOST": "0.0.0.0", "OPENCODE_PORT": "5099"},
            clear=True,
        ):
            settings = Settings()

        self.assertEqual(settings.opencode_base_url, "http://opencode.example:7000")

    def test_explicit_empty_high_level_token_disables_legacy_auth_fallback(self):
        from memory_agent.config import Settings

        with mock.patch.dict(
            os.environ,
            {"HIGH_LEVEL_MCP_AUTH_TOKEN": "", "MEMORY_AGENT_AUTH_TOKEN": "legacy-secret"},
            clear=True,
        ):
            settings = Settings()

        self.assertEqual(settings.auth_token, "")

    def test_legacy_memory_agent_auth_token_still_supported_when_high_level_unset(self):
        from memory_agent.config import Settings

        with mock.patch.dict(os.environ, {"MEMORY_AGENT_AUTH_TOKEN": "legacy-secret"}, clear=True):
            settings = Settings()

        self.assertEqual(settings.auth_token, "legacy-secret")

    def test_explicit_empty_high_level_allowed_hosts_disables_legacy_fallback(self):
        from memory_agent.config import Settings

        with mock.patch.dict(
            os.environ,
            {"HIGH_LEVEL_ALLOWED_HOSTS": "", "MEMORY_AGENT_ALLOWED_HOSTS": "legacy.example,localhost"},
            clear=True,
        ):
            settings = Settings()

        self.assertEqual(settings.allowed_hosts, ())

    def test_legacy_memory_agent_allowed_hosts_still_supported_when_high_level_unset(self):
        from memory_agent.config import Settings

        with mock.patch.dict(os.environ, {"MEMORY_AGENT_ALLOWED_HOSTS": "legacy.example, localhost:8080"}, clear=True):
            settings = Settings()

        self.assertEqual(settings.allowed_hosts, ("legacy.example", "localhost:8080"))

    def test_non_empty_high_level_allowed_hosts_parse_as_comma_separated_list(self):
        from memory_agent.config import Settings

        with mock.patch.dict(
            os.environ,
            {"HIGH_LEVEL_ALLOWED_HOSTS": " high.example,localhost:8080 ,, 127.0.0.1 ", "MEMORY_AGENT_ALLOWED_HOSTS": "legacy.example"},
            clear=True,
        ):
            settings = Settings()

        self.assertEqual(settings.allowed_hosts, ("high.example", "localhost:8080", "127.0.0.1"))

    def test_generated_config_limits_permissions_to_low_level_memory_tools(self):
        from memory_agent.config import Settings
        from memory_agent.constants import LOW_LEVEL_MCP_NAME, MEMORY_AGENT_NAME
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

        self.assertEqual(data["mcp"][LOW_LEVEL_MCP_NAME]["type"], "remote")
        self.assertEqual(data["mcp"][LOW_LEVEL_MCP_NAME]["url"], "https://low-level.example/mcp")
        self.assertEqual(
            data["mcp"][LOW_LEVEL_MCP_NAME]["headers"],
            {"Authorization": "Bearer low-secret"},
        )
        self.assertEqual(data["permission"], {"*": "deny", ALLOWED_TOOL_PATTERN: "allow"})
        self.assertNotIn("variant", data)
        self.assertNotIn("reasoningEffort", data)
        self.assertEqual(data["agent"][MEMORY_AGENT_NAME]["variant"], "high")
        self.assertEqual(data["agent"][MEMORY_AGENT_NAME]["mode"], "primary")
        prompt = data["agent"][MEMORY_AGENT_NAME]["prompt"]
        self.assertIn("You are `klona-memory`", prompt)
        self.assertIn("Use only low-level Klona memory MCP tools", prompt)
        self.assertIn("klona_memory_server_vault_tree", prompt)
        self.assertIn("KLONA_MEMORY_MENTAL_MODEL.md", prompt)
        self.assertIn("content verbatim without semantic summarization", prompt)
        self.assertIn("Storage gating", prompt)
        self.assertEqual(
            data["agent"][MEMORY_AGENT_NAME]["permission"],
            {"*": "deny", ALLOWED_TOOL_PATTERN: "allow"},
        )
        serialized_permissions = json.dumps(data["agent"][MEMORY_AGENT_NAME]["permission"])
        for dangerous in ["bash", "shell", "edit", "filesystem"]:
            self.assertNotIn(dangerous, serialized_permissions.lower())

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

    def test_generated_config_omits_explicit_empty_reasoning_even_with_settings_default(self):
        from memory_agent.config import Settings
        from memory_agent.constants import MEMORY_AGENT_NAME
        from memory_agent.runtime import generate_opencode_config

        with tempfile.TemporaryDirectory() as tempdir:
            settings = Settings(
                opencode_config_path=Path(tempdir) / "opencode.json",
                opencode_reasoning_effort="high",
            )

            written = generate_opencode_config(settings, model="opencode/gpt-5-mini", reasoning_effort="")
            data = json.loads(written.read_text())

        agent_config = data["agent"][MEMORY_AGENT_NAME]
        self.assertEqual(agent_config["model"], "opencode/gpt-5-mini")
        self.assertNotIn("variant", agent_config)
        self.assertNotIn("reasoningEffort", agent_config)

    def test_generated_config_uses_settings_reasoning_when_reasoning_omitted(self):
        from memory_agent.config import Settings
        from memory_agent.constants import MEMORY_AGENT_NAME
        from memory_agent.runtime import generate_opencode_config

        with tempfile.TemporaryDirectory() as tempdir:
            settings = Settings(
                opencode_config_path=Path(tempdir) / "opencode.json",
                opencode_reasoning_effort="high",
            )

            written = generate_opencode_config(settings, model="opencode/gpt-5")
            data = json.loads(written.read_text())

        self.assertEqual(data["agent"][MEMORY_AGENT_NAME]["variant"], "high")

    def test_no_variant_model_selection_feeds_config_without_stale_reasoning(self):
        from memory_agent import runtime
        from memory_agent.config import Settings
        from memory_agent.constants import MEMORY_AGENT_NAME

        models = [runtime.ModelChoice(model="opencode/gpt-5-mini", raw='opencode/gpt-5-mini\n{"id":"opencode/gpt-5-mini"}', variants=())]

        with tempfile.TemporaryDirectory() as tempdir, mock.patch.object(runtime, "discover_models", return_value=models), mock.patch(
            "builtins.input", side_effect=["1"]
        ), mock.patch("sys.stdout", io.StringIO()):
            model, reasoning = runtime.choose_model_and_reasoning(default_reasoning="high")
            written = runtime.generate_opencode_config(
                Settings(opencode_config_path=Path(tempdir) / "opencode.json", opencode_reasoning_effort="high"),
                model,
                reasoning,
            )
            data = json.loads(written.read_text())

        self.assertEqual((model, reasoning), ("opencode/gpt-5-mini", ""))
        self.assertNotIn("variant", data["agent"][MEMORY_AGENT_NAME])
        self.assertNotIn("reasoningEffort", data["agent"][MEMORY_AGENT_NAME])

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

    def test_model_list_formatting_excludes_raw_json_and_variants_dump(self):
        from memory_agent.runtime import ModelChoice, format_model_options

        lines = format_model_options(
            [
                ModelChoice(
                    model="opencode/gpt-5-nano",
                    raw='opencode/gpt-5-nano\n{"id":"opencode/gpt-5-nano","variants":{"low":{},"high":{}}}',
                    variants=("low", "high"),
                )
            ]
        )

        self.assertEqual(lines, ("1. opencode/gpt-5-nano",))
        rendered = "\n".join(lines)
        self.assertNotIn("{", rendered)
        self.assertNotIn("variants", rendered.lower())
        self.assertNotIn("low", rendered)

    def test_variant_choices_are_shown_only_after_selected_model(self):
        from memory_agent import runtime

        models = [
            runtime.ModelChoice(
                model="opencode/gpt-5-nano",
                raw='opencode/gpt-5-nano\n{"variants":{"low":{},"high":{}}}',
                variants=("low", "high"),
            ),
            runtime.ModelChoice(
                model="opencode/gpt-5",
                raw='opencode/gpt-5\n{"variants":{"deep":{}}}',
                variants=("deep",),
            ),
        ]
        stdout = io.StringIO()

        with mock.patch.object(runtime, "discover_models", return_value=models), mock.patch("builtins.input", side_effect=["2", "1"]), mock.patch(
            "sys.stdout", stdout
        ):
            model, reasoning = runtime.choose_model_and_reasoning()

        output = stdout.getvalue()
        self.assertEqual((model, reasoning), ("opencode/gpt-5", "deep"))
        self.assertIn("Reasoning effort/variant options for opencode/gpt-5", output)
        self.assertIn("1. deep", output)
        self.assertNotIn("low", output)
        self.assertNotIn("high", output)
        self.assertNotIn("{", output)

    def test_model_input_zero_is_rejected_before_valid_selection(self):
        from memory_agent import runtime

        models = [
            runtime.ModelChoice(model="opencode/gpt-5-nano", raw="opencode/gpt-5-nano", variants=()),
            runtime.ModelChoice(model="opencode/gpt-5", raw="opencode/gpt-5", variants=()),
        ]
        stdout = io.StringIO()

        with mock.patch.object(runtime, "discover_models", return_value=models), mock.patch("builtins.input", side_effect=["0", "1"]), mock.patch(
            "sys.stdout", stdout
        ):
            model, reasoning = runtime.choose_model_and_reasoning()

        self.assertEqual((model, reasoning), ("opencode/gpt-5-nano", ""))
        self.assertIn("Choose a valid model number.", stdout.getvalue())

    def test_variant_input_zero_is_rejected_before_valid_selection(self):
        from memory_agent import runtime

        models = [runtime.ModelChoice(model="opencode/gpt-5", raw="opencode/gpt-5", variants=("low", "high"))]
        stdout = io.StringIO()

        with mock.patch.object(runtime, "discover_models", return_value=models), mock.patch("builtins.input", side_effect=["1", "0", "2"]), mock.patch(
            "sys.stdout", stdout
        ):
            model, reasoning = runtime.choose_model_and_reasoning()

        self.assertEqual((model, reasoning), ("opencode/gpt-5", "high"))
        self.assertIn("Choose a valid reasoning effort/variant number.", stdout.getvalue())

    def test_model_with_no_variants_does_not_prompt_or_dump_json(self):
        from memory_agent import runtime

        models = [
            runtime.ModelChoice(
                model="opencode/gpt-5-mini",
                raw='opencode/gpt-5-mini\n{"id":"opencode/gpt-5-mini"}',
                variants=(),
            )
        ]
        stdout = io.StringIO()

        with mock.patch.object(runtime, "discover_models", return_value=models), mock.patch("builtins.input", side_effect=["1"]) as fake_input, mock.patch(
            "sys.stdout", stdout
        ):
            model, reasoning = runtime.choose_model_and_reasoning(default_reasoning="high")

        self.assertEqual((model, reasoning), ("opencode/gpt-5-mini", ""))
        self.assertEqual(fake_input.call_count, 1)
        output = stdout.getvalue()
        self.assertIn("1. opencode/gpt-5-mini", output)
        self.assertNotIn("{", output)
        self.assertNotIn("reasoning effort/variant options", output.lower())

    def test_blank_variant_choice_does_not_use_default_outside_selected_variants(self):
        from memory_agent import runtime

        models = [
            runtime.ModelChoice(
                model="opencode/gpt-5-nano",
                raw='opencode/gpt-5-nano\n{"variants":{"low":{},"medium":{}}}',
                variants=("low", "medium"),
            )
        ]

        with mock.patch.object(runtime, "discover_models", return_value=models), mock.patch("builtins.input", side_effect=["1", ""]), mock.patch(
            "sys.stdout", io.StringIO()
        ):
            model, reasoning = runtime.choose_model_and_reasoning(default_reasoning="high")

        self.assertEqual((model, reasoning), ("opencode/gpt-5-nano", ""))


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
            if "allowed_hosts" in kwargs and kwargs["allowed_hosts"] is None:
                raise ValueError("allowed_hosts must be omitted or a list")
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


class HighLevelTransportSecurityTests(unittest.TestCase):
    def import_server_with_env(self, env):
        with tempfile.TemporaryDirectory() as tempdir:
            full_env = {"MEMORY_AGENT_QUEUE_DB": str(Path(tempdir) / "queue.sqlite3"), **env}
            sys.modules.pop("memory_agent.server", None)
            with mock.patch.dict(sys.modules, install_fake_server_dependencies()):
                with mock.patch.dict(os.environ, full_env, clear=True):
                    server = importlib.import_module("memory_agent.server")
            self.addCleanup(sys.modules.pop, "memory_agent.server", None)
            return server

    def test_empty_high_level_allowed_hosts_disables_dns_rebinding_without_allowed_hosts(self):
        server = self.import_server_with_env({"HIGH_LEVEL_ALLOWED_HOSTS": ""})

        self.assertEqual(
            server.transport_security.kwargs,
            {"enable_dns_rebinding_protection": False},
        )

    def test_non_empty_high_level_allowed_hosts_enables_dns_rebinding_with_list(self):
        server = self.import_server_with_env(
            {"HIGH_LEVEL_ALLOWED_HOSTS": " high.example, localhost:8080 ", "MEMORY_AGENT_ALLOWED_HOSTS": "legacy.example"}
        )

        self.assertEqual(
            server.transport_security.kwargs,
            {"enable_dns_rebinding_protection": True, "allowed_hosts": ["high.example", "localhost:8080"]},
        )


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

    async def test_high_level_empty_token_disables_auth_check(self):
        self.assertTrue(self.server._is_authorized("", ""))
        self.assertTrue(self.server._is_authorized("Bearer anything", ""))

    async def test_high_level_non_empty_token_requires_bearer_auth(self):
        self.assertFalse(self.server._is_authorized("", "high-secret"))
        self.assertFalse(self.server._is_authorized("Bearer wrong", "high-secret"))
        self.assertTrue(self.server._is_authorized("Bearer high-secret", "high-secret"))


class LowLevelServerAuthTests(unittest.TestCase):
    def setUp(self):
        self.module_patcher = mock.patch.dict(sys.modules, install_fake_server_dependencies())
        self.module_patcher.start()
        self.addCleanup(self.module_patcher.stop)
        self.env_patcher = mock.patch.dict(os.environ, {"VAULT_DIR": "/tmp/klona-test-vault", "AUTH_TOKEN": ""})
        self.env_patcher.start()
        self.addCleanup(self.env_patcher.stop)
        sys.modules.pop("server", None)
        self.server = importlib.import_module("server")

    def test_empty_token_disables_auth_check(self):
        self.assertTrue(self.server._is_authorized("", ""))
        self.assertTrue(self.server._is_authorized("Bearer anything", ""))

    def test_non_empty_token_requires_bearer_auth(self):
        self.assertFalse(self.server._is_authorized("", "low-secret"))
        self.assertFalse(self.server._is_authorized("Bearer wrong", "low-secret"))
        self.assertTrue(self.server._is_authorized("Bearer low-secret", "low-secret"))


class PromptTests(unittest.TestCase):
    def test_recall_prompt_is_compact_and_relies_on_system_prompt(self):
        from memory_agent.config import Settings
        from memory_agent.prompts import recall_prompt

        prompt = recall_prompt(
            "Return the exact current content of /KLONA_MEMORY_MENTAL_MODEL.md if it exists.",
            Settings(low_level_mcp_url="http://memory-server:8000/mcp"),
        )

        self.assertIn("/KLONA_MEMORY_MENTAL_MODEL.md", prompt)
        self.assertLess(len(prompt), 400)
        self.assertNotIn("vault_tree", prompt)
        self.assertNotIn("Storage gating", prompt)
        self.assertNotIn("content verbatim without semantic summarization", prompt)

    def test_remember_prompt_is_compact_and_relies_on_system_prompt(self):
        from memory_agent.config import Settings
        from memory_agent.prompts import remember_prompt

        prompt = remember_prompt("Leo prefers concise memory recall.", Settings())

        self.assertIn("Task: remember", prompt)
        self.assertIn("Leo prefers concise memory recall.", prompt)
        self.assertLess(len(prompt), 300)
        self.assertNotIn("vault_tree", prompt)
        self.assertNotIn("Storage gating", prompt)

    def test_system_prompt_contains_exact_file_content_behavior(self):
        from memory_agent.system_prompt import MEMORY_AGENT_SYSTEM_PROMPT

        self.assertIn("exact current file content", MEMORY_AGENT_SYSTEM_PROMPT)
        self.assertIn("content verbatim without semantic summarization", MEMORY_AGENT_SYSTEM_PROMPT)
        self.assertIn("low-level MCP tools", MEMORY_AGENT_SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
