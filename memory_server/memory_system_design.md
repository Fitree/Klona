# Klona Memory System Design

## Overview

A remote MCP server that exposes a markdown knowledge base as a mounted filesystem. The KLONA memory agent navigates it intelligently from supported agent clients. The server is dumb — it provides file operations, locking, and automatic metadata. The agent is the brain — it decides what to store, how to connect, and where to look.

## Architecture

```
Agent client (A)  ──┐
Agent client (B)  ──┼──→  Remote MCP Server  ──→  Knowledge Base
Agent client (C)  ──┘     (filesystem API)        (markdown + wikilinks)
                         (read-write lock)
                         (auto frontmatter)
```

- **Server**: Dumb filesystem API with concurrency control. Runs in a Docker container. No search engine, no index files, no AI.
- **KLONA memory agent**: Lives in each supported agent client. Navigates hierarchy, follows wikilinks, decides what to store/recall/update.
- **Storage**: Markdown files with minimal frontmatter and `[[wikilinks]]`. Organized in a hierarchical directory structure. Vault directory is a Docker volume mount.

## MCP Tools

| Tool | Purpose | Lock |
|------|---------|------|
| `vault_tree()` | Directory structure only (no files) | Read |
| `vault_ls(path)` | Files and subdirs at path | Read |
| `vault_read(path)` | Read file content | Read |
| `vault_write(path, content)` | Create new file (fails if exists) | Write |
| `vault_update(path, content)` | Update existing file (fails if not exists) | Write |
| `vault_delete(path)` | Delete file | Write |
| `vault_move(src, dst)` | Move or rename file | Write |
| `vault_mkdir(path)` | Create directory | Write |
| `vault_grep(pattern, path)` | Search content within scoped path | Read |
| `vault_backlinks(path)` | Find files that `[[link]]` to this file | Read |

### `vault_grep` Output Format

Returns file paths with matching lines and line numbers:

```json
[
  {
    "path": "/facts/preferences/dark-mode.md",
    "matches": [
      {"line": 3, "text": "User prefers dark mode across all editors"}
    ]
  },
  {
    "path": "/people/leo.md",
    "matches": [
      {"line": 12, "text": "Also uses [[dark-mode]] in all terminals"}
    ]
  }
]
```

The KLONA memory agent uses the matching lines to decide which files are worth reading in full.

## Vault Structure

```
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

- No index files. The directory hierarchy IS the index.
- Each directory should stay under ~50 files. When it grows, split into sub-directories.
- The KLONA memory agent navigates by calling `vault_tree()` for orientation, then drilling down with `vault_ls()` and `vault_read()`.

## File Format

### Frontmatter

One field, server-managed:

```yaml
---
updated: 2026-04-05T15:30:00Z
---
```

- `updated` is set automatically by the server on every `vault_write()` and `vault_update()`.
- The agent never manages this field.
- `vault_move()` preserves the existing `updated` value.

### Filenames

Descriptive, slugified. No UUIDs. The filename should be the most useful identifier for that category.

```
facts/tech/python-preferred-for-backend.md
decisions/use-fastmcp.md
events/2026-04-05-replaced-openmemory.md
people/leo.md
```

- **Events**: Date prefix is useful — `vault_ls("events/")` shows chronological order.
- **Facts, decisions, people**: Title only — date is in frontmatter, not needed in filename.
- The filename IS the summary. Directory IS the type.

### Wikilinks

Notes link to related notes using `[[wikilinks]]`:

```markdown
---
updated: 2026-04-05T15:30:00Z
---

Klona Memory MCP Server uses FastMCP with markdown-based storage.
Replaced [[replaced-openmemory]] on 2026-04-05.
Tech stack: [[python-preferred-for-backend]], [[use-fastmcp]].
```

- Links create the knowledge graph.
- Hierarchy provides categorical navigation (tree).
- Wikilinks provide associative navigation (graph).
- `vault_backlinks()` enables reverse traversal.

### Broken Links

A broken link is a `[[wikilink]]` that points to a file that doesn't exist.

**On `vault_read`**: The server returns the file content as-is. Broken links are not validated on read — the KLONA memory agent follows them and handles the "not found" error naturally.

**On `vault_write`**: The server accepts the write but validates all `[[wikilinks]]` in the content. If any link targets don't exist, the response includes warnings:

```json
{
  "status": "ok",
  "warnings": [
    {"type": "broken_link", "target": "nonexistent-note", "line": 5}
  ]
}
```

The write is NOT rejected — the KLONA memory agent may be creating linked notes in sequence (write A with `[[B]]`, then write B). Rejecting would force the agent to figure out write order. Instead, the KLONA memory agent can fix broken links immediately or leave them for the maintenance agent.

**On maintenance**: The nightly agent scans for all broken links and either removes them, updates them, or creates the missing target notes.

### Duplicate Filenames

Filenames must be globally unique across the entire vault. Two files with the same name in different directories would make `[[wikilinks]]` ambiguous.

**On `vault_write`**: If a file with the same name already exists at a different path, the server **rejects the write** with an error:

```json
{
  "status": "error",
  "error": "duplicate_filename",
  "message": "Filename 'use-fastmcp.md' already exists at 'decisions/use-fastmcp.md'"
}
```

The KLONA memory agent must choose a more descriptive, unique filename before retrying.

### Backlinks Implementation

`vault_backlinks(path)` returns all files that contain a `[[wikilink]]` pointing to the given path.

**Performance**: Scanning every file on each call is O(N). At scale this is too slow. The server maintains an **in-memory backlink cache**.

**Cache design**:
- **On startup**: Full scan of all `.md` files. Parse `[[wikilinks]]`, build a map: `target → set of source files`. O(N) once.
- **On `vault_write`/`vault_update`**: Parse old content (if overwriting) to remove stale entries. Parse new content to add new entries. O(links in file).
- **On `vault_delete`**: Remove all entries where the deleted file is a source. O(links in file).
- **On `vault_move`**: Update entries for old path → new path. O(links in file).
- **On `vault_backlinks` call**: Look up the map. O(1).

The server is the single writer, so the cache is always consistent — no external mutations to worry about.

## Concurrency

In-process async read-write lock:

- **Read operations** (`vault_tree`, `vault_ls`, `vault_read`, `vault_grep`, `vault_backlinks`): Acquire read lock. Multiple concurrent readers allowed.
- **Write operations** (`vault_write`, `vault_update`, `vault_delete`, `vault_move`, `vault_mkdir`): Acquire write lock. Exclusive — blocks all other operations.
- **Maintenance swap**: Write lock held during vault replacement.

All file writes are atomic (temp file + `os.rename()`).

## Nightly Maintenance

Self-evolving vault management run by a scheduled KLONA maintenance agent.

### Trigger

Time-based + count-based. Example: run at 2:00 AM if there have been 10+ writes since the last maintenance.

### Process

1. Acquire write lock on the MCP server (lock all methods).
2. Copy the entire vault to a working directory.
3. Release write lock (MCP server resumes normal operation on the original vault).
4. The maintenance agent session polishes the copied vault:
   - Merge near-duplicate notes.
   - Remove outdated or conflicting information.
   - Add missing `[[wikilinks]]` between related notes.
   - Split overgrown directories into sub-categories.
   - Validate directory and filename consistency.
5. When done, acquire write lock again.
6. Atomic swap: `vault/` → `vault.old/`, `vault.new/` → `vault/`.
7. Release write lock.

### Safety

- The live vault is never directly mutated by the maintenance agent.
- During the swap (step 5-7), all MCP methods are locked.
- `vault.old/` is kept as a backup until the next maintenance run.

## KLONA Memory Agent Behavior

### Session Start

The KLONA memory agent calls `vault_tree()` to get the directory structure. This is the map of the knowledge base — categories and their sub-categories. The KLONA memory agent holds this in session context.

### Recall

Three navigation modes: **hierarchical** (tree/ls), **content search** (grep), **graph traversal** (read links + backlinks).

**When the KLONA memory agent knows where to look** — navigate the hierarchy:
```
The KLONA memory agent wants: "What tech stack for klona?"

1. vault_tree()       → sees projects/, decisions/, facts/tech/
2. vault_ls("projects/") → sees klona-memory.md
3. vault_read("projects/klona-memory.md") → content + [[wikilinks]]
4. vault_read("decisions/use-fastmcp.md") → follows outgoing link
5. vault_backlinks("projects/klona-memory.md") → discovers notes that reference this one
```

**When the KLONA memory agent doesn't know where to look** — grep then navigate:
```
The KLONA memory agent wants: "What do I know about dark mode?"

1. vault_tree()       → no obvious category
2. vault_grep("dark mode", "/") → finds facts/preferences/dark-mode.md
3. vault_read("facts/preferences/dark-mode.md") → content + [[wikilinks]]
4. vault_backlinks("facts/preferences/dark-mode.md") → related notes
```

The KLONA memory agent decides which mode to use based on the query. Hierarchy-first when the topic maps to a known category. Grep as fallback when it doesn't.

### Store

```
The KLONA memory agent wants to store: "Leo prefers dark mode"

1. vault_tree()       → sees facts/preferences/
2. vault_ls("facts/preferences/") → check for existing similar notes
3. vault_write("facts/preferences/dark-mode.md", content_with_links)
```

The KLONA memory agent decides the path, the filename, the wikilinks. Server just writes and stamps `updated`.

### Update

```
The KLONA memory agent finds outdated info in a note:

1. vault_read("facts/tech/python-preferred-for-backend.md")
2. vault_update("facts/tech/python-preferred-for-backend.md", updated_content)
```

Server auto-bumps `updated` in frontmatter.

## MCP Server Specification

### Server Info

```
Name:    klona-memory-server
Version: 0.2.0
Transport: streamable-http
Internal container endpoint: http://<container-host>:8000/mcp
Local Docker Compose endpoint: http://localhost:32310/mcp
Auth: Bearer token (via Authorization header)
```

### Server Instructions (returned on initialize)

The MCP server returns these instructions to every connecting client:

```
You are connected to the Klona Memory Server — a markdown knowledge base.

The vault is a directory of markdown files organized hierarchically.
Navigate it like a filesystem: tree → ls → read → follow [[wikilinks]].

Tools:
  vault_tree      — get the directory structure (start here)
  vault_ls        — list files in a directory
  vault_read      — read a file
  vault_write     — create a new file (fails if exists)
  vault_update    — update an existing file (fails if not exists)
  vault_delete    — delete a file
  vault_move      — move or rename a file
  vault_mkdir     — create a directory
  vault_grep      — search file contents by keyword
  vault_backlinks — find files that link to a given file

The server auto-manages the `updated` frontmatter field on writes.
Filenames must be globally unique across the vault.
All API paths start with `/` and are relative to the vault root.
```

### Tool Documentation

---

#### `vault_tree`

Returns the complete directory structure of the vault. No files — directories only.

**Parameters**: None

**Returns**:
```json
{
  "people": {},
  "projects": {},
  "decisions": {},
  "facts": {
    "tech": {},
    "preferences": {}
  },
  "feedback": {},
  "events": {
    "2026": {}
  }
}
```

**Errors**: None (always succeeds).

**Lock**: Read

---

#### `vault_ls`

Lists files and immediate subdirectories at the given path.

**Parameters**:
| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `path` | string | Yes | Directory path relative to vault root. Use `"/"` for root. |

**Returns**:
```json
{
  "path": "/projects/",
  "dirs": ["/projects/klona/"],
  "files": [
    {"name": "klona-memory.md", "updated": "2026-04-05T15:30:00Z"},
    {"name": "leo-terraform.md", "updated": "2026-04-03T10:00:00Z"}
  ]
}
```

Files include the `updated` timestamp from frontmatter so the KLONA memory agent can see recency without reading the file.

**Errors**:
- `path_not_found` — directory does not exist

**Lock**: Read

---

#### `vault_read`

Reads the full content of a markdown file.

**Parameters**:
| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `path` | string | Yes | File path relative to vault root. |

**Returns**:
```json
{
  "path": "/projects/klona-memory.md",
  "content": "---\nupdated: 2026-04-05T15:30:00Z\n---\n\nKlona Memory MCP Server uses...",
  "links": ["replaced-openmemory", "python-preferred-for-backend", "use-fastmcp"]
}
```

`links` is a convenience field — the server parses `[[wikilinks]]` from the content and returns them as a list. Saves the KLONA memory agent from parsing markdown.

**Errors**:
- `file_not_found` — file does not exist

**Lock**: Read

---

#### `vault_write`

Creates a new file. Fails if the file already exists — use `vault_update` to modify existing files. The server creates parent directories automatically, atomically writes the file, and auto-sets the `updated` frontmatter field to the current UTC time.

**Parameters**:
| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `path` | string | Yes | File path relative to vault root. Parent directories are created automatically. |
| `content` | string | Yes | Markdown content. May include `[[wikilinks]]`. Frontmatter is optional — server adds/updates `updated` field automatically. |

**Returns** (success):
```json
{
  "status": "ok",
  "path": "/facts/preferences/dark-mode.md",
  "updated": "2026-04-05T16:00:00Z",
  "warnings": []
}
```

**Returns** (success with broken links):
```json
{
  "status": "ok",
  "path": "/facts/preferences/dark-mode.md",
  "updated": "2026-04-05T16:00:00Z",
  "warnings": [
    {"type": "broken_link", "target": "nonexistent-note", "line": 5}
  ]
}
```

**Errors**:
- `file_already_exists` — file already exists at this path (use `vault_update` instead)
- `duplicate_filename` — a file with the same name exists at a different path
- `locked` — the vault is locked for maintenance

**Lock**: Write

---

#### `vault_update`

Updates an existing file. Fails if the file does not exist — use `vault_write` to create new files. The server atomically writes the file and auto-sets the `updated` frontmatter field to the current UTC time.

**Parameters**:
| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `path` | string | Yes | File path relative to vault root. Must be an existing file. |
| `content` | string | Yes | New markdown content. Replaces the entire file. |

**Returns** (success):
```json
{
  "status": "ok",
  "path": "/facts/preferences/dark-mode.md",
  "updated": "2026-04-05T17:00:00Z",
  "warnings": []
}
```

Warnings follow the same format as `vault_write` (broken link detection).

**Errors**:
- `file_not_found` — file does not exist (use `vault_write` instead)
- `locked` — the vault is locked for maintenance

**Lock**: Write

---

#### `vault_delete`

Deletes a file. Does not delete directories (use with individual files only).

**Parameters**:
| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `path` | string | Yes | File path relative to vault root. |

**Returns**:
```json
{
  "status": "ok",
  "deleted": "/facts/preferences/dark-mode.md",
  "orphaned_backlinks": ["/people/leo.md"]
}
```

`orphaned_backlinks` lists files that contained `[[wikilinks]]` to the deleted file. The KLONA memory agent can update them or leave for the maintenance agent.

**Errors**:
- `file_not_found` — file does not exist
- `locked` — the vault is locked for maintenance

**Lock**: Write

---

#### `vault_move`

Moves or renames a file. Preserves the existing `updated` frontmatter (moving is not a content change). The server creates destination parent directories automatically and updates the backlink cache for the new path.

**Parameters**:
| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `src` | string | Yes | Current file path. |
| `dst` | string | Yes | New file path. Destination parent directories are created automatically. |

**Returns**:
```json
{
  "status": "ok",
  "from": "/facts/dark-mode.md",
  "to": "/facts/preferences/dark-mode.md",
  "referencing_files": ["/people/leo.md", "/projects/klona-memory.md"]
}
```

`referencing_files` lists files that contain `[[wikilinks]]` to the old filename. Their links are now broken. The KLONA memory agent should update them.

**Errors**:
- `file_not_found` — source file does not exist
- `duplicate_filename` — a file with the destination name exists at a different path
- `locked` — the vault is locked for maintenance

**Lock**: Write

---

#### `vault_mkdir`

Creates a directory. Creates parent directories if they don't exist (like `mkdir -p`).

**Parameters**:
| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `path` | string | Yes | Directory path relative to vault root. |

**Returns**:
```json
{
  "status": "ok",
  "path": "/facts/preferences/"
}
```

**Errors**:
- `already_exists` — directory already exists (not an error in practice — idempotent, returns `ok`)
- `locked` — the vault is locked for maintenance

**Lock**: Write

---

#### `vault_grep`

Searches file contents for a pattern within a scoped path. Case-insensitive by default. Returns matching lines with context.

**Parameters**:
| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `pattern` | string | Yes | Search string (plain text, not regex). |
| `path` | string | No | Directory to scope the search. Defaults to `"/"` (entire vault). |

**Returns**:
```json
{
  "pattern": "dark mode",
  "results": [
    {
      "path": "/facts/preferences/dark-mode.md",
      "matches": [
        {"line": 5, "text": "User prefers dark mode across all editors and terminals."}
      ]
    },
    {
      "path": "/people/leo.md",
      "matches": [
        {"line": 12, "text": "Also uses [[dark-mode]] in all terminals."}
      ]
    }
  ],
  "total_matches": 2
}
```

**Errors**:
- `path_not_found` — scoped directory does not exist

**Lock**: Read

---

#### `vault_backlinks`

Returns all files that contain a `[[wikilink]]` pointing to the given file. Uses the in-memory backlink cache (O(1) lookup).

**Parameters**:
| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `path` | string | Yes | File path relative to vault root. |

**Returns**:
```json
{
  "path": "/decisions/use-fastmcp.md",
  "backlinks": [
    {
      "path": "/projects/klona-memory.md",
      "line": 7,
      "text": "Tech stack: [[python-preferred-for-backend]], [[use-fastmcp]]."
    }
  ],
  "count": 1
}
```

Each backlink includes the matching line for context — same as grep output, so the KLONA memory agent can assess relevance without reading the full file.

**Errors**:
- `file_not_found` — target file does not exist

**Lock**: Read

---

## Design Principles

1. **The server is a filesystem API.** No intelligence, no search, no indexing.
2. **The agent is the brain.** The KLONA memory agent navigates, decides, connects.
3. **The directory hierarchy IS the index.** No separate index files.
4. **Wikilinks ARE the knowledge graph.** No external graph database.
5. **Filenames ARE the summaries.** Descriptive, slugified titles.
6. **One frontmatter field.** `updated`, server-managed.
7. **Atomic everything.** Atomic file writes, atomic vault swap.
8. **Lock for safety.** Read-write lock for concurrent sessions.
9. **Self-evolving.** Nightly maintenance polishes the vault using an AI agent.
