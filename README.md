# KLONA

**KLONA** = **K**nowledge-**L**inked **O**mni **N**eural **A**ssistant.

KLONA is an agent memory system that pairs a markdown-vault MCP server with agent integrations. Phase 1 keeps this repository as a source repository with scripts, not an installable Python package or command-line distribution.

## What is included in Phase 1

- `memory_server/`: a self-contained Python 3.12 MCP server for a markdown knowledge vault.
- `klona_agent/opencode/`: OpenCode integration assets and installer logic.
- `install_agent.py`: root installer router for supported agent platforms.
- `klona_agent/claude/README.md`: notes for future Claude integration.

## Repository layout

```text
KLONA/
  install_agent.py
  memory_server/
  klona_agent/
    opencode/
    claude/
```

## Start the memory server

```bash
cd memory_server
cp .env.example .env
# edit .env and set AUTH_TOKEN to a private value
docker compose up -d --build
```

Verify the server is running:

```bash
curl http://localhost:32310/health
```

The MCP endpoint is typically:

```text
http://localhost:32310/mcp
```

## Install the OpenCode integration

From the repository root:

```bash
python install_agent.py --platform opencode
```

The installer always targets:

```text
~/.config/opencode
```

During install it asks for:

1. the KLONA memory MCP URL, such as `http://localhost:32310/mcp`;
2. the bearer token matching `memory_server/.env`.

The installer manages only KLONA-owned OpenCode pieces:

- a marker-delimited block in `~/.config/opencode/AGENTS.md`;
- `~/.config/opencode/agents/klona-memory.md`;
- `~/.config/opencode/plugins/klona-memory-session.js`;
- `mcp.klona_memory_server` in `~/.config/opencode/opencode.json`.

Re-running the installer refreshes the managed marker block and owned files without duplicating content. Unrelated OpenCode config is preserved.

## Uninstall the OpenCode integration

```bash
python install_agent.py --uninstall --platform opencode
```

Uninstall removes only KLONA-owned OpenCode files, the KLONA marker block, and `mcp.klona_memory_server`. Unrelated user config remains in place.

## Public safety notes

- Source repository histories from the pre-migration projects are not preserved.
- Runtime `.env` files and vault data must not be committed.
- The MCP bearer token is entered at install time and must not be stored in this repository.

## Phase 2 direction

Phase 2 may add packaging, a command-line interface, additional platform installers, or broader agent bundles. Phase 1 intentionally stays focused on source layout, scripts, OpenCode install/uninstall, and the self-contained memory server.
