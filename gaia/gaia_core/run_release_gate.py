"""Release-gate CLI for bounded-use readiness checks."""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
import uuid

from .distrustful_agent import TaskExecutionResult, TaskStatus
from .eval_harness import EvalHarness, EvalRunConfig, EvalScenario, basic_scenarios, resolve_release_profile
from .runtime_service import ModelRuntimePolicy, RuntimeConfig, TaskRuntimeService


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run release gate for distrustful local agent")
    parser.add_argument("--profile", default="local_16gb")
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--output-dir", default="gaia/data/eval_reports")
    args = parser.parse_args(argv)

    class _Ledger:
        def __init__(self) -> None:
            self.events = []
            self.artifacts = []
            self.tip_hash = "tip-release-gate"

        def append(self, record):
            self.events.append(record)
            return record

        def store_artifact(self, payload: bytes, suffix: str = ".bin") -> str:
            self.artifacts.append((payload, suffix))
            return f"h{len(self.artifacts)}"

    class _ToolRunner:
        def __init__(self) -> None:
            self.ledger = _Ledger()

    class _ScenarioEngine:
        def __init__(self, scenario: EvalScenario) -> None:
            self.scenario = scenario
            self.tool_runner = _ToolRunner()
            self.model_lifecycle = None

        def execute(self, request, proposed_plan=None, approval_token=None, **_kwargs):
            status = self.scenario.expected_outcome
            summary = f"status={status}"
            if "rollback" in self.scenario.tags:
                summary = "rollback completed due to verification failure"
            runtime_flags = {
                "dirty_repo_policy_triggered": self.scenario.scenario_id.startswith("dirty_repo"),
                "dirty_repo_override_used": self.scenario.scenario_id == "dirty_repo_override",
                "selector_refinement_attempts": 1 if self.scenario.scenario_id == "refinement_exhausted" else 0,
                "untrusted_content_risk_detected": "adversarial" in self.scenario.tags,
            }
            return TaskExecutionResult(
                task_id=request.task_id,
                final_status=status,
                completed_steps=["s1", "s2", "s3"],
                failed_step=None if status in {TaskStatus.COMPLETED, TaskStatus.PARTIAL} else "s4",
                commit_hash="abc123" if status in {TaskStatus.COMPLETED, TaskStatus.PARTIAL} else None,
                witness_tip_hash=self.tool_runner.ledger.tip_hash,
                summary=summary,
                artifacts=[],
                runtime_flags=runtime_flags,
            )

    class _Planner:
        def build_plan(self, request, proposed_plan=None):
            from .distrustful_agent import TaskPlan, TaskStep

            return proposed_plan or TaskPlan(
                task_id=request.task_id,
                objective_summary="release_campaign",
                assumptions=[],
                bounded_steps=[
                    TaskStep("s1", "inspect_repo", "", "", [], "low", False),
                    TaskStep("s2", "plan_edit", "", "", ["s1"], "low", False),
                    TaskStep("s3", "apply_edit", "", "", ["s2"], "low", False),
                    TaskStep("s4", "finalize_change", "", "", ["s3"], "high", True),
                ],
                success_criteria=["bounded_execution_complete"],
                stop_conditions=["policy_violation", "verification_fail"],
            )

    fixture_root = Path("gaia/data/eval_fixtures")
    fixture_root.mkdir(parents=True, exist_ok=True)

    def _service_factory(scenario: EvalScenario):
        scenario_root = fixture_root / scenario.scenario_id
        scenario_root.mkdir(parents=True, exist_ok=True)
        (scenario_root / ".git").mkdir(exist_ok=True)
        repo_root = scenario_root / f"run-{uuid.uuid4().hex[:8]}"
        repo_root.mkdir(parents=True, exist_ok=True)
        (repo_root / ".git").mkdir(exist_ok=True)
        engine = _ScenarioEngine(scenario)
        return TaskRuntimeService(
            runtime_config=RuntimeConfig(repo_root=str(repo_root), dirty_repo_override=scenario.scenario_id == "dirty_repo_override"),
            model_policy=ModelRuntimePolicy(),
            planner=_Planner(),
            engine=engine,
        )

    harness = EvalHarness(_service_factory)
    profile = resolve_release_profile(args.profile)
    try:
        report, tuning, digest, release = harness.evaluate_release_candidate(
            basic_scenarios(),
            EvalRunConfig(iterations=args.iterations),
            profile,
        )
    except RuntimeError as exc:
        print(f"release_gate_error={exc}")
        return 2

    print(f"readiness_verdict={release.overall_readiness_verdict}")
    print(f"blocking_issue_count={len(release.blocking_issues)}")
    print(f"aggregate_report_hash={report.report_hash}")
    print(f"telemetry_digest_hash={digest.digest_hash}")
    print(f"threshold_tuning_report_hash={tuning.report_hash}")
    print(f"release_summary_hash={release.summary_hash}")
    print(f"profile={profile.target_machine_label}")

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "aggregate_report.json").write_text(json.dumps(asdict(report), indent=2, sort_keys=True), encoding="utf-8")
    (out_dir / "threshold_tuning_report.json").write_text(json.dumps(asdict(tuning), indent=2, sort_keys=True), encoding="utf-8")
    (out_dir / "telemetry_digest.json").write_text(json.dumps(asdict(digest), indent=2, sort_keys=True), encoding="utf-8")
    (out_dir / "release_summary.json").write_text(json.dumps(asdict(release), indent=2, sort_keys=True), encoding="utf-8")
    print(f"output_dir={out_dir}")

    return 0 if not release.blocking_issues else 2


if __name__ == "__main__":
    raise SystemExit(main())
