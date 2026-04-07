from __future__ import annotations

import json

from gaia.gaia_core import run_release_gate
from gaia.gaia_core import run_eval
from gaia.gaia_core.eval_harness import EvalAggregateReport, ReleaseSummary, TelemetryDigest, ThresholdTuningReport


def test_release_gate_nonzero_on_blocking_failures(monkeypatch, tmp_path) -> None:
    class FakeHarness:
        def __init__(self, *_args, **_kwargs):
            pass

        def evaluate_release_candidate(self, *_args, **_kwargs):
            report = EvalAggregateReport(
                suite_id="release_candidate",
                generated_at="2026-01-01T00:00:00Z",
                run_count=1,
                pass_rate=0.0,
                nominal_pass_rate=0.0,
                protocol_pass_rate=0.0,
                mean_runtime_s=1.0,
                median_runtime_s=1.0,
                max_runtime_s=1.0,
                avg_peak_memory_mb=1.0,
                failure_histogram={"x": 1},
                unexpected_failure_histogram={"x": 1},
                expected_failure_histogram={},
                final_status_distribution={},
                rollback_occurrence_count=0,
                approval_pause_count=0,
                partial_finalization_count=0,
                expected_partial_finalization_count=0,
                unexpected_partial_finalization_count=0,
                adversarial_risk_count=0,
                refinement_attempt_distribution={},
                dirty_repo_policy_trigger_count=0,
                mutation_blocked_count=0,
                isolation_activation_count=0,
                isolation_required_and_triggered_count=0,
                dirty_repo_policy_required_and_triggered_count=0,
                clean_nominal_run_count=0,
                override_assisted_run_count=0,
                nominal_selector_refinement_count=0,
                nominal_deterministic_narrowing_count=0,
                unstable_scenarios=[],
                recommendation="not_ready",
                readiness_score={},
                report_hash="rhash",
            )
            tuning = ThresholdTuningReport("local_16gb", {}, {}, [], 0.0, 0.0, 0.0, 0.0, 0.0, "thash")
            digest = TelemetryDigest(1.0, 1.0, 1.0, [1.0, 1.0], 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0, {}, {}, 0, 0, 0.0, 0.0, "dhash")
            release = ReleaseSummary("needs hardening", {}, ["x"], ["fix"], "rhash", "dhash", {}, 0.0, 0.0, 0, 0, {}, {"x": 1}, 0.0, "shash")
            return report, tuning, digest, release

    monkeypatch.setattr(run_release_gate, "EvalHarness", FakeHarness)
    code = run_release_gate.main(["--profile", "local_16gb", "--iterations", "1", "--output-dir", str(tmp_path / "out")])
    assert code == 2


def test_release_gate_reports_clean_nominal_improvement(tmp_path) -> None:
    out_dir = tmp_path / "campaign"
    code = run_release_gate.main(["--profile", "local_16gb", "--iterations", "1", "--output-dir", str(out_dir)])
    assert code == 0
    aggregate = json.loads((out_dir / "aggregate_report.json").read_text(encoding="utf-8"))
    assert aggregate["clean_nominal_run_count"] >= 1
    assert aggregate["override_assisted_run_count"] == 0
    assert (out_dir / "override_diagnostics.json").exists()
    assert (out_dir / "nominal_path_optimization_report.json").exists()


def test_run_eval_release_candidate_uses_release_gate_path(tmp_path) -> None:
    code = run_eval.main(["--suite", "release_candidate", "--iterations", "1", "--output-dir", str(tmp_path / "out")])
    assert code == 0
