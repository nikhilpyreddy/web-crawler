from __future__ import annotations

import asyncio
import logging
import random
from typing import Awaitable, Callable, TypeVar

import aiohttp

from crawler.config import Settings

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Status codes that are transient and worth retrying
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class RetryPolicy:
    """Exponential back-off with full jitter for async coroutines."""

    def __init__(self, settings: Settings) -> None:
        self._max_retries = settings.max_retries
        self._base = settings.base_backoff_seconds
        self._max = settings.max_backoff_seconds

    def _delay(self, attempt: int, retry_after: int | None = None) -> float:
        if retry_after:
            return float(retry_after)
        cap = min(self._base * (2 ** attempt), self._max)
        return random.uniform(0, cap)  # full jitter

    async def execute(
        self,
        coro_fn: Callable[[], Awaitable[T]],
        *,
        label: str = "",
    ) -> T:
        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                return await coro_fn()
            except aiohttp.ClientResponseError as e:
                if e.status not in _RETRYABLE_STATUS or attempt == self._max_retries:
                    raise
                retry_after = None
                if e.status == 429 and e.headers and "Retry-After" in e.headers:
                    try:
                        retry_after = int(e.headers["Retry-After"])
                    except ValueError:
                        pass
                delay = self._delay(attempt, retry_after)
                logger.warning(
                    "Retryable HTTP %d for %s (attempt %d/%d), sleeping %.1fs",
                    e.status, label, attempt + 1, self._max_retries, delay,
                )
                await asyncio.sleep(delay)
                last_exc = e
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if attempt == self._max_retries:
                    raise
                delay = self._delay(attempt)
                logger.warning(
                    "Network error for %s (attempt %d/%d): %s, sleeping %.1fs",
                    label, attempt + 1, self._max_retries, e, delay,
                )
                await asyncio.sleep(delay)
                last_exc = e

        raise RuntimeError(f"Exhausted retries for {label}") from last_exc
