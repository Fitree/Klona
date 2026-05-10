# KLONA Memory MCP Server

A remote MCP server that exposes a markdown knowledge base as a mounted filesystem. Navigate it with vault tools for tree, listing, reading, writing, updating, deleting, moving, directory creation, search, and backlinks.

This is the low-level direct/admin server. In the full server-side KLONA stack, it is internal-only on the Docker Compose network by default; normal user-side agents should connect to the high-level `memory-agent` endpoint instead of this vault endpoint.

Run the supported Docker Compose stack from the repository root. This directory contains the low-level server implementation and Docker image only; it intentionally does not carry a separate Compose file or env example.

## Quick start

### 1. Start the full stack

```bash
python3 scripts/init_memory_stack.py
```

Verify:

```bash
curl http://localhost:32310/health
```

This starts the root Compose stack with `memory-server` internal-only and `memory-agent` published as the high-level MCP endpoint.

### 2. Connect a trusted direct/admin MCP client

The low-level server is for trusted admin/direct MCP clients that intentionally use the vault tools listed below. In the supported full stack, this endpoint is not published to the host; it is reachable inside the Docker Compose network at `http://memory-server:8000/mcp` for `memory-agent` internal use.

Do not use the normal root KLONA OpenCode installer for this standalone endpoint. That installer configures the full-stack high-level KLONA integration, where agents call `recall(input: str)` and `remember(input: str)` through `memory-agent` instead of direct vault/admin tools.

For rare local admin/debug use where you intentionally expose the low-level endpoint, run a one-off container from the repository root with an explicit vault mount, localhost-only port binding, and a private bearer token:

```bash
docker build -t klona-memory-server ./memory_server
docker run --rm -p 127.0.0.1:32311:8000 \
  -v "$PWD/vault:/vault" \
  -e VAULT_DIR=/vault \
  -e AUTH_TOKEN='<private-token>' \
  klona-memory-server
```

Leave `AUTH_TOKEN` empty only for intentionally isolated local debugging.

The direct/admin MCP URL for that one-off container is:

```text
http://localhost:32311/mcp
```

## Tools

| Tool | Purpose |
|------|---------|
| `vault_tree()` | Directory structure without files. |
| `vault_ls(path)` | List files and subdirectories at a path. |
| `vault_read(path)` | Read file content and parsed wikilinks. |
| `vault_write(path, content)` | Create a new file. |
| `vault_update(path, content)` | Update an existing file. |
| `vault_delete(path)` | Delete a file. |
| `vault_move(src, dst)` | Move or rename a file. |
| `vault_mkdir(path)` | Create a directory. |
| `vault_grep(pattern, path)` | Search content by keyword. |
| `vault_backlinks(path)` | Find files that link to a file. |

## Vault structure

Files are organized hierarchically. The directory structure is the index.

```text
/vault/
  people/
  projects/
  decisions/
  facts/
    tech/
    preferences/
  feedback/
  events/
```

## File format

Markdown files use one server-managed frontmatter field:

```markdown
---
updated: 2026-04-05T15:30:00Z
---

Content here. Link to related notes with [[wikilinks]].
```

- `updated` is set automatically on every write or update.
- Filenames are descriptive, slugified titles. No UUIDs.
- Filenames must be globally unique across the vault.
- `[[wikilinks]]` create the knowledge graph.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `AUTH_TOKEN` | empty | Bearer token. Empty disables auth. Use a private value for normal use. |
| `ALLOWED_HOSTS` | empty | Optional comma-separated Host allowlist for DNS rebinding protection. |
| `VAULT_DIR` | `/vault` | Container path to the mounted vault directory. |

## Full server-side stack

From the repository root, use `python3 scripts/init_memory_stack.py` or copy the root `.env.example` to `.env` and run `docker compose up --build`. The root Compose file runs this service as `memory-server` and a separate high-level `memory-agent` service. Only `memory-server` mounts `HOST_VAULT_DIR`; `memory-agent` persists only queue/state in a named Docker volume. The root Compose stack does not publish this low-level service to the host; `memory-agent` reaches it internally at `http://memory-server:8000/mcp`.

## Public safety

Do not commit `.env` or vault data. The repository `.gitignore` excludes these paths.
