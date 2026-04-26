# KLONA: Knowledge-Linked Omni Neural Assistant

Your agent should not wake up every morning with amnesia.

Klona is an inspectable memory layer for any AI agent you use. It stores knowledge in a local markdown vault, exposes it through an MCP server, and gives agents a shared way to recall, store, and inject useful context across sessions.

In Swedish, **klona** means "to clone." The spirit of this project is to help agents clone enough of your working knowledge, preferences, projects, and intent to collaborate like long-lived partners instead of stateless chat windows.

## Quickstart

### Run the Klona MCP server

The Klona MCP server exposes your markdown vault through the MCP protocol. Any platform that supports MCP can connect to it directly, even without a Klona-specific agent integration.

Requirement: Docker with Docker Compose v2.

Start the server from this repository:

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

See [`memory_server/README.md`](memory_server/README.md) for memory server configuration, vault format, MCP tools, and public safety notes.

### Agent integration

Klona agents are platform-specific integrations designed to use the Klona MCP server as a complete memory workflow: recall when context is missing, store durable knowledge, and inject useful memory into future sessions.

#### OpenCode

Requirements: Python 3 and OpenCode.

Install the OpenCode integration:

```bash
python install_agent.py --platform opencode
```

The installer asks for the Klona memory MCP URL and bearer token, then writes the OpenCode integration to `~/.config/opencode`.

For non-interactive installs, pass the MCP URL and bearer token as dashed arguments:

```bash
python install_agent.py --platform opencode --klona-memory-server-url {your-klona-mcp-server-url} --klona-memory-server-token {your-klona-mcp-server-token}
```

Uninstall the OpenCode integration:

```bash
python install_agent.py --uninstall --platform opencode
```

## How Klona works

Klona has a few small pieces that work together:

1. **Markdown vault**: Your memory is stored as ordinary markdown files. The directory tree is the index, files can link to each other with `[[wikilinks]]`, and `MENTAL_MODEL.md` is a special summary file intended for fast session-start context.
2. **MCP memory server**: `memory_server/` exposes the vault through MCP tools for tree/list/read/write/update/delete/move/search/backlinks operations.
3. **`klona-memory` subagent**: The OpenCode integration installs a dedicated memory specialist agent that handles recall and storage requests through the MCP server.
4. **OpenCode integration**: The installer adds Klona-managed OpenCode files and MCP configuration under `~/.config/opencode`.
5. **`MENTAL_MODEL.md` injection**: The OpenCode plugin reads `/MENTAL_MODEL.md` from the vault and prepends it to the first user message of a root OpenCode session. It also marks sessions for reinjection after compaction.

The result is a memory loop where the agent can retrieve durable knowledge from the vault, update the vault when something is worth remembering, and start later sessions with a compact mental model already in context.

## Progress and roadmap

Klona is early, but the core memory loop is already working.

Done:
- [x] Markdown vault MCP server.
- [x] OpenCode agent integration.

Next:
- [ ] Claude Code agent integration.
- [ ] Codex agent integration.
- [ ] Vault auto-maintenance agent.
- [ ] Knowledge graph dashboard.
