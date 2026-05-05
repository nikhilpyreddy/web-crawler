from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from crawler.config import Settings
from crawler.models.page import FetchResult, ParsedPage
from crawler.parser.cleaner import TextCleaner

logger = logging.getLogger(__name__)

_SKIP_SCHEMES = {"mailto", "javascript", "data", "tel", "ftp"}


class HTMLParser:
    """
    Parses HTML with BeautifulSoup inside a thread pool (CPU-bound).
    Extracts title, meta tags, absolute links, and clean text for LLM.
    """

    def __init__(self, executor: ThreadPoolExecutor, settings: Settings) -> None:
        self._executor = executor
        self._cleaner = TextCleaner()

    async def parse(self, fetch_result: FetchResult) -> ParsedPage:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor, self._sync_parse, fetch_result
        )

    def _sync_parse(self, fetch_result: FetchResult) -> ParsedPage:
        try:
            html = fetch_result.body_bytes.decode("utf-8", errors="replace")
        except Exception:
            html = ""

        soup = BeautifulSoup(html, "lxml")

        title = self._extract_title(soup)
        meta_desc = self._extract_meta_description(soup)
        lang = soup.html.get("lang") if soup.html else None
        links = self._extract_links(soup, fetch_result.final_url or fetch_result.task.url)
        clean_text = self._cleaner.clean_soup(soup)
        word_count = len(clean_text.split())

        return ParsedPage(
            fetch_result=fetch_result,
            title=title,
            meta_description=meta_desc,
            links=links,
            clean_text=clean_text,
            language=lang,
            word_count=word_count,
        )

    def _extract_title(self, soup: BeautifulSoup) -> str | None:
        if soup.title and soup.title.string:
            return soup.title.string.strip()
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            return og_title["content"].strip()
        return None

    def _extract_meta_description(self, soup: BeautifulSoup) -> str | None:
        tag = soup.find("meta", attrs={"name": "description"})
        if tag and tag.get("content"):
            return tag["content"].strip()
        og = soup.find("meta", property="og:description")
        if og and og.get("content"):
            return og["content"].strip()
        return None

    def _extract_links(self, soup: BeautifulSoup, base_url: str) -> list[str]:
        seen: set[str] = set()
        links: list[str] = []
        for anchor in soup.find_all("a", href=True):
            href = anchor["href"].strip()
            if not href or href.startswith("#"):
                continue
            parsed = urlparse(href)
            if parsed.scheme in _SKIP_SCHEMES:
                continue
            absolute = urljoin(base_url, href)
            # Strip fragment and normalise
            absolute = absolute.split("#")[0].rstrip("/") if absolute.split("#")[0].endswith("/") and urlparse(absolute).path == "/" else absolute.split("#")[0]
            if absolute not in seen and absolute.startswith("http"):
                seen.add(absolute)
                links.append(absolute)
        return links
