from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, HttpUrl, field_validator


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class CrawlTask(BaseModel):
    task_id: str = ""
    url: str
    depth: int = 0
    parent_url: Optional[str] = None
    priority: float = 0.0
    status: TaskStatus = TaskStatus.PENDING
    retry_count: int = 0
    created_at: datetime = None  # type: ignore[assignment]
    enqueued_at: Optional[datetime] = None
    domain: str = ""
    metadata: dict[str, Any] = {}

    def model_post_init(self, __context: Any) -> None:
        if not self.task_id:
            self.task_id = str(uuid.uuid4())
        if self.created_at is None:
            self.created_at = datetime.now(timezone.utc)
        if not self.domain:
            parsed = urlparse(self.url)
            self.domain = parsed.netloc

    def to_redis_dict(self) -> dict[str, str]:
        """Serialize to flat string dict for Redis Streams."""
        return {
            "task_id": self.task_id,
            "url": self.url,
            "depth": str(self.depth),
            "parent_url": self.parent_url or "",
            "priority": str(self.priority),
            "status": self.status.value,
            "retry_count": str(self.retry_count),
            "created_at": self.created_at.isoformat(),
            "enqueued_at": self.enqueued_at.isoformat() if self.enqueued_at else "",
            "domain": self.domain,
        }

    @classmethod
    def from_redis_dict(cls, data: dict[str, str]) -> "CrawlTask":
        return cls(
            task_id=data["task_id"],
            url=data["url"],
            depth=int(data["depth"]),
            parent_url=data["parent_url"] or None,
            priority=float(data["priority"]),
            status=TaskStatus(data["status"]),
            retry_count=int(data["retry_count"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            enqueued_at=datetime.fromisoformat(data["enqueued_at"]) if data.get("enqueued_at") else None,
            domain=data["domain"],
        )
