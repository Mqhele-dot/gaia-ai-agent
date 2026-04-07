from __future__ import annotations

from dataclasses import dataclass

from gaia.gaia_core.distrustful_agent import TaskExecutionResult, TaskStatus
from gaia.gaia_core.eval_harness import (
    ArtifactRetentionManager,
    EvalHarness,
    EvalRunConfig,
    EvalRunResult,
    EvalScenario,
    FailureInjectionConfig,
    ReleaseReadinessProfile,
)


class FakeLedger:
    def __init__(self) -> None:
        self.events = [{"phase": "runtime_model_state"}, {"phase": "task_completed"}]
        self.artifacts = [b"a", b"b"]


class FakeToolRunner:
    def __init__(self) -> None:
        self.ledger = FakeLedger()


class FakeEngine:
    def __init__(self) -> None:
        self.tool_runner = FakeToolRunner()


class FakeService:
    def __init__(self, status: str = TaskStatus.COMPLETED, summary_status: str | None = None) -> None:
        self.engine = FakeEngine()
        self.calls = 0
        self.status = status
        self.summary_status = summary_status or status

    def execute_one_task(self, objective: str, strict: bool = False, approval_token=None):
        self.calls += 1
        result = TaskExecutionResult(
            task_id="task-1",
            final_status=self.status,
            completed_steps=["s1"],
            failed_step=None,
            commit_hash="abc" if self.status in {TaskStatus.COMPLETED, TaskStatus.PARTIAL} else None,
            witness_tip_hash="tip",
            summary=f"status={self.status}",
            artifacts=[],
        )
        return {
            "result": result,
            "result_bundle_hash": "bundle-h",
            "session_snapshot_hash": "snap-h",
            "operator_summary": {"final_status": self.summary_status},
        }


def test_scenario_runner_uses_runtime_service() -> None:
    service = FakeService()
    harness = EvalHarness(lambda _: service)
    scenario = EvalScenario("s", "t", "obj", "fixture", TaskStatus.COMPLETED, False, False, [])
    results = harness.run_scenario(scenario, EvalRunConfig(iterations=1))
    assert service.calls == 1
    assert results[0].success


def test_aggregate_report_is_deterministic_hash() -> None:
    harness = EvalHarness(lambda _: FakeService())
    runs = [
        EvalRunResult("s", "r1", TaskStatus.COMPLETED, True, "abc", "tip", None, 1.0, 10.0, ["a"], [], []),
        EvalRunResult("s", "r2", TaskStatus.COMPLETED, True, "abc", "tip", None, 2.0, 11.0, ["a"], [], []),
    ]
    one = harness.aggregate(runs, suite_id="x")
    two = harness.aggregate(runs, suite_id="x")
    assert one.report_hash == two.report_hash


def test_injected_failures_recorded() -> None:
    harness = EvalHarness(lambda _: FakeService())
    scenario = EvalScenario(
        "s",
        "t",
        "obj",
        "fixture",
        TaskStatus.COMPLETED,
        False,
        False,
        [],
        failure_injection=FailureInjectionConfig(git_notes_failure=True, pytest_failure=True),
    )
    results = harness.run_scenario(scenario, EvalRunConfig(iterations=1, enable_failure_injection=True))
    assert "git_notes_failure" in results[0].injected_failures
    assert "pytest_failure" in results[0].injected_failures


def test_pass_rate_calculation() -> None:
    harness = EvalHarness(lambda _: FakeService())
    runs = [
        EvalRunResult("s", "r1", TaskStatus.COMPLETED, True, None, "tip", None, 1, 1, [], [], []),
        EvalRunResult("s", "r2", TaskStatus.FAILED, False, None, "tip", "s2", 1, 1, [], [], []),
    ]
    report = harness.aggregate(runs, suite_id="x")
    assert report.pass_rate == 0.5


def test_readiness_scoring_stable() -> None:
    harness = EvalHarness(lambda _: FakeService())
    runs = [EvalRunResult("s", "r1", TaskStatus.COMPLETED, True, None, "tip", None, 1, 1, [], [], [])]
    score1 = harness.aggregate(runs, suite_id="x").readiness_score
    score2 = harness.aggregate(runs, suite_id="x").readiness_score
    assert score1 == score2


def test_invariant_violation_marks_failure() -> None:
    bad_service = FakeService(status=TaskStatus.COMPLETED, summary_status=TaskStatus.FAILED)
    harness = EvalHarness(lambda _: bad_service)
    scenario = EvalScenario("s", "t", "obj", "fixture", TaskStatus.COMPLETED, False, False, [])
    results = harness.run_scenario(scenario, EvalRunConfig(iterations=1))
    assert "summary_mismatch" in results[0].invariant_violations
    assert not results[0].success


def test_profiling_fields_present() -> None:
    harness = EvalHarness(lambda _: FakeService())
    scenario = EvalScenario("s", "t", "obj", "fixture", TaskStatus.COMPLETED, False, False, [])
    result = harness.run_scenario(scenario, EvalRunConfig(iterations=1))[0]
    assert result.runtime_s >= 0.0
    assert result.peak_memory_mb >= 0.0
    assert len(result.artifact_hashes) >= 1


def test_burn_in_mixed_outcomes_aggregated() -> None:
    statuses = [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.PARTIAL]

    def _factory(_):
        status = statuses.pop(0)
        return FakeService(status=status)

    harness = EvalHarness(_factory)
    scenario = EvalScenario("s", "t", "obj", "fixture", TaskStatus.COMPLETED, False, False, [])
    runs = harness.run_scenario(scenario, EvalRunConfig(iterations=3))
    report = harness.aggregate(runs, suite_id="x")
    assert report.run_count == 3
    assert report.final_status_distribution[TaskStatus.COMPLETED] == 1
    assert report.final_status_distribution[TaskStatus.FAILED] == 1
    assert report.final_status_distribution[TaskStatus.PARTIAL] == 1


def test_threshold_tuning_report_is_deterministic() -> None:
    harness = EvalHarness(lambda _: FakeService())
    runs = [EvalRunResult("s", "r1", TaskStatus.COMPLETED, True, None, "tip", None, 1, 1, [], [], [])]
    report = harness.aggregate(runs, suite_id="x")
    profile = ReleaseReadinessProfile()
    one = harness.build_threshold_tuning_report(report, profile)
    two = harness.build_threshold_tuning_report(report, profile)
    assert one.report_hash == two.report_hash


def test_telemetry_digest_is_deterministic() -> None:
    harness = EvalHarness(lambda _: FakeService())
    runs = [
        EvalRunResult("s", "r1", TaskStatus.COMPLETED, True, None, "tip", None, 1, 1, [], [], [], runtime_flags={}),
        EvalRunResult("s", "r2", TaskStatus.PARTIAL, False, None, "tip", None, 2, 2, [], [], [], runtime_flags={"dirty_repo_policy_triggered": True}),
    ]
    one = harness.build_telemetry_digest(runs)
    two = harness.build_telemetry_digest(runs)
    assert one.digest_hash == two.digest_hash


def test_quarantine_visibility_in_report() -> None:
    harness = EvalHarness(lambda _: FakeService())
    runs = [EvalRunResult("preflight_fail", "r1", TaskStatus.HALTED, False, None, "tip", None, 1, 1, [], [], [])]
    report = harness.aggregate(runs, suite_id="x")
    assert report.scenario_triage["preflight_fail"] == "quarantined"
    assert "preflight_fail" in report.quarantined_scenarios


def test_retention_policy_never_deletes_witness(tmp_path) -> None:
    mgr = ArtifactRetentionManager()
    (tmp_path / "witness.jsonl").write_text("{}", encoding="utf-8")
    (tmp_path / "old1.json").write_text("{}", encoding="utf-8")
    (tmp_path / "old2.json").write_text("{}", encoding="utf-8")
    removed = mgr.apply(tmp_path, "keep_last_n_runs", keep_last_n=1)
    assert "witness.jsonl" not in removed
    assert (tmp_path / "witness.jsonl").exists()
