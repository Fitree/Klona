"""Mockable HTTP wrapper for `opencode serve`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import httpx


@dataclass(frozen=True)
class OpenCodeSession:
    id: str
    title: str = ""


class OpenCodeClient:
    """Thin async client for the documented OpenCode session HTTP endpoints."""

    def __init__(self, base_url: str, *, timeout: float = 600.0, agent: str = "klona-memory"):
        self.base_url = base_url.rstrip("/")
        self.agent = agent
        self.timeout = timeout
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout)

    async def close(self) -> None:
        await self._client.aclose()

    async def list_sessions(self) -> list[OpenCodeSession]:
        response = await self._client.get("/session")
        response.raise_for_status()
        data = response.json()
        rows = data if isinstance(data, list) else data.get("sessions", [])
        sessions: list[OpenCodeSession] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            session_id = row.get("id") or row.get("sessionID") or row.get("session_id")
            if session_id:
                sessions.append(OpenCodeSession(id=str(session_id), title=str(row.get("title") or "")))
        return sessions

    async def create_session(self) -> OpenCodeSession:
        response = await self._client.post("/session", json={"title": "KLONA memory agent"})
        response.raise_for_status()
        data = response.json()
        session_id = data.get("id") or data.get("sessionID") or data.get("session_id")
        if not session_id:
            raise RuntimeError(f"OpenCode create session response missing id: {data!r}")
        return OpenCodeSession(id=str(session_id), title=str(data.get("title") or "KLONA memory agent"))

    async def get_or_create_session(self) -> OpenCodeSession:
        sessions = await self.list_sessions()
        for session in sessions:
            if session.title == "KLONA memory agent":
                return session
        return await self.create_session()

    async def send_message(self, session_id: str, content: str) -> str:
        payload: dict[str, Any] = {
            "agent": self.agent,
            "parts": [{"type": "text", "text": content}],
        }
        response = await self._client.post(f"/session/{session_id}/message", json=payload)
        response.raise_for_status()
        data = response.json()
        return self._extract_text(data)

    @staticmethod
    def _extract_text(data: Any) -> str:
        if isinstance(data, str):
            return data
        if isinstance(data, dict):
            for key in ("text", "content", "message", "result", "output"):
                value = data.get(key)
                if isinstance(value, str):
                    return value
            if isinstance(data.get("message"), dict):
                nested = OpenCodeClient._extract_text(data["message"])
                if nested:
                    return nested
            if isinstance(data.get("parts"), list):
                parts = [p.get("text", "") for p in data["parts"] if isinstance(p, dict)]
                if any(parts):
                    return "".join(parts)
        return str(data)


class SharedSessionOpenCodeAgent:
    """Keeps all memory work in one OpenCode session for working-memory continuity."""

    def __init__(self, client: OpenCodeClient):
        self.client = client
        self._session: OpenCodeSession | None = None

    async def ask(self, prompt: str) -> str:
        if self._session is None:
            self._session = await self.client.get_or_create_session()
        return await self.client.send_message(self._session.id, prompt)
