# KLONA: memory for agents that should remember you

> Your agent should not wake up every morning with amnesia.

Klona is an inspectable memory layer for agents: a user-owned markdown knowledge base plus a server-side memory agent that makes recall and storage feel consistent across the tools you use.

In Swedish, **klona** means "to clone." The goal is not to clone a person; it is to clone enough of your knowledge, preferences, projects, and intent that AI agents can collaborate with you over time instead of restarting from zero in every session.

The new direction is server-side: Klona should become **your working memory for agents**. The aim is that whether you use Claude, OpenCode, Pi, ChatGPT, Codex, or another platform, the memory behavior comes from the same user-owned server rather than from each platform's separate context silo.

## ✦ The memory layer your agents share

- **Wiki-style knowledge base**: Memory lives in an inspectable markdown vault with ordinary files, directories, and `[[wikilinks]]`.
- **Server-side memory agent**: Local/user-side agents call your Klona MCP server, while the server-side agent owns working context, recall, remember queues, duplicate checks, and memory refinement.
- **REM sleep maintenance**: Automatic or manual REM sleep jobs give the vault a deeper maintenance pass, keeping recently captured memories organized and useful.
- **Dashboard**: `/dashboard` lets you inspect recall, remember, and REM sleep queues, trigger manual REM sleep jobs, and remove pending REM sleep jobs.
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

- **Klona MCP host port**: the host port where your agents connect to Klona. The default is `32310`, so local agents use `http://localhost:32310/mcp` and remote agents can use `http://<server-host-or-ip>:32310/mcp` when your network/firewall permits it.
- **Host markdown vault directory**: the folder on your machine where memory is stored as markdown. The default is `./vault`.
- **Klona MCP bearer token**: optional authentication token for the MCP server. Empty means no token is configured. When set, the MCP server requires this token for authentication. Set a private token before exposing Klona beyond localhost.
- **Allowed hosts**: optional Host-header allowlist shared by the MCP endpoint and `/dashboard`. Empty allows all Host headers. For external deployments, prefer a narrow allowlist and include every host clients will use, such as `localhost,127.0.0.1,klona.example.com,klona.example.com:32310,203.0.113.10,203.0.113.10:32310`.
- **OpenCode auth/model prompts**: the server-side memory agent currently runs through OpenCode, so the setup may ask whether to run OpenCode auth login and which model/reasoning effort to use.
- **REM sleep settings**: optional automatic vault-maintenance enqueueing after successful `remember` jobs. `KLONA_REM_SLEEP_REMEMBER_THRESHOLD=20` by default; set the threshold to `0` or less, or disable `KLONA_REM_SLEEP_ENABLED`, to turn off automatic REM sleep. Manual REM sleep from `/dashboard` still works.

The script writes `.env`, builds the Docker images, starts the Klona MCP server, and runs the server-side memory agent. The memory agent may ask OpenCode auth/model prompts inside the container. Once healthy, the script detaches and leaves the stack running.

Verify the health endpoint:

```bash
curl http://localhost:32310/health
```

Inspect the FIFO queue dashboard, including pending `recall`, `remember`, and `rem-sleep` jobs:

```text
http://localhost:32310/dashboard
```

The dashboard is dependency-free server-rendered HTML intended for local/admin use. Compose publishes the high-level MCP service on the configured host port. `/dashboard` uses the same `HIGH_LEVEL_ALLOWED_HOSTS` semantics as the MCP endpoint: empty allows all Host headers, while a non-empty value allows exact listed Host headers; use `host:port` for an exact port or `host:*` for any port on that host. For external deployments, set a private `HIGH_LEVEL_MCP_AUTH_TOKEN` and prefer a narrow `HIGH_LEVEL_ALLOWED_HOSTS` allowlist. It can append a manual REM sleep job to the end of the FIFO queue and delete only pending, not-started REM sleep jobs. Queue inputs are truncated in the table for safer inspection.

If `HIGH_LEVEL_MCP_AUTH_TOKEN` is set, `/dashboard` first shows a browser token login form. A successful login stores an HttpOnly, SameSite dashboard session cookie derived from the configured token rather than the raw token. If no high-level token is configured, the local/admin dashboard remains usable and shows that dashboard auth is not configured.

Default MCP endpoint for the root stack:

```text
http://localhost:32310/mcp
```

Stop the stack later with:

```bash
docker compose down
```

### 2. Connect your local agent

Choose one of two connection paths based on what gets installed in the local agent.

#### MCP-only connection

Use this path with any MCP-capable local agent or client that can connect to your Klona MCP server endpoint. Connect it to:

```text
http://localhost:32310/mcp
```

The MCP tools are:

- `recall(input: str)`
- `remember(input: str)`

If you configured a bearer token during stack setup, set the same token in your MCP client. If the Klona MCP server has no token configured, the MCP client sends no bearer token.

This exposes only the `recall` and `remember` MCP tools to the agent. It does not install any platform-specific Klona instructions, system prompt updates, or plugins.

#### Full Klona integration

Use this path to install the complete local-agent integration: MCP config plus Klona-managed instructions/system prompt and the mental-model plugin. OpenCode is currently the only supported full integration.

Install or refresh the OpenCode integration:

```bash
python install_agent.py --platform opencode
```

When prompted, enter the Klona MCP URL for the default stack:

```text
http://localhost:32310/mcp
```

For the bearer token, set the same Klona MCP token you configured during stack setup. If the Klona MCP server has no token configured, the OpenCode MCP config is written without a bearer token.

Non-interactive install for the default stack:

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
- Add a knowledge graph dashboard for exploring the markdown memory vault visually.
