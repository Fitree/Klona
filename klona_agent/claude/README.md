# KLONA Claude Code Integration

This package contains the Claude Code full Klona integration. The root installer routes `--platform claude` here to configure Claude Code with:

- the `klona_memory` MCP server (`recall(input: str)` and `remember(input: str)`),
- Klona memory workflow instructions,
- session mental-model context where Claude Code hooks support it.

## Install or refresh

Interactive install:

```bash
python install_agent.py --platform claude
```

Non-interactive install:

```bash
python install_agent.py --platform claude \
  --klona-memory-server-url http://localhost:32310/mcp \
  --klona-memory-server-token '<your-token>'
```

Pass `--klona-memory-server-token ''` when your Klona MCP server does not require a bearer token.

## Uninstall

```bash
python install_agent.py --uninstall --platform claude
```

Uninstall removes only Klona-owned Claude Code integration files/config and should preserve unrelated Claude Code settings.
