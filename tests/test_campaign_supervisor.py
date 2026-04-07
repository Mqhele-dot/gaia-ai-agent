from __future__ import annotations

import json
from pathlib import Path

from gaia.gaia_core.campaign_supervisor import (
    CampaignPolicy,
    CampaignRequest,
    CampaignSupervisor,
    execute_campaign_api,
)
from gaia.gaia_core.distrustful_agent import TaskExecutionResult, TaskStatus


class FakeLedger:
    def __init__(self) -> None:
        self.events = []
        self.artifacts = []
        self.tip_hash = "tip-campaign"


class FakeToolRunner:
    def __init__(self) -> None:
        self.ledger = FakeLedger()


class FakeEngine:
    def __init__(self) -> None:
        self.tool_runner = FakeToolRunner()


class FakeRuntimeConfig:
    def __init__(self, repo_root: str) -> None:
        self.repo_root = repo_root
        self.min_artifact_free_space_mb = 0


class FakeService:
    def __init__(self, repo_root: Path, statuses: list[str]) -> None:
        self.repo_root = repo_root
        self.runtime_config = FakeRuntimeConfig(str(repo_root))
        self.engine = FakeEngine()
        self.statuses = list(statuses)

    def execute_one_task(self, objective: str, strict: bool = False, approval_token=None):
        status = self.statuses.pop(0) if self.statuses else TaskStatus.COMPLETED
        commit = "abc123" if status in {TaskStatus.COMPLETED, TaskStatus.PARTIAL} and "run smoke" not in objective and "run full" not in objective else None
        result = TaskExecutionResult(
            task_id="task-campaign",
            final_status=status,
            completed_steps=["s1"],
            failed_step=None if status != TaskStatus.HALTED else "s1",
            commit_hash=commit,
            witness_tip_hash=self.engine.tool_runner.ledger.tip_hash,
            summary=f"status={status}",
            artifacts=[],
            runtime_flags={"selector_refinement_attempts": 1 if status == TaskStatus.HALTED else 0},
        )
        return {
            "session_id": "session-1",
            "task_id": "task-campaign",
            "result": result,
            "result_bundle_hash": "bundle-h",
            "session_snapshot_hash": "snap-h",
            "operator_summary": {"final_status": status},
        }


def _request(tmp_path: Path) -> CampaignRequest:
    return CampaignRequest(
        campaign_id="campaign-test",
        objective="improve module reliability",
        repo_root=str(tmp_path),
        total_time_budget_s=600,
        target_duration_hours=0.1,
        strict_mode=False,
        approval_required=False,
        allow_mutation=True,
        created_at="2026-01-01T00:00:00Z",
    )


def test_campaign_runs_multiple_bounded_tasks(tmp_path: Path) -> None:
    service = FakeService(tmp_path, [TaskStatus.COMPLETED, TaskStatus.COMPLETED, TaskStatus.COMPLETED, TaskStatus.COMPLETED])
    sup = CampaignSupervisor(service, CampaignPolicy(max_tasks_per_campaign=5, checkpoint_interval_tasks=1))
    result = sup.run_campaign(_request(tmp_path))
    assert result.completed_task_count >= 3


def test_campaign_creates_checkpoints(tmp_path: Path) -> None:
    service = FakeService(tmp_path, [TaskStatus.COMPLETED, TaskStatus.COMPLETED, TaskStatus.COMPLETED])
    sup = CampaignSupervisor(service, CampaignPolicy(checkpoint_interval_tasks=1))
    result = sup.run_campaign(_request(tmp_path))
    assert result.campaign_artifacts["last_checkpoint_hash"]
    checkpoints = list((tmp_path / "gaia" / "data" / "campaigns" / "campaign-test").glob("checkpoint-*.json"))
    assert checkpoints


def test_campaign_halts_on_consecutive_failures(tmp_path: Path) -> None:
    service = FakeService(tmp_path, [TaskStatus.HALTED, TaskStatus.HALTED, TaskStatus.HALTED, TaskStatus.HALTED])
    sup = CampaignSupervisor(service, CampaignPolicy(max_consecutive_failures=1))
    result = sup.run_campaign(_request(tmp_path))
    assert result.final_status == "HALTED"


def test_campaign_halts_when_backlog_exhausted_without_progress(tmp_path: Path) -> None:
    service = FakeService(tmp_path, [TaskStatus.HALTED, TaskStatus.HALTED, TaskStatus.HALTED])
    sup = CampaignSupervisor(
        service,
        CampaignPolicy(max_consecutive_failures=99, max_replans=0, smoke_test_interval_tasks=999, full_test_interval_tasks=999),
    )
    result = sup.run_campaign(_request(tmp_path))
    assert result.final_status == "HALTED"


def test_campaign_pause_on_approval_boundary(tmp_path: Path) -> None:
    service = FakeService(tmp_path, [TaskStatus.AWAITING_APPROVAL])
    sup = CampaignSupervisor(service, CampaignPolicy())
    result = sup.run_campaign(_request(tmp_path))
    assert result.final_status == "PAUSED"


def test_campaign_execute_api_returns_structured_payload(tmp_path: Path) -> None:
    service = FakeService(tmp_path, [TaskStatus.COMPLETED, TaskStatus.COMPLETED, TaskStatus.COMPLETED])
    sup = CampaignSupervisor(service, CampaignPolicy())
    out = execute_campaign_api(sup, "stabilize", repo_root=str(tmp_path), hours=0.1, approval_required=False)
    assert out["result"].campaign_id.startswith("campaign-")


def test_campaign_result_bundle_written(tmp_path: Path) -> None:
    service = FakeService(tmp_path, [TaskStatus.COMPLETED, TaskStatus.COMPLETED, TaskStatus.COMPLETED])
    sup = CampaignSupervisor(service, CampaignPolicy())
    result = sup.run_campaign(_request(tmp_path))
    bundle = Path(result.campaign_artifacts["campaign_result_bundle"])
    assert bundle.exists()
    payload = json.loads(bundle.read_text(encoding="utf-8"))
    assert "telemetry" in payload
