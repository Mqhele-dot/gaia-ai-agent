from __future__ import annotations

from types import SimpleNamespace

from gaia.gaia_core import run_release_gate


def test_release_gate_nonzero_on_blocking_failures(monkeypatch) -> None:
    class FakeHarness:
        def __init__(self, *_args, **_kwargs):
            pass

        def evaluate_release_candidate(self, *_args, **_kwargs):
            report = SimpleNamespace(report_hash="rhash")
            tuning = SimpleNamespace()
            digest = SimpleNamespace(digest_hash="dhash")
            release = SimpleNamespace(overall_readiness_verdict="needs hardening", blocking_issues=["x"], summary_hash="shash")
            return report, tuning, digest, release

    monkeypatch.setattr(run_release_gate, "EvalHarness", FakeHarness)
    code = run_release_gate.main(["--profile", "local_16gb", "--iterations", "1"])
    assert code == 2
