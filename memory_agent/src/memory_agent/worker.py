"""Queue worker for server-side memory-agent work."""

from __future__ import annotations

import asyncio
import logging

from .config import load_settings, Settings
from .opencode_client import OpenCodeClient, SharedSessionOpenCodeAgent
from .prompts import recall_prompt, remember_prompt
from .queue import MemoryQueue, QueueItem

logger = logging.getLogger(__name__)


class MemoryWorker:
    def __init__(self, settings: Settings | None = None, queue: MemoryQueue | None = None, agent: SharedSessionOpenCodeAgent | None = None):
        self.settings = settings or load_settings()
        self.queue = queue or MemoryQueue(self.settings.queue_db_path)
        self.agent = agent
        self._owned_client: OpenCodeClient | None = None

    async def _ensure_agent(self) -> SharedSessionOpenCodeAgent:
        if self.agent is None:
            self._owned_client = OpenCodeClient(
                self.settings.opencode_base_url,
                timeout=self.settings.recall_timeout_seconds,
            )
            self.agent = SharedSessionOpenCodeAgent(self._owned_client)
        return self.agent

    async def process_one(self) -> bool:
        item = self.queue.claim_next(
            processing_lease_seconds=self.settings.processing_lease_seconds,
            max_retries=self.settings.max_retries,
        )
        if item is None:
            return False
        try:
            result = await self._process_item(item)
        except Exception as exc:  # noqa: BLE001 - failures are persisted for retry/accounting.
            status = self.queue.mark_failed_or_retry(item.id, str(exc), self.settings.max_retries)
            logger.warning("memory queue item %s failed; status=%s error=%s", item.id, status, exc)
        else:
            self.queue.mark_succeeded(item.id, result)
        return True

    async def _process_item(self, item: QueueItem) -> str:
        agent = await self._ensure_agent()
        if item.kind == "recall":
            return await agent.ask(recall_prompt(item.input, self.settings))
        if item.kind == "remember":
            await agent.ask(remember_prompt(item.input, self.settings))
            return ""
        raise ValueError(f"unknown queue item kind: {item.kind}")

    async def run_forever(self) -> None:
        try:
            while True:
                processed = await self.process_one()
                if not processed:
                    await asyncio.sleep(self.settings.worker_idle_sleep_seconds)
        finally:
            if self._owned_client is not None:
                await self._owned_client.close()


async def amain() -> None:
    logging.basicConfig(level=logging.INFO)
    await MemoryWorker().run_forever()


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
