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

## Uninstall OpenCode integration

```bash
python install_agent.py --uninstall --platform opencode
```
