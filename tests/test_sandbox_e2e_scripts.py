import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "sandbox" / "docker-compose.e2e.yml"
RUNNER = ROOT / "sandbox" / "run_e2e.sh"
INNER = ROOT / "sandbox" / "e2e_test.sh"
README = ROOT / "README.md"


class SandboxE2EScriptTests(unittest.TestCase):
    def test_compose_defines_test_memory_server_and_test_env(self):
        content = COMPOSE.read_text()

        self.assertIn("test-memory-server:", content)
        self.assertNotRegex(content, r"(?m)^  memory-server:\s*$")
        self.assertIn("test-env:", content)
        self.assertIn("dockerfile: sandbox/Dockerfile", content)
        self.assertIn("context: ../memory_server", content)
        self.assertIn("AUTH_TOKEN=e2e-token", content)
        self.assertIn("VAULT_DIR=/vault", content)
        self.assertIn("ALLOWED_HOSTS=test-memory-server:8000,test-env:*,localhost:8000", content)
        self.assertIn("http://localhost:8000/health", content)
        self.assertIn("condition: service_healthy", content)
        self.assertIn("/workspace/KLONA", content)
        self.assertIn("http://test-memory-server:8000/mcp", content)
        self.assertNotIn("http://memory-server:8000/mcp", content)
        self.assertNotIn("ALLOWED_HOSTS=memory-server:8000", content)

    def test_run_e2e_is_executable_and_cleans_up(self):
        mode = RUNNER.stat().st_mode
        self.assertTrue(mode & stat.S_IXUSR, "sandbox/run_e2e.sh must be executable")

        content = RUNNER.read_text()
        self.assertIn("set -euo pipefail", content)
        self.assertIn("docker compose", content)
        self.assertIn('COMPOSE_PROJECT_NAME:-', content)
        self.assertIn('PROJECT_NAME=', content)
        self.assertIn('sha256sum', content)
        self.assertIn('printf \'%s\' "$REPO_ROOT"', content)
        self.assertIn('-p "$PROJECT_NAME"', content)
        self.assertIn("--abort-on-container-exit", content)
        self.assertIn("--exit-code-from test-env", content)
        self.assertIn("down -v", content)
        self.assertIn("trap cleanup EXIT", content)
        self.assertIn("cleanup_status", content)
        self.assertIn("WARNING", content)

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
        mode = INNER.stat().st_mode
        self.assertTrue(mode & stat.S_IXUSR, "sandbox/e2e_test.sh must be executable")

        content = INNER.read_text()
        self.assertIn("set -euo pipefail", content)
        self.assertIn("python3 -B -m unittest discover -s tests", content)
        self.assertIn("PYTHONPYCACHEPREFIX=/tmp/klona-e2e-pycache", content)
        self.assertIn("python3 -B -m compileall -q install_agent.py klona_agent tests memory_server/src/server.py", content)
        self.assertIn('E2E_HOME="${KLONA_E2E_HOME:-/tmp/klona-e2e-home}"', content)
        self.assertIn('/tmp/klona-e2e-*', content)
        self.assertIn('refusing to remove unsafe E2E path', content)
        self.assertIn('rm -rf -- "$E2E_HOME"', content)
        self.assertIn('rm -rf -- "$INVALID_HOME"', content)
        self.assertIn('grep -F -- "$marker" "$file" || true', content)
        self.assertIn("http://test-memory-server:8000/health", content)
        self.assertIn("http://test-memory-server:8000/mcp", content)
        self.assertNotIn("http://memory-server:8000/health", content)
        self.assertNotIn("http://memory-server:8000/mcp", content)
        self.assertIn("--klona-memory-server-url", content)
        self.assertIn("--klona-memory-server-token", content)
        self.assertIn("<!-- KLONA:BEGIN -->", content)
        self.assertIn("klona_memory_server", content)
        self.assertIn("diff -u", content)
        self.assertIn("--uninstall", content)

    def test_readme_documents_one_command_e2e(self):
        content = README.read_text()

        self.assertIn("sandbox/run_e2e.sh", content)
        self.assertIn("Docker Compose v2", content)
        self.assertIn("running Docker daemon", content)


if __name__ == "__main__":
    unittest.main()
