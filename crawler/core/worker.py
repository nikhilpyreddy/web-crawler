from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from crawler.config import Settings
from crawler.core.rate_limiter import AdaptiveRateLimiter
from crawler.core.scheduler import Scheduler
from crawler.extraction.extractor import LLMExtractor
from crawler.fetcher.http_client import AsyncHTTPClient
from crawler.fetcher.robots import RobotsCache
from crawler.models.extracted import ExtractedData
from crawler.models.page import FetchResult
from crawler.parser.html_parser import HTMLParser

logger = logging.getLogger(__name__)


class CrawlWorker:
    """
    Long-running coroutine: dequeues tasks, fetches, parses, extracts, writes,
    and discovers new URLs. Stateless between iterations.
    """

    def __init__(
        self,
        worker_id: str,
        scheduler: Scheduler,
        http_client: AsyncHTTPClient,
        html_parser: HTMLParser,
        extractor: LLMExtractor,
        rate_limiter: AdaptiveRateLimiter,
        robots: RobotsCache,
        settings: Settings,
        stop_event: asyncio.Event,
        pages_crawled: "asyncio.Queue[int]",
        pages_failed: "asyncio.Queue[int]",
    ) -> None:
        self._id = worker_id
        self._scheduler = scheduler
        self._http = http_client
        self._parser = html_parser
        self._extractor = extractor
        self._rate_limiter = rate_limiter
        self._robots = robots
        self._settings = settings
        self._stop = stop_event
        self._pages_crawled = pages_crawled
        self._pages_failed = pages_failed
        self._output_dir = settings.output_dir

    async def run(self) -> None:
        logger.info("Worker %s started", self._id)
        while not self._stop.is_set():
            result = await self._scheduler.get_next(self._id, timeout_ms=2000)
            if result is None:
                continue
            msg_id, task = result
            try:
                await self._process_task(msg_id, task)
            except Exception as e:
                logger.exception("Unexpected error processing %s: %s", task.url, e)
                await self._scheduler.mark_failed(msg_id, task, str(e))
                await self._pages_failed.put(1)
        logger.info("Worker %s stopped", self._id)

    async def _process_task(self, msg_id: str, task) -> None:
        # robots.txt check
        if not await self._robots.is_allowed(task.url):
            logger.debug("robots.txt disallows %s", task.url)
            await self._scheduler.mark_done(msg_id, task)
            return

        # adaptive rate limiting
        await self._rate_limiter.acquire(task.domain)

        fetch_result: FetchResult = await self._http.get(task)

        if not fetch_result.succeeded:
            self._rate_limiter.record_error(task.domain)
            err = fetch_result.error or f"HTTP {fetch_result.status_code}"
            if fetch_result.status_code in (429, 503):
                self._rate_limiter.record_throttle(task.domain)
            await self._scheduler.mark_failed(msg_id, task, err)
            await self._pages_failed.put(1)
            return

        self._rate_limiter.record_success(task.domain)

        if not fetch_result.is_html:
            await self._scheduler.mark_done(msg_id, task)
            return

        parsed = await self._parser.parse(fetch_result)
        extracted = await self._extractor.extract(parsed)

        await self._write_result(extracted)
        discovered = await self._scheduler.enqueue_discovered(task, parsed.links)
        logger.debug(
            "Worker %s: crawled %s (%d links, %d new)",
            self._id, task.url, len(parsed.links), discovered,
        )

        await self._scheduler.mark_done(msg_id, task)
        await self._pages_crawled.put(1)

    async def _write_result(self, data: ExtractedData) -> None:
        import aiofiles
        from urllib.parse import urlparse
        domain = urlparse(data.url).netloc
        date_str = data.crawled_at.strftime("%Y-%m-%d")
        out_path = self._output_dir / domain / f"{date_str}.jsonl"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        line = data.model_dump_json() + "\n"
        async with aiofiles.open(out_path, "a", encoding="utf-8") as f:
            await f.write(line)
