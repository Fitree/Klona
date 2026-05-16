"""Vault-native skill readers for the high-level memory agent."""

from __future__ import annotations

import json
import posixpath
import re
from pathlib import PurePosixPath
from typing import Any

from .config import Settings
from .mental_model import MCP_ACCEPT_HEADER, MCP_PROTOCOL_VERSION, _extract_result_object, _parse_mcp_response

SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
SKILLS_ROOT = "/skills"
SKILL_FILE_NAME = "SKILL.md"
VAULT_LS_TOOL_NAME = "vault_ls"
VAULT_READ_TOOL_NAME = "vault_read"
VAULT_LS_TOOL_FALLBACK_NAME = "klona_memory_server_vault_ls"
VAULT_READ_TOOL_FALLBACK_NAME = "klona_memory_server_vault_read"


class SkillValidationError(ValueError):
    """Raised when a skill name or resource path is invalid."""


class LowLevelMcpVaultSkillsClient:
    """Small streamable-HTTP MCP client for exact vault-native skill reads."""

    def __init__(self, settings: Settings):
        self.url = settings.low_level_mcp_url
        self.auth_token = settings.low_level_mcp_auth_token
        self.timeout = settings.recall_timeout_seconds

    async def list_skills(self) -> dict:
        async with await self._session() as session:
            root = await session.call_tool(VAULT_LS_TOOL_NAME, VAULT_LS_TOOL_FALLBACK_NAME, {"path": _skill_dir_path("")})
            if _is_not_found(root):
                return {"status": "ok", "skills": []}
            _raise_if_error(root)
            skills = []
            for directory in root.get("dirs", []):
                name = PurePosixPath(str(directory).strip("/")).name
                if not _valid_skill_name(name):
                    continue
                skill = await self._load_skill_metadata_in_session(session, name)
                if skill.get("status") == "ok" and skill["description"]:
                    skills.append({"name": skill["name"], "description": skill["description"], "updated": skill["updated"]})
            return {"status": "ok", "skills": sorted(skills, key=lambda item: item["name"])}

    async def load_skill(self, name: str) -> dict:
        _validate_skill_name(name)
        async with await self._session() as session:
            return await self._load_skill_in_session(session, name, include_content=True)

    async def load_skill_resource(self, skill_name: str, path: str) -> dict:
        _validate_skill_name(skill_name)
        resource_path = _validate_resource_path(path)
        async with await self._session() as session:
            result = await session.call_tool(
                VAULT_READ_TOOL_NAME,
                VAULT_READ_TOOL_FALLBACK_NAME,
                {"path": _skill_resource_vault_path(skill_name, resource_path)},
            )
            if _is_not_found(result):
                return {"status": "missing", "skill_name": skill_name, "path": resource_path, "content": ""}
            _raise_if_error(result)
            return {"status": "ok", "skill_name": skill_name, "path": resource_path, "content": _content_from_result(result)}

    async def _load_skill_in_session(self, session: "_McpSession", name: str, include_content: bool) -> dict:
        result = await session.call_tool(VAULT_READ_TOOL_NAME, VAULT_READ_TOOL_FALLBACK_NAME, {"path": _skill_file_path(name)})
        if _is_not_found(result):
            return {"status": "missing", "name": name, "description": "", "content": "", "resources": [], "updated": ""}
        _raise_if_error(result)
        content = _content_from_result(result)
        metadata = _validated_skill_metadata(name, content)
        resource_paths = await self._list_resource_paths(session, name, _skill_dir_path(name)) if include_content else []
        response = {
            "status": "ok",
            "name": name,
            "description": metadata["description"],
            "content": content if include_content else "",
            "resources": [{"path": path} for path in resource_paths],
            "updated": metadata["updated"],
        }
        return response

    async def _load_skill_metadata_in_session(self, session: "_McpSession", name: str) -> dict:
        result = await session.call_tool(VAULT_READ_TOOL_NAME, VAULT_READ_TOOL_FALLBACK_NAME, {"path": _skill_file_path(name)})
        if _is_not_found(result):
            return {"status": "missing", "name": name, "description": "", "updated": ""}
        _raise_if_error(result)
        try:
            metadata = _validated_skill_metadata(name, _content_from_result(result))
        except SkillValidationError:
            return {"status": "invalid", "name": name, "description": "", "updated": ""}
        description = _sanitize_catalog_description(metadata["description"])
        return {"status": "ok", "name": name, "description": description, "updated": metadata["updated"]}

    async def _list_resource_paths(self, session: "_McpSession", skill_name: str, directory: str) -> list[str]:
        result = await session.call_tool(VAULT_LS_TOOL_NAME, VAULT_LS_TOOL_FALLBACK_NAME, {"path": directory})
        if _is_not_found(result):
            return []
        _raise_if_error(result)
        resources = []
        for file_info in result.get("files", []):
            file_name = str(file_info.get("name") or "")
            if file_name and file_name != SKILL_FILE_NAME:
                resources.append(_resource_relative_path(skill_name, posixpath.join(directory, file_name)))
        for child_dir in result.get("dirs", []):
            resources.extend(await self._list_resource_paths(session, skill_name, str(child_dir)))
        return sorted(resources)

    async def _session(self) -> "_McpSession":
        if not self.url:
            raise RuntimeError("LOW_LEVEL_MCP_URL is not configured")
        import httpx

        headers = {
            "Accept": MCP_ACCEPT_HEADER,
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {self.auth_token}"} if self.auth_token else {}),
        }
        http_client = httpx.AsyncClient(timeout=self.timeout)
        await http_client.__aenter__()
        session = _McpSession(self.url, http_client, headers)
        try:
            await session.initialize()
            return session
        except BaseException:
            await http_client.__aexit__(None, None, None)
            raise


class _McpSession:
    def __init__(self, url: str, client: Any, headers: dict[str, str]):
        self.url = url
        self.client = client
        self.headers = headers
        self.session_id: str | None = None

    async def __aenter__(self) -> "_McpSession":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        await self.client.__aexit__(exc_type, exc, tb)
        return False

    async def initialize(self) -> None:
        payload, self.session_id = await self._post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "klona-memory-agent-vault-skill-reader", "version": "1.0.0"},
                },
            }
        )
        if payload and payload.get("error"):
            raise RuntimeError(payload["error"].get("message") or "MCP initialize failed")
        await self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    async def call_tool(self, name: str, fallback_name: str, arguments: dict[str, Any]) -> dict:
        try:
            return await self._call_tool_once(name, arguments)
        except RuntimeError as error:
            lowered_error = str(error).lower()
            if "method not found" not in lowered_error and "tool not found" not in lowered_error and "unknown tool" not in lowered_error:
                raise
            return await self._call_tool_once(fallback_name, arguments)

    async def _call_tool_once(self, name: str, arguments: dict[str, Any]) -> dict:
        payload, _ = await self._post(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
        )
        if not payload:
            raise RuntimeError("empty MCP tool response")
        if payload.get("error"):
            raise RuntimeError(payload["error"].get("message") or "MCP tool call failed")
        result = payload.get("result")
        if isinstance(result, dict) and result.get("isError"):
            raise RuntimeError(json.dumps(result))
        candidate = _extract_result_object(result)
        if not isinstance(candidate, dict):
            raise RuntimeError("MCP tool response did not contain an object")
        return candidate

    async def _post(self, body: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
        request_headers = dict(self.headers)
        if self.session_id:
            request_headers["Mcp-Session-Id"] = self.session_id
        response = await self.client.post(self.url, headers=request_headers, json=body)
        payload = await _parse_mcp_response(response)
        return payload, response.headers.get("Mcp-Session-Id") or self.session_id


def _valid_skill_name(name: str) -> bool:
    return 1 <= len(name) <= 64 and bool(SKILL_NAME_RE.fullmatch(name))


def _validate_skill_name(name: str) -> None:
    if not _valid_skill_name(name):
        raise SkillValidationError("skill name must match ^[a-z0-9]+(-[a-z0-9]+)*$ and be 1-64 characters")


def _validate_resource_path(path: str) -> str:
    if not path or path.startswith("/"):
        raise SkillValidationError("resource path must be relative to the skill directory")
    raw_parts = PurePosixPath(path).parts
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise SkillValidationError("resource path must stay inside the skill directory")
    normalized = posixpath.normpath(path)
    parts = PurePosixPath(normalized).parts
    if normalized in {".", SKILL_FILE_NAME} or any(part in {"", ".", ".."} for part in parts):
        raise SkillValidationError("resource path must stay inside the skill directory")
    if PurePosixPath(normalized).name == SKILL_FILE_NAME:
        raise SkillValidationError("SKILL.md must be loaded with load_skill, not load_skill_resource")
    return normalized


def _skill_dir_path(name: str) -> str:
    return f"{SKILLS_ROOT}/{name}/" if name else f"{SKILLS_ROOT}/"


def _skill_file_path(name: str) -> str:
    return f"{_skill_dir_path(name)}{SKILL_FILE_NAME}"


def _skill_resource_vault_path(skill_name: str, resource_path: str) -> str:
    return f"{_skill_dir_path(skill_name)}{resource_path}"


def _resource_relative_path(skill_name: str, vault_path: str) -> str:
    prefix = _skill_dir_path(skill_name)
    return str(vault_path).removeprefix(prefix).strip("/")


def _parse_frontmatter(content: str) -> dict[str, str]:
    match = re.match(r"^---\n(.*?)\n---(?:\n|$)", content, re.DOTALL)
    if not match:
        return {}
    fields = {}
    for line in match.group(1).splitlines():
        key, separator, value = line.partition(":")
        if separator:
            fields[key.strip()] = value.strip().strip('"\'')
    return fields


def _validated_skill_metadata(canonical_name: str, content: str) -> dict[str, str]:
    metadata = _parse_frontmatter(content)
    declared_name = str(metadata.get("name") or "").strip()
    description = str(metadata.get("description") or "").strip()
    updated = str(metadata.get("updated") or "").strip()
    if not declared_name:
        raise SkillValidationError("skill frontmatter must include name")
    if not _valid_skill_name(declared_name):
        raise SkillValidationError("skill frontmatter name must match ^[a-z0-9]+(-[a-z0-9]+)*$ and be 1-64 characters")
    if declared_name != canonical_name:
        raise SkillValidationError("skill frontmatter name must match its /skills/<skill-name>/ directory")
    if not description:
        raise SkillValidationError("skill frontmatter must include a non-empty description")
    return {"name": declared_name, "description": description, "updated": updated}


def _sanitize_catalog_description(description: str) -> str:
    compact = re.sub(r"[\s\x00-\x1f\x7f]+", " ", description)
    compact = compact.replace("<Klona_vault_skills>", "").replace("</Klona_vault_skills>", "")
    compact = re.sub(r"\s+", " ", compact)
    return compact.strip()[:300]


def _content_from_result(result: dict) -> str:
    content = result.get("content")
    if not isinstance(content, str):
        raise RuntimeError("MCP vault_read response did not contain content")
    return content


def _is_not_found(result: dict) -> bool:
    return result.get("error") in {"file_not_found", "path_not_found"}


def _raise_if_error(result: dict) -> None:
    if result.get("error"):
        raise RuntimeError(result.get("message") or result["error"])
