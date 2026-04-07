"""Desktop build profile helpers for local bounded Gaia operation."""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List


@dataclass(frozen=True)
class DesktopBuildProfile:
    profile_name: str
    repo_root: str
    artifact_storage_path: str
    witness_path: str
    min_artifact_free_space_mb: int
    strict_mode_default: bool
    approval_required_default: bool
    retention_policy: str
    model_runtime_policy: Dict[str, int | bool]
    campaign_defaults: Dict[str, int | bool | float]
    required_tools: List[str] = field(default_factory=lambda: ["git", "python", "pytest"])


def load_desktop_profile(path: str | Path) -> DesktopBuildProfile:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return DesktopBuildProfile(**payload)


def ensure_profile_paths(profile: DesktopBuildProfile) -> None:
    Path(profile.artifact_storage_path).mkdir(parents=True, exist_ok=True)
    Path(profile.witness_path).mkdir(parents=True, exist_ok=True)


def validate_desktop_preflight(profile: DesktopBuildProfile) -> Dict[str, object]:
    repo = Path(profile.repo_root)
    tools = {name: bool(shutil.which(name)) for name in profile.required_tools}
    free_mb = shutil.disk_usage(repo).free / (1024 * 1024)
    lock_path = repo / ".gaia_task.lock"
    campaign = dict(profile.campaign_defaults)
    policy_ok = bool(campaign.get("checkpoint_interval_tasks", 0)) and bool(campaign.get("smoke_test_interval_tasks", 0))
    return {
        "repo_exists": repo.exists(),
        "repo_writable": os.access(repo, os.W_OK),
        "git_dir_exists": (repo / ".git").exists(),
        "free_space_mb": round(free_mb, 2),
        "min_required_space_mb": profile.min_artifact_free_space_mb,
        "disk_headroom_ok": free_mb >= float(profile.min_artifact_free_space_mb),
        "tools": tools,
        "all_tools_ok": all(tools.values()),
        "artifact_path_ready": Path(profile.artifact_storage_path).exists(),
        "witness_path_ready": Path(profile.witness_path).exists(),
        "lock_parent_ready": lock_path.parent.exists(),
        "model_policy_single_active": bool(profile.model_runtime_policy.get("force_single_active_reasoning_model", True)),
        "campaign_policy_sane": policy_ok and int(campaign.get("max_tasks_per_campaign", 0)) > 0,
    }


def profile_to_json(profile: DesktopBuildProfile) -> str:
    return json.dumps(asdict(profile), sort_keys=True, indent=2)
