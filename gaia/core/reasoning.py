from __future__ import annotations

from typing import Any

from .policy import evaluate


def analyze_capsule(text: str) -> dict[str, Any]:
    policy = evaluate(text)
    tokens = [token for token in text.split() if token]
    unique = len({token.lower() for token in tokens})
    lexical_diversity = round(unique / max(len(tokens), 1), 3)
    return {
        "policy": policy,
        "length": len(text),
        "tokens": len(tokens),
        "unique_tokens": unique,
        "lexical_diversity": lexical_diversity,
        "complexity_score": round(min(1.0, lexical_diversity * 1.2), 3),
    }


def simulate_capsule(text: str) -> dict[str, Any]:
    policy = evaluate(text)
    if not policy["ok"]:
        return {"ok": False, "policy": policy, "plan": [], "risks": [], "impact": {}}
    tokens = [token for token in text.split() if token]
    depth = min(len(tokens) / 120.0, 1.0)
    return {
        "ok": True,
        "policy": policy,
        "plan": [
            "Clarify the objective and measurable success criteria.",
            "Identify stakeholders, dependencies, and environmental constraints.",
            "Run a bounded pilot before scaling.",
            "Measure outcomes and compare them with the baseline.",
        ],
        "risks": [
            "Evidence may be incomplete or context-specific.",
            "Implementation costs may differ from the initial estimate.",
            "Operational monitoring is required before broader deployment.",
        ],
        "impact": {
            "economic": round(0.35 + 0.45 * depth, 2),
            "environmental": round(0.4 + 0.4 * depth, 2),
            "confidence": round(0.45 + 0.35 * depth, 2),
        },
    }


def evaluate_run(*, research_ok: bool, analysis_ok: bool, simulation_ok: bool, novelty: float = 0.5) -> dict[str, Any]:
    components = [float(research_ok), float(analysis_ok), float(simulation_ok), max(0.0, min(1.0, novelty))]
    score = round(sum(components) / len(components), 3)
    return {
        "score": score,
        "research_success": research_ok,
        "analysis_success": analysis_ok,
        "simulation_success": simulation_ok,
        "novelty_score": round(max(0.0, min(1.0, novelty)), 3),
    }
