from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_data_dir() -> Path:
    explicit = os.getenv("GAIA_DATA_DIR")
    if explicit:
        return Path(explicit)
    hf_data = Path("/data")
    if hf_data.exists() and os.access(hf_data, os.W_OK):
        return hf_data / "gaia"
    return Path(os.getenv("GAIA_LOCAL_DATA_DIR", "data"))


DATA_DIR = _resolve_data_dir()
CAPSULE_DIR = DATA_DIR / "capsules"
EVENTS_FILE = DATA_DIR / "events.jsonl"
STATE_FILE = DATA_DIR / "state.json"
SETTINGS_FILE = DATA_DIR / "settings.json"

DEFAULT_SETTINGS = {
    "autonomy_enabled": False,
    "research_enabled": True,
    "analysis_enabled": True,
    "simulation_enabled": True,
    "reflection_enabled": True,
    "evaluation_enabled": True,
}

for path in (DATA_DIR, CAPSULE_DIR):
    path.mkdir(parents=True, exist_ok=True)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
        temp_name = handle.name
    os.replace(temp_name, path)


def append_event(
    *,
    actor: str,
    event_type: str,
    action: str,
    status: str,
    summary: str,
    run_id: str | None = None,
    ref_id: str | None = None,
    duration_ms: float | None = None,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    event = {
        "id": f"evt-{uuid.uuid4().hex[:12]}",
        "ts": _now(),
        "run_id": run_id,
        "actor": actor,
        "type": event_type,
        "action": action,
        "status": status,
        "summary": summary,
        "ref_id": ref_id,
        "duration_ms": round(duration_ms, 2) if duration_ms is not None else None,
        "data": data or {},
    }
    EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with EVENTS_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event


def list_events(limit: int = 100, *, event_type: str | None = None, actor: str | None = None) -> list[dict[str, Any]]:
    if not EVENTS_FILE.exists():
        return []
    items: list[dict[str, Any]] = []
    with EVENTS_FILE.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event_type and item.get("type") != event_type:
                continue
            if actor and item.get("actor") != actor:
                continue
            items.append(item)
    if limit > 0:
        items = items[-limit:]
    return list(reversed(items))


def save_capsule(text: str, tag: str = "general", *, source: str = "operator", title: str | None = None, origin_run_id: str | None = None) -> dict[str, Any]:
    capsule_id = f"cap-{uuid.uuid4().hex[:12]}"
    payload = {
        "id": capsule_id,
        "title": title or (text.strip().splitlines()[0][:80] if text.strip() else "Untitled capsule"),
        "text": text.strip(),
        "tag": tag.strip() or "general",
        "source": source,
        "status": "captured",
        "created_at": _now(),
        "updated_at": _now(),
        "last_run_at": None,
        "origin_run_id": origin_run_id,
        "research_refs": [],
        "scores": {},
    }
    _atomic_json(CAPSULE_DIR / f"{capsule_id}.json", payload)
    return payload


def list_capsules(limit: int = 200) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for path in CAPSULE_DIR.glob("*.json"):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        items.append(item)
    items.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return items[:limit] if limit > 0 else items


def get_capsule(capsule_id: str) -> dict[str, Any] | None:
    path = CAPSULE_DIR / f"{capsule_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def delete_capsule(capsule_id: str) -> bool:
    path = CAPSULE_DIR / f"{capsule_id}.json"
    if not path.exists():
        return False
    path.unlink()
    return True


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {"halted": False}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"halted": False}
    return {"halted": bool(data.get("halted", False))}


def save_state(payload: dict[str, Any]) -> dict[str, Any]:
    state = {"halted": bool(payload.get("halted", False)), "updated_at": _now()}
    _atomic_json(STATE_FILE, state)
    return state


def load_settings() -> dict[str, Any]:
    if not SETTINGS_FILE.exists():
        return dict(DEFAULT_SETTINGS)
    try:
        incoming = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULT_SETTINGS)
    merged = dict(DEFAULT_SETTINGS)
    for key in merged:
        if key in incoming:
            merged[key] = bool(incoming[key])
    return merged


def save_settings(payload: dict[str, Any]) -> dict[str, Any]:
    merged = load_settings()
    for key in DEFAULT_SETTINGS:
        if key in payload:
            merged[key] = bool(payload[key])
    _atomic_json(SETTINGS_FILE, merged)
    return merged
