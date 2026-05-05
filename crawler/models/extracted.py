from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


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
    crawled_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    classification: PageClassification = PageClassification()
    title: str = ""
    summary: str = ""
    key_entities: list[str] = []
    structured_fields: dict[str, Any] = {}
    outbound_links: list[str] = []
    raw_text_hash: str = ""

