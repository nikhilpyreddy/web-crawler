from __future__ import annotations

import asyncio
import hashlib
import logging

from crawler.config import Settings
from crawler.extraction.chains import ClassifyChain, ExtractChain
from crawler.models.extracted import ExtractedData, PageClassification
from crawler.models.page import ParsedPage

logger = logging.getLogger(__name__)


class LLMExtractor:
    """
    Orchestrates classification + structured extraction via LangChain chains.

    A semaphore caps concurrent OpenAI API calls to avoid rate-limit errors.
    ChatOpenAI supports native async via .ainvoke(), so no thread pool needed here.
    """

    def __init__(self, settings: Settings) -> None:
        self._classify_chain = ClassifyChain(settings)
        self._extract_chain = ExtractChain(settings)
        self._sem = asyncio.Semaphore(settings.llm_max_concurrency)
        self._enabled = settings.openai_api_key is not None

    async def extract(self, page: ParsedPage) -> ExtractedData:
        task = page.fetch_result.task
        text_hash = hashlib.sha256(page.clean_text.encode()).hexdigest()

        if not self._enabled:
            return self._fallback(page, text_hash)

        async with self._sem:
            return await self._run_chains(page, text_hash)

    async def _run_chains(self, page: ParsedPage, text_hash: str) -> ExtractedData:
        task = page.fetch_result.task

        classify_inputs = {
            "url": task.url,
            "title": page.title or "",
            "meta_description": page.meta_description or "",
            "text_snippet": page.clean_text[:500],
        }
        classification = await self._classify_chain.ainvoke(classify_inputs)

        structured: dict = {}
        summary = ""
        key_entities: list[str] = []
        title = page.title or ""

        if classification.should_extract:
            extract_inputs = {
                "url": task.url,
                "title": page.title or "",
                "category": classification.category.value,
                "clean_text": page.clean_text,
            }
            result = await self._extract_chain.ainvoke(extract_inputs)
            title = result.get("title") or title
            summary = result.get("summary", "")
            key_entities = result.get("key_entities", [])
            structured = result.get("structured_fields", {})

        return ExtractedData(
            task_id=task.task_id,
            url=task.url,
            classification=classification,
            title=title,
            summary=summary,
            key_entities=key_entities,
            structured_fields=structured,
            outbound_links=page.links,
            raw_text_hash=text_hash,
        )

    def _fallback(self, page: ParsedPage, text_hash: str) -> ExtractedData:
        """Used when no OpenAI key is configured — still extracts links and metadata."""
        return ExtractedData(
            task_id=page.fetch_result.task.task_id,
            url=page.fetch_result.task.url,
            classification=PageClassification(),
            title=page.title or "",
            summary=page.meta_description or "",
            key_entities=[],
            structured_fields={},
            outbound_links=page.links,
            raw_text_hash=text_hash,
        )
