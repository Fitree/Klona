import json
import os
import shutil
import subprocess
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread

from klona_agent.claude import install as installer


ASSETS = Path(__file__).resolve().parents[1] / "klona_agent" / "claude" / "assets" / "plugin"


class MentalModelHandler(BaseHTTPRequestHandler):
    seen_authorization = None

    def do_GET(self):
        MentalModelHandler.seen_authorization = self.headers.get("Authorization")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"status": "ok", "content": "remembered context"}).encode())

    def log_message(self, *_args):
        return


class ClaudePluginAssetsTests(unittest.TestCase):
    def test_assets_contain_claude_hooks_and_no_legacy_local_subagent(self):
        self.assertFalse((ASSETS / "plugin.json").exists())
        self.assertFalse((ASSETS / "marketplace.json").exists())
        plugin = json.loads((ASSETS / ".claude-plugin" / "plugin.json").read_text())
        marketplace = json.loads((ASSETS / ".claude-plugin" / "marketplace.json").read_text())
        self.assertEqual(
            plugin,
            {
                "name": "klona-memory-plugin",
                "description": "Klona high-level memory MCP tools and session context for Claude Code.",
                "version": "0.1.0",
                "author": {"name": "Klona"},
                "license": "MIT",
            },
        )
        self.assertEqual(
            marketplace,
            {
                "name": "klona-plugins",
                "owner": {"name": "Klona"},
                "plugins": [
                    {
                        "name": "klona-memory-plugin",
                        "source": "./",
                        "description": "Connect Claude Code to the Klona high-level memory MCP server.",
                        "version": "0.1.0",
                    }
                ],
            },
        )
        hooks = json.loads((ASSETS / "hooks" / "hooks.json").read_text())
        self.assertIn("SessionStart", hooks["hooks"])
        self.assertEqual(hooks["hooks"]["SessionStart"][0]["matcher"], "startup|clear|compact")
        self.assertIn("UserPromptSubmit", hooks["hooks"])
        source_text = "\n".join(path.read_text() for path in ASSETS.rglob("*") if path.is_file())
        self.assertIn("hookSpecificOutput", source_text)
        self.assertIn("additionalContext", source_text)
        self.assertIn("klona_memory", source_text)
        self.assertIn("Mandatory per-turn memory workflow", (ASSETS / "instructions" / "klona-memory.md").read_text())
        self.assertIn("[Recall decision]──yes──▶ Recall workflow", (ASSETS / "instructions" / "klona-memory.md").read_text())

    def test_claude_instruction_asset_mirrors_opencode_memory_snippet(self):
        instruction = (ASSETS / "instructions" / "klona-memory.md").read_text()
        canonical_snippets = [
            "# KLONA — Knowledge-Linked Omni Neural Assistant",
            "Use the configured `klona_memory` high-level MCP tools for **all** memory operations.",
            "### Mandatory per-turn memory workflow",
            "[Recall decision]──yes──▶ Recall workflow ──▶ Process input",
            "Call `klona_memory` MCP `recall` with one string argument named `input`",
            "Call `klona_memory` MCP `remember` with a single string argument named `input`",
            "Memory storage is silent — no confirmation, no summary, no mention of what was stored.",
        ]

        for snippet in canonical_snippets:
            self.assertIn(snippet, instruction)

    def test_session_start_hook_transforms_mcp_urls_to_internal_endpoint(self):
        if shutil.which("node") is None:
            self.skipTest("node is not available")
        source = (ASSETS / "hooks" / "session-start.js").read_text()
        source = source.replace(
            "main().catch(() => emit(readInstructions()))",
            "globalThis.__mentalModelEndpointUrl = mentalModelEndpointUrl",
        )
        script = "\n".join(
            [
                source,
                "const cases = [",
                "  'http://127.0.0.1:32310/mcp',",
                "  'http://127.0.0.1:32310/mcp/',",
                "  'http://127.0.0.1:32310/api/mcp?x=1#hash',",
                "  'http://127.0.0.1:32310/custom',",
                "]",
                "process.stdout.write(JSON.stringify(cases.map(globalThis.__mentalModelEndpointUrl)))",
            ]
        )
        result = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)

        self.assertEqual(
            json.loads(result.stdout),
            [
                "http://127.0.0.1:32310/internal/mental-model",
                "http://127.0.0.1:32310/internal/mental-model",
                "http://127.0.0.1:32310/api/internal/mental-model",
                "http://127.0.0.1:32310/internal/mental-model",
            ],
        )

    def test_user_prompt_submit_hook_emits_recall_store_nudge(self):
        if shutil.which("node") is None:
            self.skipTest("node is not available")
        result = subprocess.run(
            ["node", str(ASSETS / "hooks" / "user-prompt-submit.js")],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(result.stdout)
        context = payload["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(payload["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
        self.assertIn("klona_memory.recall", context)
        self.assertIn("klona_memory.remember", context)

    def test_session_start_hook_emits_workflow_and_fetches_wrapped_mental_model(self):
        if shutil.which("node") is None:
            self.skipTest("node is not available")
        server = HTTPServer(("127.0.0.1", 0), MentalModelHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                hooks_dir = root / "hooks"
                hooks_dir.mkdir()
                instructions_dir = root / "instructions"
                instructions_dir.mkdir()
                script = hooks_dir / "session-start.js"
                script.write_text((ASSETS / "hooks" / "session-start.js").read_text())
                (instructions_dir / "klona-memory.md").write_text((ASSETS / "instructions" / "klona-memory.md").read_text())
                mcp = installer._mcp_config(f"http://127.0.0.1:{server.server_port}/mcp", "secret")
                (root / ".mcp.json").write_text(json.dumps(mcp))
                result = subprocess.run(
                    ["node", str(script)],
                    check=True,
                    capture_output=True,
                    text=True,
                    env={**os.environ, "CLAUDE_PLUGIN_ROOT": str(root)},
                )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        payload = json.loads(result.stdout)
        context = payload["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(payload["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertIn("# KLONA — Knowledge-Linked Omni Neural Assistant", context)
        self.assertIn("### Mandatory per-turn memory workflow", context)
        self.assertIn("Recall decision", context)
        self.assertIn("Recall workflow", context)
        self.assertIn("Store decision", context)
        self.assertIn("Store workflow", context)
        self.assertIn("Call `klona_memory` MCP `recall`", context)
        self.assertIn("Call `klona_memory` MCP `remember`", context)
        self.assertIn("<Klona_memory_mental_model>\nremembered context\n</Klona_memory_mental_model>", context)
        self.assertEqual(MentalModelHandler.seen_authorization, "Bearer secret")


if __name__ == "__main__":
    unittest.main()
