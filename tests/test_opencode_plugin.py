import unittest

from klona_agent.opencode import install as installer


class OpenCodePluginTests(unittest.TestCase):
    def test_plugin_is_named_klona_memory_mental_model_injector(self):
        self.assertEqual(installer.PLUGIN_SOURCE.name, "klona-memory-mental-model-injector.js")

        plugin = installer.PLUGIN_SOURCE.read_text()

        self.assertIn("export const KlonaMemoryMentalModelInjectorPlugin", plugin)
        self.assertIn('"klona-memory-mental-model-injector"', plugin)
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

    def test_plugin_uses_private_high_level_mental_model_endpoint(self):
        plugin = installer.PLUGIN_SOURCE.read_text()

        self.assertIn('const DEFAULT_MCP_NAME = "klona_memory"', plugin)
        self.assertIn('const DEFAULT_MCP_TIMEOUT_MS = 600_000', plugin)
        self.assertIn('timeout: typeof mcp.timeout === "number" ? mcp.timeout : DEFAULT_MCP_TIMEOUT_MS', plugin)
        self.assertIn('const INTERNAL_MENTAL_MODEL_PATH = "/internal/mental-model"', plugin)
        self.assertIn('function mentalModelEndpointUrl(mcpUrl)', plugin)
        self.assertIn('method: "GET"', plugin)
        self.assertIn('Accept: "application/json"', plugin)
        self.assertNotIn('name: "recall"', plugin)
        self.assertNotIn('tools/call', plugin)
        self.assertNotIn('name: "vault_read"', plugin)

    def test_plugin_marks_missing_or_empty_mental_model_as_checked(self):
        plugin = installer.PLUGIN_SOURCE.read_text()

        self.assertIn('if (response.status === 404 && payload?.status === "missing") return payload', plugin)
        self.assertIn('const memoryResult = await readKlonaMemoryMentalModel()', plugin)
        self.assertIn('await writeInjectionStatus(sessionID, { should_inject: false, reason: `${status.reason}-consumed` })', plugin)
        self.assertIn('status: memoryResult.status', plugin)

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
        self.assertIn("async function getSessionMessageHistoryStatus(sessionID)", plugin)
        self.assertIn("async function ensureInjectionStatus(sessionID)", plugin)
        self.assertIn("if (status.should_inject === false) return", plugin)
        self.assertIn("const status = await ensureInjectionStatus(sessionID)", plugin)
        self.assertIn("if (!status) return", plugin)
        self.assertIn("should_inject: true", plugin)
        self.assertIn("reason: \"first-user-message\"", plugin)
        self.assertIn('await writeInjectionStatus(sessionID, { should_inject: false, reason: `${status.reason}-consumed` })', plugin)
        self.assertNotIn("claimInjectedSessionMarker", plugin)
        self.assertNotIn("ensureInjectedSessionMarker", plugin)
        self.assertNotIn("removeInjectedSessionMarker", plugin)
        self.assertNotIn("KLONA_MEMORY_MENTAL_MODEL_MARKER", plugin)
        self.assertNotIn("KLONA_MEMORY_MENTAL_MODEL_FILE_PATH", plugin)

    def test_plugin_does_not_inject_resumed_mid_session_when_status_is_missing(self):
        plugin = installer.PLUGIN_SOURCE.read_text()

        self.assertIn("await client.session.messages({", plugin)
        self.assertIn("path: { id: sessionID }", plugin)
        self.assertIn("query: { limit: 1 }", plugin)
        self.assertIn("const messages = response?.data ?? response", plugin)
        self.assertIn("hasExistingMessages: messages.length > 0", plugin)
        self.assertIn("const messageHistoryStatus = await getSessionMessageHistoryStatus(sessionID)", plugin)
        self.assertIn("if (messageHistoryStatus.hasExistingMessages) {", plugin)
        self.assertIn('await writeInjectionStatus(sessionID, { should_inject: false, reason: messageHistoryStatus.reason })', plugin)
        self.assertIn('reason: messages.length > 0 ? "resumed-existing-session" : "new-empty-session"', plugin)
        self.assertIn('return { hasExistingMessages: true, reason: "message-history-unverified" }', plugin)
        self.assertIn("return null", plugin)
        self.assertIn("session messages lookup returned an error", plugin)
        self.assertIn("session messages lookup returned an unexpected shape", plugin)
        self.assertIn("Skipping KLONA_MEMORY_MENTAL_MODEL.md injection because session messages lookup failed", plugin)

    def test_plugin_revalidates_stale_persisted_first_user_status(self):
        plugin = installer.PLUGIN_SOURCE.read_text()

        persisted_first_user_check = 'status.should_inject === true && status.reason === "first-user-message"'
        history_check = "const messageHistoryStatus = await getSessionMessageHistoryStatus(sessionID)"
        stale_reason = "reason: `stale-first-user-message-${messageHistoryStatus.reason}`"
        return_status = "return status"
        post_compaction_marker = 'reason: "post-compaction"'

        self.assertIn(persisted_first_user_check, plugin)
        self.assertIn(history_check, plugin)
        self.assertIn("if (messageHistoryStatus.hasExistingMessages) {", plugin)
        self.assertIn(stale_reason, plugin)
        self.assertIn("return null", plugin)
        self.assertIn(return_status, plugin)
        self.assertLess(plugin.index(persisted_first_user_check), plugin.index(return_status))
        self.assertIn(post_compaction_marker, plugin)

    def test_plugin_consumes_eligible_message_before_fetch_or_no_text_return(self):
        plugin = installer.PLUGIN_SOURCE.read_text()

        consume = 'await writeInjectionStatus(sessionID, { should_inject: false, reason: `${status.reason}-consumed` })'
        fetch = "const memoryResult = await readKlonaMemoryMentalModel()"
        no_text_return = "if (!injected) return"

        self.assertIn(consume, plugin)
        self.assertLess(plugin.index(consume), plugin.index(fetch))
        self.assertLess(plugin.index(consume), plugin.index(no_text_return))

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
