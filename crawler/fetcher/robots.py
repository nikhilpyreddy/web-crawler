from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import aiohttp

from crawler.config import Settings

logger = logging.getLogger(__name__)

_CACHE_TTL = 3600  # 1 hour


class RobotsCache:
    """
    Fetches and caches robots.txt per domain with a TTL.
    Falls back to allowing all if robots.txt is unreachable.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._cache: dict[str, tuple[RobotFileParser, float]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._session: Optional[aiohttp.ClientSession] = None

    def set_session(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    def _robots_url(self, url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}/robots.txt"

    async def _fetch_robots(self, robots_url: str) -> RobotFileParser:
        parser = RobotFileParser()
        parser.set_url(robots_url)
        try:
            assert self._session is not None
            async with self._session.get(robots_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    text = await resp.text(errors="replace")
                    parser.parse(text.splitlines())
                # 404/403 → allow all (parser stays empty = allow all)
        except Exception as e:
            logger.debug("Failed to fetch %s: %s; allowing all", robots_url, e)
        return parser

    async def is_allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        domain = f"{parsed.scheme}://{parsed.netloc}"
        robots_url = self._robots_url(url)

        if domain not in self._locks:
            self._locks[domain] = asyncio.Lock()

        async with self._locks[domain]:
            cached = self._cache.get(domain)
            if cached is None or (time.monotonic() - cached[1]) > _CACHE_TTL:
                parser = await self._fetch_robots(robots_url)
                self._cache[domain] = (parser, time.monotonic())
            else:
                parser = cached[0]

        return parser.can_fetch(self._settings.user_agent, url)

    async def crawl_delay(self, url: str) -> Optional[float]:
        parsed = urlparse(url)
        domain = f"{parsed.scheme}://{parsed.netloc}"
        cached = self._cache.get(domain)
        if cached:
            delay = cached[0].crawl_delay(self._settings.user_agent)
            return float(delay) if delay is not None else None
        return None
