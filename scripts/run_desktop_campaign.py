#!/usr/bin/env python3
"""Desktop-first wrapper for Gaia campaign runs."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gaia.gaia_core.campaign_supervisor import main as campaign_main
from gaia.gaia_core.desktop_profile import ensure_profile_paths, load_desktop_profile, validate_desktop_preflight


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run Gaia desktop campaign with profile preflight")
    parser.add_argument("--objective", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--profile-path", default="gaia/config/desktop_local_profile.json")
    parser.add_argument("--hours", type=float, default=5.0)
    args = parser.parse_args(argv)

    profile = load_desktop_profile(args.profile_path)
    ensure_profile_paths(profile)
    preflight = validate_desktop_preflight(profile)
    print(f"profile={profile.profile_name}")
    print(f"profile_path={args.profile_path}")
    print(json.dumps(preflight, sort_keys=True, indent=2))
    if not preflight["disk_headroom_ok"] or not preflight["all_tools_ok"] or not preflight["campaign_policy_sane"]:
        print("desktop_campaign_preflight_failed")
        return 2

    code = campaign_main(
        [
            "--objective",
            args.objective,
            "--repo",
            args.repo,
            "--hours",
            str(args.hours),
            "--profile-path",
            args.profile_path,
        ]
    )
    campaign_dir = Path(args.repo) / "gaia" / "data" / "campaigns"
    print(f"campaign_path={campaign_dir}")
    print(f"artifact_path={profile.artifact_storage_path}")
    print(f"witness_path={profile.witness_path}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
