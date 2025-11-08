"""Runtime metrics and health tracking for Gaia."""
from __future__ import annotations

import threading
import time
from typing import Dict, Iterable, List, Optional


ACTION_LABELS = {
    "status": "Status checked",
    "capsule_saved": "Capsule saved",
    "capsule_rejected": "Capsule rejected",
    "capsules_list": "Capsules listed",
    "capsule_analyze": "Capsule analyzed",
    "capsule_analysis_blocked": "Capsule analysis blocked",
    "simulate": "Simulation completed",
    "simulate_blocked": "Simulation blocked",
    "learning_step": "Learning step recorded",
    "upgrade_accepted": "Upgrade accepted",
    "upgrade_rejected": "Upgrade rejected",
    "upgrade_blocked": "Upgrade blocked",
    "kill_switch": "Kill switch engaged",
    "kill_denied": "Kill switch denied",
    "export_capsules": "Capsules exported",
    "export_capsules_failed": "Capsule export failed",
    "export_logs": "Logs exported",
    "research_explore": "Scientific research explored",
    "learning_history": "Learning history viewed",
    "insight_reflect": "Strategic insight generated",
    "version_set": "Version updated",
    "system_halted": "System halted",
    "system_resumed": "System resumed",
    "settings_view": "Settings viewed",
    "settings_update": "Settings updated",
    "autonomy_research": "Autonomy research",
    "autonomy_research_error": "Autonomy research error",
    "autonomy_capsule": "Autonomy capsule saved",
    "autonomy_capsule_blocked": "Autonomy capsule blocked",
    "autonomy_analysis": "Autonomy analysis",
    "autonomy_analysis_blocked": "Autonomy analysis blocked",
    "autonomy_simulation": "Autonomy simulation",
    "autonomy_simulation_blocked": "Autonomy simulation blocked",
    "autonomy_learning": "Autonomy learning",
    "autonomy_insight": "Autonomy insight",
    "autonomy_idle": "Autonomy idle",
}


def _humanize(action: str, summary: Optional[str]) -> str:
    if summary:
        return summary
    if action in ACTION_LABELS:
        return ACTION_LABELS[action]
    cleaned = action.replace("_", " ").strip()
    return cleaned.capitalize() if cleaned else action


class MetricsTracker:
    """Thread-safe metrics collector used across the application."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._start_ts = time.time()
        self._data: Dict[str, float] = {
            "api_calls": 0,
            "capsules_processed": 0,
            "processing_ms_total": 0.0,
        }
        self._last_action: Optional[str] = None
        self._last_action_label: Optional[str] = None
        self._last_action_event: Optional[str] = None
        self._version = "gaia-v1.0"
        self._halted = False
        self._learning_version: Optional[str] = None
        self._learning_delta: Optional[float] = None
        self._learning_history: List[float] = []

    @staticmethod
    def _derive_label(action: str, summary: Optional[str], humanized: str) -> str:
        base = summary or ACTION_LABELS.get(action) or action.replace("_", " ").title()
        cleaned = base.split("Δ", 1)[0]
        cleaned = cleaned.split("(", 1)[0]
        cleaned = cleaned.split(":", 1)[0]
        cleaned = cleaned.strip()
        if cleaned:
            return cleaned
        fallback = humanized.split("(", 1)[0].strip()
        return fallback or humanized

    def _update_last_action(
        self,
        action: str,
        summary: Optional[str],
        *,
        humanized: Optional[str] = None,
        event: Optional[str] = None,
    ) -> None:
        detail = humanized or _humanize(action, summary)
        self._last_action = detail
        self._last_action_label = self._derive_label(action, summary, detail)
        self._last_action_event = event or action

    def record_api_call(
        self,
        action: str,
        processing_ms: float,
        *,
        capsules_delta: int = 0,
        summary: Optional[str] = None,
    ) -> None:
        with self._lock:
            self._data["api_calls"] += 1
            self._data["processing_ms_total"] += float(processing_ms)
            if capsules_delta:
                self._data["capsules_processed"] += capsules_delta
            humanized = _humanize(action, summary)
            self._update_last_action(action, summary, humanized=humanized)

    def record_background(
        self,
        action: str,
        *,
        capsules_delta: int = 0,
        summary: Optional[str] = None,
    ) -> None:
        with self._lock:
            if capsules_delta:
                self._data["capsules_processed"] += capsules_delta
            humanized = _humanize(action, summary)
            self._update_last_action(action, summary, humanized=humanized, event=action)

    def set_version(self, version: str) -> None:
        with self._lock:
            self._version = version
            summary = f"Version set to {version}"
            self._update_last_action("version_set", summary, humanized=summary, event="version_set")

    def set_halted(self, halted: bool) -> None:
        with self._lock:
            self._halted = halted
            summary = "System halted" if halted else "System resumed"
            event = "system_halted" if halted else "system_resumed"
            self._update_last_action(event, summary, humanized=summary, event=event)

    def update_learning(self, version: str, delta: float) -> None:
        with self._lock:
            self._learning_version = version
            self._learning_delta = delta
            self._learning_history.append(delta)
            if len(self._learning_history) > 50:
                self._learning_history = self._learning_history[-50:]
            summary = f"Learning step Δ{delta:+.3f}"
            self._update_last_action("learning_step", summary, humanized=summary, event="learning_step")

    def seed_learning_history(
        self,
        version: Optional[str],
        delta: Optional[float],
        history: Iterable[float],
    ) -> None:
        with self._lock:
            if version:
                self._learning_version = version
            if delta is not None:
                self._learning_delta = delta
            self._learning_history = list(history)[-50:]

    def is_halted(self) -> bool:
        with self._lock:
            return self._halted

    def get_status(self) -> Dict[str, object]:
        with self._lock:
            uptime_s = time.time() - self._start_ts
            api_calls = int(self._data["api_calls"])
            avg_ms = self._data["processing_ms_total"] / api_calls if api_calls else 0.0
            return {
                "uptime_s": round(uptime_s, 2),
                "version": self._version,
                "last_action": self._last_action,
                "last_action_label": self._last_action_label,
                "last_action_event": self._last_action_event,
                "capsules_processed": int(self._data["capsules_processed"]),
                "api_calls": api_calls,
                "processing_ms_avg": round(avg_ms, 2),
                "halted": self._halted,
                "learning_version": self._learning_version or self._version,
                "learning_delta": self._learning_delta,
                "learning_history": list(self._learning_history),
            }


_metrics = MetricsTracker()


def tracker() -> MetricsTracker:
    return _metrics


__all__ = ["tracker", "MetricsTracker"]
