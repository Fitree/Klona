import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from klona_agent.claude import install as installer


class ClaudeInstallerTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.home = Path(self.tempdir.name)
        self.claude = self.home / ".claude"
        self.plugin = self.claude / "plugins" / "klona-memory-plugin"
        self.cache_plugin = self.claude / "plugins" / "cache" / "klona-plugins" / "klona-memory-plugin" / "0.1.0"
        self.settings = self.claude / "settings.json"

    def install_with_args(self, mcp_url="https://memory.example/mcp", mcp_token="secret-token"):
        with mock.patch.object(installer.Path, "home", return_value=self.home), contextlib.redirect_stdout(io.StringIO()):
            installer.install(mcp_url=mcp_url, mcp_token=mcp_token)

    def uninstall(self):
        with mock.patch.object(installer.Path, "home", return_value=self.home), contextlib.redirect_stdout(io.StringIO()):
            installer.uninstall()

    def read_mcp(self):
        return json.loads((self.plugin / ".mcp.json").read_text())

    def read_cache_mcp(self):
        return json.loads((self.cache_plugin / ".mcp.json").read_text())

    def read_settings(self):
        return json.loads(self.settings.read_text())

    def test_install_writes_plugin_assets_mcp_and_registry(self):
        self.install_with_args(mcp_url="https://memory.example/mcp", mcp_token="abc123")

        self.assertTrue((self.plugin / ".klona-owned").is_file())
        self.assertTrue((self.plugin / ".claude-plugin" / "plugin.json").is_file())
        self.assertTrue((self.plugin / ".claude-plugin" / "marketplace.json").is_file())
        self.assertTrue((self.plugin / "hooks" / "session-start.js").is_file())
        self.assertTrue((self.cache_plugin / ".klona-owned").is_file())
        self.assertTrue((self.cache_plugin / ".claude-plugin" / "plugin.json").is_file())
        entry = self.read_mcp()["mcpServers"][installer.MCP_NAME]
        self.assertEqual(entry, {"type": "http", "url": "https://memory.example/mcp", "headers": {"Authorization": "Bearer abc123"}})
        self.assertEqual(self.read_cache_mcp(), self.read_mcp())
        marketplaces = json.loads((self.claude / "plugins" / "known_marketplaces.json").read_text())
        installed = json.loads((self.claude / "plugins" / "installed_plugins.json").read_text())
        self.assertNotIn("marketplaces", marketplaces)
        self.assertEqual(
            marketplaces["klona-plugins"],
            {
                "source": {"source": "directory", "path": str(self.plugin)},
                "installLocation": str(self.plugin),
                "lastUpdated": marketplaces["klona-plugins"]["lastUpdated"],
            },
        )
        self.assertEqual(installed["version"], 2)
        installed_entry = installed["plugins"]["klona-memory-plugin@klona-plugins"]
        self.assertIsInstance(installed_entry, list)
        self.assertEqual(len(installed_entry), 1)
        installed_entry = installed_entry[0]
        self.assertEqual(installed_entry["installPath"], str(self.cache_plugin))
        self.assertEqual(installed_entry["version"], "0.1.0")
        self.assertEqual(installed_entry["scope"], "user")
        self.assertIn("installedAt", installed_entry)
        self.assertIn("lastUpdated", installed_entry)
        self.assertIn("gitCommitSha", installed_entry)
        self.assertNotIn("ownedBy", marketplaces["klona-plugins"])
        self.assertNotIn("ownedBy", installed_entry)
        self.assertEqual(self.read_settings()["enabledPlugins"], {"klona-memory-plugin@klona-plugins": True})

    def test_install_creates_settings_when_absent(self):
        self.install_with_args()

        self.assertEqual(self.read_settings()["enabledPlugins"], {"klona-memory-plugin@klona-plugins": True})

    def test_install_preserves_unrelated_settings_and_plugins(self):
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text(json.dumps({"theme": "dark", "enabledPlugins": {"other@market": False}}))

        self.install_with_args()

        self.assertEqual(
            self.read_settings(),
            {"theme": "dark", "enabledPlugins": {"other@market": False, "klona-memory-plugin@klona-plugins": True}},
        )

    def test_install_empty_token_omits_authorization_header(self):
        self.install_with_args(mcp_token="  \n")

        entry = self.read_mcp()["mcpServers"][installer.MCP_NAME]
        self.assertEqual(entry["type"], "http")
        self.assertNotIn("headers", entry)

    def test_reinstall_is_idempotent_and_replaces_owned_files(self):
        self.install_with_args(mcp_url="https://first.example/mcp", mcp_token="first")
        first_files = sorted(str(path.relative_to(self.plugin)) for path in self.plugin.rglob("*") if path.is_file())
        self.install_with_args(mcp_url="https://second.example/mcp", mcp_token="second")
        second_files = sorted(str(path.relative_to(self.plugin)) for path in self.plugin.rglob("*") if path.is_file())

        self.assertEqual(first_files, second_files)
        entry = self.read_mcp()["mcpServers"][installer.MCP_NAME]
        self.assertEqual(entry["url"], "https://second.example/mcp")
        self.assertEqual(entry["headers"]["Authorization"], "Bearer second")
        self.assertEqual(self.read_settings()["enabledPlugins"], {"klona-memory-plugin@klona-plugins": True})

    def test_install_removes_historical_plugin_dirs_and_stale_legacy_agents(self):
        for target in [self.plugin, self.cache_plugin]:
            (target / ".claude-plugin").mkdir(parents=True)
            (target / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "klona-memory-plugin"}))
            (target / "agents").mkdir()
            (target / "agents" / "klona-memory.md").write_text("legacy")

        self.install_with_args()

        self.assertFalse((self.plugin / "agents" / "klona-memory.md").exists())
        self.assertFalse((self.cache_plugin / "agents" / "klona-memory.md").exists())
        self.assertTrue((self.plugin / ".klona-owned").is_file())
        self.assertTrue((self.cache_plugin / ".klona-owned").is_file())

    def test_install_refuses_unrecognized_existing_plugin_dirs(self):
        self.plugin.mkdir(parents=True)
        (self.plugin / "plugin.json").write_text("foreign")

        with self.assertRaisesRegex(SystemExit, "refusing to overwrite unrecognized"):
            self.install_with_args()

        self.assertEqual((self.plugin / "plugin.json").read_text(), "foreign")
        self.assertFalse(self.cache_plugin.exists())

    def test_install_rolls_back_owned_source_when_foreign_cache_fails_prepare(self):
        existing = self.plugin / "plugin.json"
        existing.parent.mkdir(parents=True)
        existing.write_text("existing")
        (self.plugin / ".klona-owned").write_text("owned")
        self.cache_plugin.mkdir(parents=True)
        (self.cache_plugin / "plugin.json").write_text("foreign-cache")

        with self.assertRaisesRegex(SystemExit, "refusing to overwrite unrecognized"):
            self.install_with_args()

        self.assertEqual(existing.read_text(), "existing")
        self.assertTrue((self.plugin / ".klona-owned").exists())
        self.assertEqual((self.cache_plugin / "plugin.json").read_text(), "foreign-cache")

    def test_uninstall_removes_only_owned_klona_files(self):
        self.install_with_args()
        other_plugin = self.claude / "plugins" / "other-plugin"
        other_plugin.mkdir(parents=True)
        (other_plugin / "plugin.json").write_text("{}")
        foreign_klona = self.claude / "plugins" / "foreign-klona"
        foreign_klona.mkdir(parents=True)
        (foreign_klona / "plugin.json").write_text("{}")
        known_marketplaces = self.claude / "plugins" / "known_marketplaces.json"
        installed_plugins = self.claude / "plugins" / "installed_plugins.json"
        known_marketplaces.write_text(json.dumps({"custom": {"path": "/tmp/custom"}}))
        installed_plugins.write_text(json.dumps({"version": 9, "plugins": {"custom@market": [{"installPath": "/tmp/custom"}]}}))
        self.install_with_args()

        self.uninstall()

        self.assertFalse(self.plugin.exists())
        self.assertTrue((other_plugin / "plugin.json").exists())
        self.assertTrue((foreign_klona / "plugin.json").exists())
        self.assertFalse(self.cache_plugin.exists())
        self.assertEqual(json.loads(known_marketplaces.read_text()), {"custom": {"path": "/tmp/custom"}})
        installed_after = json.loads(installed_plugins.read_text())
        self.assertEqual(installed_after["version"], 9)
        self.assertEqual(installed_after["plugins"], {"custom@market": [{"installPath": "/tmp/custom"}]})
        self.assertEqual(self.read_settings(), {})

    def test_uninstall_preserves_unowned_same_name_plugin_dir_and_registry(self):
        self.plugin.mkdir(parents=True)
        (self.plugin / "plugin.json").write_text("foreign")
        self.cache_plugin.mkdir(parents=True)
        (self.cache_plugin / "plugin.json").write_text("foreign-cache")
        known_marketplaces = self.claude / "plugins" / "known_marketplaces.json"
        known_marketplaces.parent.mkdir(parents=True, exist_ok=True)
        known_marketplaces.write_text(json.dumps({"klona-plugins": {"source": {"source": "directory", "path": "/tmp/foreign"}, "installLocation": "/tmp/foreign"}}))
        installed_plugins = self.claude / "plugins" / "installed_plugins.json"
        installed_plugins.write_text(json.dumps({"plugins": {"klona-memory-plugin@klona-plugins": [{"installPath": "/tmp/foreign", "version": "0.1.0"}]}}))

        self.uninstall()

        self.assertEqual((self.plugin / "plugin.json").read_text(), "foreign")
        self.assertEqual((self.cache_plugin / "plugin.json").read_text(), "foreign-cache")
        self.assertIn("klona-plugins", json.loads(known_marketplaces.read_text()))
        self.assertIn("klona-memory-plugin@klona-plugins", json.loads(installed_plugins.read_text())["plugins"])

    def test_uninstall_preserves_registry_entries_for_unowned_same_path_dirs(self):
        self.plugin.mkdir(parents=True)
        (self.plugin / "plugin.json").write_text("foreign")
        self.cache_plugin.mkdir(parents=True)
        (self.cache_plugin / "plugin.json").write_text("foreign-cache")
        known_marketplaces = self.claude / "plugins" / "known_marketplaces.json"
        known_marketplaces.parent.mkdir(parents=True, exist_ok=True)
        known_marketplaces.write_text(json.dumps({"klona-plugins": {"source": {"source": "directory", "path": str(self.plugin)}, "installLocation": str(self.plugin)}}))
        installed_plugins = self.claude / "plugins" / "installed_plugins.json"
        installed_plugins.write_text(json.dumps({"plugins": {"klona-memory-plugin@klona-plugins": [{"scope": "user", "installPath": str(self.cache_plugin), "version": "0.1.0"}]}}))
        self.settings.write_text(json.dumps({"enabledPlugins": {"klona-memory-plugin@klona-plugins": True}}))

        self.uninstall()

        self.assertEqual((self.plugin / "plugin.json").read_text(), "foreign")
        self.assertEqual((self.cache_plugin / "plugin.json").read_text(), "foreign-cache")
        self.assertIn("klona-plugins", json.loads(known_marketplaces.read_text()))
        self.assertIn("klona-memory-plugin@klona-plugins", json.loads(installed_plugins.read_text())["plugins"])
        self.assertEqual(self.read_settings(), {"enabledPlugins": {"klona-memory-plugin@klona-plugins": True}})

    def test_uninstall_removes_only_scoped_enabled_plugin_setting(self):
        self.install_with_args()
        self.settings.write_text(json.dumps({"enabledPlugins": {"other@market": True, "klona-memory-plugin@klona-plugins": True}, "theme": "light"}))

        self.uninstall()

        self.assertEqual(self.read_settings(), {"enabledPlugins": {"other@market": True}, "theme": "light"})

    def test_install_rolls_back_plugin_and_registry_on_late_write_failure(self):
        existing = self.plugin / "plugin.json"
        existing.parent.mkdir(parents=True)
        existing.write_text("existing")
        (self.plugin / ".klona-owned").write_text("owned")
        with mock.patch.object(installer.Path, "home", return_value=self.home), mock.patch.object(
            installer, "_write_registry_files", side_effect=RuntimeError("write failed")
        ), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(RuntimeError):
                installer.install(mcp_url="https://memory.example/mcp", mcp_token="token")

        self.assertEqual(existing.read_text(), "existing")
        self.assertFalse((self.plugin / ".mcp.json").exists())
        self.assertTrue((self.plugin / ".klona-owned").exists())
        self.assertFalse(self.cache_plugin.exists())

    def test_install_invalid_registry_json_fails_before_mutation(self):
        known_marketplaces = self.claude / "plugins" / "known_marketplaces.json"
        known_marketplaces.parent.mkdir(parents=True)
        known_marketplaces.write_text("{")

        with self.assertRaisesRegex(SystemExit, "invalid JSON"):
            self.install_with_args()

        self.assertEqual(known_marketplaces.read_text(), "{")
        self.assertFalse(self.plugin.exists())
        self.assertFalse(self.cache_plugin.exists())

        known_marketplaces.write_text("{}")
        installed_plugins = self.claude / "plugins" / "installed_plugins.json"
        installed_plugins.write_text("{")

        with self.assertRaisesRegex(SystemExit, "invalid JSON"):
            self.install_with_args()

        self.assertEqual(installed_plugins.read_text(), "{")
        self.assertFalse(self.plugin.exists())
        self.assertFalse(self.cache_plugin.exists())

    def test_install_invalid_settings_json_fails_before_mutation(self):
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text("{")

        with self.assertRaisesRegex(SystemExit, "invalid JSON"):
            self.install_with_args()

        self.assertEqual(self.settings.read_text(), "{")
        self.assertFalse(self.plugin.exists())
        self.assertFalse(self.cache_plugin.exists())

    def test_install_preserves_unrelated_registry_entries_and_top_level_version(self):
        known_marketplaces = self.claude / "plugins" / "known_marketplaces.json"
        installed_plugins = self.claude / "plugins" / "installed_plugins.json"
        known_marketplaces.parent.mkdir(parents=True)
        known_marketplaces.write_text(json.dumps({"other-market": {"path": "/tmp/other"}}))
        installed_plugins.write_text(json.dumps({"version": 7, "plugins": {"other@market": [{"installPath": "/tmp/other"}]}}))

        self.install_with_args()

        marketplaces = json.loads(known_marketplaces.read_text())
        installed = json.loads(installed_plugins.read_text())
        self.assertNotIn("marketplaces", marketplaces)
        self.assertIn("other-market", marketplaces)
        self.assertIn("klona-plugins", marketplaces)
        self.assertEqual(installed["version"], 7)
        self.assertIn("other@market", installed["plugins"])
        self.assertIn("klona-memory-plugin@klona-plugins", installed["plugins"])

    def test_install_preserves_other_same_plugin_key_records(self):
        installed_plugins = self.claude / "plugins" / "installed_plugins.json"
        installed_plugins.parent.mkdir(parents=True)
        other_records = [
            {"scope": "project", "installPath": str(self.cache_plugin), "version": "0.1.0"},
            {"scope": "user", "installPath": "/tmp/other-cache", "version": "0.1.0"},
            {"scope": "user", "installPath": str(self.cache_plugin), "version": "9.9.9"},
        ]
        installed_plugins.write_text(json.dumps({"plugins": {"klona-memory-plugin@klona-plugins": other_records + [{"scope": "user", "installPath": str(self.cache_plugin), "version": "0.1.0", "old": True}]}}))

        self.install_with_args()

        records = json.loads(installed_plugins.read_text())["plugins"]["klona-memory-plugin@klona-plugins"]
        self.assertEqual(records[:3], other_records)
        self.assertEqual(len(records), 4)
        self.assertNotIn("old", records[3])
        self.assertEqual(records[3]["scope"], "user")
        self.assertEqual(records[3]["installPath"], str(self.cache_plugin))
        self.assertEqual(records[3]["version"], "0.1.0")

    def test_uninstall_removes_only_this_same_plugin_key_record(self):
        self.install_with_args()
        installed_plugins = self.claude / "plugins" / "installed_plugins.json"
        other_records = [
            {"scope": "project", "installPath": str(self.cache_plugin), "version": "0.1.0"},
            {"scope": "user", "installPath": "/tmp/other-cache", "version": "0.1.0"},
        ]
        installed = json.loads(installed_plugins.read_text())
        installed["plugins"]["klona-memory-plugin@klona-plugins"] = other_records + installed["plugins"]["klona-memory-plugin@klona-plugins"]
        installed_plugins.write_text(json.dumps(installed))

        self.uninstall()

        records = json.loads(installed_plugins.read_text())["plugins"]["klona-memory-plugin@klona-plugins"]
        self.assertEqual(records, other_records)

    def test_install_prompts_for_missing_values_and_rejects_empty_url(self):
        with mock.patch.object(installer.Path, "home", return_value=self.home), mock.patch(
            "builtins.input", return_value="https://prompted.example/mcp"
        ) as input_mock, mock.patch("getpass.getpass", return_value="prompt-token") as getpass_mock, contextlib.redirect_stdout(io.StringIO()):
            installer.install()

        input_mock.assert_called_once_with("Klona high-level memory MCP URL: ")
        getpass_mock.assert_called_once_with("Klona high-level memory bearer token (empty disables auth): ")
        self.assertEqual(self.read_mcp()["mcpServers"][installer.MCP_NAME]["url"], "https://prompted.example/mcp")

        with self.assertRaisesRegex(SystemExit, "Klona high-level memory MCP URL cannot be empty"):
            self.install_with_args(mcp_url="   ", mcp_token="token")


if __name__ == "__main__":
    unittest.main()
