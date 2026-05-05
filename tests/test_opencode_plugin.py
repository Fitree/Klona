import unittest

from klona_agent.opencode import install as installer


class OpenCodePluginTests(unittest.TestCase):
    def test_plugin_is_named_klona_memory_mental_model_injector(self):
        self.assertEqual(installer.PLUGIN_SOURCE.name, "klona-memory-mental-model-injector.js")

        plugin = installer.PLUGIN_SOURCE.read_text()

        self.assertIn("export const KlonaMemoryMentalModelInjectorPlugin", plugin)
        self.assertIn('"klona-memory-mental-model-injector"', plugin)
        self.assertIn('name: "klona-memory-mental-model-injector-plugin"', plugin)
        self.assertIn('plugin: "klona-memory-mental-model-injector"', plugin)
        self.assertNotIn("KlonaMemorySessionPlugin", plugin)
        self.assertNotIn('service: "klona-memory-session"', plugin)

    def test_klona_memory_mental_model_injection_is_root_session_aware_and_fail_closed(self):
        plugin = installer.PLUGIN_SOURCE.read_text()

        self.assertIn("async function isRootSession(sessionID)", plugin)
        self.assertIn("client.session.get({ path: { id: sessionID } })", plugin)
        self.assertIn("response?.error", plugin)
        self.assertIn("const session = response?.data ?? response", plugin)
        self.assertIn("session.id !== sessionID", plugin)
        self.assertIn("return !session.parentID", plugin)
        self.assertIn("if (!(await isRootSession(sessionID))) return", plugin)
        self.assertIn("session lookup returned an error", plugin)
        self.assertIn("session lookup returned an unexpected shape", plugin)
        self.assertIn("Skipping KLONA_MEMORY_MENTAL_MODEL.md injection because session lookup failed", plugin)
        self.assertNotIn("KLONA_MEMORY_MENTAL_MODEL_INJECTION_AGENT", plugin)
        self.assertNotIn("agent.trim() ===", plugin)

    def test_plugin_observes_compaction_and_marks_next_root_message_for_injection(self):
        plugin = installer.PLUGIN_SOURCE.read_text()

        self.assertIn('event: async (event) => {', plugin)
        self.assertIn('event?.event?.type === "session.compacted"', plugin)
        self.assertIn('event?.type === "session.compacted"', plugin)
        self.assertIn('await writeInjectionStatus(sessionID, { should_inject: true, reason: "post-compaction" })', plugin)
        self.assertNotIn("markSessionNeedsPostCompactionInjection", plugin)
        self.assertNotIn("hasPostCompactionInjectionMarker", plugin)
        self.assertNotIn("removePostCompactionInjectionMarker", plugin)
        self.assertNotIn("postCompactionMarkerFilePath", plugin)
        self.assertNotIn(".post-compaction.json", plugin)
        self.assertIn("post-compaction", plugin)

    def test_plugin_uses_one_injection_status_file_per_session(self):
        plugin = installer.PLUGIN_SOURCE.read_text()

        self.assertNotIn("XDG_DATA_HOME", plugin)
        self.assertIn("const PLUGIN_STATE_DIR = path.join(", plugin)
        self.assertIn("os.homedir()", plugin)
        self.assertIn('".local"', plugin)
        self.assertIn('"share"', plugin)
        self.assertIn("function injectionStatusFilePath(sessionID)", plugin)
        self.assertIn("async function readInjectionStatus(sessionID)", plugin)
        self.assertIn("async function writeInjectionStatus(sessionID, status)", plugin)
        self.assertIn("async function ensureInjectionStatus(sessionID)", plugin)
        self.assertIn("if (status.should_inject === false) return", plugin)
        self.assertIn("const status = await ensureInjectionStatus(sessionID)", plugin)
        self.assertIn("should_inject: true", plugin)
        self.assertIn("reason: \"first-user-message\"", plugin)
        self.assertIn("await writeInjectionStatus(sessionID, { should_inject: false })", plugin)
        self.assertNotIn("claimInjectedSessionMarker", plugin)
        self.assertNotIn("ensureInjectedSessionMarker", plugin)
        self.assertNotIn("removeInjectedSessionMarker", plugin)
        self.assertNotIn("KLONA_MEMORY_MENTAL_MODEL_MARKER", plugin)
        self.assertNotIn("KLONA_MEMORY_MENTAL_MODEL_FILE_PATH", plugin)

    def test_plugin_does_not_check_legacy_session_markers_after_rename(self):
        plugin = installer.PLUGIN_SOURCE.read_text()

        self.assertNotIn("const LEGACY_PLUGIN_STATE_DIR", plugin)
        self.assertNotIn('"klona-memory-session"', plugin)
        self.assertNotIn("function legacyMarkerFilePath(sessionID)", plugin)
        self.assertNotIn("await markerExists(legacyMarkerFilePath(sessionID))", plugin)

    def test_node_is_required_for_true_js_behavioral_plugin_tests(self):
        self.skipTest(
            "Node.js is required for true JS behavioral execution; "
            "Phase 1 Python verification uses source-level plugin guardrail checks."
        )


if __name__ == "__main__":
    unittest.main()
