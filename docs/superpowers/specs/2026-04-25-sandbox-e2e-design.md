# Sandbox E2E Design

## Goal

Provide a single command, `sandbox/run_e2e.sh`, that starts a complete Docker-based test environment, runs all sandbox E2E checks automatically, and cleans up containers and volumes afterward.

## Architecture

The E2E environment uses Docker Compose with two services:

- `test-memory-server`: builds from `memory_server/Dockerfile`, runs the KLONA memory MCP server, uses `AUTH_TOKEN=e2e-token`, and stores data in an isolated test vault volume.
- `test-env`: builds from `sandbox/Dockerfile`, mounts the repository at `/workspace/KLONA`, waits for `test-memory-server` health, and runs the E2E assertions.

The test container reaches the memory server through Docker Compose service DNS:

```text
http://test-memory-server:8000
http://test-memory-server:8000/mcp
```

It must not use `localhost` for cross-container calls because `localhost` inside `test-env` refers to `test-env` itself.

## Files

- `sandbox/docker-compose.e2e.yml`: Compose definition for `test-memory-server` and `test-env`.
- `sandbox/run_e2e.sh`: host entrypoint that runs Compose, propagates the `test-env` exit code, and cleans up with `docker compose down -v` even on failure.
- `sandbox/e2e_test.sh`: script executed inside `test-env` that runs unit tests, compile checks, memory-server checks, installer checks, idempotency checks, and uninstall checks.

## Test Flow

`sandbox/run_e2e.sh` should:

1. Resolve the repository root.
2. Start the E2E Compose stack with build enabled.
3. Use `--abort-on-container-exit` and `--exit-code-from test-env` so the host command fails when E2E tests fail.
4. Always run project-scoped cleanup, equivalent to `docker compose -p "$PROJECT_NAME" -f sandbox/docker-compose.e2e.yml down -v --remove-orphans`, before exiting.

`sandbox/e2e_test.sh` should:

1. Run from `/workspace/KLONA`.
2. Run Python unit tests: `python3 -B -m unittest discover -s tests`.
3. Run Python compile check: `python3 -B -m compileall -q install_agent.py klona_agent tests memory_server/src/server.py`.
4. Verify `test-memory-server` health from inside `test-env`.
5. Verify unauthenticated MCP access is rejected.
6. Verify non-interactive install with `HOME=/tmp/klona-e2e-home` succeeds using `http://test-memory-server:8000/mcp` and `e2e-token`.
7. Verify installed OpenCode files exist.
8. Verify `AGENTS.md` contains exactly one KLONA managed block and preserves unrelated content across reinstall.
9. Verify `opencode.json` contains `mcp.klona_memory_server` with the expected URL, enabled flag, OAuth flag, and bearer token header.
10. Verify installed agent and plugin files match repository assets byte-for-byte.
11. Verify invalid empty URL/token inputs fail without creating a clean-home OpenCode config.
12. Verify uninstall removes only KLONA-owned files/config while preserving unrelated content.

## Error Handling

All E2E scripts should use `set -euo pipefail`. Assertions should print clear failure messages. `run_e2e.sh` must preserve the failing exit code while still cleaning up the Compose stack.

## Out of Scope

This first E2E does not need to drive an interactive OpenCode chat session. It should verify installed OpenCode assets and config, plus memory-server container reachability and auth behavior. Full OpenCode CLI behavioral automation can be added later if the CLI can be driven reliably in CI.
