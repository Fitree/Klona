# Sandbox E2E Design

## Goal

Provide a single command, `sandbox/run_e2e.sh`, that starts a complete Docker-based test environment, installs the OpenCode KLONA integration, verifies the actual OpenCode session plugin injects the mounted mental model into a real `opencode run` model request, uninstalls the integration, and cleans up containers and volumes afterward.

## Architecture

The E2E environment uses Docker Compose with two services:

- `test-memory-server`: builds from `memory_server/Dockerfile`, runs the KLONA memory MCP server, uses `AUTH_TOKEN=e2e-token`, and mounts `sandbox/test_vault` at `/vault` so fixture notes are visible through MCP.
- `test-env`: builds from `sandbox/Dockerfile`, mounts the repository at `/workspace/KLONA`, waits for `test-memory-server` health, and runs Scenario 1.

The test container reaches the memory server through Docker Compose service DNS:

```text
http://test-memory-server:8000
http://test-memory-server:8000/mcp
```

It must not use `localhost` for cross-container calls because `localhost` inside `test-env` refers to `test-env` itself.

## Files

- `sandbox/docker-compose.e2e.yml`: Compose definition for `test-memory-server` and `test-env`.
- `sandbox/run_e2e.sh`: host entrypoint that runs Compose, propagates the `test-env` exit code, and cleans up with `docker compose down -v` even on failure.
- `sandbox/e2e_scenario1.py`: Python entrypoint executed inside `test-env` that installs KLONA for OpenCode, checks installed files against repository assets, configures a fake OpenAI-compatible provider using `@ai-sdk/openai-compatible`, runs actual `opencode run`, verifies the fake provider captured `KLONA_E2E_MENTAL_MODEL_LOADED_7f4e2d1a9c6b4380b5e21f0d3a8c9e62` wrapped in `<Mental_model>`, uninstalls KLONA, and verifies KLONA-owned artifacts are removed.
- `sandbox/test_vault/MENTAL_MODEL.md`: mounted vault fixture containing the unique marker `KLONA_E2E_MENTAL_MODEL_LOADED_7f4e2d1a9c6b4380b5e21f0d3a8c9e62`.

## Test Flow

`sandbox/run_e2e.sh` should:

1. Resolve the repository root.
2. Start the E2E Compose stack with build enabled and fixed project name `sandbox`.
3. Use `--abort-on-container-exit` and `--exit-code-from test-env` so the host command fails when E2E tests fail.
4. Always run project-scoped cleanup, equivalent to `docker compose -p "$PROJECT_NAME" -f sandbox/docker-compose.e2e.yml down -v --remove-orphans`, before exiting.

`sandbox/e2e_scenario1.py` should:

1. Confirm it is running as `test_user` with `HOME=/home/test_user`.
2. Reset only the scenario-owned fake-provider temp capture directory.
3. Run `python3 install_agent.py --platform opencode` with non-interactive MCP URL/token arguments.
4. Verify `AGENTS.md`, `opencode.json`, `agents/klona-memory.md`, and `plugins/klona-memory-session.js` are installed, and compare copied assets against repository sources byte-for-byte.
5. Merge fake provider config into `opencode.json` without removing `mcp.klona_memory_server`.
6. Start a stdlib Python fake provider on `127.0.0.1:4545` that handles `GET /health`, `GET /v1/models`, and `POST /v1/chat/completions` for streaming and non-stream requests.
7. Run actual `opencode run --model fake/e2e-model` and assert the captured provider request contains `/v1/chat/completions`, `KLONA_E2E_MENTAL_MODEL_LOADED_7f4e2d1a9c6b4380b5e21f0d3a8c9e62`, `<Mental_model>`, and the original prompt.
8. Run `python3 install_agent.py --uninstall --platform opencode` and verify KLONA markers, copied agent/plugin files, and `mcp.klona_memory_server` are removed.
9. Print `E2E PASS`.

The fake provider config may remain after uninstall; Scenario 1 only requires KLONA-owned artifacts to be removed.

## Error Handling

The host shell runner should use `set -euo pipefail`. `run_e2e.sh` must preserve the failing exit code while still cleaning up the Compose stack as checks are added incrementally. The Python scenario should raise clear exceptions/SystemExit failures and shut down the fake provider in a `finally` block.

## Out of Scope

Interactive TUI coverage, broader installer idempotency cases, and full unit/compile checks inside the container are out of scope for Scenario 1.
