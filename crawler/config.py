from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="CRAWLER_",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Redis
    redis_url: str = "redis://localhost:6379/0"
    redis_pool_size: int = 20
    redis_stream_name: str = "crawler:tasks"
    redis_consumer_group: str = "workers"
    redis_checkpoint_key: str = "crawler:checkpoint"
    redis_dedup_key: str = "crawler:seen"

    # Crawl behaviour
    max_depth: int = 3
    max_pages: int = 100_000
    concurrency: int = 50
    thread_pool_size: int = 8
    request_timeout_seconds: float = 30.0
    max_retries: int = 3
    base_backoff_seconds: float = 1.0
    max_backoff_seconds: float = 60.0

    # Per-domain rate limiting
    default_rate_limit_rps: float = 2.0
    min_rate_limit_rps: float = 0.5
    max_rate_limit_rps: float = 10.0

    # LLM / OpenAI
    openai_api_key: Optional[SecretStr] = None
    openai_model: str = "gpt-4o-mini"
    llm_temperature: float = 0.0
    llm_max_tokens: int = 1024
    llm_request_timeout: float = 30.0
    llm_max_concurrency: int = 10

    # HTTP client
    user_agent: str = "Mozilla/5.0 (compatible; AsyncCrawler/1.0; +https://github.com/example/web-crawler)"
    follow_redirects: bool = True
    max_redirects: int = 5

    # Output
    output_dir: Path = Path("./output")
    log_level: str = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
