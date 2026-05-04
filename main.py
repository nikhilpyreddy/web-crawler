#!/usr/bin/env python3
"""
Async Web Crawler — entry point.

Usage:
    python main.py https://example.com https://example.org
    python main.py --depth 5 --concurrency 100 --resume https://example.com
    python main.py --max-pages 50000 --output ./data https://example.com
"""
from __future__ import annotations

import asyncio
import logging
import sys
from typing import Optional

import typer

from crawler.config import Settings, get_settings
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
    # Silence noisy third-party loggers
    for noisy in ("httpcore", "httpx", "openai", "urllib3", "aiohttp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


@app.command()
def crawl(
    urls: list[str] = typer.Argument(..., help="Seed URL(s) to start crawling from"),
    depth: Optional[int] = typer.Option(None, "--depth", "-d", help="Max crawl depth (overrides env)"),
    concurrency: Optional[int] = typer.Option(None, "--concurrency", "-c", help="Async worker count"),
    max_pages: Optional[int] = typer.Option(None, "--max-pages", "-n", help="Stop after N pages"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Output directory path"),
    resume: bool = typer.Option(False, "--resume", "-r", help="Resume from last checkpoint"),
    log_level: str = typer.Option("INFO", "--log-level", "-l", help="Logging level"),
    redis_url: Optional[str] = typer.Option(None, "--redis", help="Redis URL (overrides env)"),
) -> None:
    """Crawl one or more seed URLs, classify pages with an LLM, and store results as JSONL."""

    settings = get_settings()

    # CLI overrides
    if depth is not None:
        settings.max_depth = depth
    if concurrency is not None:
        settings.concurrency = concurrency
    if max_pages is not None:
        settings.max_pages = max_pages
    if output is not None:
        from pathlib import Path
        settings.output_dir = Path(output)
    if redis_url is not None:
        settings.redis_url = redis_url

    _configure_logging(log_level)
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
    """Clear Redis state (dedup set, task queue, checkpoint) to start fresh."""
    import redis as sync_redis

    settings = get_settings()
    if redis_url:
        settings.redis_url = redis_url

    r = sync_redis.from_url(settings.redis_url, decode_responses=True)
    keys = [
        settings.redis_dedup_key,
        settings.redis_checkpoint_key,
        settings.redis_stream_name,
    ]
    deleted = r.delete(*keys)
    typer.echo(f"Deleted {deleted} Redis keys. Ready for a fresh crawl.")


if __name__ == "__main__":
    app()
