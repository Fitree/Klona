import ast
import importlib.util
import struct
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
        ]:
            self.assertIn(f"{name}=", env)
        self.assertNotIn("LOW_LEVEL_MCP_HOST_PORT", env)
        self.assertNotIn("LOW_LEVEL_MCP_AUTH_TOKEN", env)
        self.assertNotIn("LOW_LEVEL_ALLOWED_HOSTS", env)
        self.assertNotIn("LOW_LEVEL_MCP_URL", env)
        self.assertNotIn("MEMORY_AGENT_STATE_DIR", env)
        self.assertNotIn("OPENCODE_HOST", env)
        self.assertNotIn("OPENCODE_PORT", env)
        self.assertNotIn("MEMORY_AGENT_QUEUE_DB", env)
        self.assertNotIn("MEMORY_AGENT_TIMEOUT_SECONDS", env)
        self.assertNotIn("MEMORY_AGENT_MAX_RETRIES", env)
        self.assertIn("HIGH_LEVEL_ALLOWED_HOSTS=\n", env)
        self.assertIn("dashboard", env)
        self.assertIn("Empty disables Host-header allowlisting", env)
        self.assertIn("publishing externally", env)
        self.assertIn("klona.example.com:32310", env)
        self.assertIn("203.0.113.10:32310", env)
        self.assertIn("HIGH_LEVEL_MCP_AUTH_TOKEN=\n", env)
        self.assertIn("Empty disables auth", env)
        self.assertIn("publishing this service beyond localhost", env)
        self.assertIn("HIGH_LEVEL_MCP_HOST_PORT=32310", env)
        self.assertIn("KLONA_REM_SLEEP_ENABLED=true", env)
        self.assertIn("KLONA_REM_SLEEP_REMEMBER_THRESHOLD=20", env)
        self.assertIn("threshold <=0", env)
        self.assertIn("/dashboard", env)
        self.assertNotIn("/queue dashboard", env)

    def test_docs_reference_dashboard_not_queue_route(self):
        readme = (ROOT / "README.md").read_text()

        self.assertIn("http://localhost:32310/dashboard", readme)
        self.assertIn("/dashboard` first shows a browser token login form", readme)
        self.assertNotIn("http://localhost:32310/queue", readme)
        self.assertNotIn("`/queue`", readme)

    def test_compose_passes_empty_allowed_hosts_without_non_empty_fallbacks(self):
        compose = (ROOT / "docker-compose.yml").read_text()
        self.assertIn("HIGH_LEVEL_ALLOWED_HOSTS: ${HIGH_LEVEL_ALLOWED_HOSTS-}", compose)
        self.assertIn('"${HIGH_LEVEL_MCP_HOST_PORT:-32310}:8080"', compose)
        self.assertNotIn('"127.0.0.1:${HIGH_LEVEL_MCP_HOST_PORT:-32310}:8080"', compose)
        self.assertNotIn('"${HIGH_LEVEL_MCP_HOST_PORT:-32311}:8080"', compose)
        self.assertNotIn("LOW_LEVEL_ALLOWED_HOSTS", compose)
        self.assertNotIn("LOW_LEVEL_ALLOWED_HOSTS:-localhost", compose)
        self.assertNotIn("HIGH_LEVEL_ALLOWED_HOSTS:-localhost", compose)
        self.assertNotIn("MEMORY_AGENT_STATE_DIR", compose)

    def test_compose_uses_fixed_internal_opencode_url_without_host_port_knobs(self):
        compose = (ROOT / "docker-compose.yml").read_text()
        self.assertIn("OPENCODE_BASE_URL: ${OPENCODE_BASE_URL:-http://127.0.0.1:4096}", compose)
        self.assertNotIn("OPENCODE_HOST", compose)
        self.assertNotIn("OPENCODE_PORT", compose)

    def test_compose_preserves_internal_memory_agent_defaults(self):
        compose = (ROOT / "docker-compose.yml").read_text()
        self.assertIn("MEMORY_AGENT_QUEUE_DB: ${MEMORY_AGENT_QUEUE_DB:-/state/queue.db}", compose)
        self.assertIn("MEMORY_AGENT_TIMEOUT_SECONDS: ${MEMORY_AGENT_TIMEOUT_SECONDS:-600}", compose)
        self.assertIn("MEMORY_AGENT_MAX_RETRIES: ${MEMORY_AGENT_MAX_RETRIES:-2}", compose)
        self.assertIn("KLONA_REM_SLEEP_ENABLED: ${KLONA_REM_SLEEP_ENABLED:-true}", compose)
        self.assertIn("KLONA_REM_SLEEP_REMEMBER_THRESHOLD: ${KLONA_REM_SLEEP_REMEMBER_THRESHOLD:-20}", compose)

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
        self.assertNotIn("OPENCODE_HOST", script)
        self.assertNotIn("OPENCODE_PORT", script)
        self.assertNotIn("LOW_LEVEL_MCP_HOST_PORT", script)
        self.assertNotIn("LOW_LEVEL_MCP_AUTH_TOKEN", script)
        self.assertNotIn("LOW_LEVEL_ALLOWED_HOSTS", script)
        self.assertNotIn("LOW_LEVEL_MCP_URL", script)
        self.assertIn('"HIGH_LEVEL_ALLOWED_HOSTS": ""', script)
        self.assertIn("empty allows all", script)
        self.assertIn("narrow for external access", script)
        self.assertNotIn("secrets", script)
        self.assertNotIn("token_urlsafe", script)
        self.assertIn('"HIGH_LEVEL_MCP_AUTH_TOKEN": ""', script)
        self.assertIn("empty disables auth", script)
        self.assertIn('"HIGH_LEVEL_MCP_HOST_PORT": "32310"', script)
        self.assertIn('"KLONA_REM_SLEEP_ENABLED": "true"', script)
        self.assertIn('"KLONA_REM_SLEEP_REMEMBER_THRESHOLD": "20"', script)
        self.assertIn("manual dashboard action still works", script)

    def test_init_collect_values_prompts_only_user_facing_settings(self):
        module = load_init_script_module()
        answers = iter(["32312", "/tmp/vault", "token", "localhost,127.0.0.1", "true", "7"])

        with mock.patch("builtins.input", side_effect=lambda prompt: next(answers)) as input_mock:
            values = module.collect_values()

        self.assertEqual(input_mock.call_count, 6)
        prompts = "\n".join(call.args[0] for call in input_mock.call_args_list)
        self.assertIn("High-level user-agent MCP host port", prompts)
        self.assertIn("Host markdown vault directory", prompts)
        self.assertIn("High-level user-agent MCP bearer token", prompts)
        self.assertIn("High-level allowed hosts", prompts)
        self.assertIn("Automatic REM sleep enabled", prompts)
        self.assertIn("Successful remember jobs before automatic REM sleep", prompts)
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
        self.assertEqual(values["KLONA_REM_SLEEP_ENABLED"], "true")
        self.assertEqual(values["KLONA_REM_SLEEP_REMEMBER_THRESHOLD"], "7")
        self.assertNotIn("MEMORY_AGENT_QUEUE_DB", values)
        self.assertNotIn("MEMORY_AGENT_STATE_DIR", values)
        self.assertNotIn("MEMORY_AGENT_TIMEOUT_SECONDS", values)
        self.assertNotIn("MEMORY_AGENT_MAX_RETRIES", values)
        self.assertNotIn("OPENCODE_HOST", values)
        self.assertNotIn("OPENCODE_PORT", values)

        generated_env = module.build_env(values)
        self.assertNotIn("MEMORY_AGENT_QUEUE_DB", generated_env)
        self.assertNotIn("MEMORY_AGENT_TIMEOUT_SECONDS", generated_env)
        self.assertNotIn("MEMORY_AGENT_MAX_RETRIES", generated_env)
        self.assertIn("KLONA_REM_SLEEP_ENABLED=true", generated_env)
        self.assertIn("KLONA_REM_SLEEP_REMEMBER_THRESHOLD=7", generated_env)

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

    def test_init_copies_terminal_size_to_pty(self):
        module = load_init_script_module()
        window_size = struct.pack("HHHH", 40, 120, 0, 0)
        calls = []

        def fake_ioctl(fd, request, arg):
            calls.append((fd, request, arg))
            if request == module.termios.TIOCGWINSZ:
                return window_size
            return b""

        with mock.patch.object(module.fcntl, "ioctl", side_effect=fake_ioctl):
            module._copy_terminal_size_to_pty(10, 20)

        self.assertEqual(calls[0], (10, module.termios.TIOCGWINSZ, struct.pack("HHHH", 0, 0, 0, 0)))
        self.assertEqual(calls[1], (20, module.termios.TIOCSWINSZ, window_size))

    def test_init_skips_invalid_terminal_size(self):
        module = load_init_script_module()

        with mock.patch.object(module.fcntl, "ioctl", return_value=struct.pack("HHHH", 0, 120, 0, 0)) as ioctl_mock:
            module._copy_terminal_size_to_pty(10, 20)

        self.assertEqual(ioctl_mock.call_count, 1)

    def test_init_registers_and_restores_sigwinch_handler_for_pty(self):
        script = (ROOT / "scripts" / "init_memory_stack.py").read_text()
        fork_index = script.index("pty.fork()")
        copy_index = script.index("_copy_terminal_size_to_pty(stdin_fd, master_fd)", fork_index)
        signal_index = script.index("signal.signal(signal.SIGWINCH, handle_sigwinch)", copy_index)
        restore_index = script.index("signal.signal(signal.SIGWINCH, old_sigwinch_handler)", signal_index)
        close_index = script.index("os.close(master_fd)", restore_index)

        self.assertLess(fork_index, copy_index)
        self.assertLess(copy_index, signal_index)
        self.assertLess(signal_index, restore_index)
        self.assertLess(restore_index, close_index)

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

    def test_legacy_local_memory_agent_asset_is_removed(self):
        asset = ROOT / "klona_agent" / "opencode" / "assets" / "agents" / "klona-memory.md"
        self.assertFalse(asset.exists())

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
        self.assertNotIn("cp .env.example .env", readme)
        self.assertNotIn("docker compose up -d --build", readme)
        self.assertNotIn("http://localhost:32310/mcp", readme)
        self.assertIn("trusted admin/direct MCP clients", readme)
        self.assertIn("not published to the host", readme)
        self.assertIn("recall(input: str)", readme)
        self.assertIn("remember(input: str)", readme)

    def test_obsolete_memory_server_local_compose_assets_are_removed(self):
        self.assertFalse((ROOT / "memory_server" / "docker-compose.yml").exists())
        self.assertFalse((ROOT / "memory_server" / ".env.example").exists())

    def test_opencode_snippet_uses_high_level_tools_not_local_subagent(self):
        snippet = (ROOT / "klona_agent" / "opencode" / "assets" / "AGENT.md.snippet").read_text()
        self.assertIn("recall(input: str)", snippet)
        self.assertIn("remember(input: str)", snippet)
        self.assertIn("`klona_memory` MCP", snippet)
        self.assertNotIn("KLONA MCP", snippet)
        self.assertNotIn("Do **not** delegate memory work to a local `klona-memory` subagent", snippet)

    def test_opencode_snippet_defaults_to_aggressive_recall_under_uncertainty(self):
        snippet = (ROOT / "klona_agent" / "opencode" / "assets" / "AGENT.md.snippet").read_text()
        self.assertIn("Default to **True** (do recall) whenever there is any uncertainty", snippet)
        self.assertIn("durable memory could affect the response", snippet)
        self.assertIn("Set **False** (do not recall) only when highly confident", snippet)
        self.assertIn("purely local or mechanical, and independent of stored user/project context", snippet)

    def test_opencode_snippet_requires_mid_turn_recall_for_new_gaps(self):
        snippet = (ROOT / "klona_agent" / "opencode" / "assets" / "AGENT.md.snippet").read_text()
        self.assertIn("### Mandatory continuous memory workflow", snippet)
        self.assertIn("then keep making recall decisions during the turn", snippet)
        self.assertIn("### Mid-turn recall decision", snippet)
        self.assertIn("If exploration, file reads, tool results, errors, clarifications", snippet)
        self.assertIn("new information gap that durable memory could fill", snippet)

    def test_opencode_snippet_recalls_before_decisions_and_actions(self):
        snippet = (ROOT / "klona_agent" / "opencode" / "assets" / "AGENT.md.snippet").read_text()
        self.assertIn("Recall before decisions or actions", snippet)
        self.assertIn("could affect what you edit, run, recommend, skip, prioritize, or tell the user", snippet)
        self.assertIn("before choosing a direction", snippet)

    def test_opencode_snippet_treats_mental_model_as_orientation_cache_only(self):
        snippet = (ROOT / "klona_agent" / "opencode" / "assets" / "AGENT.md.snippet").read_text()
        self.assertIn("KLONA_MEMORY_MENTAL_MODEL.md", snippet)
        self.assertIn("Treat it as an orientation cache, not a substitute for recall", snippet)
        self.assertIn("Do not rely on the session-start mental model as a substitute for recall", snippet)


if __name__ == "__main__":
    unittest.main()
