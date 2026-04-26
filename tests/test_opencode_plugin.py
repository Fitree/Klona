import unittest

from klona_agent.opencode import install as installer


class OpenCodePluginTests(unittest.TestCase):
    def test_plugin_is_named_mental_model_injector(self):
        self.assertEqual(installer.PLUGIN_SOURCE.name, "klona-mental-model-injector.js")

        plugin = installer.PLUGIN_SOURCE.read_text()

        self.assertIn("export const KlonaMentalModelInjectorPlugin", plugin)
        self.assertIn('"klona-mental-model-injector"', plugin)
        self.assertIn('name: "klona-mental-model-injector-plugin"', plugin)
        self.assertIn('plugin: "klona-mental-model-injector"', plugin)
        self.assertNotIn("KlonaMemorySessionPlugin", plugin)
        self.assertNotIn('service: "klona-memory-session"', plugin)

    def test_mental_model_injection_is_root_session_aware_and_fail_closed(self):
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
        self.assertIn("Skipping MENTAL_MODEL.md injection because session lookup failed", plugin)
        self.assertNotIn("MENTAL_MODEL_INJECTION_AGENT", plugin)
        self.assertNotIn("agent.trim() ===", plugin)

    def test_plugin_observes_compaction_and_marks_next_root_message_for_injection(self):
        plugin = installer.PLUGIN_SOURCE.read_text()

        self.assertIn('event: async (event) => {', plugin)
        self.assertIn('event?.event?.type === "session.compacted"', plugin)
        self.assertIn('event?.type === "session.compacted"', plugin)
        self.assertIn("await markSessionNeedsPostCompactionInjection(sessionID)", plugin)
        self.assertIn("async function hasPostCompactionInjectionMarker(sessionID)", plugin)
        self.assertIn("async function removePostCompactionInjectionMarker(sessionID)", plugin)
        self.assertIn("const shouldInjectAfterCompaction = await hasPostCompactionInjectionMarker(sessionID)", plugin)
        self.assertIn("if (!claimedMarker && !shouldInjectAfterCompaction) return", plugin)
        self.assertIn("if (shouldInjectAfterCompaction) {", plugin)
        self.assertIn("await ensureInjectedSessionMarker(sessionID)", plugin)
        self.assertIn("await removePostCompactionInjectionMarker(sessionID)", plugin)
        self.assertIn("post-compaction", plugin)

    def test_plugin_preserves_legacy_session_markers_after_rename(self):
        plugin = installer.PLUGIN_SOURCE.read_text()

        self.assertIn("const LEGACY_PLUGIN_STATE_DIR", plugin)
        self.assertIn('"klona-memory-session"', plugin)
        self.assertIn("function legacyMarkerFilePath(sessionID)", plugin)
        self.assertIn("await markerExists(legacyMarkerFilePath(sessionID))", plugin)

    def test_node_is_required_for_true_js_behavioral_plugin_tests(self):
        self.skipTest(
            "Node.js is required for true JS behavioral execution; "
            "Phase 1 Python verification uses source-level plugin guardrail checks."
        )


if __name__ == "__main__":
    unittest.main()
