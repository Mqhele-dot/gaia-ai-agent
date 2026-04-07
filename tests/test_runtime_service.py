from __future__ import annotations

from pathlib import Path

from gaia.gaia_core.distrustful_agent import TaskExecutionResult, TaskStatus
from gaia.gaia_core.runtime_service import (
    ModelRuntimePolicy,
    RuntimeConfig,
    TaskRuntimeService,
    execute_task_api,
)


class FakeLedger:
    def __init__(self) -> None:
        self.events = []
        self.artifacts = []
        self.tip_hash = "tip-runtime"

    def append(self, record):
        self.events.append(record)
        return record

    def store_artifact(self, payload: bytes, suffix: str = ".bin") -> str:
        self.artifacts.append((payload, suffix))
        return f"h{len(self.artifacts)}"


class FakeToolRunner:
    def __init__(self) -> None:
        self.ledger = FakeLedger()


class FakeEngine:
    def __init__(self, statuses):
        self.tool_runner = FakeToolRunner()
        self.statuses = list(statuses)
        self.calls = []
        self.model_lifecycle = None

    def execute(self, request, proposed_plan=None, approval_token=None, **kwargs):
        self.calls.append((request.task_id, [s.step_id for s in proposed_plan.bounded_steps], approval_token))
        status = self.statuses.pop(0)
        completed = ["s1", "s2", "s3"] if status != TaskStatus.AWAITING_APPROVAL else ["s1", "s2", "s3"]
        failed = "s4" if status == TaskStatus.AWAITING_APPROVAL else None
        return TaskExecutionResult(
            task_id=request.task_id,
            final_status=status,
            completed_steps=completed,
            failed_step=failed,
            commit_hash="abc123" if status in {TaskStatus.COMPLETED, TaskStatus.PARTIAL} else None,
            witness_tip_hash=self.tool_runner.ledger.tip_hash,
            summary=f"status={status}",
            artifacts=[],
            runtime_flags={"dirty_repo_policy_triggered": status == TaskStatus.HALTED, "selector_refinement_attempts": 1},
        )


class FakePlanner:
    def build_plan(self, request, proposed_plan=None):
        from gaia.gaia_core.distrustful_agent import TaskPlan, TaskStep

        return proposed_plan or TaskPlan(
            task_id=request.task_id,
            objective_summary="obj",
            assumptions=[],
            bounded_steps=[
                TaskStep("s1", "inspect_repo", "", "", [], "low", False),
                TaskStep("s2", "plan_edit", "", "", ["s1"], "low", False),
                TaskStep("s3", "apply_edit", "", "", ["s2"], "low", False),
                TaskStep("s4", "finalize_change", "", "", ["s3"], "high", True),
                TaskStep("s5", "summarize_result", "", "", ["s4"], "low", False),
            ],
            success_criteria=["ok"],
            stop_conditions=["halt"],
        )


def _service(tmp_path: Path, statuses):
    (tmp_path / ".git").mkdir(parents=True, exist_ok=True)
    engine = FakeEngine(statuses)
    return TaskRuntimeService(
        runtime_config=RuntimeConfig(repo_root=str(tmp_path)),
        model_policy=ModelRuntimePolicy(),
        planner=FakePlanner(),
        engine=engine,
    ), engine


def test_preflight_failure_halts_before_execution(tmp_path: Path) -> None:
    engine = FakeEngine([TaskStatus.COMPLETED])
    service = TaskRuntimeService(
        runtime_config=RuntimeConfig(repo_root=str(tmp_path)),
        model_policy=ModelRuntimePolicy(),
        planner=FakePlanner(),
        engine=engine,
    )
    try:
        service.execute_one_task("x")
        assert False, "expected preflight failure"
    except RuntimeError:
        pass
    assert len(engine.calls) == 0


def test_api_entrypoint_returns_structured_result(tmp_path: Path) -> None:
    service, _ = _service(tmp_path, [TaskStatus.COMPLETED])
    out = execute_task_api(service, "objective", strict=True)
    assert "result" in out
    assert out["result"].final_status == TaskStatus.COMPLETED


def test_session_snapshot_artifact_created(tmp_path: Path) -> None:
    service, engine = _service(tmp_path, [TaskStatus.COMPLETED])
    out = service.execute_one_task("objective")
    assert out["session_snapshot_hash"].startswith("h")
    assert any(suffix == ".session.json" for _, suffix in engine.tool_runner.ledger.artifacts)


def test_policy_violation_halts_with_runtime_policy_violation(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir(parents=True, exist_ok=True)
    engine = FakeEngine([TaskStatus.COMPLETED])
    service = TaskRuntimeService(
        runtime_config=RuntimeConfig(repo_root=str(tmp_path), max_parallel_tasks=2),
        model_policy=ModelRuntimePolicy(),
        planner=FakePlanner(),
        engine=engine,
    )
    try:
        service.execute_one_task("objective")
        assert False, "expected runtime policy violation"
    except RuntimeError as exc:
        assert "runtime_policy_violation" in str(exc)


def test_model_lifecycle_events_logged(tmp_path: Path) -> None:
    service, engine = _service(tmp_path, [TaskStatus.COMPLETED])
    service.execute_one_task("objective")
    decisions = [evt.get("controller_decision") for evt in engine.tool_runner.ledger.events]
    assert "reasoning_hot" in decisions
    assert "reasoning_exit" in decisions


def test_approval_required_returns_awaiting_approval_cleanly(tmp_path: Path) -> None:
    service, _ = _service(tmp_path, [TaskStatus.AWAITING_APPROVAL])
    out = service.execute_one_task("objective")
    assert out["result"].final_status == TaskStatus.AWAITING_APPROVAL


def test_approval_resume_completes_without_redoing_unrelated_steps(tmp_path: Path) -> None:
    service, engine = _service(tmp_path, [TaskStatus.AWAITING_APPROVAL, TaskStatus.COMPLETED])
    first = service.execute_one_task("objective")
    second = service.resume_with_approval(first["session_id"], "token")
    assert second["result"].final_status == TaskStatus.COMPLETED
    resumed_steps = engine.calls[1][1]
    assert "s1" not in resumed_steps and "s2" not in resumed_steps and "s3" not in resumed_steps


def test_conflicting_session_lock_blocks_second_task(tmp_path: Path) -> None:
    service, _ = _service(tmp_path, [TaskStatus.COMPLETED])
    lock = Path(tmp_path) / ".gaia_task.lock"
    lock.write_text("busy", encoding="utf-8")
    try:
        service.execute_one_task("objective")
        assert False, "expected lock conflict"
    except RuntimeError:
        pass


def test_result_bundle_artifact_is_produced(tmp_path: Path) -> None:
    service, engine = _service(tmp_path, [TaskStatus.COMPLETED])
    out = service.execute_one_task("objective")
    assert out["result_bundle_hash"].startswith("h")
    assert any(suffix == ".result_bundle.json" for _, suffix in engine.tool_runner.ledger.artifacts)


def test_operator_summary_uses_deterministic_result_fields(tmp_path: Path) -> None:
    service, _ = _service(tmp_path, [TaskStatus.PARTIAL])
    out = service.execute_one_task("objective")
    summary = out["operator_summary"]
    assert summary["final_status"] == TaskStatus.PARTIAL
    assert "result_bundle_hash" in summary
    assert "runtime_flags" in summary
