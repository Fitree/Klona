import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
E2E_DIR = ROOT / "e2e_test"
OLD_E2E_DIR = ROOT / "sandbox"
COMPOSE = E2E_DIR / "docker-compose.e2e.yml"
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

    def test_compose_defines_test_memory_server_and_test_env(self):
        content = COMPOSE.read_text()

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
        self.assertIn('command: ["python3", "e2e_test/e2e_runner.py"]', content)
        self.assertIn("e2e-runtime-vault:/vault", content)
        self.assertIn("e2e-runtime-vault:/runtime-vault", content)
        self.assertIn("volumes:", content)
        self.assertNotIn("./test_vault:/vault", content)
        self.assertNotIn("sandbox/e2e_test.sh", content)
        self.assertNotIn("HOME=/home/ubuntu", content)
        self.assertNotIn("HOME=/tmp/klona-e2e-home", content)
        self.assertNotIn("http://memory-server:8000/mcp", content)
        self.assertNotIn("ALLOWED_HOSTS=memory-server:8000", content)

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
        self.assertIn('PROJECT_NAME="e2e-test"', content)
        self.assertIn('-p "$PROJECT_NAME"', content)
        self.assertIn("--abort-on-container-exit", content)
        self.assertIn("--exit-code-from test-env", content)
        self.assertIn("down -v", content)
        self.assertIn("trap cleanup EXIT", content)
        self.assertIn("cleanup_status", content)
        self.assertIn("WARNING", content)

    def test_e2e_runner_runs_all_scenarios_and_resets_runtime_vault(self):
        content = E2E_RUNNER.read_text()

        self.assertIn("RUNTIME_VAULT_DIR", content)
        self.assertIn("SOURCE_VAULT_DIR", content)
        self.assertIn("copytree", content)
        self.assertIn("e2e_scenario1.py", content)
        self.assertIn("e2e_scenario2.py", content)
        self.assertIn("reset_runtime_vault", content)
        self.assertIn("assert_source_vault_unchanged", content)
        self.assertNotIn("opencode", content)

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
        self.assertIn("plugins/klona-memory-session.js", content)
        self.assertIn("unified_diff", content)
        self.assertIn("ThreadingHTTPServer", content)
        self.assertIn("BaseHTTPRequestHandler", content)
        self.assertIn("subprocess.run", content)
        self.assertIn("@ai-sdk/openai-compatible", content)
        self.assertIn("/v1/chat/completions", content)
        self.assertIn("opencode", content)
        self.assertIn("fake/e2e-model", content)
        self.assertIn('"Hello from scenario 1"', content)
        self.assertNotIn('prompt = "Hello from scenario 1"', content)
        self.assertIn("MENTAL_MODEL_FILE", content)
        self.assertIn("<Mental_model>", content)
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
            'shutil.rmtree(OPENCODE_DATA_DIR / "plugin-state" / "klona-memory-session"',
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
