import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


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
            "LOW_LEVEL_MCP_HOST_PORT",
            "LOW_LEVEL_MCP_AUTH_TOKEN",
            "LOW_LEVEL_ALLOWED_HOSTS",
            "HIGH_LEVEL_MCP_HOST_PORT",
            "HIGH_LEVEL_MCP_AUTH_TOKEN",
            "HIGH_LEVEL_ALLOWED_HOSTS",
            "LOW_LEVEL_MCP_URL",
            "MEMORY_AGENT_QUEUE_DB",
            "MEMORY_AGENT_STATE_DIR",
            "MEMORY_AGENT_TIMEOUT_SECONDS",
            "MEMORY_AGENT_MAX_RETRIES",
        ]:
            self.assertIn(f"{name}=", env)
        self.assertIn("LOW_LEVEL_ALLOWED_HOSTS=localhost,127.0.0.1,memory-server:8000", env)
        self.assertIn("LOW_LEVEL_MCP_AUTH_TOKEN=\n", env)
        self.assertIn("HIGH_LEVEL_MCP_AUTH_TOKEN=\n", env)
        self.assertIn("Empty disables auth", env)

    def test_init_script_is_parseable_and_runs_compose_attached(self):
        script = (ROOT / "scripts" / "init_memory_stack.py").read_text()
        ast.parse(script)
        self.assertIn('["docker", "compose", "up", "--build", "--abort-on-container-exit"]', script)
        self.assertNotIn("OPENCODE_MODEL", script)
        self.assertNotIn("OPENCODE_REASONING_EFFORT", script)
        self.assertIn('"localhost,127.0.0.1,memory-server:8000"', script)
        self.assertNotIn("secrets", script)
        self.assertNotIn("token_urlsafe", script)
        self.assertIn('"LOW_LEVEL_MCP_AUTH_TOKEN": ""', script)
        self.assertIn('"HIGH_LEVEL_MCP_AUTH_TOKEN": ""', script)
        self.assertIn("empty disables auth", script)

    def test_legacy_local_memory_agent_asset_is_marked_deprecated(self):
        asset = (ROOT / "klona_agent" / "opencode" / "assets" / "agents" / "klona-memory.md").read_text()
        self.assertIn("DEPRECATED", asset)
        self.assertIn("not installed", asset.lower())

    def test_memory_agent_dockerfile_documents_runtime_command(self):
        dockerfile = (ROOT / "memory_agent" / "Dockerfile").read_text()
        self.assertIn('CMD ["python", "-m", "memory_agent.runtime"]', dockerfile)
        self.assertIn("OpenCode CLI", dockerfile)

    def test_opencode_snippet_uses_high_level_tools_not_local_subagent(self):
        snippet = (ROOT / "klona_agent" / "opencode" / "assets" / "AGENT.md.snippet").read_text()
        self.assertIn("recall(input: str)", snippet)
        self.assertIn("remember(input: str)", snippet)
        self.assertIn("Do **not** delegate memory work to a local `klona-memory` subagent", snippet)


if __name__ == "__main__":
    unittest.main()
