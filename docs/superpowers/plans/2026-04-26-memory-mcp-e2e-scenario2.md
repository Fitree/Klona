# Memory MCP E2E Scenario 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Scenario 2 for the memory MCP server that directly calls every MCP method/tool and runs after Scenario 1 from the single E2E entrypoint.

**Architecture:** Keep one `docker-compose.e2e.yml` with a stable named runtime vault mounted at `/vault` in `test-memory-server` and `/runtime-vault` in `test-env`. Add a Python E2E runner that copies immutable `e2e_test/test_vault` into the runtime vault before each scenario, then runs Scenario 1 and Scenario 2 sequentially; Scenario 2 uses stdlib JSON-RPC HTTP calls directly against `/mcp`, not OpenCode.

**Tech Stack:** Bash, Docker Compose v2, Python stdlib `urllib.request`, `json`, `shutil`, `subprocess`, and existing `unittest` script checks.

---

## Files

- Modify `tests/test_sandbox_e2e_scripts.py` to add RED coverage for the unified runner, named runtime vault, and direct Scenario 2 MCP calls.
- Modify `e2e_test/docker-compose.e2e.yml` to mount `e2e-runtime-vault` instead of mounting `test_vault` directly, and to run `e2e_runner.py`.
- Create `e2e_test/e2e_runner.py` to reset/copy the runtime vault before each scenario and verify source fixture hashes are unchanged.
- Create `e2e_test/e2e_scenario2.py` to call `/health`, `initialize`, `notifications/initialized`, and all required `tools/call` tools directly.
- Keep `e2e_test/e2e_scenario1.py` behavior intact; it reads the source fixture for expected mental-model content while the server reads the copied runtime vault.
- No `memory_server/src/server.py` changes unless direct E2E execution reveals a real server bug.

## TDD Steps

- [ ] Add failing static/unit tests for Scenario 2, the unified runner, and runtime vault isolation.
- [ ] Run `python3 -B -m unittest tests.test_sandbox_e2e_scripts` and confirm RED because `e2e_runner.py`/`e2e_scenario2.py` and unified vault logic are missing.
- [ ] Implement the minimal runner, compose changes, and Scenario 2 direct JSON-RPC client.
- [ ] Re-run `python3 -B -m unittest tests.test_sandbox_e2e_scripts` and confirm GREEN.
- [ ] Run full verification commands, including `e2e_test/run_e2e.sh`.

## Verification

- `python3 -B -m unittest tests.test_sandbox_e2e_scripts`
- `python3 -B -m unittest discover -s tests`
- `python3 -B -m compileall -q install_agent.py klona_agent tests memory_server/src/server.py e2e_test/e2e_scenario1.py e2e_test/e2e_scenario2.py`
- `e2e_test/run_e2e.sh`

## Isolation Guarantee

`e2e_test/test_vault` is never mounted as `/vault`. The server mutates only the Docker named volume `e2e-runtime-vault`; `e2e_runner.py` deletes and recopies that volume content from the source fixture before each scenario and compares source fixture file hashes before and after the scenario sequence.
