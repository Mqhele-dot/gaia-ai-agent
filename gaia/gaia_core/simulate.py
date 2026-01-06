"""Simulation utilities for Gaia."""
from __future__ import annotations

from typing import Dict, List

from .policy import check_capsule


def simulate(text: str) -> Dict[str, object]:
    """Produce a deterministic mock simulation for *text*.

    The policy gate is consulted first; violations raise ``ValueError`` with a
    descriptive message so the caller can relay the issue to the client.
    """
    result = check_capsule(text)
    if not result["ok"]:
        reasons = "; ".join(result["reasons"]) or "Policy check failed."
        raise ValueError(f"Disallowed by policy: {reasons}")

    tokens = [tok for tok in text.split() if tok]
    unique = sorted(set(tokens), key=tokens.index)

    plan: List[str] = [
        "Review capsule objectives for alignment with ethical policies.",
        "Identify stakeholders and environmental considerations.",
        "Outline actionable steps prioritizing transparency and consent.",
        "Summarize expected benefits and responsible safeguards.",
    ]

    if unique:
        plan.append(f"Incorporate {unique[0]} as an initial focus area.")

    risks = [
        "Stakeholder misunderstanding if communication is unclear.",
        "Resource constraints may limit full implementation.",
        "Monitoring required to ensure ongoing compliance with policy.",
    ]

    length_factor = min(len(tokens) / 120.0, 1.0)
    impact = {
        "economy": round(0.2 + 0.6 * length_factor, 2),
        "environment": round(0.3 + 0.5 * (1 - abs(0.5 - length_factor)), 2),
    }

    return {
        "plan": plan,
        "risks": risks,
        "expected_impact": impact,
    }


__all__ = ["simulate"]
