from __future__ import annotations

import logging
import time
from typing import Optional

import aiohttp

from crawler.config import Settings
from crawler.fetcher.retry import RetryPolicy
from crawler.models.page import FetchResult
from crawler.models.task import CrawlTask

logger = logging.getLogger(__name__)


class AsyncHTTPClient:
    """
    Shared aiohttp session for all workers in a process.
    DNS cache + connection pooling are configured on the connector level.
    Use as an async context manager.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._retry = RetryPolicy(settings)
        self._session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self) -> "AsyncHTTPClient":
        connector = aiohttp.TCPConnector(
            limit=self._settings.concurrency,
            ttl_dns_cache=300,
            enable_cleanup_closed=True,
        )
        timeout = aiohttp.ClientTimeout(total=self._settings.request_timeout_seconds)
        self._session = aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            headers={"User-Agent": self._settings.user_agent},
            max_redirects=self._settings.max_redirects,
        )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._session:
            await self._session.close()

    async def get(self, task: CrawlTask, extra_headers: Optional[dict[str, str]] = None) -> FetchResult:
        assert self._session is not None, "AsyncHTTPClient must be used as a context manager"

        async def _do_request() -> FetchResult:
            start = time.monotonic()
            async with self._session.get(  # type: ignore[union-attr]
                task.url,
                headers=extra_headers,
                allow_redirects=self._settings.follow_redirects,
                raise_for_status=True,
            ) as resp:
                body = await resp.read()
                elapsed = (time.monotonic() - start) * 1000
                content_type = resp.content_type or ""
                return FetchResult(
                    task=task,
                    status_code=resp.status,
                    headers=dict(resp.headers),
                    body_bytes=body,
                    content_type=content_type,
                    fetch_duration_ms=elapsed,
                    final_url=str(resp.url),
                )

        try:
            return await self._retry.execute(_do_request, label=task.url)
        except aiohttp.ClientResponseError as e:
            return FetchResult(
                task=task,
                status_code=e.status,
                error=f"HTTP {e.status}: {e.message}",
                final_url=task.url,
            )
        except Exception as e:
            return FetchResult(
                task=task,
                status_code=0,
                error=str(e),
                final_url=task.url,
            )
