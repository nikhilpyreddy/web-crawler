from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel


class PageCategory(str, Enum):
    ARTICLE = "article"
    PRODUCT = "product"
    FORUM_POST = "forum_post"
    DOCUMENTATION = "documentation"
    LANDING_PAGE = "landing_page"
    ERROR = "error"
    OTHER = "other"


class PageClassification(BaseModel):
    category: PageCategory = PageCategory.OTHER
    confidence: float = 0.0
    language: str = "en"
    should_extract: bool = True


class ExtractedData(BaseModel):
    task_id: str
    url: str
    crawled_at: datetime = None  # type: ignore[assignment]
    classification: PageClassification = PageClassification()
    title: str = ""
    summary: str = ""
    key_entities: list[str] = []
    structured_fields: dict[str, Any] = {}
    outbound_links: list[str] = []
    raw_text_hash: str = ""

    def model_post_init(self, __context: Any) -> None:
        if self.crawled_at is None:
            self.crawled_at = datetime.now(timezone.utc)
