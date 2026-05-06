# KLONA: Knowledge-Linked Omni Neural Assistant

Your agent should not wake up every morning with amnesia.

Klona is an inspectable memory layer for any AI agent you use. It stores knowledge in a local markdown vault, exposes it through an MCP server, and gives agents a shared way to recall, store, and inject useful context across sessions.

In Swedish, **klona** means "to clone." The spirit of this project is to help agents clone enough of your working knowledge, preferences, projects, and intent to collaborate like long-lived partners instead of stateless chat windows.

## Quickstart

### Run the server-side Klona memory stack

The recommended setup runs two MCP services with Docker Compose:

- `memory-server`: the low-level direct/admin MCP endpoint for vault tools. This is the only service that mounts `HOST_VAULT_DIR`.
- `memory-agent`: the high-level user-agent MCP endpoint exposing `recall(input: str)` and `remember(input: str)`. It stores only queue/state in a named Docker volume and never mounts the vault.

Requirement: Docker with Docker Compose v2.

Start the stack interactively from this repository:

```bash
python3 scripts/init_memory_stack.py
```

The init script asks non-model setup questions first, writes `.env`, builds the images, starts `memory-server` detached, then runs `memory-agent` in the foreground with service ports enabled. This keeps the low-level server away from stdin while surfacing the memory-agent prompts, including `Run OpenCode auth login now? [y/N]`. MCP bearer-token prompts default to empty; an empty token disables auth for that MCP endpoint, while a non-empty token requires `Authorization: Bearer <token>`. OpenCode auth, model selection, and reasoning-effort selection happen later inside the final `memory-agent` container so choices match the runtime environment. When the foreground memory-agent exits or is interrupted, the init script stops the detached `memory-server` container.

Verify the services are running:

```bash
curl http://localhost:32310/health  # low-level memory-server
curl http://localhost:32311/health  # high-level memory-agent
```

Default MCP endpoints:

```text
http://localhost:32310/mcp  low-level direct/admin vault tools
http://localhost:32311/mcp  high-level user-agent recall/remember tools
```

Use the high-level endpoint for normal agents. Keep the low-level endpoint for trusted admin/direct clients only.

See [`memory_server/README.md`](memory_server/README.md) for standalone low-level server configuration, vault format, MCP tools, and public safety notes.

### Agent integration

Klona agents are platform-specific integrations designed to use the Klona MCP server as a complete memory workflow: recall when context is missing, store durable knowledge, and inject useful memory into future sessions.

#### OpenCode

Requirements: Python 3 and OpenCode.

Install the OpenCode integration:

```bash
python install_agent.py --platform opencode
```

The installer asks for the high-level Klona memory MCP URL and bearer token, then writes the OpenCode integration to `~/.config/opencode`. For the default stack, use `http://localhost:32311/mcp` and `HIGH_LEVEL_MCP_AUTH_TOKEN` from `.env`; leave the installer token empty if the high-level token is empty.

For non-interactive installs, pass the MCP URL and bearer token as dashed arguments:

```bash
python install_agent.py --platform opencode --klona-memory-server-url {your-high-level-klona-mcp-url} --klona-memory-server-token {your-high-level-klona-mcp-token}
```

Uninstall the OpenCode integration:

```bash
python install_agent.py --uninstall --platform opencode
```

## How Klona works

Klona has a few small pieces that work together:

1. **Markdown vault**: Your memory is stored as ordinary markdown files. The directory tree is the index, files can link to each other with `[[wikilinks]]`, and `KLONA_MEMORY_MENTAL_MODEL.md` is a special summary file intended for fast session-start context.
2. **Low-level MCP memory server**: `memory_server/` exposes the vault through direct/admin MCP tools for tree/list/read/write/update/delete/move/search/backlinks operations. It is the only container with the vault mount.
3. **High-level memory-agent**: `memory_agent/` exposes `recall(input)` and `remember(input)` for user-side agents. `recall` waits for the server-side memory agent to retrieve context; `remember` acknowledges after queue insert and is processed asynchronously.
4. **OpenCode integration**: The installer adds Klona-managed OpenCode files and MCP configuration under `~/.config/opencode`, pointing normal agents at the high-level MCP endpoint.
5. **`KLONA_MEMORY_MENTAL_MODEL.md` injection**: The OpenCode plugin reads `/KLONA_MEMORY_MENTAL_MODEL.md` through the configured MCP server and prepends it to the first user message of a root OpenCode session. It also marks sessions for reinjection after compaction.

The result is a memory loop where user-side agents use simple recall/remember tools while the server-side memory-agent owns vault navigation, storage gating, duplicate checks, and working continuity.

### Auth and model flow

- Low-level and high-level MCP endpoints use separate bearer tokens and allowed-host settings. Empty tokens disable auth for the corresponding endpoint; non-empty tokens require exact Bearer auth.
- The memory-agent receives the low-level URL/token over the internal Compose network.
- During first attached startup, the memory-agent container asks whether to run `opencode auth login`. If auth fails, it asks whether to retry, proceed without auth, or terminate.
- After auth/proceed, the container discovers available OpenCode models and asks for model and reasoning-effort choices inside the final runtime container.

### Non-goals for this architecture

- No dashboard/UI.
- No direct vault mount in `memory-agent`.
- No named Docker volume for OpenCode auth/session/cache; only memory-agent queue/state is persisted.
- No automated test requires real OpenCode/GPT auth.

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
