from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate

CLASSIFY_SYSTEM = """\
You are a precise web page classifier. Given metadata about a web page, output a JSON object
that classifies the page. Return ONLY valid JSON—no prose, no markdown fences.

Schema:
{
  "category": one of ["article","product","forum_post","documentation","landing_page","error","other"],
  "confidence": float between 0.0 and 1.0,
  "language": ISO-639-1 language code (e.g. "en"),
  "should_extract": true if the page contains meaningful structured content worth extracting
}
"""

CLASSIFY_HUMAN = """\
URL: {url}
Title: {title}
Meta description: {meta_description}
Text snippet (first 500 chars): {text_snippet}
"""

CLASSIFY_PROMPT = ChatPromptTemplate.from_messages(
    [("system", CLASSIFY_SYSTEM), ("human", CLASSIFY_HUMAN)]
)


EXTRACT_SYSTEM = """\
You are a structured data extractor. Given the cleaned text of a web page, extract key information
and return a single JSON object. Return ONLY valid JSON—no prose, no markdown fences.

Schema:
{
  "title": string,
  "summary": string (2-3 sentence summary),
  "key_entities": list of strings (people, organisations, products, places),
  "structured_fields": object with domain-relevant key-value pairs
    (e.g. for articles: {"author": ..., "published_date": ..., "tags": [...]},
         for products: {"price": ..., "brand": ..., "sku": ...},
         for docs: {"version": ..., "section": ...})
}
"""

EXTRACT_HUMAN = """\
URL: {url}
Title: {title}
Category: {category}
Full text (truncated to 8000 chars):
{clean_text}
"""

EXTRACT_PROMPT = ChatPromptTemplate.from_messages(
    [("system", EXTRACT_SYSTEM), ("human", EXTRACT_HUMAN)]
)
