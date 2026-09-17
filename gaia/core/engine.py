from __future__ import annotations

import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from .policy import evaluate as policy_evaluate
from .reasoning import analyze_capsule, evaluate_run, simulate_capsule
from .research import search_research
from .store import append_event, list_capsules, load_settings, load_state, save_capsule

DEFAULT_TOPICS = [
    "sodium ion battery grid storage",
    "green hydrogen industrial heat",
    "water desalination energy efficiency",
    "circular economy battery recycling",
    "climate resilient agriculture",
    "low carbon cement materials",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class GaiaEngine:
    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._heartbeat_at: str | None = None
        self._current_run_id: str | None = None
        self._current_stage = "idle"
        self._last_run_id: str | None = None
        self._last_completed_at: str | None = None
        self._last_error: str | None = None
        self._topic_index = 0
        self._cycle = 0
        self._scheduler_interval = max(5, int(os.getenv("GAIA_SCHEDULER_INTERVAL", "30")))
        self._research_interval = max(30, int(os.getenv("GAIA_RESEARCH_INTERVAL", "900")))
        self._last_research_monotonic = 0.0

    def start(self) -> None:
        if os.getenv("GAIA_AUTONOMY_ENABLED", "0") != "1":
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="GaiaEngine", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    def status(self) -> dict[str, Any]:
        return {
            "enabled": os.getenv("GAIA_AUTONOMY_ENABLED", "0") == "1",
            "running": bool(self._thread and self._thread.is_alive()),
            "cycle": self._cycle,
            "current_stage": self._current_stage,
            "current_run_id": self._current_run_id,
            "last_run_id": self._last_run_id,
            "last_completed_at": self._last_completed_at,
            "heartbeat_at": self._heartbeat_at,
            "last_error": self._last_error,
        }

    def run_once(self, topic: str | None = None) -> dict[str, Any]:
        with self._lock:
            if load_state().get("halted"):
                return {"ok": False, "status": "halted"}
            run_id = f"run-{uuid.uuid4().hex[:12]}"
            self._current_run_id = run_id
            self._cycle += 1
            append_event(actor="autonomy", event_type="run", action="start", status="started", summary="Gaia run started", run_id=run_id)
            started = time.perf_counter()
            try:
                chosen_topic = topic or self._next_topic()
                self._current_stage = "research"
                research = search_research(chosen_topic)
                append_event(actor="autonomy", event_type="research", action="search", status="ok", summary=f"Research completed for {chosen_topic}", run_id=run_id, data={"query": chosen_topic, "result_count": len(research.get("results", [])), "source": research.get("source")})

                first = (research.get("results") or [None])[0]
                capsule = None
                if first:
                    self._current_stage = "capsule"
                    text = first.get("abstract") or first.get("title") or chosen_topic
                    title = first.get("title") or chosen_topic
                    policy = policy_evaluate(text)
                    if policy["ok"]:
                        capsule = save_capsule(text, "research", source="autonomy", title=title, origin_run_id=run_id)
                        append_event(actor="autonomy", event_type="capsule", action="create", status="ok", summary=f"Created capsule: {title}", run_id=run_id, ref_id=capsule["id"])

                self._current_stage = "analysis"
                analysis = analyze_capsule(capsule["text"] if capsule else chosen_topic)
                append_event(actor="autonomy", event_type="analysis", action="analyze", status="ok" if analysis["policy"]["ok"] else "blocked", summary="Analysis completed", run_id=run_id, ref_id=capsule["id"] if capsule else None, data=analysis)

                self._current_stage = "simulation"
                simulation = simulate_capsule(capsule["text"] if capsule else chosen_topic)
                append_event(actor="autonomy", event_type="simulation", action="simulate", status="ok" if simulation.get("ok") else "blocked", summary="Simulation completed" if simulation.get("ok") else "Simulation blocked", run_id=run_id, ref_id=capsule["id"] if capsule else None, data=simulation)

                self._current_stage = "evaluation"
                evaluation = evaluate_run(research_ok=bool(research.get("results")), analysis_ok=analysis["policy"]["ok"], simulation_ok=bool(simulation.get("ok")), novelty=0.5)
                append_event(actor="autonomy", event_type="evaluation", action="score", status="ok", summary=f"Run evaluation score {evaluation['score']}", run_id=run_id, data=evaluation)

                elapsed = (time.perf_counter() - started) * 1000
                append_event(actor="autonomy", event_type="run", action="complete", status="ok", summary="Gaia run completed", run_id=run_id, duration_ms=elapsed, data={"topic": chosen_topic})
                self._last_run_id = run_id
                self._last_completed_at = _now()
                self._last_error = None
                return {"ok": True, "run_id": run_id, "topic": chosen_topic, "capsule": capsule, "analysis": analysis, "simulation": simulation, "evaluation": evaluation}
            except Exception as exc:
                self._last_error = str(exc)
                append_event(actor="autonomy", event_type="error", action="run", status="failed", summary="Gaia run failed", run_id=run_id, data={"error": str(exc)})
                return {"ok": False, "run_id": run_id, "error": str(exc)}
            finally:
                self._current_stage = "idle"
                self._current_run_id = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._heartbeat_at = _now()
            settings = load_settings()
            halted = load_state().get("halted")
            if settings.get("autonomy_enabled") and not halted:
                now = time.monotonic()
                if now - self._last_research_monotonic >= self._research_interval:
                    self.run_once()
                    self._last_research_monotonic = now
            self._stop.wait(self._scheduler_interval)

    def _next_topic(self) -> str:
        topic = DEFAULT_TOPICS[self._topic_index % len(DEFAULT_TOPICS)]
        self._topic_index += 1
        return topic


ENGINE = GaiaEngine()
