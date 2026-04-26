# Memory MCP E2E Scenario 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Scenario 2 for the memory MCP server that directly calls every MCP method/tool and runs after Scenario 1 from `e2e_test/run_e2e.sh`.

**Architecture:** Use a split-compose E2E design. `docker-compose.base.yml` defines shared services, including `vault-seeder`, `test-memory-server`, `test-env`, and the runtime vault volume. Per-scenario overlays (`docker-compose.scenario1.yml`, `docker-compose.scenario2.yml`) only select the scenario command. `run_e2e.sh` runs Scenario 1 and Scenario 2 sequentially as isolated Compose projects, invoking `vault-seeder` before each scenario. `test-env` does not mount the runtime vault; `test-memory-server` is the only runtime vault consumer. Scenario 2 uses stdlib JSON-RPC HTTP calls directly against `/mcp`, not OpenCode.

**Tech Stack:** Bash, Docker Compose v2, Python stdlib `urllib.request`, `json`, `shutil`, `subprocess`, and existing `unittest` script checks.

---

## Files

- Modify `tests/test_sandbox_e2e_scripts.py` to add RED coverage for split Compose files, isolated per-scenario projects, per-scenario vault seeding, and direct Scenario 2 MCP calls.
- Create `e2e_test/docker-compose.base.yml` with shared services and the `vault-seeder` that copies `e2e_test/test_vault` into the runtime vault.
- Create `e2e_test/docker-compose.scenario1.yml` and `e2e_test/docker-compose.scenario2.yml` as minimal command overlays for each scenario.
- Update `e2e_test/run_e2e.sh` to run scenarios sequentially with distinct Compose project names, seed the vault before each scenario, clean up each project, and verify source fixture hashes are unchanged.
- Create `e2e_test/e2e_scenario2.py` to call `/health`, `initialize`, `notifications/initialized`, and all required `tools/call` tools directly.
- Keep `e2e_test/e2e_scenario1.py` behavior intact; it reads expected data from the source fixture while the server reads the copied runtime vault.
- No `memory_server/src/server.py` changes unless direct E2E execution reveals a real server bug.

## TDD Steps

- [ ] Add failing static/unit tests for Scenario 2, split Compose files, sequential isolated projects, per-scenario seeding, and runtime vault isolation.
- [ ] Run `python3 -B -m unittest tests.test_sandbox_e2e_scripts` and confirm RED because Scenario 2 and split-compose orchestration are missing.
- [ ] Implement the minimal split-compose orchestration and Scenario 2 direct JSON-RPC client.
- [ ] Re-run `python3 -B -m unittest tests.test_sandbox_e2e_scripts` and confirm GREEN.
- [ ] Run full verification commands, including `e2e_test/run_e2e.sh`.

## Verification

- `python3 -B -m unittest tests.test_sandbox_e2e_scripts`
- `python3 -B -m unittest discover -s tests`
- `python3 -B -m compileall -q install_agent.py klona_agent tests memory_server/src/server.py e2e_test/e2e_scenario1.py e2e_test/e2e_scenario2.py`
- `e2e_test/run_e2e.sh`

## Isolation Guarantee

`e2e_test/test_vault` is never mounted as `/vault`. `vault-seeder` copies the fixture into the runtime vault before each scenario. `test-env` has no runtime vault mount; only `test-memory-server` consumes the runtime vault at `/vault`. `run_e2e.sh` runs each scenario in an isolated Compose project, cleans up volumes between scenarios, and compares source fixture file hashes before and after the scenario sequence.
