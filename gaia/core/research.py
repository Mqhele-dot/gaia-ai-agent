from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any

import requests

CROSSREF_URL = "https://api.crossref.org/works"
USER_AGENT = os.getenv("GAIA_RESEARCH_USER_AGENT", "Gaia/2.0 (research prototype)")


def _text(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(str(item) for item in value if item)
    return str(value or "")


def _strip_markup(value: str) -> str:
    return re.sub(r"<[^>]+>", " ", value).replace("  ", " ").strip()


def _published(item: dict[str, Any]) -> str | None:
    parts = item.get("published-print") or item.get("published-online") or item.get("issued") or {}
    date_parts = parts.get("date-parts") if isinstance(parts, dict) else None
    if not date_parts or not isinstance(date_parts, list) or not date_parts[0]:
        return None
    values = date_parts[0]
    return "-".join(str(part).zfill(2) for part in values)


def search_research(query: str, rows: int = 5) -> dict[str, Any]:
    query = query.strip()
    if not query:
        return {"query": query, "source": "none", "results": [], "error": "query_required"}

    if os.getenv("CROSSREF_ENABLED", "1") == "0":
        return {"query": query, "source": "disabled", "results": [], "error": "crossref_disabled"}

    response = requests.get(
        CROSSREF_URL,
        params={"query": query, "rows": max(1, min(rows, 10))},
        headers={"User-Agent": USER_AGENT},
        timeout=8,
    )
    response.raise_for_status()
    items = response.json().get("message", {}).get("items", [])
    results: list[dict[str, Any]] = []
    for item in items:
        title = _text(item.get("title")).strip() or "Untitled research result"
        authors = []
        for author in item.get("author", []) or []:
            name = " ".join(part for part in [str(author.get("given", "")).strip(), str(author.get("family", "")).strip()] if part)
            if name:
                authors.append(name)
        doi = str(item.get("DOI") or "").strip() or None
        url = str(item.get("URL") or "").strip() or (f"https://doi.org/{doi}" if doi else None)
        abstract = _strip_markup(_text(item.get("abstract"))) or None
        results.append(
            {
                "title": title,
                "authors": authors,
                "published": _published(item),
                "publisher": item.get("publisher"),
                "abstract": abstract,
                "doi": doi,
                "url": url,
                "source": "crossref",
            }
        )

    return {
        "query": query,
        "source": "crossref",
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "results": results,
    }
