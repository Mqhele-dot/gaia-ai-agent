"""Runtime metrics and health tracking for Gaia."""
from __future__ import annotations

import threading
import time
from typing import Dict, Optional


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
        self._version = "gaia-v1.0"
        self._halted = False
        self._learning_version: Optional[str] = None
        self._learning_delta: Optional[float] = None

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
            self._last_action = _humanize(action, summary)

    def set_version(self, version: str) -> None:
        with self._lock:
            self._version = version
            self._last_action = f"Version set to {version}"

    def set_halted(self, halted: bool) -> None:
        with self._lock:
            self._halted = halted
            self._last_action = "System halted" if halted else "System resumed"

    def update_learning(self, version: str, delta: float) -> None:
        with self._lock:
            self._learning_version = version
            self._learning_delta = delta
            self._last_action = _humanize("learning_step", f"Learning step Δ{delta:+.3f}")

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
                "capsules_processed": int(self._data["capsules_processed"]),
                "api_calls": api_calls,
                "processing_ms_avg": round(avg_ms, 2),
                "halted": self._halted,
                "learning_version": self._learning_version or self._version,
                "learning_delta": self._learning_delta,
            }


_metrics = MetricsTracker()


def tracker() -> MetricsTracker:
    return _metrics


__all__ = ["tracker", "MetricsTracker"]
