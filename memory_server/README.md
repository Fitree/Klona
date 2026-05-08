# KLONA Memory MCP Server

A remote MCP server that exposes a markdown knowledge base as a mounted filesystem. Navigate it with vault tools for tree, listing, reading, writing, updating, deleting, moving, directory creation, search, and backlinks.

This is the low-level direct/admin server. In the full server-side KLONA stack, it is internal-only on the Docker Compose network by default; normal user-side agents should connect to the high-level `memory-agent` endpoint instead of this vault endpoint.

This directory is self-contained. Run memory server commands from `KLONA/memory_server/`.

## Quick start

### 1. Configure

```bash
cp .env.example .env
```

Edit `.env` and optionally set a private bearer token. Empty `AUTH_TOKEN` disables auth; a non-empty value requires `Authorization: Bearer <token>`.

```text
AUTH_TOKEN=
```

### 2. Start

```bash
docker compose up -d --build
```

Verify:

```bash
curl http://localhost:32310/health
```

### 3. Connect a trusted direct/admin MCP client

The standalone low-level server is for trusted admin/direct MCP clients that intentionally use the vault tools listed below. Configure those clients manually for the low-level MCP endpoint and bearer token from `.env`; leave the client token empty if `AUTH_TOKEN` is empty.

Do not use the normal root KLONA OpenCode installer for this standalone endpoint. That installer configures the full-stack high-level KLONA integration, where agents call `recall(input: str)` and `remember(input: str)` through `memory-agent` instead of direct vault/admin tools.

For standalone local Docker Compose usage, the low-level MCP URL is usually:

```text
http://localhost:32310/mcp
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
| `HOST_VAULT_DIR` | `./vault` | Host path to vault directory mounted into the container. |
| `ALLOWED_HOSTS` | empty | Optional comma-separated Host allowlist for DNS rebinding protection. |

## Full server-side stack

From the repository root, use `python3 scripts/init_memory_stack.py` or copy `.env.example` to `.env` and run `docker compose up --build`. The root Compose file runs this service as `memory-server` and a separate high-level `memory-agent` service. Only `memory-server` mounts `HOST_VAULT_DIR`; `memory-agent` persists only queue/state in a named Docker volume. The root Compose stack does not publish this low-level service to the host; `memory-agent` reaches it internally at `http://memory-server:8000/mcp`.

## Public safety

Do not commit `memory_server/.env` or vault data. The repository `.gitignore` excludes these paths.
