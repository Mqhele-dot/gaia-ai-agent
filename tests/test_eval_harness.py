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
    resolve_release_profile,
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
    report = harness.aggregate(runs, suite_id="x")
    one = harness.build_telemetry_digest(runs, report)
    two = harness.build_telemetry_digest(runs, report)
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


def test_expected_halted_outcome_is_not_marked_unstable() -> None:
    harness = EvalHarness(lambda _: FakeService(status=TaskStatus.HALTED))
    runs = [EvalRunResult("s", "r1", TaskStatus.HALTED, True, None, "tip", "s1", 1, 1, [], [], [])]
    report = harness.aggregate(runs, suite_id="x")
    assert report.unstable_scenarios == []


def test_resolve_release_profile_uses_known_presets() -> None:
    profile = resolve_release_profile("local_8gb")
    assert profile.target_machine_label == "local_8gb"
    assert profile.max_allowed_peak_memory_mb == 8_192.0


def test_adversarial_expected_halt_counts_as_pass() -> None:
    harness = EvalHarness(lambda _: FakeService(status=TaskStatus.HALTED))
    scenario = EvalScenario("adv", "t", "obj", "fixture", TaskStatus.HALTED, False, False, [], scenario_class="adversarial")
    result = harness.run_scenario(scenario, EvalRunConfig(iterations=1))[0]
    assert result.success


def test_nominal_completed_missing_invariant_fails_validation() -> None:
    scenario = EvalScenario("nominal", "t", "obj", "fixture", TaskStatus.COMPLETED, False, False, [], scenario_class="nominal")
    verdict = EvalHarness.validate_scenario_run(
        {"final_status": TaskStatus.COMPLETED, "witness_tip_hash": "", "commit_hash": "abc", "runtime_flags": {}, "invariant_violations": []},
        scenario,
    )
    assert not verdict["scenario_passed"]
    assert "missing_witness_tip" in verdict["invariant_failures"]


def test_nominal_protocol_pass_rates_and_partial_split() -> None:
    harness = EvalHarness(lambda _: FakeService())
    scenarios = [
        EvalScenario("n1", "n1", "obj", "f", TaskStatus.COMPLETED, False, False, [], scenario_class="nominal"),
        EvalScenario("p1", "p1", "obj", "f", TaskStatus.PARTIAL, False, False, [], scenario_class="boundary"),
    ]
    runs = [
        EvalRunResult("n1", "r1", TaskStatus.COMPLETED, True, "abc", "tip", None, 1, 1, [], [], []),
        EvalRunResult("p1", "r2", TaskStatus.PARTIAL, True, "abc", "tip", None, 1, 1, [], [], []),
    ]
    report = harness.aggregate(runs, suite_id="x", scenarios=scenarios)
    assert report.nominal_pass_rate == 1.0
    assert report.protocol_pass_rate == 1.0
    assert report.expected_partial_finalization_count == 1
    assert report.unexpected_partial_finalization_count == 0


def test_quarantine_visibility_without_nominal_distortion() -> None:
    harness = EvalHarness(lambda _: FakeService())
    scenarios = [
        EvalScenario("simple", "t", "obj", "f", TaskStatus.COMPLETED, False, False, [], scenario_class="nominal"),
        EvalScenario("preflight_fail", "t", "obj", "f", TaskStatus.HALTED, False, False, [], scenario_class="infrastructure"),
    ]
    runs = [
        EvalRunResult("simple", "r1", TaskStatus.COMPLETED, True, "abc", "tip", None, 1, 1, [], [], []),
        EvalRunResult("preflight_fail", "r2", TaskStatus.HALTED, True, None, "tip", "s1", 1, 1, [], [], []),
    ]
    report = harness.aggregate(runs, suite_id="x", scenarios=scenarios)
    assert report.nominal_pass_rate == 1.0
    assert report.scenario_triage["preflight_fail"] == "quarantined"


def test_release_summary_tracks_override_assisted_runs_not_clean() -> None:
    harness = EvalHarness(lambda _: FakeService())
    scenarios = [EvalScenario("dirty_repo_override", "t", "obj", "f", TaskStatus.COMPLETED, False, False, [], scenario_class="nominal")]
    runs = [
        EvalRunResult(
            "dirty_repo_override",
            "r1",
            TaskStatus.COMPLETED,
            True,
            "abc",
            "tip",
            None,
            1,
            1,
            [],
            [],
            [],
            runtime_flags={"dirty_repo_override_used": True, "dirty_repo_policy_triggered": True},
        )
    ]
    report = harness.aggregate(runs, suite_id="x", scenarios=scenarios)
    digest = harness.build_telemetry_digest(runs, report)
    summary = harness.build_release_summary(report, digest, ReleaseReadinessProfile())
    assert summary.clean_nominal_pass_rate == 0.0
