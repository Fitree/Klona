#!/usr/bin/env bash
set -euo pipefail

MCP_URL="${KLONA_E2E_MCP_URL:-http://test-memory-server:8000/mcp}"
TOKEN="${KLONA_E2E_TOKEN:-e2e-token}"
E2E_HOME="${KLONA_E2E_HOME:-/tmp/klona-e2e-home}"

fail() {
  printf 'E2E FAIL: %s\n' "$*" >&2
  exit 1
}

assert_file_exists() {
  test -f "$1" || fail "expected file to exist: $1"
}

assert_file_missing() {
  test ! -e "$1" || fail "expected path to be absent: $1"
}

assert_contains() {
  local file="$1"
  local needle="$2"
  grep -Fq -- "$needle" "$file" || fail "expected $file to contain: $needle"
}

assert_not_contains() {
  local file="$1"
  local needle="$2"
  if grep -Fq -- "$needle" "$file"; then
    fail "expected $file not to contain: $needle"
  fi
}

assert_marker_count() {
  local file="$1"
  local marker="$2"
  local expected="$3"
  local actual
  actual="$({ grep -F -- "$marker" "$file" || true; } | wc -l | tr -d ' ')"
  test "$actual" = "$expected" || fail "expected $expected occurrences of $marker in $file, got $actual"
}

assert_safe_tmp_path() {
  local path="$1"
  case "$path" in
    /tmp/klona-e2e-*) ;;
    *) fail "refusing to remove unsafe E2E path: $path" ;;
  esac
}

printf '== Python unit tests ==\n'
python3 -B -m unittest discover -s tests

printf '== Python compile check ==\n'
PYTHONPYCACHEPREFIX=/tmp/klona-e2e-pycache python3 -B -m compileall -q install_agent.py klona_agent tests memory_server/src/server.py

printf '== Memory server health ==\n'
curl -fsS http://test-memory-server:8000/health | grep -F 'klona-memory-server' >/dev/null

printf '== Memory server auth rejection ==\n'
unauth_status="$(curl -sS -o /tmp/klona-unauth.json -w '%{http_code}' "$MCP_URL")"
test "$unauth_status" = "401" || fail "expected unauthenticated MCP request to return 401, got $unauth_status"
grep -F 'Unauthorized' /tmp/klona-unauth.json >/dev/null || fail "expected unauthorized response body"

printf '== Prepare isolated OpenCode home ==\n'
assert_safe_tmp_path "$E2E_HOME"
rm -rf -- "$E2E_HOME"
mkdir -p "$E2E_HOME/.config/opencode/agents" "$E2E_HOME/.config/opencode/plugins"
cat > "$E2E_HOME/.config/opencode/AGENTS.md" <<'AGENTS'
# Existing instructions

Keep this user content.
AGENTS
cat > "$E2E_HOME/.config/opencode/opencode.json" <<'JSON'
{
  "theme": "dark",
  "mcp": {
    "other_server": {
      "type": "local",
      "command": ["true"]
    }
  }
}
JSON
printf 'custom agent\n' > "$E2E_HOME/.config/opencode/agents/custom.md"
printf 'custom plugin\n' > "$E2E_HOME/.config/opencode/plugins/custom.js"

printf '== Non-interactive install ==\n'
HOME="$E2E_HOME" python3 install_agent.py --platform opencode --klona-memory-server-url "$MCP_URL" --klona-memory-server-token "$TOKEN"
HOME="$E2E_HOME" python3 install_agent.py --platform opencode --klona-memory-server-url "$MCP_URL" --klona-memory-server-token "$TOKEN"

AGENTS_FILE="$E2E_HOME/.config/opencode/AGENTS.md"
CONFIG_FILE="$E2E_HOME/.config/opencode/opencode.json"
AGENT_COPY="$E2E_HOME/.config/opencode/agents/klona-memory.md"
PLUGIN_COPY="$E2E_HOME/.config/opencode/plugins/klona-memory-session.js"

assert_file_exists "$AGENTS_FILE"
assert_file_exists "$CONFIG_FILE"
assert_file_exists "$AGENT_COPY"
assert_file_exists "$PLUGIN_COPY"
assert_contains "$AGENTS_FILE" "# Existing instructions"
assert_contains "$AGENTS_FILE" "Keep this user content."
assert_marker_count "$AGENTS_FILE" "<!-- KLONA:BEGIN -->" "1"
assert_marker_count "$AGENTS_FILE" "<!-- KLONA:END -->" "1"
assert_contains "$AGENTS_FILE" "# KLONA"

diff -u klona_agent/opencode/assets/agents/klona-memory.md "$AGENT_COPY"
diff -u klona_agent/opencode/assets/plugins/klona-memory-session.js "$PLUGIN_COPY"

python3 - "$CONFIG_FILE" "$MCP_URL" "$TOKEN" <<'PY'
import json
import sys

path, expected_url, expected_token = sys.argv[1:]
data = json.loads(open(path, encoding="utf-8").read())
entry = data["mcp"]["klona_memory_server"]
assert data["theme"] == "dark"
assert data["mcp"]["other_server"] == {"type": "local", "command": ["true"]}
assert entry == {
    "type": "remote",
    "url": expected_url,
    "enabled": True,
    "oauth": False,
    "headers": {"Authorization": f"Bearer {expected_token}"},
}
PY

printf '== Invalid args fail without clean-home mutation ==\n'
INVALID_HOME="/tmp/klona-e2e-invalid-home"
assert_safe_tmp_path "$INVALID_HOME"
rm -rf -- "$INVALID_HOME"
if HOME="$INVALID_HOME" python3 install_agent.py --platform opencode --klona-memory-server-url "" --klona-memory-server-token "$TOKEN"; then
  fail "empty MCP URL unexpectedly succeeded"
fi
test ! -e "$INVALID_HOME/.config/opencode/opencode.json" || fail "invalid URL created config"

assert_safe_tmp_path "$INVALID_HOME"
rm -rf -- "$INVALID_HOME"
if HOME="$INVALID_HOME" python3 install_agent.py --platform opencode --klona-memory-server-url "$MCP_URL" --klona-memory-server-token ""; then
  fail "empty MCP token unexpectedly succeeded"
fi
test ! -e "$INVALID_HOME/.config/opencode/opencode.json" || fail "invalid token created config"

printf '== Uninstall ==\n'
HOME="$E2E_HOME" python3 install_agent.py --uninstall --platform opencode
assert_file_exists "$AGENTS_FILE"
assert_contains "$AGENTS_FILE" "# Existing instructions"
assert_contains "$AGENTS_FILE" "Keep this user content."
assert_not_contains "$AGENTS_FILE" "<!-- KLONA:BEGIN -->"
assert_not_contains "$AGENTS_FILE" "<!-- KLONA:END -->"
assert_file_missing "$AGENT_COPY"
assert_file_missing "$PLUGIN_COPY"
assert_file_exists "$E2E_HOME/.config/opencode/agents/custom.md"
assert_file_exists "$E2E_HOME/.config/opencode/plugins/custom.js"

python3 - "$CONFIG_FILE" <<'PY'
import json
import sys

data = json.loads(open(sys.argv[1], encoding="utf-8").read())
assert data["theme"] == "dark"
assert "klona_memory_server" not in data["mcp"]
assert data["mcp"]["other_server"] == {"type": "local", "command": ["true"]}
PY

printf 'E2E PASS\n'
