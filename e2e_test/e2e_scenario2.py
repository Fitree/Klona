#!/usr/bin/env python3
import json
import os
import urllib.error
import urllib.parse
import urllib.request


MCP_URL = os.environ["KLONA_E2E_MCP_URL"]
TOKEN = os.environ["KLONA_E2E_TOKEN"]
KLONA_MEMORY_MENTAL_MODEL_MARKER = "KLONA_E2E_KLONA_MEMORY_MENTAL_MODEL_LOADED_7f4e2d1a9c6b4380b5e21f0d3a8c9e62"


def phase(name):
    print(f"\n==> {name}", flush=True)


def require(condition, message):
    if not condition:
        raise SystemExit(message)


def require_equal(actual, expected, message):
    if actual != expected:
        raise SystemExit(f"{message}: expected {expected!r}, got {actual!r}")


def health_url():
    parts = urllib.parse.urlsplit(MCP_URL)
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, "/health", "", ""))


def read_json_response(response):
    raw = response.read().decode("utf-8")
    if not raw:
        return None

    content_type = response.headers.get("Content-Type", "")
    if content_type.startswith("text/event-stream"):
        data_lines = []
        for line in raw.splitlines():
            if line.startswith("data: "):
                value = line[len("data: ") :]
                if value != "[DONE]":
                    data_lines.append(value)
        require(data_lines, f"SSE response had no data events: {raw!r}")
        return json.loads("\n".join(data_lines))

    return json.loads(raw)


class McpClient:
    def __init__(self, url, token):
        self.url = url
        self.token = token
        self.session_id = None
        self.next_id = 1

    def headers(self, include_auth=True, include_session=True):
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if include_auth:
            headers["Authorization"] = f"Bearer {self.token}"
        if include_session and self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    def post(self, payload, include_auth=True, include_session=True, expected_status=200):
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.url,
            data=body,
            headers=self.headers(include_auth=include_auth, include_session=include_session),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                require_equal(response.status, expected_status, "unexpected HTTP status")
                if response.headers.get("mcp-session-id"):
                    self.session_id = response.headers.get("mcp-session-id")
                return read_json_response(response)
        except urllib.error.HTTPError as exc:
            if exc.code != expected_status:
                error_body = exc.read().decode("utf-8", errors="replace")
                raise SystemExit(f"unexpected HTTP error {exc.code}: {error_body}") from exc
            return json.loads(exc.read().decode("utf-8"))

    def initialize(self):
        payload = {
            "jsonrpc": "2.0",
            "id": self.next_id,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "klona-e2e-scenario2", "version": "1.0"},
            },
        }
        self.next_id += 1
        response = self.post(payload, include_session=False)
        require(isinstance(response, dict), "initialize did not return a JSON object")
        require(response.get("jsonrpc") == "2.0", "initialize response is not JSON-RPC 2.0")
        require("result" in response, f"initialize failed: {response}")
        require(self.session_id, "initialize did not return an MCP session id")
        return response["result"]

    def initialized(self):
        payload = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        response = self.post(payload, expected_status=202)
        require(response is None, f"initialized notification returned unexpected body: {response}")

    def call_tool(self, name, arguments=None):
        payload = {
            "jsonrpc": "2.0",
            "id": self.next_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        }
        self.next_id += 1
        response = self.post(payload)
        require("result" in response, f"tool call {name} failed: {response}")
        result = response["result"]
        require(not result.get("isError"), f"tool call {name} returned isError: {result}")
        return extract_tool_payload(result)


def extract_tool_payload(result):
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        if "result" in structured and isinstance(structured["result"], dict):
            return structured["result"]
        return structured

    content = result.get("content")
    require(isinstance(content, list) and content, f"tool result has no content: {result}")
    first = content[0]
    require(isinstance(first, dict) and isinstance(first.get("text"), str), f"unexpected tool content: {content}")
    return json.loads(first["text"])


def check_health_is_auth_free():
    with urllib.request.urlopen(health_url(), timeout=10) as response:
        payload = read_json_response(response)
    require_equal(payload.get("status"), "ok", "health endpoint did not report ok")
    require_equal(payload.get("server"), "klona-memory-server", "health endpoint server mismatch")


def check_mcp_requires_auth(client):
    payload = {
        "jsonrpc": "2.0",
        "id": 999,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "unauthorized-check", "version": "1.0"},
        },
    }
    response = client.post(payload, include_auth=False, include_session=False, expected_status=401)
    require_equal(response, {"error": "Unauthorized"}, "unauthorized MCP response mismatch")


def require_warning(warnings, expected_type, expected_fields):
    for warning in warnings:
        if warning.get("type") != expected_type:
            continue
        if all(warning.get(key) == value for key, value in expected_fields.items()):
            return
    raise SystemExit(f"missing warning {expected_type} {expected_fields}: {warnings}")


def run_tool_checks(client):
    phase("Read initial copied vault")
    tree = client.call_tool("vault_tree")
    require_equal(tree, {}, "initial vault tree should contain no directories")

    root_listing = client.call_tool("vault_ls", {"path": "/"})
    require_equal(root_listing["path"], "/", "root listing path mismatch")
    require("KLONA_MEMORY_MENTAL_MODEL.md" in [item["name"] for item in root_listing["files"]], "KLONA_MEMORY_MENTAL_MODEL.md missing from root listing")

    klona_memory_mental_model = client.call_tool("vault_read", {"path": "/KLONA_MEMORY_MENTAL_MODEL.md"})
    require_equal(klona_memory_mental_model["path"], "/KLONA_MEMORY_MENTAL_MODEL.md", "Klona memory mental model read path mismatch")
    require(KLONA_MEMORY_MENTAL_MODEL_MARKER in klona_memory_mental_model["content"], "Klona memory mental model marker missing from copied runtime vault")
    require_equal(klona_memory_mental_model["links"], [], "Klona memory mental model links mismatch")

    marker_search = client.call_tool("vault_grep", {"pattern": "unique MARKER", "path": "/"})
    require_equal(marker_search["total_matches"], 1, "case-insensitive grep should find Klona memory mental model")
    require_equal(marker_search["results"][0]["path"], "/KLONA_MEMORY_MENTAL_MODEL.md", "grep result path mismatch")

    phase("Mutate runtime vault through MCP tools")
    mkdir_result = client.call_tool("vault_mkdir", {"path": "/scenario2/nested/"})
    require_equal(mkdir_result, {"status": "ok", "path": "/scenario2/nested/"}, "mkdir result mismatch")

    tree = client.call_tool("vault_tree")
    require_equal(tree, {"scenario2": {"nested": {}}}, "vault tree after mkdir mismatch")

    target_write = client.call_tool("vault_write", {"path": "/scenario2/target.md", "content": "# Target\n"})
    require_equal(target_write["status"], "ok", "target write status mismatch")
    require_equal(target_write["path"], "/scenario2/target.md", "target write path mismatch")
    require_equal(target_write["warnings"], [], "target write warnings mismatch")
    require(target_write.get("updated"), "target write did not return updated timestamp")

    linker_write = client.call_tool(
        "vault_write",
        {
            "path": "/scenario2/linker.md",
            "content": "# Linker\nSee [[scenario2/target.md]] and [[missing-note]].\n",
        },
    )
    require_equal(linker_write["status"], "ok", "linker write status mismatch")
    require_warning(linker_write["warnings"], "wikilink_rewritten", {"original": "scenario2/target.md", "rewritten": "target"})
    require_warning(linker_write["warnings"], "broken_link", {"target": "missing-note", "line": 6})

    linker_read = client.call_tool("vault_read", {"path": "/scenario2/linker.md"})
    require("[[target]]" in linker_read["content"], "sanitized target wikilink missing")
    require("[[scenario2/target.md]]" not in linker_read["content"], "malformed wikilink was not rewritten")
    require_equal(linker_read["links"], ["target", "missing-note"], "linker links mismatch")

    backlinks = client.call_tool("vault_backlinks", {"path": "/scenario2/target.md"})
    require_equal(backlinks["path"], "/scenario2/target.md", "backlinks path mismatch")
    require_equal(backlinks["count"], 1, "target backlink count mismatch")
    require_equal(backlinks["backlinks"][0]["path"], "/scenario2/linker.md", "target backlink source mismatch")

    grep_result = client.call_tool("vault_grep", {"pattern": "SEE [[TARGET]]", "path": "/scenario2/"})
    require_equal(grep_result["total_matches"], 1, "scenario grep should find linker")
    require_equal(grep_result["results"][0]["path"], "/scenario2/linker.md", "scenario grep path mismatch")

    update_result = client.call_tool(
        "vault_update",
        {"path": "/scenario2/linker.md", "content": "# Linker updated\nSee [[target|Target Note]].\n"},
    )
    require_equal(update_result["status"], "ok", "linker update status mismatch")
    require_equal(update_result["path"], "/scenario2/linker.md", "linker update path mismatch")
    require_equal(update_result["warnings"], [], "linker update warnings mismatch")

    alias_backlinks = client.call_tool("vault_backlinks", {"path": "/scenario2/target.md"})
    require_equal(alias_backlinks["count"], 1, "target alias backlink count mismatch")
    require("[[target|Target Note]]" in alias_backlinks["backlinks"][0]["text"], "alias backlink text mismatch")

    move_result = client.call_tool("vault_move", {"src": "/scenario2/target.md", "dst": "/scenario2/nested/moved-target.md"})
    require_equal(
        move_result,
        {
            "status": "ok",
            "from": "/scenario2/target.md",
            "to": "/scenario2/nested/moved-target.md",
            "referencing_files": ["/scenario2/linker.md"],
        },
        "move result mismatch",
    )

    moved_read = client.call_tool("vault_read", {"path": "/scenario2/nested/moved-target.md"})
    require_equal(moved_read["path"], "/scenario2/nested/moved-target.md", "moved target read path mismatch")

    doomed_write = client.call_tool("vault_write", {"path": "/scenario2/doomed.md", "content": "# Doomed\n"})
    require_equal(doomed_write["status"], "ok", "doomed write status mismatch")
    doom_link_write = client.call_tool("vault_write", {"path": "/scenario2/doom-link.md", "content": "References [[doomed]].\n"})
    require_equal(doom_link_write["warnings"], [], "doom link write warnings mismatch")

    delete_result = client.call_tool("vault_delete", {"path": "/scenario2/doomed.md"})
    require_equal(
        delete_result,
        {"status": "ok", "deleted": "/scenario2/doomed.md", "orphaned_backlinks": ["/scenario2/doom-link.md"]},
        "delete result mismatch",
    )

    missing_read = client.call_tool("vault_read", {"path": "/scenario2/doomed.md"})
    require_equal(missing_read["error"], "file_not_found", "deleted file should not be readable")

    runtime_update = client.call_tool(
        "vault_update",
        {"path": "/KLONA_MEMORY_MENTAL_MODEL.md", "content": "# Runtime-only mutation\nScenario 2 changed the copied vault only.\n"},
    )
    require_equal(runtime_update["status"], "ok", "runtime Klona memory mental model update status mismatch")
    runtime_read = client.call_tool("vault_read", {"path": "/KLONA_MEMORY_MENTAL_MODEL.md"})
    require("Runtime-only mutation" in runtime_read["content"], "runtime Klona memory mental model update was not applied")


def main():
    client = McpClient(MCP_URL, TOKEN)

    phase("Verify health and auth behavior")
    check_health_is_auth_free()
    check_mcp_requires_auth(client)

    phase("Initialize direct MCP JSON-RPC session")
    initialize_result = client.initialize()
    require("serverInfo" in initialize_result, f"initialize result missing serverInfo: {initialize_result}")
    client.initialized()

    run_tool_checks(client)

    print("\nE2E SCENARIO 2 PASS", flush=True)


if __name__ == "__main__":
    main()
