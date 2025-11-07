from __future__ import annotations

import csv
import io
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Iterable

# python-dotenv is optional at runtime; provide a no-op fallback if missing.
try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - exercised when dependency missing
    def load_dotenv() -> bool:  # type: ignore
        return False
from flask import (
    Flask,
    Response,
    jsonify,
    render_template,
    request,
    send_file,
)

from gaia.gaia_core.learning import learning_step
from gaia.gaia_core.metrics import tracker
from gaia.gaia_core.policy import check_capsule
from gaia.gaia_core.simulate import simulate
from gaia.gaia_core.storage import (
    ACTIVITY_LOG,
    append_log,
    list_capsules,
    load_state,
    save_capsule,
    save_state,
)
from gaia.gaia_core.research import explore_science
from gaia.gaia_core.upgrades import propose_upgrade

load_dotenv()

APP_ROOT = Path(__file__).resolve().parent
app = Flask(__name__, template_folder=str(APP_ROOT / "templates"), static_folder=str(APP_ROOT / "static"))

ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "changeme")
HEALTH_VERSION = os.getenv("GAIA_VERSION", "gaia-v1.0")
tracker().set_version(HEALTH_VERSION)

_runtime_state = load_state()
if _runtime_state.get("halted"):
    tracker().set_halted(True)


def _record_event(
    action: str,
    start_time: float,
    *,
    capsules_delta: int = 0,
    log_payload: Dict[str, Any] | None = None,
    summary: str | None = None,
) -> None:
    elapsed_ms = (time.time() - start_time) * 1000
    tracker().record_api_call(
        action,
        elapsed_ms,
        capsules_delta=capsules_delta,
        summary=summary,
    )
    payload: Dict[str, Any] = {"event": action, "processing_ms": round(elapsed_ms, 2)}
    if capsules_delta:
        payload["capsules_delta"] = capsules_delta
    if log_payload:
        payload.update(log_payload)
        payload.setdefault("event", action)
    append_log(payload)


def _policy_guard(text: str) -> Dict[str, Any] | None:
    result = check_capsule(text)
    if not result["ok"]:
        return result
    return None


@app.route("/")
def index() -> str:
    return render_template("dashboard.html")


@app.route("/status")
def status() -> Response:
    start = time.time()
    snapshot = tracker().get_status()
    _record_event(
        "status",
        start,
        log_payload={"event": "status", "halted": snapshot["halted"]},
        summary="Status checked",
    )
    return jsonify(tracker().get_status())


@app.route("/capsules/save", methods=["POST"])
def capsules_save() -> Response:
    start = time.time()
    payload = request.get_json(force=True) or {}
    text = str(payload.get("text", ""))
    tag = str(payload.get("tag", "general"))
    if not tag.strip():
        tag = "general"

    violation = _policy_guard(text)
    if violation:
        _record_event(
            "capsule_rejected",
            start,
            log_payload={"event": "capsule_rejected", "tag": tag, "reasons": violation["reasons"]},
            summary="Capsule rejected",
        )
        return jsonify({"error": "Disallowed by policy.", "reasons": violation["reasons"]}), 400

    capsule = save_capsule(text, tag)
    log_payload = {
        "event": "capsule_saved",
        "capsule_id": capsule["id"],
        "tag": tag,
        "len": len(text),
    }
    _record_event(
        "capsule_saved",
        start,
        capsules_delta=1,
        log_payload=log_payload,
        summary=f"Capsule saved ({tag})",
    )
    return jsonify(capsule), 201


@app.route("/capsules/list")
def capsules_list() -> Response:
    start = time.time()
    tag = request.args.get("tag")
    query = request.args.get("q")
    capsules = list_capsules(tag=tag, query=query)
    _record_event(
        "capsules_list",
        start,
        log_payload={
            "event": "capsules_list",
            "tag": tag,
            "query": query,
            "count": len(capsules),
        },
        summary="Capsules listed",
    )
    return jsonify({"capsules": capsules})


@app.route("/capsules/analyze", methods=["POST"])
def capsules_analyze() -> Response:
    start = time.time()
    payload = request.get_json(force=True) or {}
    text = str(payload.get("text", ""))

    analysis_policy = check_capsule(text)
    if not analysis_policy["ok"]:
        _record_event(
            "capsule_analysis_blocked",
            start,
            log_payload={"event": "capsule_analysis_blocked", "reasons": analysis_policy["reasons"]},
            summary="Capsule analysis blocked",
        )
        return jsonify({"error": "Disallowed by policy.", "reasons": analysis_policy["reasons"]}), 400

    tokens = [tok for tok in text.split() if tok]
    unique_tokens = len(set(tok.lower() for tok in tokens))
    length = len(text)
    complexity = round(min(1.0, (unique_tokens / max(len(tokens), 1)) * 1.2), 2)
    ethics = analysis_policy["ethics_score"]

    result = {
        "length": length,
        "unique_tokens": unique_tokens,
        "complexity_score": complexity,
        "ethics_score": ethics,
    }
    _record_event(
        "capsule_analyze",
        start,
        log_payload={
            "event": "capsule_analyze",
            "length": length,
            "unique_tokens": unique_tokens,
        },
        summary="Capsule analyzed",
    )
    return jsonify(result)


@app.route("/simulate/run", methods=["POST"])
def simulate_run() -> Response:
    start = time.time()
    payload = request.get_json(force=True) or {}
    text = str(payload.get("text", ""))
    capsule_id = payload.get("capsule_id")

    if tracker().is_halted():
        _record_event(
            "simulate_blocked",
            start,
            log_payload={
                "event": "simulate_blocked",
                "capsule_id": capsule_id or "ad-hoc",
                "reason": "halted",
            },
            summary="Simulation blocked",
        )
        return jsonify({"error": "System halted. Simulation disabled."}), 423

    try:
        simulation = simulate(text)
    except ValueError as exc:
        message = str(exc)
        _record_event(
            "simulate_blocked",
            start,
            log_payload={
                "event": "simulate_blocked",
                "capsule_id": capsule_id or "ad-hoc",
                "error": message,
            },
            summary="Simulation blocked",
        )
        if "Disallowed by policy" in message:
            detail = message.split(":", 1)[1].strip() if ":" in message else message
            return jsonify({"error": "Disallowed by policy.", "details": detail}), 400
        raise

    log_payload = {
        "event": "simulate",
        "capsule_id": capsule_id or "ad-hoc",
        "safety": "pass",
        "risk": "low",
    }
    _record_event(
        "simulate",
        start,
        log_payload=log_payload,
        summary="Simulation completed",
    )
    return jsonify(simulation)


@app.route("/learning/step", methods=["POST"])
def learning_step_route() -> Response:
    start = time.time()
    payload = request.get_json(force=True) or {}
    metrics_payload = {
        "engagement": float(payload.get("engagement", 0.0)),
        "success_rate": float(payload.get("success_rate", 0.0)),
        "feedback_score": float(payload.get("feedback_score", 0.0)),
    }
    snapshot = learning_step(metrics_payload)
    log_payload = {
        "event": "learning_step",
        "delta_score": snapshot["delta_score"],
        "version": snapshot["version"],
    }
    tracker().update_learning(snapshot["version"], float(snapshot["delta_score"]))
    _record_event(
        "learning_step",
        start,
        log_payload=log_payload,
        summary=f"Learning step Δ{snapshot['delta_score']:+.3f}",
    )
    return jsonify(snapshot)


@app.route("/upgrades/propose", methods=["POST"])
def upgrades_propose() -> Response:
    start = time.time()
    payload = request.get_json(force=True) or {}
    if tracker().is_halted():
        _record_event(
            "upgrade_blocked",
            start,
            log_payload={"event": "upgrade_blocked", "reason": "halted"},
            summary="Upgrade blocked",
        )
        return jsonify({"error": "System halted. Upgrade proposals are paused."}), 423

    result = propose_upgrade(payload)

    action = "upgrade_accepted" if result["accepted"] else "upgrade_rejected"
    log_payload = {
        "event": action,
        "ethics_score": result["ethics_score"],
        "notes": result["notes"],
        "decision_id": result.get("decision_id"),
        "proposal": result.get("proposal"),
    }

    if not result["accepted"]:
        log_payload["event"] = "upgrade_rejected"
        _record_event(
            "upgrade_rejected",
            start,
            log_payload=log_payload,
            summary="Upgrade rejected",
        )
        if result["notes"]:
            return (
                jsonify(
                    {
                        "error": "Disallowed by policy.",
                        "notes": result["notes"],
                        "ethics_score": result["ethics_score"],
                    }
                ),
                400,
            )
        return jsonify(result)

    _record_event(
        "upgrade_accepted",
        start,
        log_payload=log_payload,
        summary="Upgrade accepted",
    )
    return jsonify(result)


@app.route("/research/explore")
def research_explore() -> Response:
    start = time.time()
    if tracker().is_halted():
        _record_event(
            "research_explore",
            start,
            log_payload={"event": "research_explore", "reason": "halted"},
            summary="Research blocked",
        )
        return jsonify({"error": "System halted. Research exploration paused."}), 423

    query = request.args.get("q", "").strip()
    if not query:
        _record_event(
            "research_explore",
            start,
            log_payload={"event": "research_explore", "query": query, "error": "missing_query"},
            summary="Research query missing",
        )
        return jsonify({"error": "Query parameter 'q' is required."}), 400

    research = explore_science(query)
    log_payload: Dict[str, Any] = {
        "event": "research_explore",
        "query": query,
        "source": research.get("source"),
        "result_count": len(research.get("results", [])),
    }
    _record_event(
        "research_explore",
        start,
        log_payload=log_payload,
        summary=f"Explored research on '{query}'",
    )
    status_code = 200 if research.get("results") else 202
    return jsonify(research), status_code


@app.route("/admin/kill", methods=["POST"])
def admin_kill() -> Response:
    start = time.time()
    token = request.headers.get("X-ADMIN-TOKEN")
    if token != ADMIN_TOKEN:
        _record_event(
            "kill_denied",
            start,
            log_payload={"event": "kill_denied"},
            summary="Kill switch denied",
        )
        return jsonify({"error": "Invalid admin token."}), 403

    tracker().set_halted(True)
    save_state({"halted": True})
    _record_event(
        "kill_switch",
        start,
        log_payload={"event": "kill_switch", "status": "HALTED"},
        summary="Kill switch engaged",
    )
    return jsonify({"status": "HALTED"})


@app.route("/export/logs")
def export_logs() -> Response:
    start = time.time()
    if not ACTIVITY_LOG.exists():
        ACTIVITY_LOG.touch()
    _record_event(
        "export_logs",
        start,
        log_payload={"event": "export_logs"},
        summary="Logs exported",
    )
    return send_file(ACTIVITY_LOG, mimetype="application/json", as_attachment=True, download_name="activity.jsonl")


def _capsules_as_csv(capsules: Iterable[Dict[str, Any]]) -> bytes:
    output = io.StringIO()
    fieldnames = ["id", "tag", "text", "created_at"]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for item in capsules:
        writer.writerow({key: item.get(key, "") for key in fieldnames})
    return output.getvalue().encode("utf-8")


def _capsules_as_txt(capsules: Iterable[Dict[str, Any]]) -> bytes:
    lines = []
    for item in capsules:
        lines.append(f"[{item.get('id')}] ({item.get('tag')}) {item.get('text')}")
    return "\n".join(lines).encode("utf-8")


@app.route("/export/capsules")
def export_capsules() -> Response:
    start = time.time()
    fmt = request.args.get("fmt", "json").lower()
    capsules = list_capsules()

    if fmt == "json":
        data = json.dumps(capsules).encode("utf-8")
        mimetype = "application/json"
        filename = "capsules.json"
    elif fmt == "csv":
        data = _capsules_as_csv(capsules)
        mimetype = "text/csv"
        filename = "capsules.csv"
    elif fmt == "txt":
        data = _capsules_as_txt(capsules)
        mimetype = "text/plain"
        filename = "capsules.txt"
    else:
        _record_event(
            "export_capsules_failed",
            start,
            log_payload={"event": "export_capsules_failed", "fmt": fmt},
            summary="Capsule export failed",
        )
        return jsonify({"error": "Unsupported format.", "allowed": ["json", "csv", "txt"]}), 400

    _record_event(
        "export_capsules",
        start,
        log_payload={"event": "export_capsules", "fmt": fmt, "count": len(capsules)},
        summary=f"Capsules exported ({fmt})",
    )
    return Response(data, mimetype=mimetype, headers={"Content-Disposition": f"attachment; filename={filename}"})


@app.after_request
def enforce_headers(response: Response) -> Response:
    response.headers.setdefault("Cache-Control", "no-store")
    return response


if __name__ == "__main__":
    app.run(debug=True)
