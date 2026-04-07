"""CLI module entrypoint for distrustful evaluation harness."""
from __future__ import annotations

import argparse

from .eval_harness import main as eval_main
from .run_release_gate import main as release_gate_main


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--suite", default="basic")
    parser.add_argument("--profile", default="local_16gb")
    parser.add_argument("--iterations", type=int, default=1)
    parser.add_argument("--output-dir", default="gaia/data/eval_reports/release_candidate_local_16gb")
    args, _ = parser.parse_known_args(argv)
    if args.suite == "release_candidate":
        return release_gate_main(["--profile", args.profile, "--iterations", str(args.iterations), "--output-dir", args.output_dir])
    return eval_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
