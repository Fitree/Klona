import contextlib
import importlib.util
import io
import json
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from klona_agent.codex import install as installer


NEW_BEGIN_MARKER = "<Klona_Memory>"
NEW_END_MARKER = "</Klona_Memory>"


class CodexInstallerTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.codex = Path(self.tempdir.name) / "codex-home"

    def install_with_args(self, mcp_url="https://memory.example/mcp", mcp_token="secret-token"):
        with mock.patch.dict("os.environ", {"CODEX_HOME": str(self.codex)}, clear=False), mock.patch(
            "builtins.input", return_value="https://prompted.example/mcp"
        ) as input_mock, mock.patch(
            "getpass.getpass", return_value="prompt-token"
        ) as getpass_mock, contextlib.redirect_stdout(io.StringIO()):
            installer.install(mcp_url=mcp_url, mcp_token=mcp_token)
        return input_mock, getpass_mock

    def uninstall(self):
        with mock.patch.dict("os.environ", {"CODEX_HOME": str(self.codex)}, clear=False), contextlib.redirect_stdout(io.StringIO()):
            installer.uninstall()

    def read_config(self):
        return tomllib.loads((self.codex / "config.toml").read_text())

    def read_hooks(self):
        return json.loads((self.codex / "hooks.json").read_text())

    def test_install_uses_codex_home_and_creates_agents_config_hooks_and_hook_asset(self):
        self.install_with_args(mcp_url="https://memory.example/mcp", mcp_token="abc123")

        agent = (self.codex / "AGENTS.md").read_text()
        self.assertIn(NEW_BEGIN_MARKER, agent)
        self.assertIn("KLONA memory for Codex", agent)
        self.assertIn(NEW_END_MARKER, agent)
        self.assertEqual(
            (self.codex / "hooks" / installer.HOOK_FILENAME).read_text(),
            installer.HOOK_SOURCE.read_text(),
        )
        config = self.read_config()
        self.assertTrue(config["features"]["codex_hooks"])
        self.assertEqual(config["mcp_servers"][installer.MCP_NAME]["url"], "https://memory.example/mcp")
        self.assertTrue(config["mcp_servers"][installer.MCP_NAME]["enabled"])
        self.assertEqual(
            config["mcp_servers"][installer.MCP_NAME]["http_headers"],
            {"Authorization": "Bearer abc123"},
        )
        hook = self.read_hooks()["hooks"]["UserPromptSubmit"][0]["hooks"][0]
        self.assertEqual(hook["type"], "command")
        self.assertIn(installer.HOOK_FILENAME, hook["command"])

    def test_install_strips_args_allows_empty_token_and_does_not_prompt(self):
        input_mock, getpass_mock = self.install_with_args(
            mcp_url="  https://memory.example/mcp\t", mcp_token="  \n"
        )

        input_mock.assert_not_called()
        getpass_mock.assert_not_called()
        config = self.read_config()
        server = config["mcp_servers"][installer.MCP_NAME]
        self.assertEqual(server["url"], "https://memory.example/mcp")
        self.assertNotIn("http_headers", server)

    def test_empty_url_arg_raises_and_does_not_mutate(self):
        with self.assertRaisesRegex(SystemExit, "Klona high-level memory MCP URL cannot be empty"):
            self.install_with_args(mcp_url="  ", mcp_token="token")

        self.assertFalse((self.codex / "config.toml").exists())
        self.assertFalse((self.codex / "AGENTS.md").exists())

    def test_reinstall_is_idempotent_and_preserves_unrelated_content(self):
        self.codex.mkdir(parents=True)
        (self.codex / "AGENTS.md").write_text("# User notes\n\nKeep this.\n")
        (self.codex / "config.toml").write_text(
            '[profiles.default]\nmodel = "gpt-5"\n\n[features]\ntrace = true\n\n'
            '[mcp_servers.klona_memory]\nurl = "https://manual.example/mcp"\n'
        )
        (self.codex / "hooks.json").write_text(
            json.dumps({"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "true"}]}]}})
        )

        self.install_with_args(mcp_url="https://first.example/mcp", mcp_token="first")
        self.install_with_args(mcp_url="https://second.example/mcp", mcp_token="second")

        agent = (self.codex / "AGENTS.md").read_text()
        self.assertIn("# User notes", agent)
        self.assertIn("Keep this.", agent)
        self.assertEqual(agent.count(NEW_BEGIN_MARKER), 1)
        config_text = (self.codex / "config.toml").read_text()
        self.assertIn('[profiles.default]\nmodel = "gpt-5"', config_text)
        self.assertEqual(config_text.count(installer.MCP_BEGIN), 1)
        config = self.read_config()
        self.assertEqual(config["mcp_servers"][installer.MCP_NAME]["url"], "https://second.example/mcp")
        hooks = self.read_hooks()["hooks"]["UserPromptSubmit"]
        self.assertEqual(sum(installer.HOOK_FILENAME in str(entry) for entry in hooks), 1)
        self.assertIn("true", str(hooks))

    def test_uninstall_removes_only_klona_owned_pieces_and_restores_feature_value(self):
        self.codex.mkdir(parents=True)
        (self.codex / "AGENTS.md").write_text("# Existing Codex instructions\n")
        (self.codex / "config.toml").write_text('[features]\ncodex_hooks = false\nother = true\n')
        self.install_with_args(mcp_url="https://memory.example/mcp", mcp_token="token")
        extra_hook = self.codex / "hooks" / "custom.py"
        extra_hook.write_text("custom")
        hooks = self.read_hooks()
        hooks["hooks"].setdefault("SessionStart", []).append({"hooks": [{"type": "command", "command": "true"}]})
        (self.codex / "hooks.json").write_text(json.dumps(hooks))

        self.uninstall()

        agent = (self.codex / "AGENTS.md").read_text()
        self.assertIn("# Existing Codex instructions", agent)
        self.assertNotIn(NEW_BEGIN_MARKER, agent)
        self.assertFalse((self.codex / "hooks" / installer.HOOK_FILENAME).exists())
        self.assertEqual(extra_hook.read_text(), "custom")
        config = self.read_config()
        self.assertFalse(config["features"]["codex_hooks"])
        self.assertTrue(config["features"]["other"])
        self.assertNotIn("mcp_servers", config)
        self.assertIn("SessionStart", self.read_hooks()["hooks"])

    def test_toml_table_boundaries_allow_trailing_comments_and_array_tables(self):
        self.codex.mkdir(parents=True)
        (self.codex / "config.toml").write_text(
            '[features] # user feature flags\n'
            'codex_hooks = false\n'
            '\n'
            '[[profiles]] # array table must bound features\n'
            'name = "default"\n'
            '\n'
            '[mcp_servers.klona_memory] # previous Klona config\n'
            'url = "https://old.example/mcp"\n'
            '\n'
            '[projects."/tmp/example"] # unrelated section must survive\n'
            'trust_level = "trusted"\n'
        )

        self.install_with_args(mcp_url="https://new.example/mcp", mcp_token="token")

        config_text = (self.codex / "config.toml").read_text()
        self.assertIn('[[profiles]] # array table must bound features\nname = "default"', config_text)
        self.assertIn('[projects."/tmp/example"] # unrelated section must survive', config_text)
        self.assertNotIn("https://old.example/mcp", config_text)
        config = self.read_config()
        self.assertEqual(config["profiles"][0]["name"], "default")
        self.assertEqual(config["projects"]["/tmp/example"]["trust_level"], "trusted")
        self.assertEqual(config["mcp_servers"][installer.MCP_NAME]["url"], "https://new.example/mcp")

        self.uninstall()

        config_text = (self.codex / "config.toml").read_text()
        self.assertIn('[[profiles]] # array table must bound features\nname = "default"', config_text)
        self.assertIn('[projects."/tmp/example"] # unrelated section must survive', config_text)
        config = self.read_config()
        self.assertFalse(config["features"]["codex_hooks"])
        self.assertEqual(config["profiles"][0]["name"], "default")
        self.assertEqual(config["projects"]["/tmp/example"]["trust_level"], "trusted")
        self.assertNotIn("mcp_servers", config)

    def test_mcp_table_removal_preserves_following_quoted_table_with_bracket_in_key(self):
        self.codex.mkdir(parents=True)
        (self.codex / "config.toml").write_text(
            '[features]\n'
            'codex_hooks = false\n'
            '\n'
            '[mcp_servers.klona_memory] # previous Klona config\n'
            'url = "https://old.example/mcp"\n'
            '\n'
            '[projects."/tmp/a]b"] # valid TOML key contains ]\n'
            'trust_level = "trusted"\n'
            '\n'
            '[[profiles]]\n'
            'name = "default"\n'
        )

        self.install_with_args(mcp_url="https://new.example/mcp", mcp_token="token")

        config_text = (self.codex / "config.toml").read_text()
        self.assertIn('[projects."/tmp/a]b"] # valid TOML key contains ]', config_text)
        self.assertIn('trust_level = "trusted"', config_text)
        self.assertIn('[[profiles]]\nname = "default"', config_text)
        self.assertNotIn("https://old.example/mcp", config_text)
        config = self.read_config()
        self.assertEqual(config["projects"]["/tmp/a]b"]["trust_level"], "trusted")
        self.assertEqual(config["profiles"][0]["name"], "default")

    def test_install_replaces_equivalent_quoted_klona_mcp_tables(self):
        table_headers = [
            '[mcp_servers."klona_memory"]',
            '["mcp_servers"."klona_memory"]',
        ]
        for table_header in table_headers:
            with self.subTest(table_header=table_header):
                with tempfile.TemporaryDirectory() as tempdir:
                    self.codex = Path(tempdir) / "codex-home"
                    self.codex.mkdir(parents=True)
                    (self.codex / "config.toml").write_text(
                        '[features]\n'
                        'codex_hooks = false\n'
                        '\n'
                        f'{table_header} # previous equivalent Klona config\n'
                        'url = "https://old.example/mcp"\n'
                        '\n'
                        '[projects."/tmp/a]b"]\n'
                        'trust_level = "trusted"\n'
                    )

                    self.install_with_args(mcp_url="https://new.example/mcp", mcp_token="token")

                    config_text = (self.codex / "config.toml").read_text()
                    self.assertNotIn("https://old.example/mcp", config_text)
                    self.assertEqual(config_text.count(installer.MCP_BEGIN), 1)
                    self.assertIn('[projects."/tmp/a]b"]\ntrust_level = "trusted"', config_text)
                    config = self.read_config()
                    self.assertEqual(config["mcp_servers"][installer.MCP_NAME]["url"], "https://new.example/mcp")
                    self.assertEqual(config["projects"]["/tmp/a]b"]["trust_level"], "trusted")

    def test_codex_hooks_feature_matching_is_exact(self):
        self.codex.mkdir(parents=True)
        (self.codex / "config.toml").write_text(
            '[features]\n'
            'codex_hooks_extra = false\n'
            'codex_hooks_enabled = false\n'
        )

        self.install_with_args(mcp_url="https://memory.example/mcp", mcp_token="token")

        config = self.read_config()
        self.assertTrue(config["features"]["codex_hooks"])
        self.assertFalse(config["features"]["codex_hooks_extra"])
        self.assertFalse(config["features"]["codex_hooks_enabled"])

        self.uninstall()

        config = self.read_config()
        self.assertNotIn("codex_hooks", config["features"])
        self.assertFalse(config["features"]["codex_hooks_extra"])
        self.assertFalse(config["features"]["codex_hooks_enabled"])

    def test_uninstall_preserves_preexisting_empty_comment_only_features_table(self):
        self.codex.mkdir(parents=True)
        (self.codex / "config.toml").write_text(
            '[features] # existing empty feature table\n'
            '# user keeps feature notes here\n'
            '\n'
            '[profiles.default]\n'
            'model = "gpt-5"\n'
        )

        self.install_with_args(mcp_url="https://memory.example/mcp", mcp_token="token")
        self.uninstall()

        config_text = (self.codex / "config.toml").read_text()
        self.assertIn('[features] # existing empty feature table', config_text)
        self.assertIn('# user keeps feature notes here', config_text)
        self.assertIn('[profiles.default]', config_text)
        config = self.read_config()
        self.assertEqual(config["profiles"]["default"]["model"], "gpt-5")
        self.assertNotIn("codex_hooks", config.get("features", {}))

    def test_uninstall_removes_klona_markers_but_preserves_user_content_in_klona_created_features_table(self):
        self.install_with_args(mcp_url="https://memory.example/mcp", mcp_token="token")
        config_text = (self.codex / "config.toml").read_text()
        config_text = config_text.replace(
            '[features]\n',
            '[features]\n# user comment added later\nuser_feature = true\n',
            1,
        )
        (self.codex / "config.toml").write_text(config_text)

        self.uninstall()

        config_text = (self.codex / "config.toml").read_text()
        self.assertNotIn(installer.FEATURES_TABLE_ADDED_MARKER, config_text)
        self.assertNotIn(installer.FEATURE_ADDED_MARKER, config_text)
        self.assertNotIn("codex_hooks", config_text)
        self.assertIn("[features]", config_text)
        self.assertIn("# user comment added later", config_text)
        self.assertIn("user_feature = true", config_text)
        self.assertTrue(self.read_config()["features"]["user_feature"])

    def test_uninstall_preserves_user_comment_in_klona_created_features_table(self):
        self.install_with_args(mcp_url="https://memory.example/mcp", mcp_token="token")
        config_text = (self.codex / "config.toml").read_text()
        config_text = config_text.replace(
            '[features]\n',
            '[features]\n# user comment added later\n',
            1,
        )
        (self.codex / "config.toml").write_text(config_text)

        self.uninstall()

        config_text = (self.codex / "config.toml").read_text()
        self.assertNotIn(installer.FEATURES_TABLE_ADDED_MARKER, config_text)
        self.assertNotIn(installer.FEATURE_ADDED_MARKER, config_text)
        self.assertNotIn("codex_hooks", config_text)
        self.assertIn("[features]", config_text)
        self.assertIn("# user comment added later", config_text)

    def test_clean_install_then_uninstall_removes_noop_codex_files_and_empty_hooks_dir(self):
        self.install_with_args(mcp_url="https://memory.example/mcp", mcp_token="")

        self.uninstall()

        self.assertFalse((self.codex / "AGENTS.md").exists())
        self.assertFalse((self.codex / "config.toml").exists())
        self.assertFalse((self.codex / "hooks.json").exists())
        self.assertFalse((self.codex / "hooks" / installer.HOOK_FILENAME).exists())
        self.assertFalse((self.codex / "hooks").exists())

    def test_late_install_failure_removes_new_empty_codex_home_and_hooks_dir(self):
        with mock.patch.dict("os.environ", {"CODEX_HOME": str(self.codex)}, clear=False), mock.patch.object(
            installer, "_install_mcp_config", side_effect=RuntimeError("write failed")
        ):
            with self.assertRaises(RuntimeError):
                installer.install(mcp_url="https://memory.example/mcp", mcp_token="token")

        self.assertFalse(self.codex.exists())

    def test_hook_cleanup_preserves_unrelated_same_basename_command(self):
        self.install_with_args(mcp_url="https://memory.example/mcp", mcp_token="token")
        unrelated_command = "python3 /tmp/elsewhere/klona_mental_model_user_prompt_submit.py"
        hooks = self.read_hooks()
        hooks["hooks"]["UserPromptSubmit"].append(
            {"hooks": [{"type": "command", "command": unrelated_command}]}
        )
        (self.codex / "hooks.json").write_text(json.dumps(hooks))

        self.uninstall()

        hooks = self.read_hooks()["hooks"]["UserPromptSubmit"]
        self.assertEqual(hooks, [{"hooks": [{"type": "command", "command": unrelated_command}]}])

    def test_rolls_back_on_late_config_failure(self):
        self.codex.mkdir(parents=True)
        (self.codex / "AGENTS.md").write_text("existing\n")
        with mock.patch.dict("os.environ", {"CODEX_HOME": str(self.codex)}, clear=False), mock.patch.object(
            installer, "_install_mcp_config", side_effect=RuntimeError("write failed")
        ):
            with self.assertRaises(RuntimeError):
                installer.install(mcp_url="https://memory.example/mcp", mcp_token="token")

        self.assertEqual((self.codex / "AGENTS.md").read_text(), "existing\n")
        self.assertFalse((self.codex / "hooks" / installer.HOOK_FILENAME).exists())
        self.assertFalse((self.codex / "hooks.json").exists())

    def test_invalid_toml_rolls_back_and_preserves_existing_files(self):
        self.codex.mkdir(parents=True)
        (self.codex / "config.toml").write_text("[features\n")
        (self.codex / "AGENTS.md").write_text("existing\n")

        with mock.patch.dict("os.environ", {"CODEX_HOME": str(self.codex)}, clear=False):
            with self.assertRaises(SystemExit):
                installer.install(mcp_url="https://memory.example/mcp", mcp_token="token")

        self.assertEqual((self.codex / "config.toml").read_text(), "[features\n")
        self.assertEqual((self.codex / "AGENTS.md").read_text(), "existing\n")
        self.assertFalse((self.codex / "hooks" / installer.HOOK_FILENAME).exists())
        self.assertFalse((self.codex / "hooks.json").exists())


class CodexHookAssetTests(unittest.TestCase):
    def load_hook_module(self):
        spec = importlib.util.spec_from_file_location("codex_hook_asset", installer.HOOK_SOURCE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_hook_asset_uses_user_prompt_submit_additional_context_shape(self):
        module = self.load_hook_module()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            module.output("wrapped context")

        payload = json.loads(output.getvalue())
        self.assertTrue(payload["continue"])
        self.assertTrue(payload["suppressOutput"])
        self.assertEqual(
            payload["hookSpecificOutput"],
            {"hookEventName": "UserPromptSubmit", "additionalContext": "wrapped context"},
        )

    def test_hook_asset_maps_mcp_url_to_internal_mental_model_url(self):
        module = self.load_hook_module()

        self.assertEqual(
            module.mental_model_url("http://localhost:32310/mcp"),
            "http://localhost:32310/internal/mental-model",
        )
        self.assertEqual(
            module.mental_model_url("http://localhost:32310/base/mcp"),
            "http://localhost:32310/base/internal/mental-model",
        )
        self.assertEqual(
            module.mental_model_url("http://localhost:32310/custom"),
            "http://localhost:32310/internal/mental-model",
        )

    def test_hook_asset_fails_closed_for_malformed_present_http_headers(self):
        module = self.load_hook_module()
        malformed_configs = [
            'http_headers = ["bad"]\n',
            'http_headers = { Authorization = "Bearer token", Numeric = 123 }\n',
            'http_headers = { Authorization = "token-without-bearer-prefix" }\n',
        ]
        for headers_config in malformed_configs:
            with self.subTest(headers_config=headers_config), tempfile.TemporaryDirectory() as tempdir:
                codex_home = Path(tempdir)
                (codex_home / "config.toml").write_text(
                    '[mcp_servers.klona_memory]\n'
                    'url = "https://memory.example/mcp"\n'
                    + headers_config
                )
                with mock.patch.dict("os.environ", {"CODEX_HOME": str(codex_home)}, clear=False):
                    config = module.load_mcp_config()

            self.assertIsNone(config)

    def test_hook_asset_does_not_fetch_or_context_when_present_headers_are_malformed(self):
        module = self.load_hook_module()
        with tempfile.TemporaryDirectory() as tempdir:
            codex_home = Path(tempdir)
            (codex_home / "config.toml").write_text(
                '[mcp_servers.klona_memory]\n'
                'url = "https://memory.example/mcp"\n'
                'http_headers = { Authorization = "not-bearer" }\n'
            )
            output = io.StringIO()
            with mock.patch.dict("os.environ", {"CODEX_HOME": str(codex_home)}, clear=False), mock.patch.object(
                module, "fetch_mental_model", side_effect=AssertionError("fetch should not be called")
            ), contextlib.redirect_stdout(output):
                self.assertEqual(module.main(), 0)

        payload = json.loads(output.getvalue())
        self.assertTrue(payload["continue"])
        self.assertNotIn("hookSpecificOutput", payload)

    def test_hook_asset_fails_closed_when_mcp_servers_is_not_a_table(self):
        module = self.load_hook_module()
        with tempfile.TemporaryDirectory() as tempdir:
            codex_home = Path(tempdir)
            (codex_home / "config.toml").write_text('mcp_servers = "malformed"\n')
            output = io.StringIO()
            with mock.patch.dict("os.environ", {"CODEX_HOME": str(codex_home)}, clear=False), mock.patch.object(
                module, "fetch_mental_model", side_effect=AssertionError("fetch should not be called")
            ), contextlib.redirect_stdout(output):
                self.assertEqual(module.main(), 0)

        payload = json.loads(output.getvalue())
        self.assertTrue(payload["continue"])
        self.assertNotIn("hookSpecificOutput", payload)

    def test_hook_asset_allows_absent_http_headers_for_unauthenticated_servers(self):
        module = self.load_hook_module()
        with tempfile.TemporaryDirectory() as tempdir:
            codex_home = Path(tempdir)
            (codex_home / "config.toml").write_text(
                '[mcp_servers.klona_memory]\n'
                'url = "https://memory.example/mcp"\n'
            )
            with mock.patch.dict("os.environ", {"CODEX_HOME": str(codex_home)}, clear=False):
                config = module.load_mcp_config()

        self.assertEqual(config, ("https://memory.example/mcp", {}))


if __name__ == "__main__":
    unittest.main()
