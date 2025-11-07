"""Persistent storage helpers for Gaia."""
from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List

DATA_DIR = Path(os.getenv("GAIA_DATA_DIR", Path(__file__).resolve().parents[1] / "data"))
CAPSULE_DIR = DATA_DIR / "capsules"
LOG_DIR = DATA_DIR / "logs"
MODELS_DIR = DATA_DIR / "models"
ACTIVITY_LOG = LOG_DIR / "activity.jsonl"
UPGRADE_LEDGER = DATA_DIR / "upgrades_ledger.jsonl"
STATE_FILE = DATA_DIR / "state.json"

for path in (CAPSULE_DIR, LOG_DIR, MODELS_DIR):
    path.mkdir(parents=True, exist_ok=True)


def _atomic_write(path: Path, payload: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=str(path.parent), delete=False) as tmp:
        json.dump(payload, tmp, indent=2)
        tmp.flush()
        os.fsync(tmp.fileno())
        temp_name = tmp.name
    os.replace(temp_name, path)


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def save_capsule(text: str, tag: str) -> Dict[str, object]:
    capsule_id = f"capsule-{uuid.uuid4().hex}"
    payload = {
        "id": capsule_id,
        "tag": tag,
        "text": text,
        "created_at": _timestamp(),
    }
    target = CAPSULE_DIR / f"{capsule_id}.json"
    _atomic_write(target, payload)
    return payload


def list_capsules(tag: str | None = None, query: str | None = None) -> List[Dict[str, object]]:
    results: List[Dict[str, object]] = []
    for file in sorted(CAPSULE_DIR.glob("*.json")):
        try:
            data = json.loads(file.read_text())
        except json.JSONDecodeError:
            continue
        if tag and data.get("tag") != tag:
            continue
        if query and query.lower() not in data.get("text", "").lower():
            continue
        results.append(data)
    return results


def append_log(event: Dict[str, object]) -> None:
    entry = dict(event)
    entry.setdefault("ts", _timestamp())
    ACTIVITY_LOG.parent.mkdir(parents=True, exist_ok=True)
    with ACTIVITY_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


def iter_logs() -> Iterable[str]:
    if not ACTIVITY_LOG.exists():
        return []
    with ACTIVITY_LOG.open("r", encoding="utf-8") as handle:
        for line in handle:
            yield line.rstrip("\n")


def record_upgrade_decision(payload: Dict[str, object]) -> Dict[str, object]:
    """Persist a single upgrade decision for transparency."""

    entry = dict(payload)
    entry.setdefault("decision_id", f"upgrade-{uuid.uuid4().hex}")
    entry.setdefault("recorded_at", _timestamp())
    decision = "accepted" if entry.get("accepted") else "rejected"
    entry.setdefault("decision", decision)
    entry.setdefault("notes", payload.get("notes", []))
    UPGRADE_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with UPGRADE_LEDGER.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")
    return entry


def load_state() -> Dict[str, object]:
    if not STATE_FILE.exists():
        return {"halted": False}
    try:
        with STATE_FILE.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
            if not isinstance(data, dict):
                return {"halted": False}
            return {"halted": bool(data.get("halted", False))}
    except json.JSONDecodeError:
        return {"halted": False}


def save_state(state: Dict[str, object]) -> None:
    payload = {"halted": bool(state.get("halted", False))}
    _atomic_write(STATE_FILE, payload)


__all__ = [
    "save_capsule",
    "list_capsules",
    "append_log",
    "iter_logs",
    "CAPSULE_DIR",
    "ACTIVITY_LOG",
    "MODELS_DIR",
    "UPGRADE_LEDGER",
    "STATE_FILE",
    "load_state",
    "save_state",
    "record_upgrade_decision",
]
