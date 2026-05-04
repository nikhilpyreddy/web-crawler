from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from crawler.models.task import CrawlTask


class FetchResult(BaseModel):
    task: CrawlTask
    status_code: int
    headers: dict[str, str] = {}
    body_bytes: bytes = b""
    content_type: str = ""
    fetch_duration_ms: float = 0.0
    final_url: str = ""
    error: Optional[str] = None

    model_config = {"arbitrary_types_allowed": True}

    @property
    def is_html(self) -> bool:
        return "text/html" in self.content_type

    @property
    def succeeded(self) -> bool:
        return self.error is None and 200 <= self.status_code < 300


class ParsedPage(BaseModel):
    fetch_result: FetchResult
    title: Optional[str] = None
    meta_description: Optional[str] = None
    links: list[str] = []
    clean_text: str = ""
    language: Optional[str] = None
    word_count: int = 0

    model_config = {"arbitrary_types_allowed": True}
