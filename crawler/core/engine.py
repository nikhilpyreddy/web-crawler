from __future__ import annotations

import asyncio
import logging
import platform
import signal
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import redis.asyncio as aioredis

from crawler.config import Settings
from crawler.core.rate_limiter import AdaptiveRateLimiter
from crawler.core.scheduler import Scheduler
from crawler.core.worker import CrawlWorker
from crawler.extraction.extractor import LLMExtractor
from crawler.fetcher.http_client import AsyncHTTPClient
from crawler.fetcher.robots import RobotsCache
from crawler.parser.html_parser import HTMLParser
from crawler.redis_backend.checkpoint import CheckpointManager
from crawler.redis_backend.deduplicator import URLDeduplicator
from crawler.redis_backend.task_queue import TaskQueue

logger = logging.getLogger(__name__)

_CHECKPOINT_INTERVAL = 30   # seconds
_IDLE_STOP_THRESHOLD = 10   # consecutive empty-queue ticks before auto-stop
_STALE_RECLAIM_INTERVAL = 60  # seconds between stale-PEL reclamation sweeps


@dataclass
class CrawlStats:
    pages_crawled: int = 0
    pages_failed: int = 0
    start_time: float = field(default_factory=time.monotonic)

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.start_time

    @property
    def pages_per_second(self) -> float:
        elapsed = self.elapsed_seconds
        return self.pages_crawled / elapsed if elapsed > 0 else 0.0


class CrawlEngine:
    """
    Top-level orchestrator.  Initialises all subsystems, launches worker pool,
    handles graceful shutdown on SIGINT/SIGTERM, and periodically checkpoints.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._stop_event = asyncio.Event()
        self._stats = CrawlStats()
        self._seed_urls: list[str] = []

    async def start(self, seed_urls: list[str], resume: bool = False) -> None:
        self._seed_urls = seed_urls
        self._settings.output_dir.mkdir(parents=True, exist_ok=True)

        redis_client = aioredis.from_url(
            self._settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            max_connections=self._settings.redis_pool_size,
        )

        queue = TaskQueue(redis_client, self._settings)
        dedup = URLDeduplicator(redis_client, self._settings)
        checkpoint = CheckpointManager(redis_client, self._settings)

        await queue.setup()

        scheduler = Scheduler(queue, dedup, checkpoint, self._settings)

        if resume and await checkpoint.exists():
            cp = await checkpoint.load()
            logger.info(
                "Resuming from checkpoint: %d crawled, %d failed",
                cp["pages_crawled"], cp["pages_failed"],
            )
            self._stats.pages_crawled = cp["pages_crawled"]
            self._stats.pages_failed = cp["pages_failed"]
            seed_urls = cp["seed_urls"]
        else:
            if resume:
                logger.warning(
                    "--resume requested but no checkpoint found; starting fresh crawl"
                )
            await dedup.reset()
            await checkpoint.clear()
            logger.warning(
                "Starting fresh crawl. If a previous run was interrupted, "
                "run 'python main.py reset' first to clear stale Redis state."
            )
            await scheduler.enqueue_seeds(seed_urls)

        rate_limiter = AdaptiveRateLimiter(self._settings)
        extractor = LLMExtractor(self._settings)
        executor = ThreadPoolExecutor(max_workers=self._settings.thread_pool_size)
        html_parser = HTMLParser(executor, self._settings)

        pages_crawled_q: asyncio.Queue[int] = asyncio.Queue()
        pages_failed_q: asyncio.Queue[int] = asyncio.Queue()

        async with AsyncHTTPClient(self._settings) as http_client:
            robots = RobotsCache(self._settings)
            robots.set_session(http_client.session)

            workers = [
                CrawlWorker(
                    worker_id=f"worker-{i}",
                    scheduler=scheduler,
                    http_client=http_client,
                    html_parser=html_parser,
                    extractor=extractor,
                    rate_limiter=rate_limiter,
                    robots=robots,
                    settings=self._settings,
                    stop_event=self._stop_event,
                    pages_crawled=pages_crawled_q,
                    pages_failed=pages_failed_q,
                )
                for i in range(self._settings.concurrency)
            ]

            self._install_signal_handlers()

            worker_tasks = [asyncio.create_task(w.run()) for w in workers]
            monitor = asyncio.create_task(
                self._monitor_loop(pages_crawled_q, pages_failed_q, checkpoint, queue, scheduler, seed_urls)
            )

            await asyncio.gather(*worker_tasks)
            monitor.cancel()
            try:
                await monitor
            except asyncio.CancelledError:
                pass

        executor.shutdown(wait=False)
        await redis_client.aclose()

        await self._save_checkpoint(checkpoint, seed_urls)
        logger.info(
            "Crawl complete: %d pages in %.1fs (%.1f p/s)",
            self._stats.pages_crawled,
            self._stats.elapsed_seconds,
            self._stats.pages_per_second,
        )

    async def stop(self) -> None:
        logger.info("Shutdown requested — draining workers…")
        self._stop_event.set()

    def _install_signal_handlers(self) -> None:
        """
        Register SIGINT/SIGTERM handlers for graceful shutdown.
        loop.add_signal_handler() is Unix-only; on Windows we fall back to
        a simple signal.signal() call (best-effort, not async-safe).
        """
        loop = asyncio.get_running_loop()
        if platform.system() != "Windows":
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, lambda: asyncio.create_task(self.stop()))
        else:
            # Windows: asyncio signal handlers are not supported for SIGTERM.
            # SIGINT (Ctrl-C) is handled by KeyboardInterrupt in main.py.
            try:
                signal.signal(signal.SIGINT, lambda *_: asyncio.create_task(self.stop()))
            except (OSError, ValueError):
                pass  # non-main thread — caller handles it

    async def _monitor_loop(
        self,
        crawled_q: asyncio.Queue[int],
        failed_q: asyncio.Queue[int],
        checkpoint: CheckpointManager,
        queue: TaskQueue,
        scheduler: Scheduler,
        seed_urls: list[str],
    ) -> None:
        last_checkpoint = time.monotonic()
        last_stale_reclaim = time.monotonic()
        idle_ticks = 0

        while not self._stop_event.is_set():
            await asyncio.sleep(5)

            # Drain stat queues
            while not crawled_q.empty():
                self._stats.pages_crawled += crawled_q.get_nowait()
            while not failed_q.empty():
                self._stats.pages_failed += failed_q.get_nowait()

            depth = await queue.depth()
            logger.info(
                "Stats: crawled=%d failed=%d queue=%d rate=%.1f p/s elapsed=%.0fs",
                self._stats.pages_crawled,
                self._stats.pages_failed,
                depth,
                self._stats.pages_per_second,
                self._stats.elapsed_seconds,
            )

            # Idle detection — stop when queue stays empty for N ticks
            if depth == 0:
                idle_ticks += 1
                if idle_ticks >= _IDLE_STOP_THRESHOLD:
                    logger.info("Queue has been empty for %d ticks — crawl complete", idle_ticks)
                    await self.stop()
            else:
                idle_ticks = 0

            # Periodic checkpoint
            if time.monotonic() - last_checkpoint >= _CHECKPOINT_INTERVAL:
                await self._save_checkpoint(checkpoint, seed_urls)
                last_checkpoint = time.monotonic()

            # Reclaim stale PEL entries from crashed workers
            if time.monotonic() - last_stale_reclaim >= _STALE_RECLAIM_INTERVAL:
                reclaimed = await queue.reclaim_stale(min_idle_ms=int(_STALE_RECLAIM_INTERVAL * 1000))
                if reclaimed:
                    logger.info("Reclaimed %d stale tasks from crashed workers", len(reclaimed))
                    for task in reclaimed:
                        await queue.enqueue(task)
                last_stale_reclaim = time.monotonic()

            # Stop if page limit reached
            if self._stats.pages_crawled >= self._settings.max_pages:
                logger.info("Page limit %d reached, stopping", self._settings.max_pages)
                await self.stop()

    async def _save_checkpoint(
        self, checkpoint: CheckpointManager, seed_urls: list[str]
    ) -> None:
        await checkpoint.save(
            pages_crawled=self._stats.pages_crawled,
            pages_failed=self._stats.pages_failed,
            seed_urls=seed_urls,
        )

    @property
    def stats(self) -> CrawlStats:
        return self._stats
