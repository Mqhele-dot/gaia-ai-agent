"""Operational runtime wrapper for distrustful task execution."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .distrustful_agent import (
    BoundedPlanner,
    TaskExecutionEngine,
    TaskExecutionResult,
    TaskPlan,
    TaskRequest,
    TaskStatus,
    _utc_now,
    sha256_bytes,
)


@dataclass(frozen=True)
class RuntimeConfig:
    repo_root: str
    strict_mode_default: bool = False
    max_parallel_tasks: int = 1
    enable_finalize_by_default: bool = True
    artifact_retention_policy: str = "keep"
    approval_required_default: bool = True
    dirty_repo_policy: str = "block_on_dirty_repo"
    dirty_repo_override: bool = False
    untrusted_risk_block_threshold: int = 3
    min_artifact_free_space_mb: int = 256


@dataclass(frozen=True)
class ModelRuntimePolicy:
    max_loaded_models: int = 1
    unload_model_during_verification: bool = True
    keep_alive_seconds: int = 30
    max_context_bytes: int = 32_000
    max_retrieval_chunks: int = 8
    allow_parallel_reasoning: bool = False
    force_single_active_reasoning_model: bool = True


@dataclass
class ExecutionSession:
    session_id: str
    task_id: str
    started_at: str
    current_status: str
    current_step_id: Optional[str]
    current_model_state: str
    last_witness_tip_hash: str
    approval_pending: bool
    runtime_flags: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SessionStateSnapshot:
    session_id: str
    task_id: str
    status: str
    completed_steps: Tuple[str, ...]
    current_step_id: Optional[str]
    witness_tip_hash: str
    approval_pending: bool
    finalization_occurred: bool
    runtime_flags: Dict[str, Any]
    timestamp: str


class ModelLifecycleController:
    """Deterministic model lifecycle state machine for runtime policy enforcement."""

    def __init__(self, policy: ModelRuntimePolicy, *, event_sink) -> None:
        self.policy = policy
        self._active_model: Optional[str] = None
        self._state = "cold"
        self._event_sink = event_sink

    def enter_reasoning_window(self, model_name: str) -> None:
        if self.policy.force_single_active_reasoning_model and self._active_model and self._active_model != model_name:
            raise RuntimeError("multiple_active_models_blocked")
        self._active_model = model_name
        self._state = "hot"
        self._event_sink("runtime_model_state", "reasoning_hot", "", "")

    def exit_reasoning_window(self, model_name: str) -> None:
        if self._active_model == model_name:
            if self.policy.unload_model_during_verification:
                self._state = "cold"
            else:
                self._state = "warm"
        self._event_sink("runtime_model_state", "reasoning_exit", "", "")

    def mark_model_unloaded(self, model_name: str) -> None:
        if self._active_model == model_name:
            self._state = "cold"
            self._active_model = None
        self._event_sink("runtime_model_state", "model_unloaded", "", "")

    def current_model_state(self) -> str:
        return self._state


class TaskRuntimeService:
    """Runtime entrypoint for bounded execution with session, policy and lock control."""

    def __init__(
        self,
        *,
        runtime_config: RuntimeConfig,
        model_policy: ModelRuntimePolicy,
        planner: BoundedPlanner,
        engine: TaskExecutionEngine,
    ) -> None:
        self.runtime_config = runtime_config
        self.model_policy = model_policy
        self.planner = planner
        self.engine = engine
        self.repo_root = Path(runtime_config.repo_root)
        self._lock_path = self.repo_root / ".gaia_task.lock"
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self.model_lifecycle = ModelLifecycleController(model_policy, event_sink=self._runtime_event)
        self.engine.model_lifecycle = self.model_lifecycle
        if hasattr(self.engine, "policy"):
            try:
                self.engine.policy.risk_block_threshold = runtime_config.untrusted_risk_block_threshold
            except Exception:
                # Keep immutable policy objects intact; runtime will use engine defaults.
                pass
        self._snapshot_dir = self.repo_root / "gaia" / "data" / "runtime_sessions"
        self._snapshot_dir.mkdir(parents=True, exist_ok=True)

    def execute_one_task(
        self,
        objective: str,
        *,
        strict: Optional[bool] = None,
        approval_token: Optional[str] = None,
        proposed_plan: Optional[TaskPlan] = None,
    ) -> Dict[str, Any]:
        strict_mode = self.runtime_config.strict_mode_default if strict is None else strict
        request = TaskRequest(
            task_id=f"task-{uuid.uuid4().hex[:10]}",
            user_objective=objective,
            repo_root=str(self.repo_root),
            strict_mode=strict_mode,
            approval_required=self.runtime_config.approval_required_default,
            created_at=_utc_now(),
        )
        return self.execute_request(request, approval_token=approval_token, proposed_plan=proposed_plan)

    def execute_request(
        self,
        request: TaskRequest,
        *,
        approval_token: Optional[str] = None,
        proposed_plan: Optional[TaskPlan] = None,
    ) -> Dict[str, Any]:
        self._acquire_lock()
        try:
            self._preflight_check(request)
            plan = self.planner.build_plan(request, proposed_plan=proposed_plan)
            session = ExecutionSession(
                session_id=f"session-{uuid.uuid4().hex[:10]}",
                task_id=request.task_id,
                started_at=_utc_now(),
                current_status=TaskStatus.PENDING,
                current_step_id=None,
                current_model_state=self.model_lifecycle.current_model_state(),
                last_witness_tip_hash=self.engine.tool_runner.ledger.tip_hash,
                approval_pending=False,
                runtime_flags={},
            )
            self._sessions[session.session_id] = {"request": request, "plan": plan, "session": session}
            self.model_lifecycle.enter_reasoning_window("primary_reasoning_model")
            result = self.engine.execute(
                request,
                proposed_plan=plan,
                approval_token=approval_token,
                dirty_repo_policy=self.runtime_config.dirty_repo_policy,
                dirty_repo_override=self.runtime_config.dirty_repo_override,
            )
            self.model_lifecycle.exit_reasoning_window("primary_reasoning_model")
            if result.final_status in {TaskStatus.COMPLETED, TaskStatus.PARTIAL, TaskStatus.FAILED, TaskStatus.HALTED}:
                self.model_lifecycle.mark_model_unloaded("primary_reasoning_model")

            session.current_status = result.final_status
            session.last_witness_tip_hash = result.witness_tip_hash
            session.approval_pending = result.final_status == TaskStatus.AWAITING_APPROVAL
            session.current_model_state = self.model_lifecycle.current_model_state()

            snapshot_hash = self._persist_session_snapshot(session, result)
            bundle_hash = self._materialize_result_bundle(request, plan, result, snapshot_hash)
            operator_summary = self._operator_summary(request, result, bundle_hash)

            self._sessions[session.session_id].update({"result": result, "snapshot_hash": snapshot_hash, "bundle_hash": bundle_hash})
            return {
                "session_id": session.session_id,
                "task_id": request.task_id,
                "result": result,
                "result_bundle_hash": bundle_hash,
                "session_snapshot_hash": snapshot_hash,
                "operator_summary": operator_summary,
            }
        finally:
            self._release_lock()

    def resume_with_approval(self, session_id: str, approval_token: str) -> Dict[str, Any]:
        if session_id not in self._sessions:
            raise ValueError("unknown_session")
        payload = self._sessions[session_id]
        prior_result: TaskExecutionResult = payload.get("result")
        if not prior_result or prior_result.final_status != TaskStatus.AWAITING_APPROVAL:
            raise ValueError("session_not_awaiting_approval")

        plan: TaskPlan = payload["plan"]
        remaining = [step for step in plan.bounded_steps if step.step_id not in set(prior_result.completed_steps)]
        resume_plan = TaskPlan(
            task_id=plan.task_id,
            objective_summary=plan.objective_summary,
            assumptions=plan.assumptions,
            bounded_steps=remaining,
            success_criteria=plan.success_criteria,
            stop_conditions=plan.stop_conditions,
        )
        return self.execute_request(payload["request"], approval_token=approval_token, proposed_plan=resume_plan)

    def _preflight_check(self, request: TaskRequest) -> None:
        if self.runtime_config.max_parallel_tasks != 1:
            self._runtime_event("preflight", "max_parallel_tasks_invalid", "", "runtime_policy_violation")
            raise RuntimeError("runtime_policy_violation")
        if not (self.repo_root / ".git").exists():
            self._runtime_event("preflight", "missing_git_repo", "", "runtime_policy_violation")
            raise RuntimeError("runtime_policy_violation")
        if not os.access(self.repo_root, os.W_OK):
            self._runtime_event("preflight", "repo_not_writable", "", "runtime_policy_violation")
            raise RuntimeError("runtime_policy_violation")
        if self.model_policy.max_loaded_models > 1 and self.model_policy.force_single_active_reasoning_model:
            self._runtime_event("preflight", "model_policy_conflict", "", "runtime_policy_violation")
            raise RuntimeError("runtime_policy_violation")
        usage = shutil.disk_usage(self.repo_root)
        free_mb = usage.free / (1024 * 1024)
        if free_mb < float(self.runtime_config.min_artifact_free_space_mb):
            self._runtime_event("preflight", "insufficient_artifact_disk_headroom", "", "runtime_policy_violation")
            raise RuntimeError("runtime_policy_violation")
        self._runtime_event("preflight", "ok", "", "")

    def _acquire_lock(self) -> None:
        try:
            fd = os.open(self._lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"pid={os.getpid()} ts={_utc_now()}".encode("utf-8"))
            os.close(fd)
            self._runtime_event("runtime_lock", "acquired", "", "")
        except FileExistsError as exc:
            self._runtime_event("runtime_lock", "conflict", "", "runtime_policy_violation")
            raise RuntimeError("runtime_policy_violation") from exc

    def _release_lock(self) -> None:
        if self._lock_path.exists():
            self._lock_path.unlink()
            self._runtime_event("runtime_lock", "released", "", "")

    def _persist_session_snapshot(self, session: ExecutionSession, result: TaskExecutionResult) -> str:
        snapshot = SessionStateSnapshot(
            session_id=session.session_id,
            task_id=session.task_id,
            status=result.final_status,
            completed_steps=tuple(result.completed_steps),
            current_step_id=result.failed_step,
            witness_tip_hash=result.witness_tip_hash,
            approval_pending=result.final_status == TaskStatus.AWAITING_APPROVAL,
            finalization_occurred=bool(result.commit_hash),
            runtime_flags=dict(result.runtime_flags),
            timestamp=_utc_now(),
        )
        payload = json.dumps(asdict(snapshot), sort_keys=True, separators=(",", ":")).encode("utf-8")
        digest = self.engine.tool_runner.ledger.store_artifact(payload, suffix=".session.json")
        (self._snapshot_dir / f"{digest}.json").write_bytes(payload)
        return digest

    def _materialize_result_bundle(
        self,
        request: TaskRequest,
        plan: TaskPlan,
        result: TaskExecutionResult,
        snapshot_hash: str,
    ) -> str:
        bundle = {
            "task_request": asdict(request),
            "plan": {
                "task_id": plan.task_id,
                "objective_summary": plan.objective_summary,
                "step_ids": [step.step_id for step in plan.bounded_steps],
            },
            "execution_result": {
                "task_id": result.task_id,
                "final_status": result.final_status,
                "completed_steps": result.completed_steps,
                "failed_step": result.failed_step,
                "commit_hash": result.commit_hash,
                "witness_tip_hash": result.witness_tip_hash,
            },
            "summary": result.summary,
            "runtime_flags": dict(result.runtime_flags),
            "artifact_refs": [asdict(item) for item in result.artifacts],
            "snapshot_hash": snapshot_hash,
            "failure_class": "runtime_policy_violation" if result.final_status in {TaskStatus.FAILED, TaskStatus.HALTED} else "",
        }
        payload = json.dumps(bundle, sort_keys=True, separators=(",", ":")).encode("utf-8")
        digest = self.engine.tool_runner.ledger.store_artifact(payload, suffix=".result_bundle.json")
        return digest

    def _operator_summary(self, request: TaskRequest, result: TaskExecutionResult, bundle_hash: str) -> Dict[str, Any]:
        return {
            "objective": request.user_objective,
            "final_status": result.final_status,
            "completed_step_count": len(result.completed_steps),
            "failed_step": result.failed_step,
            "rollback_occurred": "rollback" in result.summary.lower(),
            "commit_finalization_succeeded": bool(result.commit_hash),
            "git_note_provenance_succeeded": result.final_status == TaskStatus.COMPLETED,
            "runtime_flags": dict(result.runtime_flags),
            "commit_hash": result.commit_hash,
            "witness_tip_hash": result.witness_tip_hash,
            "result_bundle_hash": bundle_hash,
        }

    def _runtime_event(self, phase: str, decision: str, step_id: str, failure_class: str) -> None:
        self.engine.tool_runner.ledger.append(
            {
                "phase": phase,
                "controller_decision": decision,
                "failure_class": failure_class,
                "tool_name": "runtime_service",
                "args_hash": sha256_bytes(f"{phase}:{decision}:{step_id}".encode("utf-8")),
            }
        )


def execute_task_api(service: TaskRuntimeService, objective: str, *, strict: bool = False, approval_token: Optional[str] = None) -> Dict[str, Any]:
    return service.execute_one_task(objective, strict=strict, approval_token=approval_token)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run one bounded Gaia distrustful task")
    parser.add_argument("--objective", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--approval-token", default=None)
    args = parser.parse_args(argv)

    from .distrustful_agent import (
        ApprovalTokenManager,
        DeterministicDiffEmitter,
        InjectionSanitizer,
        RetryManager,
        VerificationRunner,
        WitnessLedger,
        ToolWitnessRunner,
    )

    repo_root = Path(args.repo)
    ledger = WitnessLedger(repo_root)
    tool_runner = ToolWitnessRunner(ledger, model_fingerprint="runtime-cli")
    verification = VerificationRunner(tool_runner, RetryManager(entropy_cap=8))

    def _unsupported_intent_provider(request, step):
        raise RuntimeError("No model intent provider configured for CLI runtime")

    engine = TaskExecutionEngine(
        planner=BoundedPlanner(max_steps=7),
        diff_emitter=DeterministicDiffEmitter(),
        verification_runner=verification,
        tool_runner=tool_runner,
        approval_tokens=ApprovalTokenManager(secret=b"runtime-secret-runtime-secret-32!!"),
        sanitizer=InjectionSanitizer(),
        intent_provider=_unsupported_intent_provider,
    )
    service = TaskRuntimeService(
        runtime_config=RuntimeConfig(repo_root=str(repo_root), strict_mode_default=args.strict),
        model_policy=ModelRuntimePolicy(),
        planner=BoundedPlanner(max_steps=7),
        engine=engine,
    )
    out = service.execute_one_task(args.objective, strict=args.strict, approval_token=args.approval_token)
    result: TaskExecutionResult = out["result"]

    print(f"task_id={result.task_id}")
    print(f"final_status={result.final_status}")
    print(f"commit_hash={result.commit_hash or ''}")
    print(f"witness_tip_hash={result.witness_tip_hash}")
    print(f"failed_step={result.failed_step or ''}")

    if result.final_status in {TaskStatus.FAILED, TaskStatus.HALTED}:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
