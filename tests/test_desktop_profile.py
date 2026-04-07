from __future__ import annotations

from pathlib import Path

from gaia.gaia_core.desktop_profile import ensure_profile_paths, load_desktop_profile, validate_desktop_preflight


def test_desktop_profile_loads_and_validates() -> None:
    profile = load_desktop_profile("gaia/config/desktop_local_profile.json")
    ensure_profile_paths(profile)
    out = validate_desktop_preflight(profile)
    assert "disk_headroom_ok" in out
    assert "all_tools_ok" in out


def test_desktop_profile_paths_created(tmp_path: Path) -> None:
    p = tmp_path / "profile.json"
    p.write_text(
        """{
  "profile_name": "tmp",
  "repo_root": "%s",
  "artifact_storage_path": "%s",
  "witness_path": "%s",
  "min_artifact_free_space_mb": 1,
  "strict_mode_default": false,
  "approval_required_default": true,
  "retention_policy": "keep_release_campaigns_only",
  "model_runtime_policy": {"max_loaded_models": 1, "force_single_active_reasoning_model": true, "unload_model_during_verification": true},
  "campaign_defaults": {"target_duration_hours": 1, "max_tasks_per_campaign": 5, "checkpoint_interval_tasks": 1, "smoke_test_interval_tasks": 1, "full_test_interval_tasks": 2, "max_consecutive_failures": 2, "max_selector_ambiguity_events": 2, "max_partial_finalizations": 2, "max_disk_pressure_events": 1},
  "required_tools": ["python"]
}"""
        % (tmp_path, tmp_path / "artifacts", tmp_path / "witness"),
        encoding="utf-8",
    )
    profile = load_desktop_profile(p)
    ensure_profile_paths(profile)
    assert (tmp_path / "artifacts").exists()
    assert (tmp_path / "witness").exists()
