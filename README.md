# KLONA

KLONA is an agent memory system that pairs a markdown-vault MCP server with agent integrations.

## Start memory server

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

## Install OpenCode integration

From the repository root:

```bash
python install_agent.py --platform opencode
```

The installer asks for the KLONA memory MCP URL and bearer token, then writes the OpenCode integration to `~/.config/opencode`.

For non-interactive installs, pass the MCP URL and bearer token as dashed arguments:

```bash
python install_agent.py --platform opencode --klona-memory-server-url http://localhost:32310/mcp --klona-memory-server-token your-private-token
```

## Uninstall OpenCode integration

```bash
python install_agent.py --uninstall --platform opencode
```

## Run E2E tests

The E2E runner starts isolated Docker Compose projects for each scenario and runs them one by one. Each scenario seeds a fresh runtime vault volume from `e2e_test/test_vault` with a one-shot `vault-seeder` container, starts the memory server on that initialized volume, then runs a test container that can access the vault only through MCP. Scenario 1 installs the actual OpenCode integration, runs `opencode run` against a fake OpenAI-compatible provider, verifies the KLONA mental model is injected into the model request, uninstalls KLONA, and cleans up automatically. Scenario 2 calls the memory MCP tools and auth/health endpoints directly.

Requires Docker Compose v2 and a running Docker daemon.

```bash
e2e_test/run_e2e.sh
```
