"""Upgrade proposal evaluation for Gaia."""
from __future__ import annotations

from typing import Dict, List

from .policy import check_capsule
from .storage import record_upgrade_decision


def propose_upgrade(payload: Dict[str, object]) -> Dict[str, object]:
    proposal_text = str(payload.get("proposal", ""))
    rationale = str(payload.get("rationale", ""))
    combined = f"{proposal_text}\n{rationale}".strip()
    policy_result = check_capsule(combined)

    accepted = policy_result["ok"] and bool(proposal_text.strip())
    notes: List[str] = []
    if not proposal_text.strip():
        notes.append("Proposal text is required.")
    if policy_result["reasons"]:
        notes.extend(policy_result["reasons"])

    record = record_upgrade_decision(
        {
            "proposal": proposal_text,
            "rationale": rationale,
            "accepted": accepted,
            "notes": notes,
            "reasons": policy_result["reasons"],
            "ethics_score": policy_result["ethics_score"],
        }
    )

    return record


__all__ = ["propose_upgrade"]
