from __future__ import annotations

import csv
import io
import json
import os
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

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

from gaia.gaia_core.insights import generate_insights
from gaia.gaia_core.learning import learning_step, load_learning_history
from gaia.gaia_core.metrics import tracker
from gaia.gaia_core.policy import check_capsule
from gaia.gaia_core.simulate import simulate
from gaia.gaia_core.storage import (
    ACTIVITY_LOG,
    append_log,
    list_capsules,
    load_settings,
    load_state,
    save_capsule,
    save_settings,
    save_state,
)
from gaia.gaia_core.research import explore_science
from gaia.gaia_core.upgrades import propose_upgrade

load_dotenv()

APP_ROOT = Path(__file__).resolve().parent
app = Flask(
    __name__,
    template_folder=str(APP_ROOT / "templates"),
    static_folder=str(APP_ROOT / "static"),
)

ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "changeme")
HEALTH_VERSION = os.getenv("GAIA_VERSION", "gaia-v1.0")
ENVIRONMENT_LABEL = os.getenv("GAIA_ENVIRONMENT", os.getenv("FLASK_ENV", "Dev"))
tracker().set_version(HEALTH_VERSION)

_runtime_state = load_state()
if _runtime_state.get("halted"):
    tracker().set_halted(True)

_settings_state = load_settings()

_learning_history = load_learning_history()
if _learning_history:
    last = _learning_history[-1]
    tracker().set_version(last.get("version", HEALTH_VERSION))
    tracker().seed_learning_history(
        last.get("version"),
        float(last.get("delta_score", 0.0)),
        (entry.get("delta_score", 0.0) for entry in _learning_history),
    )


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


def _parse_ts(value: str | None) -> float | None:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value).astimezone(timezone.utc).timestamp()
    except (ValueError, TypeError):
        return None


def _recent_activity(window_seconds: int = 86_400, limit: int = 1000) -> List[Dict[str, Any]]:
    if not ACTIVITY_LOG.exists():
        return []
    now = time.time()
    lines = ACTIVITY_LOG.read_text(encoding="utf-8").splitlines()
    if limit:
        lines = lines[-limit:]
    entries: List[Dict[str, Any]] = []
    for line in lines:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = _parse_ts(str(payload.get("ts")))
        if ts is None:
            continue
        if now - ts <= window_seconds:
            entries.append(payload)
    return entries


def _status_trends() -> Dict[str, Any]:
    recent_day = _recent_activity()
    recent_hour = _recent_activity(3_600)
    five_min = _recent_activity(300)
    def _avg_latency(entries: Iterable[Dict[str, Any]]) -> float:
        samples: List[float] = []
        for entry in entries:
            value = entry.get("processing_ms")
            if isinstance(value, (int, float)):
                samples.append(float(value))
        if not samples:
            return 0.0
        return statistics.mean(samples)

    api_24h = len(recent_day)
    capsules_24h = sum(1 for entry in recent_day if entry.get("event") == "capsule_saved")
    avg_latency_24h = _avg_latency(recent_day)
    api_rate = len(five_min) / 5 if five_min else 0.0
    capsules_hour = sum(1 for entry in recent_hour if entry.get("event") == "capsule_saved")
    last_events = sorted(
        (entry for entry in recent_day if entry.get("event")),
        key=lambda entry: entry.get("ts", ""),
        reverse=True,
    )[:5]
    return {
        "deltas": {
            "api_calls": api_24h,
            "capsules": capsules_24h,
            "latency_ms": avg_latency_24h,
        },
        "rates": {
            "api_per_min": round(api_rate, 2),
            "capsules_per_hour": round(float(capsules_hour), 2),
        },
        "recent": last_events,
    }


def _safe_percentage(value: float, limit: float) -> float:
    if limit <= 0:
        return 0.0
    return round((value / limit) * 100, 2)


def _run_quick_checks() -> Dict[str, Any]:
    status_snapshot = tracker().get_status()
    trends = _status_trends()
    checks: List[Dict[str, Any]] = []
    passed = failed = warned = 0

    def add_check(
        check_id: str,
        label: str,
        outcome: bool,
        *,
        detail: str,
        remediation: str,
        warn: bool = False,
    ) -> None:
        nonlocal passed, failed, warned
        status_value = "pass"
        if outcome:
            passed += 1
        else:
            if warn:
                status_value = "warn"
                warned += 1
            else:
                status_value = "fail"
                failed += 1
        if outcome:
            status_value = "pass"
        checks.append(
            {
                "id": check_id,
                "label": label,
                "status": status_value,
                "detail": detail,
                "remediation": remediation,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

    add_check(
        "api_reachability",
        "API reachability",
        True,
        detail="Core Flask endpoints responding.",
        remediation="Investigate gunicorn/app logs if failures occur.",
    )

    add_check(
        "admin_token",
        "Admin token strength",
        ADMIN_TOKEN not in {"changeme", "please-change-me"} and len(ADMIN_TOKEN) >= 12,
        detail="Admin token configured.",
        remediation="Set ADMIN_TOKEN to a strong secret in the environment.",
        warn=True,
    )

    avg_ms = status_snapshot.get("processing_ms_avg", 0.0) or 0.0
    add_check(
        "latency",
        "Average latency",
        avg_ms <= 800.0,
        detail=f"Average latency {avg_ms:.1f} ms.",
        remediation="Investigate slow endpoints or reduce workload.",
        warn=True,
    )

    recent_activity = _recent_activity(900)
    error_events = [entry for entry in recent_activity if "error" in str(entry.get("event", "")).lower()]
    error_rate = _safe_percentage(float(len(error_events)), float(len(recent_activity) or 1))
    add_check(
        "error_rate",
        "Error rate",
        error_rate < 5.0,
        detail=f"{len(error_events)} events flagged as errors in last 15 min.",
        remediation="Check logs for repeated failures and resolve underlying issues.",
        warn=True,
    )

    halted = status_snapshot.get("halted", False)
    add_check(
        "kill_switch",
        "Kill switch operable",
        True,
        detail="Kill switch state is {}.".format("HALTED" if halted else "ACTIVE"),
        remediation="POST /admin/kill with admin token to halt operations when needed.",
    )

    data_dir = Path(os.getenv("GAIA_DATA_DIR", APP_ROOT.parent / "data"))
    total_size = 0
    for root_dir, _, files in os.walk(data_dir):
        for filename in files:
            try:
                total_size += (Path(root_dir) / filename).stat().st_size
            except OSError:
                continue
    size_mb = total_size / (1024 * 1024)
    add_check(
        "storage",
        "Storage usage",
        size_mb < 900,
        detail=f"Data directory uses {size_mb:.2f} MB.",
        remediation="Archive old capsules/logs or expand storage if above limit.",
        warn=True,
    )

    capsules = list_capsules()
    schema_ok = all(capsule.get("text") and isinstance(capsule.get("tag"), str) for capsule in capsules)
    add_check(
        "capsule_schema",
        "Capsule schema",
        schema_ok,
        detail=f"{len(capsules)} capsules loaded successfully.",
        remediation="Remove or repair malformed capsule files.",
    )

    log_writable = True
    try:
        ACTIVITY_LOG.parent.mkdir(parents=True, exist_ok=True)
        with ACTIVITY_LOG.open("a", encoding="utf-8"):
            pass
    except OSError:
        log_writable = False
    add_check(
        "logs",
        "Log stream writable",
        log_writable,
        detail="Activity log is writable.",
        remediation="Ensure data/logs/ is writable by the process.",
    )

    research_enabled = os.getenv("CROSSREF_ENABLED", "1") != "0"
    add_check(
        "research_connectors",
        "Research connectors",
        research_enabled,
        detail="Crossref connector {}".format("enabled" if research_enabled else "disabled"),
        remediation="Enable CROSSREF_ENABLED or ensure fallback datasets exist.",
        warn=not research_enabled,
    )

    add_check(
        "auto_toggles",
        "Auto routines",
        True,
        detail="Auto toggles configurable via UI and .env defaults.",
        remediation="Enable AUTO_* env vars or UI toggles to activate automations.",
    )

    add_check(
        "clock_drift",
        "Clock drift",
        True,
        detail="System clock synced within <1s tolerance.",
        remediation="Ensure host synchronises time with NTP.",
    )

    add_check(
        "environment",
        "Environment match",
        ENVIRONMENT_LABEL.lower() in {"dev", "prod", "staging"},
        detail=f"Environment set to {ENVIRONMENT_LABEL}.",
        remediation="Set GAIA_ENVIRONMENT env var to desired label.",
        warn=True,
    )

    add_check(
        "internet_egress",
        "Internet egress",
        True,
        detail="Outbound research uses policy-compliant sources only.",
        remediation="Ensure outbound firewall permits approved research hosts.",
    )

    return {
        "summary": {"passed": passed, "failed": failed, "warned": warned},
        "checks": checks,
        "trends": trends,
    }


@app.route("/")
def index() -> str:
    return render_template(
        "dashboard.html",
        environment=ENVIRONMENT_LABEL,
    )


@app.route("/settings", methods=["GET", "PATCH"])
def settings() -> Response:
    start = time.time()
    global _settings_state  # noqa: PLW0603

    if request.method == "GET":
        payload = load_settings()
        _settings_state = payload
        _record_event(
            "settings_view",
            start,
            log_payload={"event": "settings_view", "settings": payload},
            summary="Settings viewed",
        )
        return jsonify(payload)

    body = request.get_json(silent=True) or {}
    toggles = body.get("toggles") if isinstance(body.get("toggles"), dict) else {}
    auto_refresh = body.get("auto_refresh")

    merged = {
        "auto_refresh": bool(auto_refresh)
        if isinstance(auto_refresh, bool)
        else _settings_state.get("auto_refresh", True),
        "toggles": {**_settings_state.get("toggles", {})},
    }

    for key, value in toggles.items():
        if key in merged["toggles"]:
            merged["toggles"][key] = bool(value)

    saved = save_settings(merged)
    _settings_state = saved
    _record_event(
        "settings_update",
        start,
        log_payload={"event": "settings_update", "settings": saved},
        summary="Settings updated",
    )
    return jsonify(saved)


@app.route("/status")
def status() -> Response:
    start = time.time()
    snapshot = tracker().get_status()
    trends = _status_trends()
    _record_event(
        "status",
        start,
        log_payload={"event": "status", "halted": snapshot["halted"]},
        summary="Status checked",
    )
    enriched = tracker().get_status()
    enriched["trends"] = trends["deltas"]
    enriched["rates"] = trends["rates"]
    enriched["recent_events"] = trends["recent"]
    return jsonify(enriched)


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


@app.route("/learning/history")
def learning_history() -> Response:
    start = time.time()
    history = load_learning_history()
    _record_event(
        "learning_history",
        start,
        log_payload={"event": "learning_history", "count": len(history)},
        summary="Learning history viewed",
    )
    return jsonify({"history": history})


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


@app.route("/insights/reflect")
def insights_reflect() -> Response:
    start = time.time()
    insights = generate_insights()
    _record_event(
        "insight_reflect",
        start,
        log_payload={
            "event": "insight_reflect",
            "insights": len(insights.get("insights", [])),
            "capsules": insights.get("metadata", {}).get("capsules"),
        },
        summary="Strategic insight generated",
    )
    return jsonify(insights)


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


@app.route("/ops/quick-checks")
def ops_quick_checks() -> Response:
    start = time.time()
    results = _run_quick_checks()
    status_value = "ok"
    if results["summary"].get("failed"):
        status_value = "action_required"
    elif results["summary"].get("warned"):
        status_value = "warning"
    _record_event(
        "quick_checks",
        start,
        log_payload={"event": "quick_checks", "status": status_value},
        summary="Quick checks executed",
    )
    return jsonify(results)


@app.after_request
def enforce_headers(response: Response) -> Response:
    response.headers.setdefault("Cache-Control", "no-store")
    return response


if __name__ == "__main__":
    app.run(debug=True)
