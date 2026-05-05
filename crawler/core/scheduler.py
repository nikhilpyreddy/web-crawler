from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

from crawler.config import Settings
from crawler.models.task import CrawlTask, TaskStatus
from crawler.redis_backend.checkpoint import CheckpointManager
from crawler.redis_backend.deduplicator import URLDeduplicator
from crawler.redis_backend.task_queue import TaskQueue

logger = logging.getLogger(__name__)

_SKIP_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico",
    ".pdf", ".zip", ".tar", ".gz", ".mp4", ".mp3", ".avi",
    ".css", ".js", ".woff", ".woff2", ".ttf", ".eot",
}


class Scheduler:
    """
    URL frontier management: dedup, depth enforcement, enqueue/dequeue, retry handling.
    """

    def __init__(
        self,
        queue: TaskQueue,
        dedup: URLDeduplicator,
        checkpoint: CheckpointManager,
        settings: Settings,
    ) -> None:
        self._queue = queue
        self._dedup = dedup
        self._checkpoint = checkpoint
        self._settings = settings

    def _should_skip(self, url: str, depth: int) -> Optional[str]:
        if depth > self._settings.max_depth:
            return f"depth {depth} > max {self._settings.max_depth}"
        parsed = urlparse(url)
        ext = "." + parsed.path.rsplit(".", 1)[-1].lower() if "." in parsed.path else ""
        if ext in _SKIP_EXTENSIONS:
            return f"skipped extension {ext}"
        if not parsed.scheme.startswith("http"):
            return f"non-http scheme {parsed.scheme}"
        return None

    async def enqueue(self, task: CrawlTask) -> bool:
        """
        Returns True if the URL was new and successfully enqueued, False otherwise.
        """
        skip_reason = self._should_skip(task.url, task.depth)
        if skip_reason:
            logger.debug("Skipping %s: %s", task.url, skip_reason)
            return False

        is_new = await self._dedup.mark_seen(task.url)
        if not is_new:
            return False

        await self._queue.enqueue(task)
        return True

    async def get_next(self, consumer_id: str, timeout_ms: int = 2000) -> Optional[tuple[str, CrawlTask]]:
        return await self._queue.get_next(consumer_id, timeout_ms)

    async def mark_done(self, msg_id: str, task: CrawlTask) -> None:
        task.status = TaskStatus.DONE
        await self._queue.ack(msg_id)

    async def mark_failed(self, msg_id: str, task: CrawlTask, error: str) -> None:
        await self._queue.ack(msg_id)
        if task.retry_count < self._settings.max_retries:
            task.retry_count += 1
            task.status = TaskStatus.PENDING
            task.enqueued_at = datetime.now(timezone.utc)
            # Bypass dedup for retries — the URL is already known; we just need
            # to give it another attempt through the queue directly.
            await self._queue.enqueue(task)
            logger.info("Re-enqueued %s (retry %d)", task.url, task.retry_count)
        else:
            task.status = TaskStatus.FAILED
            logger.warning("Giving up on %s after %d retries: %s", task.url, task.retry_count, error)

    async def enqueue_discovered(self, parent: CrawlTask, links: list[str]) -> int:
        added = 0
        for link in links:
            child = CrawlTask(
                url=link,
                depth=parent.depth + 1,
                parent_url=parent.url,
            )
            if await self.enqueue(child):
                added += 1
        return added

    async def enqueue_seeds(self, urls: list[str]) -> int:
        added = 0
        for url in urls:
            task = CrawlTask(url=url, depth=0)
            if await self.enqueue(task):
                added += 1
        logger.info("Seeded %d/%d URLs into the queue", added, len(urls))
        return added
