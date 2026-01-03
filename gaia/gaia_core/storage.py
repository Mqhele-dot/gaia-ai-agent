"""Persistent storage helpers for Gaia."""
from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List


def _resolve_data_dir() -> Path:
    """Determine the best storage directory for the current environment."""

    explicit = os.getenv("GAIA_DATA_DIR")
    if explicit:
        return Path(explicit)

    if os.getenv("SPACE_ID"):
        for env_key in ("HF_PERSISTENT_DIR", "HF_HOME"):
            candidate_root = os.getenv(env_key)
            if candidate_root:
                return Path(candidate_root) / "gaia"

        default_root = Path("/data")
        if default_root.exists():
            return default_root / "gaia"

    return Path(__file__).resolve().parents[1] / "data"


DATA_DIR = _resolve_data_dir()
CAPSULE_DIR = DATA_DIR / "capsules"
LOG_DIR = DATA_DIR / "logs"
MODELS_DIR = DATA_DIR / "models"
AUTONOMY_LOG = DATA_DIR / "autonomy_runs.jsonl"
ACTIVITY_LOG = LOG_DIR / "activity.jsonl"
UPGRADE_LEDGER = DATA_DIR / "upgrades_ledger.jsonl"
RESEARCH_LOG = DATA_DIR / "research_log.jsonl"
PROGRESS_LOG = DATA_DIR / "progress_metrics.jsonl"
STATE_FILE = DATA_DIR / "state.json"
SETTINGS_FILE = DATA_DIR / "settings.json"

DEFAULT_SETTINGS = {
    "auto_refresh": True,
    "toggles": {
        "analyze": True,
        "simulate": True,
        "learning": True,
        "research": True,
        "insights": True,
    },
}

for path in (CAPSULE_DIR, LOG_DIR, MODELS_DIR):
    path.mkdir(parents=True, exist_ok=True)


def _append_jsonl(path: Path, payload: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload) + "\n")


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


def delete_capsules(ids: Iterable[str]) -> Dict[str, List[str]]:
    deleted: List[str] = []
    missing: List[str] = []
    base_dir = CAPSULE_DIR.resolve()
    for capsule_id in ids:
        capsule_id = str(capsule_id).strip()
        if not capsule_id:
            continue
        target = CAPSULE_DIR / f"{capsule_id}.json"
        try:
            target.resolve().relative_to(base_dir)
        except ValueError:
            missing.append(capsule_id)
            continue
        if target.exists():
            target.unlink()
            deleted.append(capsule_id)
        else:
            missing.append(capsule_id)
    return {"deleted": deleted, "missing": missing}


def append_log(event: Dict[str, object]) -> None:
    entry = dict(event)
    entry.setdefault("ts", _timestamp())
    _append_jsonl(ACTIVITY_LOG, entry)


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
    _append_jsonl(UPGRADE_LEDGER, entry)
    return entry


def record_autonomy_event(payload: Dict[str, object]) -> None:
    entry = dict(payload)
    entry.setdefault("ts", _timestamp())
    _append_jsonl(AUTONOMY_LOG, entry)


def record_research_entry(query: str, research: Dict[str, object], *, version: str | None = None) -> Dict[str, object]:
    """Persist research results for auditability."""

    entry: Dict[str, object] = {
        "query": query,
        "source": research.get("source"),
        "results": research.get("results", []),
        "notes": research.get("notes", []),
        "ts": _timestamp(),
    }
    if version:
        entry["version"] = version
    _append_jsonl(RESEARCH_LOG, entry)
    return entry


def record_progress_snapshot(status: Dict[str, object], *, reason: str) -> Dict[str, object]:
    """Store a versioned metrics snapshot to track improvement over time."""

    snapshot = {
        "ts": _timestamp(),
        "reason": reason,
        "metrics": {
            "uptime_s": status.get("uptime_s"),
            "version": status.get("version"),
            "api_calls": status.get("api_calls"),
            "capsules_processed": status.get("capsules_processed"),
            "processing_ms_avg": status.get("processing_ms_avg"),
            "learning_version": status.get("learning_version"),
            "learning_delta": status.get("learning_delta"),
        },
    }
    _append_jsonl(PROGRESS_LOG, snapshot)
    return snapshot


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


def load_settings() -> Dict[str, object]:
    """Load dashboard settings with defaults."""

    if not SETTINGS_FILE.exists():
        return {
            "auto_refresh": DEFAULT_SETTINGS["auto_refresh"],
            "toggles": dict(DEFAULT_SETTINGS["toggles"]),
        }
    try:
        with SETTINGS_FILE.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError:
        return {
            "auto_refresh": DEFAULT_SETTINGS["auto_refresh"],
            "toggles": dict(DEFAULT_SETTINGS["toggles"]),
        }

    toggles = dict(DEFAULT_SETTINGS["toggles"])
    incoming = data.get("toggles")
    if isinstance(incoming, dict):
        for key in toggles:
            if key in incoming:
                toggles[key] = bool(incoming[key])

    auto_refresh = data.get("auto_refresh")
    if not isinstance(auto_refresh, bool):
        auto_refresh = DEFAULT_SETTINGS["auto_refresh"]

    return {"auto_refresh": auto_refresh, "toggles": toggles}


def save_settings(settings: Dict[str, object]) -> Dict[str, object]:
    """Persist dashboard settings safely."""

    toggles = dict(DEFAULT_SETTINGS["toggles"])
    incoming_toggles = settings.get("toggles")
    if isinstance(incoming_toggles, dict):
        for key in toggles:
            if key in incoming_toggles:
                toggles[key] = bool(incoming_toggles[key])

    auto_refresh = settings.get("auto_refresh")
    if not isinstance(auto_refresh, bool):
        auto_refresh = DEFAULT_SETTINGS["auto_refresh"]

    payload = {"auto_refresh": auto_refresh, "toggles": toggles}
    _atomic_write(SETTINGS_FILE, payload)
    return payload


__all__ = [
    "save_capsule",
    "list_capsules",
    "delete_capsules",
    "append_log",
    "iter_logs",
    "CAPSULE_DIR",
    "AUTONOMY_LOG",
    "ACTIVITY_LOG",
    "MODELS_DIR",
    "UPGRADE_LEDGER",
    "STATE_FILE",
    "load_state",
    "save_state",
    "record_upgrade_decision",
    "record_autonomy_event",
    "SETTINGS_FILE",
    "load_settings",
    "save_settings",
    "DEFAULT_SETTINGS",
]
