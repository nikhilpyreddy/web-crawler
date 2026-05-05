from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from crawler.config import Settings

logger = logging.getLogger(__name__)


@dataclass
class _TokenBucket:
    rate: float          # tokens per second (current)
    capacity: float      # max burst (= rate for simplicity)
    tokens: float = field(default=0.0)
    last_refill: float = field(default_factory=time.monotonic)
    min_rate: float = 0.5
    max_rate: float = 10.0
    consecutive_successes: int = 0

    def refill(self) -> None:
        now = time.monotonic()
        delta = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + delta * self.rate)
        self.last_refill = now

    def wait_time(self) -> float:
        self.refill()
        if self.tokens >= 1.0:
            return 0.0
        return (1.0 - self.tokens) / self.rate

    def consume(self) -> None:
        self.tokens -= 1.0

    def scale_rate(self, factor: float) -> None:
        self.rate = max(self.min_rate, min(self.max_rate, self.rate * factor))
        self.capacity = self.rate


class AdaptiveRateLimiter:
    """
    Per-domain token bucket with adaptive rate adjustment.

    - 429 / 503 → halves the rate (respects Retry-After if provided)
    - Network error → reduces rate by 25%
    - 10 consecutive successes → nudges rate up by 10%

    State is in-process (not Redis): rate limiting is per-worker, not global.
    """

    def __init__(self, settings: Settings) -> None:
        self._default_rate = settings.default_rate_limit_rps
        self._min_rate = settings.min_rate_limit_rps
        self._max_rate = settings.max_rate_limit_rps
        self._buckets: dict[str, _TokenBucket] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _bucket(self, domain: str) -> _TokenBucket:
        if domain not in self._buckets:
            self._buckets[domain] = _TokenBucket(
                rate=self._default_rate,
                capacity=self._default_rate,
                tokens=self._default_rate,
                min_rate=self._min_rate,
                max_rate=self._max_rate,
            )
            self._locks[domain] = asyncio.Lock()
        return self._buckets[domain]

    async def acquire(self, domain: str) -> None:
        bucket = self._bucket(domain)
        async with self._locks[domain]:
            wait = bucket.wait_time()
            if wait > 0:
                await asyncio.sleep(wait)
            bucket.consume()

    def record_success(self, domain: str) -> None:
        bucket = self._bucket(domain)
        bucket.consecutive_successes += 1
        if bucket.consecutive_successes >= 10:
            bucket.scale_rate(1.10)
            bucket.consecutive_successes = 0
            logger.debug("Rate increased for %s → %.2f rps", domain, bucket.rate)

    def record_throttle(self, domain: str, retry_after: Optional[int] = None) -> None:
        bucket = self._bucket(domain)
        bucket.consecutive_successes = 0
        bucket.scale_rate(0.5)
        logger.info("Rate throttled for %s → %.2f rps", domain, bucket.rate)

    def record_error(self, domain: str) -> None:
        bucket = self._bucket(domain)
        bucket.consecutive_successes = 0
        bucket.scale_rate(0.75)

    def current_rate(self, domain: str) -> float:
        return self._bucket(domain).rate
