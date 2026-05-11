# KLONA: memory for agents that should remember you

> Your agent should not wake up every morning with amnesia.

Klona is an inspectable memory layer for agents: a user-owned markdown knowledge base plus a server-side memory agent that makes recall and storage feel consistent across the tools you use.

In Swedish, **klona** means "to clone." The goal is not to clone a person; it is to clone enough of your knowledge, preferences, projects, and intent that AI agents can collaborate with you over time instead of restarting from zero in every session.

The new direction is server-side: Klona should become **your working memory for agents**. The aim is that whether you use Claude, OpenCode, Pi, ChatGPT, Codex, or another platform, the memory behavior comes from the same user-owned server rather than from each platform's separate context silo.

## ✦ The memory layer your agents share

- **Wiki-style knowledge base**: Memory lives in an inspectable markdown vault with ordinary files, directories, and `[[wikilinks]]`.
- **Server-side memory agent**: Local/user-side agents call the same high-level memory tools while the server-side agent owns working context, recall, remember queues, duplicate checks, and memory refinement.
- **Unified cross-platform experience**: The same memory agent is designed to serve multiple agent platforms, so changing clients does not mean changing what your assistant remembers.
- **User-owned memory server**: Memory is your asset, your leverage, and your control plane. The server, vault, tokens, and access boundaries are under your control rather than locked inside one assistant product.

## How Klona works

Klona is split into a high-level user-facing memory agent and a low-level vault server:

1. **User-side/local agents** connect through the `klona_memory` MCP tools:
   - `recall(input: str)` retrieves relevant context synchronously when an agent needs memory.
   - `remember(input: str)` queues candidate memories asynchronously so the current conversation can continue.
2. **High-level `memory-agent`** owns shared working context across calls. It handles synchronous recall, queued async remember, memory-agent reasoning, all normal user-agent MCP traffic, and internal calls to the low-level server.
3. **Low-level `memory-server`** owns and mounts the markdown vault. It exposes direct vault tools only for internal/admin use; in the supported stack, normal agents should not call this endpoint directly.
4. **Mental-model injection** uses `KLONA_MEMORY_MENTAL_MODEL.md` as a fast session-start summary. Where supported, Klona injects that summary into the first message/new session behavior so agents start with a useful model before making explicit recall calls.

The important boundary: normal agents use the high-level endpoint. The low-level vault server is internal/admin infrastructure.

## Quick start

### 1. Start your own agentic memory server

Requirement: Docker with Docker Compose v2.

From the repository root, run:

```bash
python3 scripts/init_memory_stack.py
```

The setup prompts ask for:

- **High-level user-agent MCP host port**: defaults to `32310`.
- **Host markdown vault directory**: defaults to `./vault`.
- **High-level user-agent MCP bearer token**: optional; empty disables auth for the high-level endpoint.
- **High-level allowed hosts**: optional; empty allows all Host headers, while a comma-separated list enables Host header checks.

The script writes `.env`, builds the Docker images, starts the low-level `memory-server`, then runs the high-level `memory-agent`. The `memory-agent` may ask OpenCode auth/model prompts inside the container. Once healthy, the script detaches and leaves the stack running.

Verify the high-level memory-agent health endpoint:

```bash
curl http://localhost:32310/health
```

Default user-facing MCP endpoint for the root stack:

```text
http://localhost:32310/mcp
```

Stop the stack later with:

```bash
docker compose down
```

### 2. Connect your local agent

OpenCode is the currently supported local-agent integration.

Install or refresh the OpenCode integration:

```bash
python install_agent.py --platform opencode
```

When prompted, enter the high-level MCP URL for the default stack:

```text
http://localhost:32310/mcp
```

For the bearer token, use the same high-level token you configured during stack setup. Leave it empty if you left `HIGH_LEVEL_MCP_AUTH_TOKEN` empty.

Non-interactive install:

```bash
python install_agent.py --platform opencode \
  --klona-memory-server-url http://localhost:32310/mcp \
  --klona-memory-server-token '<your-high-level-token>'
```

Uninstall the OpenCode integration:

```bash
python install_agent.py --uninstall --platform opencode
```

Planned future integrations include Claude Code, Codex, Pi, and other agent platforms.

## Low-level/admin caution

In the supported Compose stack, `memory-server` is reachable only inside the Docker network at `http://memory-server:8000/mcp` for internal `memory-agent` use. It is the only service that mounts the markdown vault.

Do not point normal user-side agents at the low-level server. Use the high-level memory-agent endpoint (`http://localhost:32310/mcp` by default) so agents interact through `recall(input: str)` and `remember(input: str)` rather than direct vault read/write/admin tools.

See [`memory_server/README.md`](memory_server/README.md) for direct/admin server details and safety notes.

## Future direction

- Expand platform compatibility beyond OpenCode while keeping one unified memory behavior.
- Improve server-side agent memory refinement so queued memories are deduplicated, organized, and distilled automatically.
