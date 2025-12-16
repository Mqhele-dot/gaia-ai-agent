"""Autonomous background operations for Gaia."""
from __future__ import annotations

import math
import os
import threading
import time
from collections import deque
from typing import Deque, Dict, Optional

from .insights import generate_insights
from .learning import learning_step
from .metrics import tracker
from .policy import check_capsule
from .research import explore_science
from .simulate import simulate
from .storage import (
    DEFAULT_SETTINGS,
    append_log,
    list_capsules,
    load_settings,
    record_autonomy_event,
    record_research_entry,
    save_capsule,
)


_AUTONOMY_QUERIES: Deque[str] = deque(
    [
        "sustainable energy innovation",
        "circular economy recycling",
        "carbon capture breakthroughs",
        "community climate resilience",
        "ethical ai governance",
        "biodiversity restoration",
        "equitable technology access",
    ],
    maxlen=32,
)


class AutonomyManager:
    """Run Gaia's autonomous workflows in the background."""

    _THREAD_KEY = "_gaia_autonomy_manager"

    def __init__(self, interval: Optional[float] = None) -> None:
        if interval is None:
            try:
                interval = float(os.getenv("GAIA_AUTONOMY_INTERVAL", "60"))
            except ValueError:
                interval = 60.0
            interval = max(15.0, interval)
        else:
            interval = max(0.01, interval)
        self._interval = interval
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._cycle_lock = threading.Lock()
        self._recent_titles: Deque[str] = deque(maxlen=12)
        self._cycle_count = 0

    # ------------------------------------------------------------------
    # Lifecycle helpers
    # ------------------------------------------------------------------
    def start(self) -> None:
        if self.is_running():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="GaiaAutonomy", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 1.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout)

    def is_running(self) -> bool:
        thread = self._thread
        return bool(thread and thread.is_alive())

    def status(self) -> Dict[str, object]:
        return {
            "running": self.is_running(),
            "interval_s": self._interval,
            "cycles": self._cycle_count,
            "recent_titles": list(self._recent_titles),
        }

    def run_cycle_once(self) -> None:
        self._execute_cycle()

    # ------------------------------------------------------------------
    # Internal execution
    # ------------------------------------------------------------------
    def _run(self) -> None:
        while not self._stop.is_set():
            self._execute_cycle()
            if self._stop.wait(self._interval):
                break

    def _execute_cycle(self) -> None:
        with self._cycle_lock:
            self._cycle_count += 1
            if tracker().is_halted():
                self._log_event("autonomy_idle", start=time.perf_counter(), data={"reason": "halted"})
                return

            settings = load_settings()
            toggles = dict(DEFAULT_SETTINGS["toggles"])
            incoming = settings.get("toggles") if isinstance(settings, dict) else None
            if isinstance(incoming, dict):
                toggles.update({key: bool(value) for key, value in incoming.items() if key in toggles})

            if toggles.get("research", True):
                self._run_research()
            if toggles.get("analyze", True):
                self._run_analysis()
            if toggles.get("simulate", True):
                self._run_simulation()
            if toggles.get("learning", True):
                self._run_learning()
            if toggles.get("insights", True):
                self._run_insights()

    # ------------------------------------------------------------------
    # Individual autonomous behaviours
    # ------------------------------------------------------------------
    def _run_research(self) -> None:
        if tracker().is_halted():
            return
        start = time.perf_counter()
        query = _AUTONOMY_QUERIES[0]
        _AUTONOMY_QUERIES.rotate(-1)
        try:
            research = explore_science(query)
        except Exception as exc:  # pragma: no cover - network issues
            self._log_event(
                "autonomy_research_error",
                start=start,
                data={"query": query, "error": str(exc)},
                summary=f"Autonomy research failed for {query}",
            )
            return

        results = research.get("results") if isinstance(research, dict) else []
        first = results[0] if isinstance(results, list) and results else None
        data = {
            "query": query,
            "source": research.get("source"),
            "result_count": len(results) if isinstance(results, list) else 0,
        }
        record_research_entry(query, research, version=tracker().get_status().get("version"))
        self._log_event(
            "autonomy_research",
            start=start,
            data=data,
            summary=f"Autonomy explored research on {query}",
        )

        if not first:
            return

        title = str(first.get("title", "")).strip()
        if title and title in self._recent_titles:
            return

        summary_text = str(first.get("summary") or "").strip()
        capsule_text = (
            f"Autonomous research summary: {title}. {summary_text}"
            if summary_text
            else f"Autonomous research summary: {title}."
        )
        if not capsule_text.strip():
            return

        policy = check_capsule(capsule_text)
        if not policy.get("ok"):
            self._log_event(
                "autonomy_capsule_blocked",
                start=time.perf_counter(),
                data={"query": query, "title": title, "reasons": policy.get("reasons")},
                summary="Autonomy capsule rejected by policy",
            )
            return

        capsule_start = time.perf_counter()
        capsule = save_capsule(capsule_text, "autonomy")
        self._recent_titles.append(title)
        self._log_event(
            "autonomy_capsule",
            start=capsule_start,
            data={"capsule_id": capsule["id"], "tag": "autonomy", "len": len(capsule_text)},
            summary=f"Autonomy captured capsule {capsule['id']}",
            capsules_delta=1,
        )

    def _run_analysis(self) -> None:
        if tracker().is_halted():
            return
        capsule = self._latest_capsule()
        if not capsule:
            return
        text = str(capsule.get("text", ""))
        start = time.perf_counter()
        policy = check_capsule(text)
        if not policy.get("ok"):
            self._log_event(
                "autonomy_analysis_blocked",
                start=start,
                data={"capsule_id": capsule.get("id"), "reasons": policy.get("reasons")},
                summary="Autonomy analysis blocked",
            )
            return
        tokens = [tok for tok in text.split() if tok]
        unique_tokens = len({tok.lower() for tok in tokens})
        complexity = round(min(1.0, (unique_tokens / max(len(tokens), 1)) * 1.2), 2)
        analysis = {
            "capsule_id": capsule.get("id"),
            "length": len(text),
            "unique_tokens": unique_tokens,
            "complexity_score": complexity,
            "ethics_score": policy.get("ethics_score"),
        }
        self._log_event(
            "autonomy_analysis",
            start=start,
            data=analysis,
            summary=f"Autonomy analysed {capsule.get('id')}",
        )

    def _run_simulation(self) -> None:
        if tracker().is_halted():
            return
        capsule = self._latest_capsule()
        if not capsule:
            return
        text = str(capsule.get("text", ""))
        start = time.perf_counter()
        try:
            simulation = simulate(text)
        except ValueError as exc:
            self._log_event(
                "autonomy_simulation_blocked",
                start=start,
                data={"capsule_id": capsule.get("id"), "error": str(exc)},
                summary="Autonomy simulation blocked",
            )
            return
        summary = f"Autonomy simulated {capsule.get('id')}"
        data = {
            "capsule_id": capsule.get("id"),
            "plan_steps": len(simulation.get("plan", [])),
            "risk_count": len(simulation.get("risks", [])),
        }
        self._log_event("autonomy_simulation", start=start, data=data, summary=summary)

    def _run_learning(self) -> None:
        if tracker().is_halted():
            return
        base = float(self._cycle_count)
        engagement = self._clamp(0.72 + 0.08 * math.sin(base / 3.0))
        success = self._clamp(0.82 + 0.05 * math.cos(base / 4.0))
        feedback = self._clamp(0.78 + 0.06 * math.sin(base / 5.0))
        metrics_payload = {
            "engagement": round(engagement, 3),
            "success_rate": round(success, 3),
            "feedback_score": round(feedback, 3),
        }
        start = time.perf_counter()
        snapshot = learning_step(metrics_payload)
        self._log_event(
            "autonomy_learning",
            start=start,
            data={
                "version": snapshot["version"],
                "delta_score": snapshot["delta_score"],
            },
            summary=f"Autonomy learning step {snapshot['version']}",
        )

    def _run_insights(self) -> None:
        if tracker().is_halted():
            return
        start = time.perf_counter()
        insights = generate_insights()
        summary = "Autonomy generated insights"
        payload = {
            "count": len(insights.get("insights", [])),
            "impact_average": insights.get("metadata", {}).get("average_impact"),
        }
        self._log_event("autonomy_insight", start=start, data=payload, summary=summary)

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------
    @staticmethod
    def bootstrap() -> "AutonomyManager":
        existing = getattr(threading, AutonomyManager._THREAD_KEY, None)
        if isinstance(existing, AutonomyManager):
            if not existing.is_running():
                existing.start()
            return existing
        if existing and hasattr(existing, "stop"):
            try:
                existing.stop()  # type: ignore[attr-defined]
            except Exception:
                pass
        manager = AutonomyManager()
        setattr(threading, AutonomyManager._THREAD_KEY, manager)
        manager.start()
        return manager

    def _log_event(
        self,
        action: str,
        *,
        start: float,
        data: Optional[Dict[str, object]] = None,
        summary: Optional[str] = None,
        capsules_delta: int = 0,
    ) -> None:
        elapsed_ms = (time.perf_counter() - start) * 1000
        payload: Dict[str, object] = {"event": action, "processing_ms": round(elapsed_ms, 2)}
        if data:
            payload.update(data)
        append_log(payload)
        record_autonomy_event(payload)
        tracker().record_background(action, capsules_delta=capsules_delta, summary=summary)

    def _latest_capsule(self) -> Optional[Dict[str, object]]:
        capsules = list_capsules()
        if not capsules:
            return None
        return max(capsules, key=lambda item: str(item.get("created_at", "")))

    @staticmethod
    def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
        return max(low, min(high, value))


__all__ = ["AutonomyManager"]
