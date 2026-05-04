from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import redis.asyncio as aioredis

from crawler.config import Settings
from crawler.models.task import CrawlTask, TaskStatus

logger = logging.getLogger(__name__)


class TaskQueue:
    """Redis Streams-based distributed task queue with consumer group support."""

    def __init__(self, client: aioredis.Redis, settings: Settings) -> None:
        self._r = client
        self._stream = settings.redis_stream_name
        self._group = settings.redis_consumer_group

    async def setup(self) -> None:
        """Create the consumer group, creating the stream if it doesn't exist."""
        try:
            await self._r.xgroup_create(self._stream, self._group, id="0", mkstream=True)
            logger.info("Created consumer group '%s' on stream '%s'", self._group, self._stream)
        except aioredis.ResponseError as e:
            if "BUSYGROUP" in str(e):
                logger.debug("Consumer group already exists, skipping creation")
            else:
                raise

    async def enqueue(self, task: CrawlTask) -> str:
        """Add a task to the stream. Returns the stream message ID."""
        task.enqueued_at = datetime.now(timezone.utc)
        task.status = TaskStatus.PENDING
        msg_id = await self._r.xadd(self._stream, task.to_redis_dict())
        logger.debug("Enqueued task %s (url=%s) -> msg %s", task.task_id, task.url, msg_id)
        return msg_id

    async def get_next(
        self, consumer_id: str, timeout_ms: int = 2000
    ) -> Optional[tuple[str, CrawlTask]]:
        """
        Block until a task is available. Returns (message_id, task) or None on timeout.
        Uses XREADGROUP so each message is delivered to exactly one consumer.
        """
        results = await self._r.xreadgroup(
            groupname=self._group,
            consumername=consumer_id,
            streams={self._stream: ">"},
            count=1,
            block=timeout_ms,
        )
        if not results:
            return None
        _stream_name, messages = results[0]
        msg_id, fields = messages[0]
        task = CrawlTask.from_redis_dict(fields)
        task.status = TaskStatus.IN_PROGRESS
        return msg_id, task

    async def ack(self, msg_id: str) -> None:
        """Acknowledge a processed message so it leaves the pending-entry list."""
        await self._r.xack(self._stream, self._group, msg_id)

    async def reclaim_stale(self, min_idle_ms: int = 60_000, count: int = 100) -> list[CrawlTask]:
        """
        Reclaim messages idle for more than min_idle_ms (e.g. from crashed workers).
        Returns reclaimed tasks so the caller can re-enqueue them.
        """
        result = await self._r.xautoclaim(
            self._stream, self._group, "recovery", min_idle_time=min_idle_ms, start_id="0-0", count=count
        )
        reclaimed = []
        # xautoclaim returns (next_start_id, messages, deleted_ids)
        messages = result[1] if isinstance(result, (list, tuple)) and len(result) > 1 else []
        for msg_id, fields in messages:
            if fields:
                task = CrawlTask.from_redis_dict(fields)
                reclaimed.append(task)
                await self.ack(msg_id)
        return reclaimed

    async def depth(self) -> int:
        """Return approximate number of pending messages in the stream."""
        info = await self._r.xinfo_stream(self._stream)
        if isinstance(info, dict):
            return info.get("length", 0)
        return 0
