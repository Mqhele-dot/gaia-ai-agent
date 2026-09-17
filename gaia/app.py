from __future__ import annotations

import hmac
import os
import time
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, send_file

from . import __version__
from .core.engine import ENGINE
from .core.policy import evaluate as policy_evaluate
from .core.reasoning import analyze_capsule, simulate_capsule
from .core.research import search_research
from .core.store import (
    DATA_DIR,
    EVENTS_FILE,
    append_event,
    delete_capsule,
    get_capsule,
    list_capsules,
    list_events,
    load_settings,
    load_state,
    save_capsule,
    save_settings,
    save_state,
)

APP_ROOT = Path(__file__).resolve().parent
app = Flask(__name__, template_folder=str(APP_ROOT / "templates"), static_folder=str(APP_ROOT / "static"))
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024
STARTED_AT = time.time()
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")


def _admin_ok() -> bool:
    supplied = request.headers.get("X-ADMIN-TOKEN", "")
    return bool(ADMIN_TOKEN) and hmac.compare_digest(supplied, ADMIN_TOKEN)


@app.get("/")
def index() -> str:
    return render_template("dashboard.html", version=__version__)


@app.get("/api/status")
def api_status() -> Response:
    capsules = list_capsules(limit=0)
    events = list_events(limit=0)
    state = load_state()
    return jsonify(
        {
            "app_version": __version__,
            "uptime_s": round(time.time() - STARTED_AT, 1),
            "halted": state.get("halted", False),
            "autonomy": ENGINE.status(),
            "counts": {
                "capsules": len(capsules),
                "events": len(events),
                "research": sum(1 for event in events if event.get("type") == "research"),
                "simulations": sum(1 for event in events if event.get("type") == "simulation"),
                "evaluations": sum(1 for event in events if event.get("type") == "evaluation"),
            },
            "storage": {
                "data_dir": str(DATA_DIR),
                "writable": os.access(DATA_DIR, os.W_OK),
                "events_bytes": EVENTS_FILE.stat().st_size if EVENTS_FILE.exists() else 0,
            },
            "admin_token_configured": bool(ADMIN_TOKEN),
        }
    )


@app.route("/api/settings", methods=["GET", "PATCH"])
def api_settings() -> Response:
    if request.method == "GET":
        return jsonify(load_settings())
    payload = request.get_json(silent=True) or {}
    settings = save_settings(payload)
    append_event(actor="operator", event_type="settings", action="update", status="ok", summary="Settings updated", data=settings)
    return jsonify(settings)


@app.get("/api/events")
def api_events() -> Response:
    limit = min(max(request.args.get("limit", default=100, type=int), 1), 1000)
    return jsonify(
        {
            "events": list_events(
                limit=limit,
                event_type=request.args.get("type") or None,
                actor=request.args.get("actor") or None,
            )
        }
    )


@app.route("/api/capsules", methods=["GET", "POST"])
def api_capsules() -> Response:
    if request.method == "GET":
        return jsonify({"capsules": list_capsules()})
    payload = request.get_json(silent=True) or {}
    text = str(payload.get("text", "")).strip()
    policy = policy_evaluate(text)
    if not policy["ok"]:
        return jsonify({"error": "policy_blocked", "policy": policy}), 400
    capsule = save_capsule(
        text,
        str(payload.get("tag", "general")),
        source=str(payload.get("source", "operator")),
        title=str(payload.get("title", "")).strip() or None,
    )
    append_event(actor="operator", event_type="capsule", action="create", status="ok", summary=f"Created capsule {capsule['id']}", ref_id=capsule["id"])
    return jsonify(capsule), 201


@app.route("/api/capsules/<capsule_id>", methods=["GET", "DELETE"])
def api_capsule(capsule_id: str) -> Response:
    capsule = get_capsule(capsule_id)
    if not capsule:
        return jsonify({"error": "not_found"}), 404
    if request.method == "GET":
        return jsonify(capsule)
    if not delete_capsule(capsule_id):
        return jsonify({"error": "not_found"}), 404
    append_event(actor="operator", event_type="capsule", action="delete", status="ok", summary=f"Deleted capsule {capsule_id}", ref_id=capsule_id)
    return jsonify({"deleted": True, "id": capsule_id})


@app.post("/api/analyze")
def api_analyze() -> Response:
    payload = request.get_json(silent=True) or {}
    text = str(payload.get("text", ""))
    result = analyze_capsule(text)
    append_event(actor="operator", event_type="analysis", action="analyze", status="ok" if result["policy"]["ok"] else "blocked", summary="Manual analysis completed", data=result)
    return jsonify(result)


@app.post("/api/simulate")
def api_simulate() -> Response:
    if load_state().get("halted"):
        return jsonify({"error": "halted"}), 423
    payload = request.get_json(silent=True) or {}
    result = simulate_capsule(str(payload.get("text", "")))
    append_event(actor="operator", event_type="simulation", action="simulate", status="ok" if result.get("ok") else "blocked", summary="Manual simulation completed", data=result)
    return jsonify(result), 200 if result.get("ok") else 400


@app.route("/api/research", methods=["GET", "POST"])
def api_research() -> Response:
    if load_state().get("halted"):
        return jsonify({"error": "halted"}), 423
    query = request.args.get("q", "") if request.method == "GET" else str((request.get_json(silent=True) or {}).get("query", ""))
    try:
        result = search_research(query)
    except Exception as exc:
        append_event(actor="operator", event_type="error", action="research", status="failed", summary="Research request failed", data={"error": str(exc)})
        return jsonify({"error": "research_failed", "message": str(exc)}), 502
    append_event(actor="operator", event_type="research", action="search", status="ok", summary=f"Research completed for {query}", data={"query": query, "source": result.get("source"), "result_count": len(result.get("results", []))})
    return jsonify(result)


@app.post("/api/run")
def api_run() -> Response:
    if load_state().get("halted"):
        return jsonify({"error": "halted"}), 423
    payload = request.get_json(silent=True) or {}
    result = ENGINE.run_once(str(payload.get("topic", "")).strip() or None)
    return jsonify(result), 200 if result.get("ok") else 500


@app.post("/api/admin/halt")
def api_halt() -> Response:
    if not _admin_ok():
        return jsonify({"error": "unauthorized"}), 403
    save_state({"halted": True})
    append_event(actor="operator", event_type="admin", action="halt", status="ok", summary="Gaia halted")
    return jsonify({"halted": True})


@app.post("/api/admin/resume")
def api_resume() -> Response:
    if not _admin_ok():
        return jsonify({"error": "unauthorized"}), 403
    save_state({"halted": False})
    append_event(actor="operator", event_type="admin", action="resume", status="ok", summary="Gaia resumed")
    return jsonify({"halted": False})


@app.get("/api/export/events")
def api_export_events() -> Response:
    if not EVENTS_FILE.exists():
        EVENTS_FILE.touch()
    return send_file(EVENTS_FILE, mimetype="application/x-ndjson", as_attachment=True, download_name="gaia-events.jsonl")


ENGINE.start()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")), debug=os.getenv("FLASK_DEBUG") == "1")
