"""Heuristic insight generation to surface Gaia's autonomous reflections."""
from __future__ import annotations

from collections import Counter
from statistics import mean
from typing import Dict, List

from .metrics import tracker
from .storage import list_capsules


def _confidence(value: float) -> float:
    return max(0.2, min(0.95, round(value, 3)))


def _summarise_focus(capsules: List[Dict[str, object]]) -> Dict[str, object]:
    tags = Counter((capsule.get("tag") or "general").lower() for capsule in capsules)
    if not tags:
        return {"tag": "general", "share": 0.0, "unique": 0}
    tag, count = tags.most_common(1)[0]
    unique = len(tags)
    share = count / max(len(capsules), 1)
    return {"tag": tag, "share": share, "unique": unique}


def generate_insights(limit: int = 3) -> Dict[str, object]:
    """Return a structured reflection about stored capsules and learning momentum."""

    capsules = list_capsules()
    status = tracker().get_status()
    learning_history = [float(value) for value in status.get("learning_history", []) if isinstance(value, (int, float))]

    if not capsules:
        return {
            "summary": "No capsules captured yet. Gaia is awaiting new knowledge to reflect on.",
            "insights": [],
            "metadata": {
                "capsules": 0,
                "focus_tags": 0,
                "learning_average": 0.0,
            },
            "autonomous_thought": "I am ready to analyse fresh ideas focused on sustainability and equitable technology.",
            "autonomous_reflections": [
                "Collect at least one capsule to trigger strategic reflections.",
                "Consider focusing on a specific sustainability theme to guide exploration.",
            ],
        }

    focus = _summarise_focus(capsules)
    average_length = mean(len(str(capsule.get("text", ""))) for capsule in capsules)
    recent_capsule = max(capsules, key=lambda item: item.get("created_at", ""))
    insights: List[Dict[str, object]] = []

    insights.append(
        {
            "title": "Primary focus area",
            "message": (
                f"{focus['tag'].title()} topics represent {focus['share'] * 100:.0f}% of stored capsules across {focus['unique']} distinct tags."
            ),
            "confidence": _confidence(0.5 + focus["share"] / 2),
            "indicator": {
                "label": focus["tag"].title(),
                "value": round(focus["share"] * 100, 1),
                "unit": "% of capsules",
            },
        }
    )

    reflections: List[str] = []

    if learning_history:
        average_delta = mean(learning_history[-10:])
        latest_delta = learning_history[-1]
        direction = "improving" if latest_delta >= average_delta else "stabilising"
        insights.append(
            {
                "title": "Learning momentum",
                "message": (
                    f"Recent learning updates average {average_delta:.3f} with the latest change of {latest_delta:.3f}, "
                    f"indicating {direction} performance."
                ),
                "confidence": _confidence(0.55 + max(0.0, latest_delta)),
                "indicator": {
                    "label": "Δ score",
                    "value": round(latest_delta, 3),
                    "unit": "delta",
                },
            }
        )
        reflections.append(
            (
                "Learning momentum is {direction}, with the latest delta at {delta:+.3f}."
            ).format(direction=direction, delta=latest_delta)
        )
    else:
        reflections.append("Learning history is minimal — schedule a step to build momentum.")

    complexity_score = min(1.0, average_length / 320)
    insights.append(
        {
            "title": "Narrative depth",
            "message": (
                f"Average capsule length is {average_length:.0f} characters, supporting a complexity score of {complexity_score:.2f}."
            ),
            "confidence": _confidence(0.45 + complexity_score / 2),
            "indicator": {
                "label": "Complexity",
                "value": round(complexity_score * 100, 1),
                "unit": "score",
            },
        }
    )
    if complexity_score >= 0.65:
        reflections.append("Capsule narratives are rich — maintain depth while highlighting measurable outcomes.")
    else:
        reflections.append("Capsule narratives are concise — consider expanding on impact metrics for clarity.")

    insights = insights[:limit]

    autonomous_thought = (
        "Focusing on {tag} efforts could unlock greater environmental impact; I recommend researching complementary policies next."
    ).format(tag=focus["tag"].replace("-", " "))

    reflections.append(
        (
            "Focus area '{tag}' accounts for {share:.0f}% of capsules across {unique} unique tags."
        ).format(tag=focus["tag"], share=focus["share"] * 100, unique=focus["unique"])
    )

    return {
        "summary": (
            f"Gaia has analysed {len(capsules)} capsules spanning {focus['unique']} focus areas. "
            f"Latest entry tagged '{focus['tag']}' was captured at {recent_capsule.get('created_at', 'unknown time')}"
        ),
        "insights": insights,
        "metadata": {
            "capsules": len(capsules),
            "focus_tags": focus["unique"],
            "learning_average": mean(learning_history) if learning_history else 0.0,
            "latest_version": status.get("learning_version"),
        },
        "autonomous_thought": autonomous_thought,
        "autonomous_reflections": reflections,
        "recommended_next": [
            "Capture a fresh capsule expanding on the leading focus area.",
            "Run a simulation to validate expected community impact.",
            "Schedule a learning step to consolidate new findings.",
        ],
    }


__all__ = ["generate_insights"]
