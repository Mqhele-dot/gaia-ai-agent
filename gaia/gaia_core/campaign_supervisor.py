"""Campaign supervisor for chaining many bounded distrustful tasks safely."""
from __future__ import annotations

import argparse
import json
import shutil
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .distrustful_agent import TaskStatus, _utc_now, sha256_bytes
from .runtime_service import TaskRuntimeService, execute_task_api


@dataclass(frozen=True)
class CampaignRequest:
    campaign_id: str
    objective: str
    repo_root: str
    total_time_budget_s: int
    target_duration_hours: float
    strict_mode: bool
    approval_required: bool
    allow_mutation: bool
    created_at: str


@dataclass(frozen=True)
class CampaignStep:
    step_id: str
    objective: str
    task_kind: str
    mutation_expected: bool
    milestone: str = ""


@dataclass(frozen=True)
class CampaignPlan:
    campaign_id: str
    objective_summary: str
    task_backlog: List[CampaignStep]
    milestone_plan: List[str]
    test_schedule: Dict[str, int]
    replanning_interval_tasks: int
    stop_conditions: List[str]


@dataclass
class CampaignState:
    campaign_id: str
    current_phase: str
    completed_tasks: List[str]
    failed_tasks: List[str]
    successful_edits: int
    current_backlog: List[str]
    elapsed_time_s: float
    remaining_time_s: float
    last_checkpoint_hash: str
    last_result_bundle_hash: str
    campaign_flags: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CampaignCheckpoint:
    campaign_id: str
    checkpoint_index: int
    state: Dict[str, Any]
    backlog_snapshot: List[str]
    completed_task_refs: List[str]
    last_witness_tip_hash: str
    runtime_counters: Dict[str, int]
    test_schedule_state: Dict[str, int]
    checkpoint_hash: str
    created_at: str


@dataclass(frozen=True)
class CampaignPolicy:
    max_campaign_runtime_s: int = 18_000
    max_tasks_per_campaign: int = 200
    max_consecutive_failures: int = 5
    max_consecutive_noop_tasks: int = 8
    max_selector_ambiguity_events: int = 12
    max_partial_finalizations: int = 10
    max_disk_pressure_events: int = 2
    max_replans: int = 20
    full_test_interval_tasks: int = 10
    smoke_test_interval_tasks: int = 2
    checkpoint_interval_tasks: int = 2


@dataclass
class CampaignTelemetry:
    elapsed_time_s: float = 0.0
    tasks_completed: int = 0
    tasks_remaining: int = 0
    successful_edits: int = 0
    failed_tasks: int = 0
    selector_ambiguity_count: int = 0
    refinement_count: int = 0
    rollback_count: int = 0
    partial_finalization_count: int = 0
    checkpoint_count: int = 0
    smoke_test_count: int = 0
    full_test_count: int = 0
    artifact_growth: int = 0
    witness_growth: int = 0
    memory_profile_samples: List[float] = field(default_factory=list)
    consecutive_failures: int = 0
    consecutive_noop_tasks: int = 0
    replans_used: int = 0
    disk_pressure_events: int = 0


@dataclass(frozen=True)
class CampaignResult:
    campaign_id: str
    final_status: str
    total_runtime_s: float
    completed_task_count: int
    failed_task_count: int
    successful_edit_count: int
    final_commit_hashes: List[str]
    witness_tip_hash: str
    summary: str
    campaign_artifacts: Dict[str, str]


class CampaignSupervisor:
    def __init__(self, service: TaskRuntimeService, policy: Optional[CampaignPolicy] = None) -> None:
        self.service = service
        self.policy = policy or CampaignPolicy()
        self._campaign_root = Path(self.service.repo_root) / "gaia" / "data" / "campaigns"
        self._campaign_root.mkdir(parents=True, exist_ok=True)

    def create_initial_plan(self, request: CampaignRequest) -> CampaignPlan:
        base = request.objective.strip() or "campaign objective"
        backlog = [
            CampaignStep("c1", f"inspect repository context for: {base}", "analysis", False, "bootstrap"),
            CampaignStep("c2", f"implement bounded improvement for: {base}", "edit", bool(request.allow_mutation), "implementation"),
            CampaignStep("c3", "summarize bounded progress and next actions", "summary", False, "summary"),
        ]
        return CampaignPlan(
            campaign_id=request.campaign_id,
            objective_summary=base[:120],
            task_backlog=backlog,
            milestone_plan=["bootstrap", "implementation", "summary"],
            test_schedule={"smoke_interval": self.policy.smoke_test_interval_tasks, "full_interval": self.policy.full_test_interval_tasks},
            replanning_interval_tasks=3,
            stop_conditions=["time_budget_reached", "fatigue_budget_exceeded", "disk_pressure_exceeded"],
        )

    def run_campaign(
        self,
        request: CampaignRequest,
        *,
        plan: Optional[CampaignPlan] = None,
        approval_token: Optional[str] = None,
    ) -> CampaignResult:
        plan = plan or self.create_initial_plan(request)
        state = CampaignState(
            campaign_id=request.campaign_id,
            current_phase="running",
            completed_tasks=[],
            failed_tasks=[],
            successful_edits=0,
            current_backlog=[step.step_id for step in plan.task_backlog],
            elapsed_time_s=0.0,
            remaining_time_s=float(request.total_time_budget_s),
            last_checkpoint_hash="",
            last_result_bundle_hash="",
            campaign_flags={"paused_for_approval": False, "halt_reason": ""},
        )
        telemetry = CampaignTelemetry(tasks_remaining=len(plan.task_backlog))
        campaign_dir = self._campaign_root / request.campaign_id
        campaign_dir.mkdir(parents=True, exist_ok=True)
        start = time.perf_counter()
        commits: List[str] = []
        witness_tip = ""
        backlog = list(plan.task_backlog)
        completed_refs: List[str] = []

        while backlog and len(state.completed_tasks) < self.policy.max_tasks_per_campaign:
            state.elapsed_time_s = time.perf_counter() - start
            state.remaining_time_s = max(0.0, request.total_time_budget_s - state.elapsed_time_s)
            telemetry.elapsed_time_s = state.elapsed_time_s
            telemetry.tasks_remaining = len(backlog)
            if self._should_halt(request, telemetry, state):
                state.current_phase = "halted"
                break

            step = backlog.pop(0)
            out = execute_task_api(self.service, step.objective, strict=request.strict_mode, approval_token=approval_token)
            result = out["result"]
            state.last_result_bundle_hash = str(out.get("result_bundle_hash", ""))
            witness_tip = result.witness_tip_hash or witness_tip
            if result.commit_hash:
                commits.append(result.commit_hash)

            flags = dict(getattr(result, "runtime_flags", {}))
            telemetry.refinement_count += int(flags.get("selector_refinement_attempts", 0))
            if flags.get("selector_refinement_attempts", 0) > 0 and result.final_status == TaskStatus.HALTED:
                telemetry.selector_ambiguity_count += 1
            if result.final_status == TaskStatus.PARTIAL:
                telemetry.partial_finalization_count += 1
            if "rollback" in result.summary.lower():
                telemetry.rollback_count += 1
            if flags.get("dirty_repo_policy_triggered") and result.final_status == TaskStatus.HALTED:
                telemetry.consecutive_failures += 1
            if not result.commit_hash:
                telemetry.consecutive_noop_tasks += 1
            else:
                telemetry.consecutive_noop_tasks = 0

            if result.final_status in {TaskStatus.COMPLETED, TaskStatus.PARTIAL, TaskStatus.AWAITING_APPROVAL}:
                state.completed_tasks.append(step.step_id)
                telemetry.tasks_completed += 1
                telemetry.consecutive_failures = 0
                if step.mutation_expected and result.commit_hash:
                    state.successful_edits += 1
                    telemetry.successful_edits += 1
            else:
                state.failed_tasks.append(step.step_id)
                telemetry.failed_tasks += 1
                telemetry.consecutive_failures += 1

            completed_refs.append(state.last_result_bundle_hash)
            if result.final_status == TaskStatus.AWAITING_APPROVAL:
                state.current_phase = "paused_for_approval"
                state.campaign_flags["paused_for_approval"] = True
                state.last_checkpoint_hash = self._checkpoint(campaign_dir, request, plan, state, telemetry, completed_refs, witness_tip)
                return self._finalize_result(state, telemetry, commits, witness_tip, campaign_dir, "PAUSED")

            if telemetry.tasks_completed % max(1, plan.test_schedule.get("smoke_interval", self.policy.smoke_test_interval_tasks)) == 0:
                self._run_scheduled_test("smoke", request.strict_mode, telemetry)
            if telemetry.tasks_completed % max(1, plan.test_schedule.get("full_interval", self.policy.full_test_interval_tasks)) == 0:
                self._run_scheduled_test("full", True, telemetry)

            if telemetry.tasks_completed and telemetry.tasks_completed % max(1, self.policy.checkpoint_interval_tasks) == 0:
                state.last_checkpoint_hash = self._checkpoint(campaign_dir, request, plan, state, telemetry, completed_refs, witness_tip)

            if telemetry.tasks_completed and telemetry.tasks_completed % max(1, plan.replanning_interval_tasks) == 0 and telemetry.replans_used < self.policy.max_replans:
                state.last_checkpoint_hash = self._checkpoint(campaign_dir, request, plan, state, telemetry, completed_refs, witness_tip)
                backlog = self._replan_backlog(backlog, plan, telemetry)
                telemetry.replans_used += 1

            self._sample_growth(telemetry)

        if state.current_phase != "halted":
            state.current_phase = "completed"
        state.last_checkpoint_hash = self._checkpoint(campaign_dir, request, plan, state, telemetry, completed_refs, witness_tip)
        return self._finalize_result(state, telemetry, commits, witness_tip, campaign_dir, "COMPLETED" if state.current_phase == "completed" else "HALTED")

    def _run_scheduled_test(self, test_type: str, strict: bool, telemetry: CampaignTelemetry) -> None:
        objective = "run smoke verification for campaign checkpoint" if test_type == "smoke" else "run full verification for campaign milestone"
        execute_task_api(self.service, objective, strict=strict)
        if test_type == "smoke":
            telemetry.smoke_test_count += 1
        else:
            telemetry.full_test_count += 1

    def _sample_growth(self, telemetry: CampaignTelemetry) -> None:
        ledger = self.service.engine.tool_runner.ledger
        telemetry.artifact_growth = len(getattr(ledger, "artifacts", []))
        telemetry.witness_growth = len(getattr(ledger, "events", []))

    def _replan_backlog(self, backlog: List[CampaignStep], plan: CampaignPlan, telemetry: CampaignTelemetry) -> List[CampaignStep]:
        if not backlog:
            return backlog
        if telemetry.consecutive_failures >= 2:
            backlog.insert(0, CampaignStep(step_id=f"r-{uuid.uuid4().hex[:6]}", objective="stabilize failing path and reduce risk before edits", task_kind="analysis", mutation_expected=False))
        return backlog

    def _checkpoint(
        self,
        campaign_dir: Path,
        request: CampaignRequest,
        plan: CampaignPlan,
        state: CampaignState,
        telemetry: CampaignTelemetry,
        completed_refs: List[str],
        witness_tip: str,
    ) -> str:
        payload = {
            "campaign_request": asdict(request),
            "campaign_plan": {"campaign_id": plan.campaign_id, "objective_summary": plan.objective_summary, "backlog": list(state.current_backlog)},
            "state": asdict(state),
            "completed_task_refs": list(completed_refs),
            "last_witness_tip_hash": witness_tip,
            "runtime_counters": asdict(telemetry),
            "test_schedule_state": {"smoke_count": telemetry.smoke_test_count, "full_count": telemetry.full_test_count},
        }
        digest = sha256_bytes(json.dumps(payload, sort_keys=True).encode("utf-8"))
        checkpoint = CampaignCheckpoint(
            campaign_id=request.campaign_id,
            checkpoint_index=telemetry.checkpoint_count + 1,
            state=payload["state"],
            backlog_snapshot=list(state.current_backlog),
            completed_task_refs=list(completed_refs),
            last_witness_tip_hash=witness_tip,
            runtime_counters={k: int(v) if isinstance(v, bool) or isinstance(v, int) else 0 for k, v in asdict(telemetry).items() if isinstance(v, (int, bool))},
            test_schedule_state=payload["test_schedule_state"],
            checkpoint_hash=digest,
            created_at=_utc_now(),
        )
        (campaign_dir / f"checkpoint-{checkpoint.checkpoint_index:04d}.json").write_text(
            json.dumps(asdict(checkpoint), sort_keys=True, indent=2),
            encoding="utf-8",
        )
        telemetry.checkpoint_count += 1
        return digest

    def _should_halt(self, request: CampaignRequest, telemetry: CampaignTelemetry, state: CampaignState) -> bool:
        if telemetry.elapsed_time_s >= min(float(request.total_time_budget_s), float(self.policy.max_campaign_runtime_s)):
            state.campaign_flags["halt_reason"] = "time_budget_reached"
            return True
        if telemetry.consecutive_failures > self.policy.max_consecutive_failures:
            state.campaign_flags["halt_reason"] = "max_consecutive_failures_exceeded"
            return True
        if telemetry.consecutive_noop_tasks > self.policy.max_consecutive_noop_tasks:
            state.campaign_flags["halt_reason"] = "max_consecutive_noop_tasks_exceeded"
            return True
        if telemetry.selector_ambiguity_count > self.policy.max_selector_ambiguity_events:
            state.campaign_flags["halt_reason"] = "max_selector_ambiguity_events_exceeded"
            return True
        if telemetry.partial_finalization_count > self.policy.max_partial_finalizations:
            state.campaign_flags["halt_reason"] = "max_partial_finalizations_exceeded"
            return True
        free_mb = shutil.disk_usage(self.service.repo_root).free / (1024 * 1024)
        if free_mb < float(self.service.runtime_config.min_artifact_free_space_mb):
            telemetry.disk_pressure_events += 1
            if telemetry.disk_pressure_events > self.policy.max_disk_pressure_events:
                state.campaign_flags["halt_reason"] = "disk_pressure_exceeded"
                return True
        return False

    def _finalize_result(
        self,
        state: CampaignState,
        telemetry: CampaignTelemetry,
        commits: List[str],
        witness_tip: str,
        campaign_dir: Path,
        final_status: str,
    ) -> CampaignResult:
        summary = f"status={final_status}; tasks={telemetry.tasks_completed}; edits={telemetry.successful_edits}; failures={telemetry.failed_tasks}"
        payload = {
            "state": asdict(state),
            "telemetry": asdict(telemetry),
            "commits": commits,
            "summary": summary,
            "witness_tip_hash": witness_tip,
        }
        result_hash = sha256_bytes(json.dumps(payload, sort_keys=True).encode("utf-8"))
        bundle = campaign_dir / "campaign_result_bundle.json"
        summary_path = campaign_dir / "campaign_summary.json"
        bundle.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")
        summary_path.write_text(
            json.dumps(
                {
                    "campaign_id": state.campaign_id,
                    "final_status": final_status,
                    "summary": summary,
                    "result_hash": result_hash,
                    "last_checkpoint_hash": state.last_checkpoint_hash,
                },
                sort_keys=True,
                indent=2,
            ),
            encoding="utf-8",
        )
        return CampaignResult(
            campaign_id=state.campaign_id,
            final_status=final_status,
            total_runtime_s=telemetry.elapsed_time_s,
            completed_task_count=telemetry.tasks_completed,
            failed_task_count=telemetry.failed_tasks,
            successful_edit_count=telemetry.successful_edits,
            final_commit_hashes=commits,
            witness_tip_hash=witness_tip,
            summary=summary,
            campaign_artifacts={
                "campaign_result_bundle": str(bundle),
                "campaign_summary": str(summary_path),
                "last_checkpoint_hash": state.last_checkpoint_hash,
                "result_hash": result_hash,
            },
        )


def execute_campaign_api(
    supervisor: CampaignSupervisor,
    objective: str,
    *,
    repo_root: str,
    hours: float = 5.0,
    strict: bool = False,
    approval_required: bool = True,
    allow_mutation: bool = True,
    approval_token: Optional[str] = None,
) -> Dict[str, Any]:
    request = CampaignRequest(
        campaign_id=f"campaign-{uuid.uuid4().hex[:10]}",
        objective=objective,
        repo_root=repo_root,
        total_time_budget_s=int(hours * 3600),
        target_duration_hours=hours,
        strict_mode=strict,
        approval_required=approval_required,
        allow_mutation=allow_mutation,
        created_at=_utc_now(),
    )
    result = supervisor.run_campaign(request, approval_token=approval_token)
    return {"request": request, "result": result}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run long campaign supervisor over bounded distrustful tasks")
    parser.add_argument("--objective", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--hours", type=float, default=5.0)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--approval-required", action="store_true")
    args = parser.parse_args(argv)

    from .runtime_service import ModelRuntimePolicy, RuntimeConfig
    from .runtime_service import TaskRuntimeService as _Service
    from .runtime_service import main as _runtime_main  # noqa: F401
    from .distrustful_agent import (
        ApprovalTokenManager,
        BoundedPlanner,
        DeterministicDiffEmitter,
        InjectionSanitizer,
        RetryManager,
        ToolWitnessRunner,
        VerificationRunner,
        WitnessLedger,
        TaskExecutionEngine,
    )

    repo_root = Path(args.repo)
    ledger = WitnessLedger(repo_root)
    tool_runner = ToolWitnessRunner(ledger, model_fingerprint="campaign-supervisor")
    verification = VerificationRunner(tool_runner, RetryManager(entropy_cap=8))

    def _unsupported_intent_provider(_request, _step):
        raise RuntimeError("No intent provider configured for campaign CLI")

    engine = TaskExecutionEngine(
        planner=BoundedPlanner(max_steps=7),
        diff_emitter=DeterministicDiffEmitter(),
        verification_runner=verification,
        tool_runner=tool_runner,
        approval_tokens=ApprovalTokenManager(secret=b"campaign-supervisor-secret-32-byte!!"),
        sanitizer=InjectionSanitizer(),
        intent_provider=_unsupported_intent_provider,
    )
    service = _Service(
        runtime_config=RuntimeConfig(repo_root=str(repo_root), strict_mode_default=args.strict),
        model_policy=ModelRuntimePolicy(),
        planner=BoundedPlanner(max_steps=7),
        engine=engine,
    )
    supervisor = CampaignSupervisor(service)
    out = execute_campaign_api(
        supervisor,
        args.objective,
        repo_root=str(repo_root),
        hours=args.hours,
        strict=args.strict,
        approval_required=args.approval_required,
    )
    result: CampaignResult = out["result"]
    print(f"campaign_id={result.campaign_id}")
    print(f"final_status={result.final_status}")
    print(f"elapsed_s={result.total_runtime_s:.2f}")
    print(f"completed_tasks={result.completed_task_count}")
    print(f"successful_edits={result.successful_edit_count}")
    print(f"witness_tip_hash={result.witness_tip_hash}")
    print(f"checkpoint_hash={result.campaign_artifacts.get('last_checkpoint_hash', '')}")
    print(f"summary={result.summary}")
    return 0 if result.final_status in {"COMPLETED", "PAUSED"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
