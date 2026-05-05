#!/usr/bin/env python3
"""
Async Web Crawler — entry point.

Usage:
    python main.py crawl https://example.com https://example.org
    python main.py crawl --depth 5 --concurrency 100 --resume https://example.com
    python main.py crawl --max-pages 50000 --output ./data https://example.com
    python main.py reset
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Optional

import typer

from crawler.config import get_settings
from crawler.core.engine import CrawlEngine

app = typer.Typer(
    name="web-crawler",
    help="High-throughput async web crawler with LLM-based content extraction.",
    add_completion=False,
)


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    for noisy in ("httpcore", "httpx", "openai", "urllib3", "aiohttp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


@app.command()
def crawl(
    urls: list[str] = typer.Argument(..., help="Seed URL(s) to start crawling from"),
    depth: Optional[int] = typer.Option(None, "--depth", "-d", help="Max crawl depth"),
    concurrency: Optional[int] = typer.Option(None, "--concurrency", "-c", help="Async worker count"),
    max_pages: Optional[int] = typer.Option(None, "--max-pages", "-n", help="Stop after N pages"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Output directory path"),
    resume: bool = typer.Option(False, "--resume", "-r", help="Resume from last checkpoint"),
    log_level: str = typer.Option("INFO", "--log-level", "-l", help="Logging level"),
    redis_url: Optional[str] = typer.Option(None, "--redis", help="Redis URL"),
) -> None:
    """Crawl seed URLs, classify pages with an LLM, and write results as JSONL."""

    _configure_logging(log_level)

    # Build a fresh Settings, then apply CLI overrides via model_copy so the
    # cached singleton in get_settings() is never mutated.
    overrides: dict = {}
    if depth is not None:
        overrides["max_depth"] = depth
    if concurrency is not None:
        overrides["concurrency"] = concurrency
    if max_pages is not None:
        overrides["max_pages"] = max_pages
    if output is not None:
        overrides["output_dir"] = Path(output)
    if redis_url is not None:
        overrides["redis_url"] = redis_url

    settings = get_settings().model_copy(update=overrides)

    logger = logging.getLogger(__name__)
    logger.info("Starting crawl of %d seed URL(s)", len(urls))

    engine = CrawlEngine(settings)
    try:
        asyncio.run(engine.start(urls, resume=resume))
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)


@app.command()
def reset(
    redis_url: Optional[str] = typer.Option(None, "--redis", help="Redis URL"),
) -> None:
    """Clear Redis state (dedup set, task queue, checkpoint) for a fresh crawl."""
    import redis as sync_redis

    settings = get_settings()
    url = redis_url or settings.redis_url

    r = sync_redis.from_url(url, decode_responses=True)
    keys = [
        settings.redis_dedup_key,
        settings.redis_checkpoint_key,
        settings.redis_stream_name,
    ]
    deleted = r.delete(*keys)
    typer.echo(f"Deleted {deleted} Redis key(s). Ready for a fresh crawl.")


if __name__ == "__main__":
    app()
