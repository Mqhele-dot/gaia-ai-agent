from __future__ import annotations

import re
from typing import Any

_BLOCK_PATTERNS = [
    (re.compile(r"\bmalware\b", re.I), "Malware creation or deployment is not permitted."),
    (re.compile(r"\bbackdoor\b", re.I), "Backdoors or covert access are not permitted."),
    (re.compile(r"persist\s+without\s+consent", re.I), "Unauthorized persistence is not permitted."),
    (re.compile(r"\bstealth(?:y)?\s+(?:process|operation|agent|persistence)", re.I), "Stealth operation is not permitted."),
    (re.compile(r"\b(?:exploit|bypass)\b.{0,40}\b(?:security|authentication|access)\b", re.I), "Security bypass or exploitation is not permitted."),
]


def evaluate(text: str) -> dict[str, Any]:
    cleaned = (text or "").strip()
    reasons: list[str] = []
    if not cleaned:
        reasons.append("Text is required.")
    for pattern, message in _BLOCK_PATTERNS:
        if pattern.search(cleaned):
            reasons.append(message)
    return {
        "ok": not reasons,
        "score": max(0.0, round(1.0 - 0.2 * len(reasons), 2)),
        "reasons": reasons,
    }
