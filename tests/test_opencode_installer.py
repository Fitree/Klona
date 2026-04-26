import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from klona_agent.opencode import install as installer

NEW_BEGIN_MARKER = "<Klona_Memory>"
NEW_END_MARKER = "</Klona_Memory>"
LEGACY_BEGIN_MARKER = "<!-- KLONA:BEGIN -->"
LEGACY_END_MARKER = "<!-- KLONA:END -->"


class OpenCodeInstallerTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.home = Path(self.tempdir.name)
        self.opencode = self.home / ".config" / "opencode"

    def install_with_prompts(self, url="https://memory.example/mcp", token="secret-token"):
        with mock.patch.object(installer.Path, "home", return_value=self.home), mock.patch(
            "builtins.input", return_value=url
        ) as input_mock, mock.patch(
            "getpass.getpass", return_value=token
        ) as getpass_mock, contextlib.redirect_stdout(io.StringIO()):
            installer.install()
        return input_mock, getpass_mock

    def install_with_args(self, mcp_url=None, mcp_token=None, input_value=None, token_value=None):
        with mock.patch.object(installer.Path, "home", return_value=self.home), mock.patch(
            "builtins.input", return_value=input_value
        ) as input_mock, mock.patch(
            "getpass.getpass", return_value=token_value
        ) as getpass_mock, contextlib.redirect_stdout(io.StringIO()):
            installer.install(mcp_url=mcp_url, mcp_token=mcp_token)
        return input_mock, getpass_mock

    def uninstall(self):
        with mock.patch.object(
            installer.Path, "home", return_value=self.home
        ), contextlib.redirect_stdout(io.StringIO()):
            installer.uninstall()

    def read_agent(self):
        return (self.opencode / "AGENTS.md").read_text()

    def read_config(self):
        return json.loads((self.opencode / "opencode.json").read_text())

    def test_install_reinstall_replaces_marker_block_and_preserves_unrelated_agent_content(self):
        agent = self.opencode / "AGENTS.md"
        agent.parent.mkdir(parents=True)
        agent.write_text("# Existing instructions\n\nKeep this.\n")

        self.install_with_prompts(url="https://first.example/mcp", token="first")
        first_agent = self.read_agent()
        self.install_with_prompts(url="https://second.example/mcp", token="second")
        second_agent = self.read_agent()

        self.assertIn("# Existing instructions", second_agent)
        self.assertIn("Keep this.", second_agent)
        self.assertEqual(second_agent.count(NEW_BEGIN_MARKER), 1)
        self.assertEqual(second_agent.count(NEW_END_MARKER), 1)
        self.assertEqual(first_agent, second_agent)

    def test_duplicate_existing_klona_blocks_collapse_to_one(self):
        agent = self.opencode / "AGENTS.md"
        agent.parent.mkdir(parents=True)
        agent.write_text(
            "before\n\n"
            f"{NEW_BEGIN_MARKER}\nold one\n{NEW_END_MARKER}\n\n"
            "middle\n\n"
            f"{NEW_BEGIN_MARKER}\nold two\n{NEW_END_MARKER}\n\n"
            "after\n"
        )

        self.install_with_prompts()
        content = self.read_agent()

        self.assertIn("before", content)
        self.assertIn("middle", content)
        self.assertIn("after", content)
        self.assertNotIn("old one", content)
        self.assertNotIn("old two", content)
        self.assertEqual(content.count(NEW_BEGIN_MARKER), 1)
        self.assertEqual(content.count(NEW_END_MARKER), 1)

    def test_reinstall_removes_legacy_marker_block_and_writes_one_new_block(self):
        agent = self.opencode / "AGENTS.md"
        agent.parent.mkdir(parents=True)
        agent.write_text(
            "before\n\n"
            f"{LEGACY_BEGIN_MARKER}\nlegacy managed\n{LEGACY_END_MARKER}\n\n"
            "after\n"
        )

        self.install_with_prompts()
        content = self.read_agent()

        self.assertIn("before", content)
        self.assertIn("after", content)
        self.assertNotIn("legacy managed", content)
        self.assertNotIn(LEGACY_BEGIN_MARKER, content)
        self.assertNotIn(LEGACY_END_MARKER, content)
        self.assertEqual(content.count(NEW_BEGIN_MARKER), 1)
        self.assertEqual(content.count(NEW_END_MARKER), 1)

    def test_uninstall_removes_legacy_marker_block_and_preserves_unrelated_agent_content(self):
        agent = self.opencode / "AGENTS.md"
        agent.parent.mkdir(parents=True)
        agent.write_text(
            "intro\n\n"
            f"{LEGACY_BEGIN_MARKER}\nlegacy managed\n{LEGACY_END_MARKER}\n\n"
            "outro\n"
        )

        self.uninstall()
        content = self.read_agent()

        self.assertIn("intro", content)
        self.assertIn("outro", content)
        self.assertNotIn("legacy managed", content)
        self.assertNotIn(LEGACY_BEGIN_MARKER, content)
        self.assertNotIn(LEGACY_END_MARKER, content)

    def test_install_creates_mcp_entry_and_preserves_unrelated_config(self):
        config = self.opencode / "opencode.json"
        config.parent.mkdir(parents=True)
        config.write_text(
            json.dumps(
                {
                    "$schema": "https://opencode.ai/config.json",
                    "theme": "dark",
                    "mcp": {"other_server": {"type": "local", "command": ["true"]}},
                }
            )
        )

        self.install_with_prompts(url="https://memory.example/mcp", token="abc123")
        data = self.read_config()

        self.assertEqual(data["theme"], "dark")
        self.assertEqual(data["mcp"]["other_server"], {"type": "local", "command": ["true"]})
        self.assertEqual(
            data["mcp"][installer.MCP_NAME],
            {
                "type": "remote",
                "url": "https://memory.example/mcp",
                "enabled": True,
                "oauth": False,
                "headers": {"Authorization": "Bearer abc123"},
            },
        )

    def test_install_copies_core_agent_and_plugin_files(self):
        self.install_with_prompts()

        agent_copy = self.opencode / "agents" / "klona-memory.md"
        plugin_copy = self.opencode / "plugins" / "klona-mental-model-injector.js"
        self.assertEqual(agent_copy.read_text(), installer.AGENT_SOURCE.read_text())
        self.assertEqual(plugin_copy.read_text(), installer.PLUGIN_SOURCE.read_text())
        self.assertFalse((self.opencode / "plugins" / "klona-memory-session.js").exists())

    def test_uninstall_removes_only_marker_owned_files_and_owned_mcp_entry(self):
        self.install_with_prompts(url="https://memory.example/mcp", token="abc123")
        agent = self.opencode / "AGENTS.md"
        agent.write_text("intro\n\n" + agent.read_text() + "\noutro\n")
        extra_agent = self.opencode / "agents" / "custom.md"
        extra_plugin = self.opencode / "plugins" / "custom.js"
        legacy_plugin = self.opencode / "plugins" / "klona-memory-session.js"
        extra_agent.write_text("custom agent")
        extra_plugin.write_text("custom plugin")
        legacy_plugin.write_text("legacy plugin")
        config = self.read_config()
        config["theme"] = "dark"
        config["mcp"]["other_server"] = {"type": "local", "command": ["true"]}
        (self.opencode / "opencode.json").write_text(json.dumps(config))

        self.uninstall()

        agent_content = self.read_agent()
        self.assertIn("intro", agent_content)
        self.assertIn("outro", agent_content)
        self.assertNotIn(NEW_BEGIN_MARKER, agent_content)
        self.assertFalse((self.opencode / "agents" / "klona-memory.md").exists())
        self.assertFalse((self.opencode / "plugins" / "klona-mental-model-injector.js").exists())
        self.assertFalse((self.opencode / "plugins" / "klona-memory-session.js").exists())
        self.assertEqual(extra_agent.read_text(), "custom agent")
        self.assertEqual(extra_plugin.read_text(), "custom plugin")
        data = self.read_config()
        self.assertEqual(data["theme"], "dark")
        self.assertNotIn(installer.MCP_NAME, data["mcp"])
        self.assertEqual(data["mcp"]["other_server"], {"type": "local", "command": ["true"]})

    def test_install_prompts_for_url_and_token(self):
        input_mock, getpass_mock = self.install_with_prompts(
            url="https://prompted.example/mcp", token="prompt-token"
        )

        input_mock.assert_called_once_with("Klona memory MCP URL: ")
        getpass_mock.assert_called_once_with("Klona memory bearer token: ")
        entry = self.read_config()["mcp"][installer.MCP_NAME]
        self.assertEqual(entry["url"], "https://prompted.example/mcp")
        self.assertEqual(entry["headers"]["Authorization"], "Bearer prompt-token")

    def test_install_with_mcp_args_does_not_prompt_and_writes_mcp_entry(self):
        input_mock, getpass_mock = self.install_with_args(
            mcp_url="https://args.example/mcp", mcp_token="arg-token"
        )

        input_mock.assert_not_called()
        getpass_mock.assert_not_called()
        entry = self.read_config()["mcp"][installer.MCP_NAME]
        self.assertEqual(entry["url"], "https://args.example/mcp")
        self.assertEqual(entry["headers"]["Authorization"], "Bearer arg-token")

    def test_install_with_empty_url_arg_raises_system_exit(self):
        with self.assertRaisesRegex(SystemExit, "Klona memory MCP URL cannot be empty"):
            self.install_with_args(mcp_url="", mcp_token="arg-token")

        self.assertFalse((self.opencode / "opencode.json").exists())

    def test_install_with_whitespace_url_arg_raises_system_exit(self):
        with self.assertRaisesRegex(SystemExit, "Klona memory MCP URL cannot be empty"):
            self.install_with_args(mcp_url="   \t", mcp_token="arg-token")

        self.assertFalse((self.opencode / "opencode.json").exists())

    def test_install_with_empty_token_arg_raises_system_exit(self):
        with self.assertRaisesRegex(SystemExit, "Klona memory bearer token cannot be empty"):
            self.install_with_args(mcp_url="https://args.example/mcp", mcp_token="")

        self.assertFalse((self.opencode / "opencode.json").exists())

    def test_install_with_whitespace_token_arg_raises_system_exit(self):
        with self.assertRaisesRegex(SystemExit, "Klona memory bearer token cannot be empty"):
            self.install_with_args(mcp_url="https://args.example/mcp", mcp_token="  \n")

        self.assertFalse((self.opencode / "opencode.json").exists())

    def test_install_with_mcp_args_strips_values(self):
        self.install_with_args(
            mcp_url="  https://args.example/mcp\t", mcp_token="  arg-token\n"
        )

        entry = self.read_config()["mcp"][installer.MCP_NAME]
        self.assertEqual(entry["url"], "https://args.example/mcp")
        self.assertEqual(entry["headers"]["Authorization"], "Bearer arg-token")

    def test_install_with_url_arg_prompts_only_for_token(self):
        input_mock, getpass_mock = self.install_with_args(
            mcp_url="https://args.example/mcp", token_value="prompted-token"
        )

        input_mock.assert_not_called()
        getpass_mock.assert_called_once_with("Klona memory bearer token: ")
        entry = self.read_config()["mcp"][installer.MCP_NAME]
        self.assertEqual(entry["url"], "https://args.example/mcp")
        self.assertEqual(entry["headers"]["Authorization"], "Bearer prompted-token")

    def test_install_with_token_arg_prompts_only_for_url(self):
        input_mock, getpass_mock = self.install_with_args(
            mcp_token="arg-token", input_value="https://prompted.example/mcp"
        )

        input_mock.assert_called_once_with("Klona memory MCP URL: ")
        getpass_mock.assert_not_called()
        entry = self.read_config()["mcp"][installer.MCP_NAME]
        self.assertEqual(entry["url"], "https://prompted.example/mcp")
        self.assertEqual(entry["headers"]["Authorization"], "Bearer arg-token")

    def test_install_invalid_json_raises_system_exit_and_leaves_config(self):
        config = self.opencode / "opencode.json"
        config.parent.mkdir(parents=True)
        config.write_text("{not json")
        agent = self.opencode / "AGENTS.md"
        agent.write_text("existing instructions\n")

        with mock.patch.object(installer.Path, "home", return_value=self.home), mock.patch(
            "builtins.input", return_value="https://memory.example/mcp"
        ), mock.patch("getpass.getpass", return_value="token"):
            with self.assertRaises(SystemExit):
                installer.install()

        self.assertEqual(config.read_text(), "{not json")
        self.assertEqual(agent.read_text(), "existing instructions\n")
        self.assertNotIn(NEW_BEGIN_MARKER, agent.read_text())
        self.assertFalse((self.opencode / "agents" / "klona-memory.md").exists())
        self.assertFalse((self.opencode / "plugins" / "klona-mental-model-injector.js").exists())
        self.assertFalse((self.opencode / "plugins" / "klona-memory-session.js").exists())

    def test_install_rolls_back_marker_and_assets_when_late_config_write_fails(self):
        config = self.opencode / "opencode.json"
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps({"theme": "dark"}))

        with mock.patch.object(installer.Path, "home", return_value=self.home), mock.patch(
            "builtins.input", return_value="https://memory.example/mcp"
        ), mock.patch("getpass.getpass", return_value="token"), mock.patch.object(
            installer, "_write_json", side_effect=RuntimeError("write failed")
        ):
            with self.assertRaises(RuntimeError):
                installer.install()

        self.assertFalse((self.opencode / "AGENTS.md").exists())
        self.assertFalse((self.opencode / "agents" / "klona-memory.md").exists())
        self.assertFalse((self.opencode / "plugins" / "klona-mental-model-injector.js").exists())
        self.assertFalse((self.opencode / "plugins" / "klona-memory-session.js").exists())
        self.assertEqual(json.loads(config.read_text()), {"theme": "dark"})


if __name__ == "__main__":
    unittest.main()
