# KLONA Memory MCP Server

A remote MCP server that exposes a markdown knowledge base as a mounted filesystem. Navigate it with vault tools for tree, listing, reading, writing, updating, deleting, moving, directory creation, search, and backlinks.

This directory is self-contained. Run memory server commands from `KLONA/memory_server/`.

## Quick start

### 1. Configure

```bash
cp .env.example .env
```

Edit `.env` and set a private bearer token:

```text
AUTH_TOKEN=replace-with-a-private-token
```

### 2. Start

```bash
docker compose up -d --build
```

Verify:

```bash
curl http://localhost:32310/health
```

### 3. Connect from OpenCode

From the repository root, run:

```bash
python install_agent.py --platform opencode
```

When prompted, enter the memory MCP URL and the bearer token from `.env`.

For local Docker Compose usage, the MCP URL is usually:

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

## Public safety

Do not commit `memory_server/.env` or vault data. The repository `.gitignore` excludes these paths.
