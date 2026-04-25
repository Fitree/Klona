import unittest

from klona_agent.opencode import install as installer


class OpenCodePluginTests(unittest.TestCase):
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

    def test_node_is_required_for_true_js_behavioral_plugin_tests(self):
        self.skipTest(
            "Node.js is required for true JS behavioral execution; "
            "Phase 1 Python verification uses source-level plugin guardrail checks."
        )


if __name__ == "__main__":
    unittest.main()
