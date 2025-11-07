"""Self-improvement mock logic for Gaia."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from .metrics import tracker
from .storage import MODELS_DIR

VERSIONS_FILE = MODELS_DIR / "versions.jsonl"


def _load_last_version() -> tuple[str, float]:
    if not VERSIONS_FILE.exists():
        return "gaia-v1.0", 0.0
    last_line = None
    with VERSIONS_FILE.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                last_line = line
    if not last_line:
        return "gaia-v1.0", 0.0
    data = json.loads(last_line)
    return data.get("version", "gaia-v1.0"), float(data.get("delta_score", 0.0))


def learning_step(metrics: Dict[str, float]) -> Dict[str, object]:
    """Record a mock learning update and persist a snapshot."""
    current_version, _ = _load_last_version()
    prefix, _, suffix = current_version.partition("-v")
    if not suffix:
        suffix = "1.0"
    major, _, minor = suffix.partition(".")
    try:
        major_i = int(major)
        minor_i = int(minor) if minor else 0
    except ValueError:
        major_i, minor_i = 1, 0
    minor_i += 1
    new_version = f"{prefix}-v{major_i}.{minor_i}"

    delta_score = round(0.05 + 0.02 * (metrics.get("engagement", 0) % 5), 3)

    snapshot = {
        "version": new_version,
        "delta_score": delta_score,
        "metrics": metrics,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    with VERSIONS_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(snapshot) + "\n")

    tracker().set_version(new_version)
    tracker().update_learning(new_version, delta_score)

    return snapshot


def load_learning_history(limit: int = 50) -> List[Dict[str, object]]:
    """Return the most recent learning snapshots for dashboard visualisations."""

    if not VERSIONS_FILE.exists():
        return []

    entries: List[Dict[str, object]] = []
    with VERSIONS_FILE.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            entry = {
                "version": data.get("version", "gaia-v1.0"),
                "delta_score": float(data.get("delta_score", 0.0)),
                "recorded_at": data.get("recorded_at"),
            }
            entries.append(entry)
    if limit > 0:
        entries = entries[-limit:]
    return entries


__all__ = ["learning_step", "VERSIONS_FILE", "load_learning_history"]
