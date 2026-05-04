from __future__ import annotations

import hashlib
import logging
from urllib.parse import urldefrag, urlparse

import redis.asyncio as aioredis

from crawler.config import Settings

logger = logging.getLogger(__name__)


class URLDeduplicator:
    """
    URL deduplication backed by a Redis SET.

    Normalises URLs before hashing (strips fragment, lowercases scheme+host,
    removes trailing slash on root) so near-duplicate variants map to the same key.

    Memory note: for very large crawls, swap the Redis SET for a RedisBloom
    Bloom filter (redis-py-bloom) by replacing SADD/SISMEMBER with BF.ADD/BF.EXISTS.
    """

    def __init__(self, client: aioredis.Redis, settings: Settings) -> None:
        self._r = client
        self._key = settings.redis_dedup_key

    @staticmethod
    def _normalise(url: str) -> str:
        """Canonical form: no fragment, lowercase scheme+host, sorted query."""
        url, _ = urldefrag(url)
        parsed = urlparse(url)
        normalised = parsed._replace(
            scheme=parsed.scheme.lower(),
            netloc=parsed.netloc.lower(),
        ).geturl()
        return normalised.rstrip("/") if normalised.endswith("/") and parsed.path in ("", "/") else normalised

    @staticmethod
    def _fingerprint(url: str) -> str:
        return hashlib.sha1(url.encode()).hexdigest()

    async def is_seen(self, url: str) -> bool:
        fp = self._fingerprint(self._normalise(url))
        return bool(await self._r.sismember(self._key, fp))

    async def mark_seen(self, url: str) -> bool:
        """Add url; returns True if it was new, False if already seen."""
        fp = self._fingerprint(self._normalise(url))
        added = await self._r.sadd(self._key, fp)
        return bool(added)

    async def seen_count(self) -> int:
        return await self._r.scard(self._key)

    async def reset(self) -> None:
        await self._r.delete(self._key)
        logger.info("URL deduplication set cleared")
