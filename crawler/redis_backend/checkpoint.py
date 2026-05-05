from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

import redis.asyncio as aioredis

from crawler.config import Settings

logger = logging.getLogger(__name__)

_FIELD_STATE = "state"
_FIELD_SAVED_AT = "saved_at"
_FIELD_PAGES_CRAWLED = "pages_crawled"
_FIELD_PAGES_FAILED = "pages_failed"
_FIELD_SEED_URLS = "seed_urls"


class CheckpointManager:
    """
    Fault-tolerant crawl state persistence using a Redis Hash.

    Saves enough state to resume after a crash:
    - pages crawled / failed counters
    - original seed URLs
    - arbitrary extra state blob (JSON)

    The task queue itself persists in Redis Streams (pending-entry list),
    so no additional URL-frontier serialisation is needed here.
    """

    def __init__(self, client: aioredis.Redis, settings: Settings) -> None:
        self._r = client
        self._key = settings.redis_checkpoint_key

    async def save(
        self,
        pages_crawled: int,
        pages_failed: int,
        seed_urls: list[str],
        extra: Optional[dict[str, Any]] = None,
    ) -> None:
        data = {
            _FIELD_SAVED_AT: datetime.now(timezone.utc).isoformat(),
            _FIELD_PAGES_CRAWLED: str(pages_crawled),
            _FIELD_PAGES_FAILED: str(pages_failed),
            _FIELD_SEED_URLS: json.dumps(seed_urls),
            _FIELD_STATE: json.dumps(extra or {}),
        }
        await self._r.hset(self._key, mapping=data)
        logger.debug("Checkpoint saved: %d crawled, %d failed", pages_crawled, pages_failed)

    async def load(self) -> Optional[dict[str, Any]]:
        raw = await self._r.hgetall(self._key)
        if not raw:
            return None
        return {
            "saved_at": raw.get(_FIELD_SAVED_AT, ""),
            "pages_crawled": int(raw.get(_FIELD_PAGES_CRAWLED, 0)),
            "pages_failed": int(raw.get(_FIELD_PAGES_FAILED, 0)),
            "seed_urls": json.loads(raw.get(_FIELD_SEED_URLS, "[]")),
            "extra": json.loads(raw.get(_FIELD_STATE, "{}")),
        }

    async def exists(self) -> bool:
        return bool(await self._r.exists(self._key))

    async def clear(self) -> None:
        await self._r.delete(self._key)
        logger.info("Checkpoint cleared")
