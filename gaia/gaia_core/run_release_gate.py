"""Release-gate CLI for bounded-use readiness checks."""
from __future__ import annotations

import argparse

from .eval_harness import EvalHarness, EvalRunConfig, ReleaseReadinessProfile, basic_scenarios


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run release gate for distrustful local agent")
    parser.add_argument("--profile", default="local_16gb")
    parser.add_argument("--iterations", type=int, default=5)
    args = parser.parse_args(argv)

    def _unsupported_factory(_scenario):
        raise RuntimeError("Release gate requires integrator-provided TaskRuntimeService factory")

    harness = EvalHarness(_unsupported_factory)
    profile = ReleaseReadinessProfile(target_machine_label=args.profile)
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
    print(f"release_summary_hash={release.summary_hash}")

    return 0 if not release.blocking_issues else 2


if __name__ == "__main__":
    raise SystemExit(main())
