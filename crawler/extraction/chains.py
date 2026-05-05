from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.output_parsers import JsonOutputParser
from langchain_openai import ChatOpenAI

from crawler.config import Settings
from crawler.extraction.prompts import CLASSIFY_PROMPT, EXTRACT_PROMPT
from crawler.models.extracted import PageCategory, PageClassification

logger = logging.getLogger(__name__)


def _build_llm(settings: Settings) -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.openai_model,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
        timeout=settings.llm_request_timeout,
        api_key=settings.openai_api_key.get_secret_value() if settings.openai_api_key else None,
    )


class ClassifyChain:
    """Classifies a page into a category using an LLM."""

    def __init__(self, settings: Settings) -> None:
        llm = _build_llm(settings)
        self._chain = CLASSIFY_PROMPT | llm | JsonOutputParser()

    async def ainvoke(self, inputs: dict[str, str]) -> PageClassification:
        try:
            raw: dict[str, Any] = await self._chain.ainvoke(inputs)
            return PageClassification(
                category=PageCategory(raw.get("category", "other")),
                confidence=float(raw.get("confidence", 0.0)),
                language=raw.get("language", "en"),
                should_extract=bool(raw.get("should_extract", True)),
            )
        except Exception as e:
            logger.warning("Classification failed: %s", e)
            return PageClassification()


class ExtractChain:
    """Extracts structured data from page text using an LLM."""

    def __init__(self, settings: Settings) -> None:
        llm = _build_llm(settings)
        self._chain = EXTRACT_PROMPT | llm | JsonOutputParser()

    async def ainvoke(self, inputs: dict[str, Any]) -> dict[str, Any]:
        try:
            return await self._chain.ainvoke(inputs)
        except Exception as e:
            logger.warning("Extraction failed: %s", e)
            return {
                "title": inputs.get("title", ""),
                "summary": "",
                "key_entities": [],
                "structured_fields": {},
            }
