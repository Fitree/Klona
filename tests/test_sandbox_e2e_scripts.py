import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
E2E_DIR = ROOT / "e2e_test"
OLD_E2E_DIR = ROOT / "sandbox"
BASE_COMPOSE = E2E_DIR / "docker-compose.base.yml"
SCENARIO1_COMPOSE = E2E_DIR / "docker-compose.scenario1.yml"
SCENARIO2_COMPOSE = E2E_DIR / "docker-compose.scenario2.yml"
LEGACY_COMPOSE = E2E_DIR / "docker-compose.e2e.yml"
DOCKERFILE = E2E_DIR / "Dockerfile"
RUNNER = E2E_DIR / "run_e2e.sh"
E2E_RUNNER = E2E_DIR / "e2e_runner.py"
INNER = E2E_DIR / "e2e_scenario1.py"
SCENARIO2 = E2E_DIR / "e2e_scenario2.py"
OLD_SCENARIO1_SHELL = ROOT / "sandbox" / "e2e_scenario1.sh"
OLD_INNER = ROOT / "sandbox" / "e2e_test.sh"
LEGACY_BUILD = ROOT / "sandbox" / "build.sh"
LEGACY_START_DOCKER = ROOT / "sandbox" / "start_docker.sh"
README = ROOT / "README.md"
MENTAL_MODEL = E2E_DIR / "test_vault" / "MENTAL_MODEL.md"
RARE_MENTAL_MODEL_MARKER = "KLONA_E2E_MENTAL_MODEL_LOADED_7f4e2d1a9c6b4380b5e21f0d3a8c9e62"


class SandboxE2EScriptTests(unittest.TestCase):
    def _service_block(self, content, service_name):
        marker = f"  {service_name}:\n"
        lines = content.splitlines(keepends=True)
        start = next(index for index, line in enumerate(lines) if line == marker)
        end = len(lines)
        for index in range(start + 1, len(lines)):
            line = lines[index]
            if line and not line.startswith(" "):
                end = index
                break
            if line.startswith("  ") and not line.startswith("    "):
                end = index
                break
        return "".join(lines[start:end])

    def test_legacy_sandbox_scripts_do_not_exist(self):
        self.assertFalse(LEGACY_BUILD.exists(), "sandbox/build.sh should not exist")
        self.assertFalse(LEGACY_START_DOCKER.exists(), "sandbox/start_docker.sh should not exist")

    def test_old_intended_sandbox_e2e_files_do_not_exist(self):
        for relative_path in [
            "Dockerfile",
            "docker-compose.e2e.yml",
            "e2e_scenario1.py",
            "run_e2e.sh",
            "test_vault/MENTAL_MODEL.md",
        ]:
            path = OLD_E2E_DIR / relative_path
            self.assertFalse(path.exists(), f"{path.relative_to(ROOT)} should not exist")

    def test_split_compose_defines_isolated_base_services(self):
        content = BASE_COMPOSE.read_text()

        self.assertIn("vault-seeder:", content)
        self.assertIn("test-memory-server:", content)
        self.assertNotRegex(content, r"(?m)^  memory-server:\s*$")
        self.assertIn("test-env:", content)
        self.assertIn("dockerfile: e2e_test/Dockerfile", content)
        self.assertIn("context: ../memory_server", content)
        self.assertIn("AUTH_TOKEN=e2e-token", content)
        self.assertIn("VAULT_DIR=/vault", content)
        self.assertIn("ALLOWED_HOSTS=test-memory-server:8000,test-env:*,localhost:8000", content)
        self.assertIn("http://localhost:8000/health", content)
        self.assertIn("condition: service_healthy", content)
        self.assertIn("/workspace/KLONA", content)
        self.assertIn("user: test_user", content)
        self.assertIn("HOME=/home/test_user", content)
        self.assertIn("http://test-memory-server:8000/mcp", content)

        seeder_block = self._service_block(content, "vault-seeder")
        self.assertIn("./test_vault:/source-vault:ro", seeder_block)
        self.assertEqual(content.count("./test_vault:/source-vault:ro"), 1)
        self.assertIn("e2e-runtime-vault:/runtime-vault", seeder_block)
        self.assertIn("rm -rf", seeder_block)
        self.assertIn("cp -a", seeder_block)

        memory_block = self._service_block(content, "test-memory-server")
        self.assertIn("e2e-runtime-vault:/vault", memory_block)
        self.assertNotIn("./test_vault", memory_block)
        self.assertNotIn("/source-vault", memory_block)
        self.assertNotIn("vault-seeder", memory_block)
        self.assertNotIn("service_completed_successfully", memory_block)

        test_env_block = self._service_block(content, "test-env")
        self.assertNotIn("e2e-runtime-vault", test_env_block)
        self.assertNotIn("/runtime-vault", test_env_block)
        self.assertNotIn("/vault", test_env_block)
        self.assertNotIn("./test_vault", test_env_block)
        self.assertNotIn("/source-vault", test_env_block)
        self.assertNotIn("command:", test_env_block)

        self.assertIn("e2e-runtime-vault:/vault", content)
        self.assertIn("volumes:", content)
        self.assertNotIn("./test_vault:/vault", content)
        self.assertNotIn("sandbox/e2e_test.sh", content)
        self.assertNotIn("HOME=/home/ubuntu", content)
        self.assertNotIn("HOME=/tmp/klona-e2e-home", content)
        self.assertNotIn("http://memory-server:8000/mcp", content)
        self.assertNotIn("ALLOWED_HOSTS=memory-server:8000", content)

    def test_scenario_compose_files_run_direct_scenario_commands(self):
        scenario1 = SCENARIO1_COMPOSE.read_text()
        scenario2 = SCENARIO2_COMPOSE.read_text()

        self.assertIn('command: ["python3", "-B", "e2e_test/e2e_scenario1.py"]', scenario1)
        self.assertIn('command: ["python3", "-B", "e2e_test/e2e_scenario2.py"]', scenario2)
        self.assertIn("container_name: e2e-scenario1-test-env", self._service_block(scenario1, "test-env"))
        self.assertIn(
            "container_name: e2e-scenario1-test-memory-server",
            self._service_block(scenario1, "test-memory-server"),
        )
        self.assertIn("container_name: e2e-scenario2-test-env", self._service_block(scenario2, "test-env"))
        self.assertIn(
            "container_name: e2e-scenario2-test-memory-server",
            self._service_block(scenario2, "test-memory-server"),
        )
        self.assertLess(scenario1.index("  test-memory-server:\n"), scenario1.index("  test-env:\n"))
        self.assertLess(scenario2.index("  test-memory-server:\n"), scenario2.index("  test-env:\n"))
        self.assertNotIn("e2e_runner.py", scenario1)
        self.assertNotIn("e2e_runner.py", scenario2)
        self.assertNotIn("e2e-runtime-vault", scenario1)
        self.assertNotIn("e2e-runtime-vault", scenario2)

    def test_legacy_unified_compose_and_runner_do_not_exist(self):
        self.assertFalse(LEGACY_COMPOSE.exists(), "old unified compose should be replaced by split compose files")
        self.assertFalse(E2E_RUNNER.exists(), "test-env should run scenario files directly")

    def test_dockerfile_creates_test_user_with_normal_home(self):
        content = DOCKERFILE.read_text()

        self.assertIn("useradd", content)
        self.assertIn("test_user", content)
        self.assertIn("/home/test_user", content)
        self.assertIn("/bin/bash", content)

    def test_test_vault_contains_mounted_mental_model(self):
        content = MENTAL_MODEL.read_text()

        self.assertIn("MENTAL_MODEL.md", str(MENTAL_MODEL))
        self.assertIn(RARE_MENTAL_MODEL_MARKER, content)

    def test_run_e2e_is_executable_and_cleans_up(self):
        mode = RUNNER.stat().st_mode
        self.assertTrue(mode & stat.S_IXUSR, "e2e_test/run_e2e.sh must be executable")

        content = RUNNER.read_text()
        self.assertIn("set -euo pipefail", content)
        self.assertIn("docker compose", content)
        self.assertIn("SCENARIOS=", content)
        self.assertIn("scenario1", content)
        self.assertIn("scenario2", content)
        self.assertIn('PROJECT_NAME="e2e-test-${scenario}"', content)
        self.assertIn('docker-compose.base.yml', content)
        self.assertIn('docker-compose.${scenario}.yml', content)
        self.assertIn('-p "$PROJECT_NAME"', content)
        self.assertIn('for scenario in "${SCENARIOS[@]}"', content)
        self.assertIn("--abort-on-container-exit", content)
        self.assertIn("--exit-code-from test-env", content)
        self.assertIn("down -v", content)
        self.assertIn("--remove-orphans", content)
        self.assertIn("cleanup_status", content)
        self.assertIn("WARNING", content)
        self.assertNotIn("source_vault_sha256", content)
        self.assertNotIn("sha256", content)
        self.assertNotIn("hashlib", content)

    def test_run_e2e_seeds_vault_before_targeted_scenario_up(self):
        content = RUNNER.read_text()

        self.assertIn('run --rm vault-seeder', content)
        self.assertIn(
            'up --build --abort-on-container-exit --exit-code-from test-env test-memory-server test-env',
            content,
        )
        self.assertLess(
            content.index('cleanup_project "$PROJECT_NAME" "$scenario_compose_file"'),
            content.index('run --rm vault-seeder'),
        )
        self.assertLess(
            content.index('run --rm vault-seeder'),
            content.index('up --build --abort-on-container-exit --exit-code-from test-env'),
        )

    def test_run_e2e_returns_cleanup_failure_after_successful_tests(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            fake_docker = temp_path / "docker"
            fake_docker.write_text(
                "#!/usr/bin/env bash\n"
                "if printf '%s\\n' \"$@\" | grep -Fxq down; then\n"
                "  exit 7\n"
                "fi\n"
                "exit 0\n",
                encoding="utf-8",
            )
            fake_docker.chmod(0o755)

            result = subprocess.run(
                [str(RUNNER)],
                env={"PATH": f"{temp_path}:/usr/bin:/bin"},
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )

        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertIn("cleanup", result.stderr.lower())

    def test_run_e2e_preserves_test_failure_when_cleanup_also_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            fake_docker = temp_path / "docker"
            fake_docker.write_text(
                "#!/usr/bin/env bash\n"
                "if printf '%s\\n' \"$@\" | grep -Fxq down; then\n"
                "  exit 7\n"
                "fi\n"
                "exit 5\n",
                encoding="utf-8",
            )
            fake_docker.chmod(0o755)

            result = subprocess.run(
                [str(RUNNER)],
                env={"PATH": f"{temp_path}:/usr/bin:/bin"},
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )

        self.assertEqual(result.returncode, 5, result.stderr)
        self.assertIn("cleanup", result.stderr.lower())

    def test_inner_e2e_script_checks_expected_behaviors(self):
        self.assertFalse(OLD_INNER.exists(), "sandbox/e2e_test.sh should not exist")
        self.assertFalse(OLD_SCENARIO1_SHELL.exists(), "sandbox/e2e_scenario1.sh should not exist")

        content = INNER.read_text()
        self.assertNotIn("No E2E checks configured yet.", content)
        self.assertIn("/home/test_user", content)
        self.assertIn("getpass.getuser", content)
        self.assertIn('"python3", "install_agent.py", "--platform", "opencode"', content)
        self.assertIn("--klona-memory-server-url", content)
        self.assertIn("--klona-memory-server-token", content)
        self.assertIn("KLONA_E2E_MCP_URL", content)
        self.assertIn("KLONA_E2E_TOKEN", content)
        self.assertIn("AGENTS.md", content)
        self.assertIn("opencode.json", content)
        self.assertIn("agents/klona-memory.md", content)
        self.assertIn("plugins/klona-mental-model-injector.js", content)
        self.assertNotIn("klona-memory-session.js", content)
        self.assertNotIn("XDG_DATA_HOME", content)
        self.assertIn('HOME / ".local" / "share" / "opencode"', content)
        self.assertIn('plugin-state" / "klona-mental-model-injector"', content)
        self.assertIn("unified_diff", content)
        self.assertIn("ThreadingHTTPServer", content)
        self.assertIn("BaseHTTPRequestHandler", content)
        self.assertIn("subprocess.run", content)
        self.assertIn("@ai-sdk/openai-compatible", content)
        self.assertIn("/v1/chat/completions", content)
        self.assertIn("opencode", content)
        self.assertIn("fake/e2e-model", content)
        self.assertIn('"Verify initial KLONA mental model injection"', content)
        self.assertIn('"Verify KLONA mental model injection after compaction"', content)
        self.assertNotIn('"Hello from scenario 1"', content)
        self.assertNotIn('"Hello after compaction"', content)
        self.assertNotIn('prompt = "Hello from scenario 1"', content)
        self.assertIn("MENTAL_MODEL_FILE", content)
        self.assertIn("<Mental_model>", content)
        self.assertIn("def check_mental_model_injection_at_user_message(message_text: str):", content)
        self.assertIn('check_mental_model_injection_at_user_message("Verify initial KLONA mental model injection")', content)
        self.assertIn('check_mental_model_injection_at_user_message("Verify KLONA mental model injection after compaction")', content)
        self.assertIn('session_title = f"klona-e2e-scenario1-', content)
        self.assertIn('"--title",', content)
        self.assertIn('session_title,', content)
        self.assertIn('"opencode", "session", "list", "--format", "json"', content)
        self.assertIn('"opencode",', content)
        self.assertIn('"serve",', content)
        self.assertIn('f"http://127.0.0.1:{OPENCODE_SERVE_PORT}"', content)
        self.assertIn('serve_process.poll()', content)
        self.assertIn('time.time() > deadline', content)
        self.assertIn('f"/session/{session_id}/summarize"', content)
        self.assertIn('"providerID": "fake"', content)
        self.assertIn('"modelID": "e2e-model"', content)
        self.assertIn('"--session",', content)
        self.assertIn('session_id,', content)
        self.assertIn("serve_process.terminate()", content)
        self.assertIn('"--uninstall", "--platform", "opencode"', content)
        self.assertIn("klona_memory_server", content)
        self.assertIn("E2E PASS", content)
        self.assertNotIn("##################", content)
        self.assertNotIn("print(needle in text)", content)
        self.assertNotIn("set -euo pipefail", content)
        self.assertNotIn("diff -u", content)
        self.assertNotIn("$HOME", content)
        self.assertNotIn("python3 -B -m unittest discover -s tests", content)
        self.assertNotIn("curl -fsS http://test-memory-server:8000/health", content)
        self.assertNotIn("http://memory-server:8000/health", content)
        self.assertNotIn("http://memory-server:8000/mcp", content)

    def test_scenario2_directly_exercises_memory_server_tools(self):
        content = SCENARIO2.read_text()

        self.assertIn("urllib.request", content)
        self.assertIn('"initialize"', content)
        self.assertIn('"notifications/initialized"', content)
        self.assertIn('"tools/call"', content)
        for tool_name in [
            "vault_tree",
            "vault_ls",
            "vault_read",
            "vault_write",
            "vault_update",
            "vault_delete",
            "vault_move",
            "vault_mkdir",
            "vault_grep",
            "vault_backlinks",
        ]:
            self.assertIn(tool_name, content)
        self.assertIn("Authorization", content)
        self.assertIn("Bearer", content)
        self.assertIn("Unauthorized", content)
        self.assertIn("/health", content)
        self.assertNotIn("opencode", content.lower())

    def test_scenario1_does_not_preclean_opencode_state(self):
        content = INNER.read_text()

        self.assertNotIn("clean_scenario_state", content)
        self.assertNotIn("unlink(missing_ok=True)", content)
        self.assertNotIn(
            'shutil.rmtree(OPENCODE_DATA_DIR / "plugin-state" / "klona-mental-model-injector"',
            content,
        )
        self.assertIn("shutil.rmtree(TMP_DIR, ignore_errors=True)", content)

    def test_readme_documents_one_command_e2e(self):
        content = README.read_text()

        self.assertIn("e2e_test/run_e2e.sh", content)
        self.assertNotIn("sandbox/run_e2e.sh", content)
        self.assertIn("Docker Compose v2", content)
        self.assertIn("running Docker daemon", content)
        self.assertIn("actual OpenCode", content)
        self.assertIn("fake OpenAI-compatible provider", content)


if __name__ == "__main__":
    unittest.main()
