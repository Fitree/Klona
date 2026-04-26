# OpenCode AGENTS Marker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Change the KLONA-managed OpenCode `AGENTS.md` enclosure from HTML comments to `<Klona_Memory>` XML-like tags while preserving reinstall and uninstall behavior.

**Architecture:** Keep marker generation and block removal centralized in `klona_agent/opencode/install.py`. Add compatibility removal for legacy `<!-- KLONA:BEGIN --> ... <!-- KLONA:END -->` blocks so existing installs are cleaned during reinstall or uninstall.

**Tech Stack:** Python standard library, `unittest`, OpenCode installer tests.

---

### Task 1: Tests and Compatibility Expectations

**Files:**
- Modify: `tests/test_opencode_installer.py`
- Modify: `e2e_test/e2e_scenario1.py`

- [ ] Update installer unit tests to expect `<Klona_Memory>` and `</Klona_Memory>` in newly written blocks.
- [ ] Add a unit test proving reinstall removes a legacy `<!-- KLONA:BEGIN --> ... <!-- KLONA:END -->` block and writes exactly one new block.
- [ ] Add a unit test proving uninstall removes a legacy block while preserving unrelated content.
- [ ] Update e2e marker assertions to use the new tags.
- [ ] Run `python3 -B -m unittest tests.test_opencode_installer` and confirm the new/updated tests fail because production still emits/removes only old markers.

### Task 2: Minimal Installer Change

**Files:**
- Modify: `klona_agent/opencode/install.py`

- [ ] Change `BEGIN_MARKER` to `<Klona_Memory>` and `END_MARKER` to `</Klona_Memory>`.
- [ ] Add legacy marker constants for `<!-- KLONA:BEGIN -->` and `<!-- KLONA:END -->`.
- [ ] Update managed-block removal to match both current and legacy marker pairs.
- [ ] Keep snippet content and spacing behavior unchanged.
- [ ] Run `python3 -B -m unittest tests.test_opencode_installer` and confirm it passes.

### Task 3: Verification

**Files:**
- No additional files expected.

- [ ] Run `python3 -B -m unittest discover -s tests`.
- [ ] Run `python3 -B -m compileall -q install_agent.py klona_agent tests memory_server/src/server.py e2e_test/e2e_scenario1.py`.
- [ ] Report modified files, test results, and whether legacy cleanup compatibility was included.
