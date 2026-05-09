import ast
import importlib.util
from unittest import mock
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_init_script_module():
    spec = importlib.util.spec_from_file_location("init_memory_stack", ROOT / "scripts" / "init_memory_stack.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeHealthResponse:
    def __init__(self, status=200, body=b'{"status":"ok","server":"klona-memory-agent","version":"0.1.0"}'):
        self.status = status
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self.body


class ServerSideAssetTests(unittest.TestCase):
    def test_compose_mounts_vault_only_into_low_level_server(self):
        compose = (ROOT / "docker-compose.yml").read_text()
        self.assertIn("memory-server:", compose)
        self.assertIn("memory-agent:", compose)
        memory_agent_section = compose.split("  memory-agent:", 1)[1]
        self.assertIn("memory_agent_queue:/state", memory_agent_section)
        self.assertNotIn("HOST_VAULT_DIR", memory_agent_section)
        self.assertNotIn(":/vault", memory_agent_section)
        self.assertIn("${HOST_VAULT_DIR:?set HOST_VAULT_DIR in .env}:/vault", compose)

    def test_compose_has_only_memory_agent_named_volume(self):
        compose = (ROOT / "docker-compose.yml").read_text()
        self.assertIn("volumes:\n  memory_agent_queue:", compose)
        self.assertNotIn("vault:", compose.split("volumes:", 1)[1])

    def test_env_example_contains_required_server_side_variables(self):
        env = (ROOT / ".env.example").read_text()
        for name in [
            "HOST_VAULT_DIR",
            "HIGH_LEVEL_MCP_HOST_PORT",
            "HIGH_LEVEL_MCP_AUTH_TOKEN",
            "HIGH_LEVEL_ALLOWED_HOSTS",
            "MEMORY_AGENT_QUEUE_DB",
            "MEMORY_AGENT_TIMEOUT_SECONDS",
            "MEMORY_AGENT_MAX_RETRIES",
        ]:
            self.assertIn(f"{name}=", env)
        self.assertNotIn("LOW_LEVEL_MCP_HOST_PORT", env)
        self.assertNotIn("LOW_LEVEL_MCP_AUTH_TOKEN", env)
        self.assertNotIn("LOW_LEVEL_ALLOWED_HOSTS", env)
        self.assertNotIn("LOW_LEVEL_MCP_URL", env)
        self.assertNotIn("MEMORY_AGENT_STATE_DIR", env)
        self.assertIn("HIGH_LEVEL_ALLOWED_HOSTS=\n", env)
        self.assertIn("Empty disables DNS rebinding protection and allows all Host headers", env)
        self.assertIn("HIGH_LEVEL_MCP_AUTH_TOKEN=\n", env)
        self.assertIn("Empty disables auth", env)
        self.assertIn("HIGH_LEVEL_MCP_HOST_PORT=32310", env)

    def test_compose_passes_empty_allowed_hosts_without_non_empty_fallbacks(self):
        compose = (ROOT / "docker-compose.yml").read_text()
        self.assertIn("HIGH_LEVEL_ALLOWED_HOSTS: ${HIGH_LEVEL_ALLOWED_HOSTS-}", compose)
        self.assertIn('"${HIGH_LEVEL_MCP_HOST_PORT:-32310}:8080"', compose)
        self.assertNotIn('"${HIGH_LEVEL_MCP_HOST_PORT:-32311}:8080"', compose)
        self.assertNotIn("LOW_LEVEL_ALLOWED_HOSTS", compose)
        self.assertNotIn("LOW_LEVEL_ALLOWED_HOSTS:-localhost", compose)
        self.assertNotIn("HIGH_LEVEL_ALLOWED_HOSTS:-localhost", compose)
        self.assertNotIn("MEMORY_AGENT_STATE_DIR", compose)

    def test_compose_keeps_low_level_server_internal_only(self):
        compose = (ROOT / "docker-compose.yml").read_text()
        memory_server_section = compose.split("  memory-server:", 1)[1].split("\n\n  memory-agent:", 1)[0]
        memory_agent_section = compose.split("  memory-agent:", 1)[1]
        self.assertNotIn("ports:", memory_server_section)
        self.assertNotIn("LOW_LEVEL_MCP_HOST_PORT", compose)
        self.assertIn("AUTH_TOKEN: ${LOW_LEVEL_MCP_AUTH_TOKEN:-}", memory_server_section)
        self.assertIn("LOW_LEVEL_MCP_AUTH_TOKEN: ${LOW_LEVEL_MCP_AUTH_TOKEN:-}", memory_agent_section)
        self.assertIn("LOW_LEVEL_MCP_URL: http://memory-server:8000/mcp", memory_agent_section)
        self.assertNotIn("LOW_LEVEL_MCP_AUTH_TOKEN", (ROOT / ".env.example").read_text())
        self.assertNotIn("LOW_LEVEL_MCP_URL:", (ROOT / ".env.example").read_text())

    def test_init_script_is_parseable_and_runs_two_phase_interactive_start(self):
        script = (ROOT / "scripts" / "init_memory_stack.py").read_text()
        ast.parse(script)
        self.assertIn('["docker", "compose", "build"]', script)
        self.assertIn('["docker", "compose", "up", "-d", "--wait", "memory-server"]', script)
        self.assertIn('["docker", "compose", "run", "--rm", "--service-ports", "--no-deps", "memory-agent"]', script)
        self.assertIn('["docker", "compose", "stop", "memory-server"]', script)
        self.assertIn('DOCKER_DETACH_SEQUENCE = b"\\x10\\x11"', script)
        self.assertIn('http://localhost:{values[\'HIGH_LEVEL_MCP_HOST_PORT\']}/health', script)
        self.assertIn("urllib.request.urlopen", script)
        self.assertIn("EXPECTED_HEALTH_SERVER = \"klona-memory-agent\"", script)
        self.assertIn("EXPECTED_HEALTH_VERSION = \"0.1.0\"", script)
        self.assertIn("json.loads", script)
        self.assertIn("pty.fork()", script)
        self.assertNotIn('"--abort-on-container-exit"', script)
        self.assertNotIn("OPENCODE_MODEL", script)
        self.assertNotIn("OPENCODE_REASONING_EFFORT", script)
        self.assertNotIn("LOW_LEVEL_MCP_HOST_PORT", script)
        self.assertNotIn("LOW_LEVEL_MCP_AUTH_TOKEN", script)
        self.assertNotIn("LOW_LEVEL_ALLOWED_HOSTS", script)
        self.assertNotIn("LOW_LEVEL_MCP_URL", script)
        self.assertIn('"HIGH_LEVEL_ALLOWED_HOSTS": ""', script)
        self.assertIn("empty allows all Host headers", script)
        self.assertNotIn("secrets", script)
        self.assertNotIn("token_urlsafe", script)
        self.assertIn('"HIGH_LEVEL_MCP_AUTH_TOKEN": ""', script)
        self.assertIn("empty disables auth", script)
        self.assertIn('"HIGH_LEVEL_MCP_HOST_PORT": "32310"', script)

    def test_init_collect_values_prompts_only_user_facing_settings(self):
        module = load_init_script_module()
        answers = iter(["32312", "/tmp/vault", "token", "localhost,127.0.0.1"])

        with mock.patch("builtins.input", side_effect=lambda prompt: next(answers)) as input_mock:
            values = module.collect_values()

        self.assertEqual(input_mock.call_count, 4)
        prompts = "\n".join(call.args[0] for call in input_mock.call_args_list)
        self.assertIn("High-level user-agent MCP host port", prompts)
        self.assertIn("Host markdown vault directory", prompts)
        self.assertIn("High-level user-agent MCP bearer token", prompts)
        self.assertIn("High-level allowed hosts", prompts)
        self.assertNotIn("Memory-agent queue DB path in container", prompts)
        self.assertNotIn("Memory-agent state dir in container", prompts)
        self.assertNotIn("Recall timeout seconds", prompts)
        self.assertNotIn("Queue retry attempts", prompts)
        self.assertNotIn("OpenCode internal host", prompts)
        self.assertNotIn("OpenCode internal port", prompts)

        self.assertEqual(values["HIGH_LEVEL_MCP_HOST_PORT"], "32312")
        self.assertEqual(values["HOST_VAULT_DIR"], "/tmp/vault")
        self.assertEqual(values["HIGH_LEVEL_MCP_AUTH_TOKEN"], "token")
        self.assertEqual(values["HIGH_LEVEL_ALLOWED_HOSTS"], "localhost,127.0.0.1")
        self.assertEqual(values["MEMORY_AGENT_QUEUE_DB"], "/state/queue.db")
        self.assertNotIn("MEMORY_AGENT_STATE_DIR", values)
        self.assertEqual(values["MEMORY_AGENT_TIMEOUT_SECONDS"], "600")
        self.assertEqual(values["MEMORY_AGENT_MAX_RETRIES"], "2")
        self.assertEqual(values["OPENCODE_HOST"], "127.0.0.1")
        self.assertEqual(values["OPENCODE_PORT"], "4096")

    def test_init_command_sequence_is_guarded_for_interactive_memory_agent(self):
        script = (ROOT / "scripts" / "init_memory_stack.py").read_text()
        self.assertLess(script.index("BUILD_CMD"), script.index("START_MEMORY_SERVER_CMD"))
        self.assertLess(script.index("START_MEMORY_SERVER_CMD"), script.index("RUN_MEMORY_AGENT_CMD"))
        self.assertLess(script.index("subprocess.run(BUILD_CMD"), script.index("subprocess.run(START_MEMORY_SERVER_CMD"))
        main_start = script.index("def main")
        memory_agent_run = script.index("run_memory_agent_until_healthy", main_start)
        self.assertLess(script.index("subprocess.run(START_MEMORY_SERVER_CMD"), memory_agent_run)
        self.assertLess(script.index("Stopping partially started low-level memory-server"), memory_agent_run)
        self.assertIn("finally:", script)
        self.assertIn("if detached:", script)
        self.assertIn("Leaving detached memory-server and memory-agent running.", script)
        self.assertIn("subprocess.run(STOP_MEMORY_SERVER_CMD", script)

    def test_init_auto_detach_keeps_same_interactive_memory_agent_container(self):
        script = (ROOT / "scripts" / "init_memory_stack.py").read_text()
        self.assertIn("Run memory-agent interactively and detach from the same container", script)
        self.assertIn("os.write(master_fd, DOCKER_DETACH_SEQUENCE)", script)
        self.assertLess(script.index("os.write(master_fd, DOCKER_DETACH_SEQUENCE)"), script.index("detached.set()"))
        self.assertIn("return _status_to_return(status, detached.is_set())", script)
        self.assertIn("detached and return_code == 0", script)
        self.assertNotIn("docker compose up -d memory-agent", script)
        self.assertNotIn('["docker", "compose", "up", "-d", "memory-agent"]', script)
        self.assertIn("Non-TTY detected; running memory-agent in blocking foreground mode without auto-detach.", script)

    def test_init_health_check_requires_memory_agent_identity(self):
        module = load_init_script_module()
        with mock.patch.object(module.urllib.request, "urlopen", return_value=FakeHealthResponse()):
            self.assertTrue(module._is_healthy("http://localhost:32310/health"))
        for body in [
            b'{"status":"ok","server":"unrelated","version":"0.1.0"}',
            b'{"status":"ok","server":"klona-memory-agent","version":"9.9.9"}',
            b'{"status":"starting","server":"klona-memory-agent","version":"0.1.0"}',
            b'not json',
        ]:
            with self.subTest(body=body):
                with mock.patch.object(module.urllib.request, "urlopen", return_value=FakeHealthResponse(body=body)):
                    self.assertFalse(module._is_healthy("http://localhost:32310/health"))
        with mock.patch.object(module.urllib.request, "urlopen", return_value=FakeHealthResponse(status=204)):
            self.assertFalse(module._is_healthy("http://localhost:32310/health"))

    def test_init_status_helper_does_not_mask_child_failures_after_detach(self):
        module = load_init_script_module()
        self.assertEqual(module._status_to_return(0, True), (0, True))
        self.assertEqual(module._status_to_return(3 << 8, True), (3, False))
        self.assertEqual(module._status_to_return(5 << 8, False), (5, False))
        self.assertEqual(module._status_to_return(None, True), (1, False))

    def test_init_poll_marks_detached_only_after_detach_write_succeeds(self):
        module = load_init_script_module()
        detached = module.threading.Event()
        done = module.threading.Event()
        with mock.patch.object(module, "_is_healthy", return_value=True), mock.patch.object(module.os, "write", side_effect=OSError):
            module._poll_health_until_detach("http://localhost:32310/health", 123, detached, done)
        self.assertFalse(detached.is_set())

        writes = []

        def record_write(fd, data):
            writes.append((fd, data))
            return len(data)

        detached = module.threading.Event()
        done = module.threading.Event()
        with mock.patch.object(module, "_is_healthy", return_value=True), mock.patch.object(module.os, "write", side_effect=record_write):
            module._poll_health_until_detach("http://localhost:32310/health", 123, detached, done)
        self.assertTrue(detached.is_set())
        self.assertEqual(writes[0], (123, module.DOCKER_DETACH_SEQUENCE))

    def test_legacy_local_memory_agent_asset_is_marked_deprecated(self):
        asset = (ROOT / "klona_agent" / "opencode" / "assets" / "agents" / "klona-memory.md").read_text()
        self.assertIn("DEPRECATED", asset)
        self.assertIn("not installed", asset.lower())

    def test_memory_agent_dockerfile_documents_runtime_command(self):
        dockerfile = (ROOT / "memory_agent" / "Dockerfile").read_text()
        self.assertIn('CMD ["python", "-m", "memory_agent.runtime"]', dockerfile)
        self.assertIn("OpenCode CLI", dockerfile)
        self.assertIn("apt-get install -y --no-install-recommends bash curl", dockerfile)
        self.assertIn("curl -fsSL https://opencode.ai/install | bash", dockerfile)
        self.assertIn('/root/.opencode/bin', dockerfile)
        self.assertIn('opencode --version', dockerfile)
        self.assertNotIn("curl -fsSL https://opencode.ai/install | sh", dockerfile)

    def test_memory_agent_pyproject_declares_hatch_package(self):
        pyproject = (ROOT / "memory_agent" / "pyproject.toml").read_text()
        self.assertIn('[tool.hatch.build.targets.wheel]', pyproject)
        self.assertIn('packages = ["src/memory_agent"]', pyproject)

    def test_low_level_readme_does_not_point_normal_installer_at_admin_endpoint(self):
        readme = (ROOT / "memory_server" / "README.md").read_text()
        self.assertNotIn("python install_agent.py --platform opencode", readme)
        self.assertIn("trusted admin/direct MCP clients", readme)
        self.assertIn("Configure those clients manually", readme)
        self.assertIn("recall(input: str)", readme)
        self.assertIn("remember(input: str)", readme)

    def test_opencode_snippet_uses_high_level_tools_not_local_subagent(self):
        snippet = (ROOT / "klona_agent" / "opencode" / "assets" / "AGENT.md.snippet").read_text()
        self.assertIn("recall(input: str)", snippet)
        self.assertIn("remember(input: str)", snippet)
        self.assertNotIn("Do **not** delegate memory work to a local `klona-memory` subagent", snippet)


if __name__ == "__main__":
    unittest.main()
