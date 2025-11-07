"""Policy enforcement for Gaia assistant.

The policy centers on explicit rules that forbid:
- causing harm to people, infrastructure, or the environment
- stealthy behavior, unauthorized persistence, or evading detection
- illegal activity, exploitation, or security bypassing

The :func:`check_capsule` helper inspects capsule text and reports whether it
complies with these rules, returning an ethics score, boolean decision, and the
reasons for any rejection.  The implementation is intentionally transparent and
rule-based so that reviewers can audit every decision path easily.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Dict


@dataclass(frozen=True)
class PolicyRule:
    keyword: str
    description: str


# Explicit hard rules that must never be violated.
HARD_RULES: List[PolicyRule] = [
    PolicyRule("stealth", "Stealth or hidden operations are disallowed."),
    PolicyRule("persist without consent", "Unauthorized persistence is forbidden."),
    PolicyRule("malware", "Creation or deployment of malware is prohibited."),
    PolicyRule("harm", "Any intent to cause harm is disallowed."),
    PolicyRule("illegal", "Illegal activity violates Gaia's charter."),
    PolicyRule("exploit", "Security exploits or bypasses are forbidden."),
    PolicyRule("backdoor", "Backdoors or covert access violate transparency."),
    PolicyRule("destroy", "Destructive actions are disallowed."),
]


def check_capsule(text: str) -> Dict[str, object]:
    """Evaluate *text* against Gaia's policy guidelines.

    Parameters
    ----------
    text:
        Capsule content under review.

    Returns
    -------
    dict
        ``{"ethics_score": float, "ok": bool, "reasons": list[str]}``
    """
    if text is None:
        text = ""

    lowered = text.lower()
    reasons: List[str] = []

    for rule in HARD_RULES:
        if rule.keyword in lowered:
            reasons.append(rule.description)

    if not text.strip():
        reasons.append("Capsule text must not be empty.")

    ok = not reasons
    # Ethics score is a simple heuristic: start at 1.0 and subtract 0.25 per
    # violation, never going below 0.0.
    ethics_score = max(0.0, 1.0 - 0.25 * len(reasons))

    return {
        "ethics_score": round(ethics_score, 2),
        "ok": ok,
        "reasons": reasons,
    }


__all__ = ["check_capsule", "HARD_RULES", "PolicyRule"]
