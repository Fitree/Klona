# KLONA: memory for agents that should remember you

> Your agent should not wake up every morning with amnesia.

Klona is an inspectable memory layer for agents: a user-owned markdown knowledge base plus a server-side memory agent that makes recall and storage feel consistent across the tools you use.

In Swedish, **klona** means "to clone." The goal is not to clone a person; it is to clone enough of your knowledge, preferences, projects, and intent that AI agents can collaborate with you over time instead of restarting from zero in every session.

The new direction is server-side: Klona should become **your working memory for agents**. The aim is that whether you use Claude, OpenCode, Pi, ChatGPT, Codex, or another platform, the memory behavior comes from the same user-owned server rather than from each platform's separate context silo.

## ✦ The memory layer your agents share

- **Wiki-style knowledge base**: Memory lives in an inspectable markdown vault with ordinary files, directories, and `[[wikilinks]]`.
- **Server-side memory agent**: Local/user-side agents call your Klona MCP server, while the server-side agent owns working context, recall, remember queues, duplicate checks, and memory refinement.
- **Unified cross-platform experience**: The same memory agent is designed to serve multiple agent platforms, so changing clients does not mean changing what your assistant remembers.
- **User-owned memory server**: Memory is your asset, your leverage, and your control plane. The server, vault, tokens, and access boundaries are under your control rather than locked inside one assistant product.

## How Klona works

Klona has four user-facing pieces working together:

1. **User-side/local agents** connect to your user-owned Klona MCP server through the `klona_memory` tools:
   - `recall(input: str)` retrieves relevant context synchronously when an agent needs memory.
   - `remember(input: str)` queues candidate memories asynchronously so the current conversation can continue.
2. **The Klona MCP server runs the server-side memory agent**. The server-side agent keeps shared working memory across calls, handles recall immediately, processes remember requests in the background, and refines memories before they are written.
3. **Mental-model injection** uses `KLONA_MEMORY_MENTAL_MODEL.md` as a fast session-start summary. Where supported, Klona injects that summary into the first message/new session behavior so agents start with a useful model before making explicit recall calls.
4. **Inspectable markdown persistence** keeps durable memory in a vault you own, with ordinary files, directories, and `[[wikilinks]]`.

The important boundary: normal agents connect to your Klona MCP server. The detailed vault service behind it is implementation/admin infrastructure, not the user-facing architecture.

## Quick start

### 1. Start your own agentic memory server

Requirement: Docker with Docker Compose v2.

From the repository root, run the setup script and follow its instructions:

```bash
python3 scripts/init_memory_stack.py
```

During setup you will see a few prompts:

- **Klona MCP host port**: the localhost port where your agents connect to Klona. The default is `32310`, so the MCP URL becomes `http://localhost:32310/mcp`.
- **Host markdown vault directory**: the folder on your machine where memory is stored as markdown. The default is `./vault`.
- **Klona MCP bearer token**: an optional password for the MCP server. Leave it empty for local unauthenticated use; set a private value if other clients or machines may connect.
- **Allowed hosts**: optional Host-header allowlist. Leave it empty for simple local setup; set a comma-separated list such as `localhost,127.0.0.1` if you want Host header checks.
- **OpenCode auth/model prompts**: the server-side memory agent currently runs through OpenCode, so the setup may ask whether to run OpenCode auth login and which model/reasoning effort to use.

The script writes `.env`, builds the Docker images, starts the Klona MCP server, and runs the server-side memory agent. The memory agent may ask OpenCode auth/model prompts inside the container. Once healthy, the script detaches and leaves the stack running.

Verify the health endpoint:

```bash
curl http://localhost:32310/health
```

Default MCP endpoint for the root stack:

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

When prompted, enter the Klona MCP URL for the default stack:

```text
http://localhost:32310/mcp
```

For the bearer token, use the same Klona MCP token you configured during stack setup. Leave it empty if you left the token empty.

Non-interactive install:

```bash
python install_agent.py --platform opencode \
  --klona-memory-server-url http://localhost:32310/mcp \
  --klona-memory-server-token '<your-token>'
```

Uninstall the OpenCode integration:

```bash
python install_agent.py --uninstall --platform opencode
```

Planned future integrations include Claude Code, Codex, Pi, and other agent platforms.

## Advanced implementation note

In the supported Compose stack, an internal vault service is reachable only inside the Docker network for server-side memory-agent use. It is the only service that mounts the markdown vault.

Do not point normal user-side agents at internal/admin services. Use your Klona MCP server (`http://localhost:32310/mcp` by default) so agents interact through `recall(input: str)` and `remember(input: str)`.

See [`memory_server/README.md`](memory_server/README.md) for direct/admin implementation details and safety notes.

## Future direction

- Expand platform compatibility beyond OpenCode while keeping one unified memory behavior.
- Improve server-side agent memory refinement so queued memories are deduplicated, organized, and distilled automatically.
