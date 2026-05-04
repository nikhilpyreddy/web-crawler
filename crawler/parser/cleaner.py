from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag

_NOISE_TAGS = {"script", "style", "nav", "footer", "header", "aside", "noscript", "iframe", "form"}
_MAX_TEXT_CHARS = 8_000
_WS_RE = re.compile(r"\s+")


class TextCleaner:
    """
    Strips boilerplate HTML tags and collapses whitespace to produce
    clean text suitable for LLM context. Runs synchronously inside a thread pool.
    """

    def clean(self, html: str | bytes) -> str:
        soup = BeautifulSoup(html, "lxml")
        for tag in soup.find_all(_NOISE_TAGS):
            tag.decompose()
        text = soup.get_text(separator=" ")
        text = _WS_RE.sub(" ", text).strip()
        return text[:_MAX_TEXT_CHARS]

    def clean_soup(self, soup: BeautifulSoup) -> str:
        for tag in soup.find_all(_NOISE_TAGS):
            if isinstance(tag, Tag):
                tag.decompose()
        text = soup.get_text(separator=" ")
        text = _WS_RE.sub(" ", text).strip()
        return text[:_MAX_TEXT_CHARS]
