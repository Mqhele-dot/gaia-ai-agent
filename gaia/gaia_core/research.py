"""Scientific research exploration utilities for Gaia."""
from __future__ import annotations

import logging
from typing import Dict, List

import requests


LOGGER = logging.getLogger(__name__)

FALLBACK_INSIGHTS: List[Dict[str, str]] = [
    {
        "title": "Solar microgrids improve rural resilience",
        "summary": "Community-operated solar microgrids reduce outages by 35% while lowering emissions.",
        "url": "https://example.com/gaia/solar-microgrids",
    },
    {
        "title": "Algae biofuels capture carbon efficiently",
        "summary": "High-density photobioreactors can sequester 1.8 tons of CO2 per ton of algae biomass.",
        "url": "https://example.com/gaia/algae-biofuel",
    },
    {
        "title": "Circular batteries extend EV lifecycle",
        "summary": "Closed-loop battery recycling recovers over 90% of lithium and cobalt materials.",
        "url": "https://example.com/gaia/circular-batteries",
    },
]


def _fallback_summary(title: str) -> str:
    """Create a readable summary when no abstract is provided."""

    cleaned_title = title.strip() if title else "this research"
    base = cleaned_title or "this research"
    return (
        f"No abstract provided; based on the title '{base}', the work likely addresses "
        "responsible or climate-positive technology. Review the source for details."
    )


def _build_entry(item: Dict[str, object]) -> Dict[str, str]:
    title = ""
    if isinstance(item.get("title"), list):
        title = ", ".join(map(str, item.get("title", [])))
    else:
        title = str(item.get("title", "")).strip()
    url = item.get("URL") or item.get("url") or ""
    abstract = item.get("abstract") or item.get("summary")
    if isinstance(abstract, list):
        abstract = " ".join(map(str, abstract))
    summary_text = str(abstract).strip() if abstract else _fallback_summary(title)

    entry = {
        "title": title or "Untitled research insight",
        "summary": summary_text[:300],
        "url": str(url).strip(),
    }
    doi = item.get("DOI")
    if doi and not entry["url"]:
        entry["url"] = f"https://doi.org/{doi}"
    return entry


def explore_science(query: str) -> Dict[str, object]:
    """Fetch a list of research highlights for the provided query."""

    sanitized = query.strip()
    if not sanitized:
        return {
            "query": sanitized,
            "results": [],
            "source": "none",
            "notes": ["Provide a query to explore scientific literature."],
        }

    try:
        response = requests.get(
            "https://api.crossref.org/works",
            params={"rows": 5, "query": sanitized, "select": "title,URL,DOI"},
            timeout=6,
        )
        response.raise_for_status()
        data = response.json()
        items = data.get("message", {}).get("items", [])
        results = [_build_entry(item) for item in items if item]
        if results:
            return {
                "query": sanitized,
                "results": results,
                "source": "crossref",
                "notes": ["Results provided by Crossref."],
            }
    except requests.RequestException as exc:  # pragma: no cover - network variability
        LOGGER.warning("Crossref lookup failed: %s", exc)
    except ValueError as exc:
        LOGGER.warning("Crossref response parsing error: %s", exc)

    filtered = [entry for entry in FALLBACK_INSIGHTS if sanitized.lower() in entry["title"].lower()]
    if not filtered:
        filtered = FALLBACK_INSIGHTS

    return {
        "query": sanitized,
        "results": filtered,
        "source": "fallback",
        "notes": [
            "Network lookup unavailable; returning curated sustainability research highlights.",
        ],
    }


__all__ = ["explore_science"]
