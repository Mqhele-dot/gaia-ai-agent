"""Deterministic burn-in evaluation harness for distrustful runtime service."""
from __future__ import annotations

import argparse
import json
import statistics
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    import resource
except Exception:  # pragma: no cover
    resource = None

from .distrustful_agent import TaskStatus, _utc_now, sha256_bytes
from .runtime_service import execute_task_api


@dataclass(frozen=True)
class FailureInjectionConfig:
    missing_tool: bool = False
    git_notes_failure: bool = False
    pytest_failure: bool = False
    ambiguous_selector: bool = False
    invalid_approval_token: bool = False
    lock_held: bool = False
    pre_existing_dirty_repo: bool = False
    policy_limit_exceeded: bool = False
    malformed_note_payload: bool = False


@dataclass(frozen=True)
class EvalScenario:
    scenario_id: str
    title: str
    objective: str
    repo_fixture: str
    expected_outcome: str
    requires_approval: bool
    strict_mode: bool
    tags: List[str]
    failure_injection: FailureInjectionConfig = FailureInjectionConfig()


@dataclass(frozen=True)
class EvalRunConfig:
    iterations: int = 1
    timeout_s: int = 60
    enable_failure_injection: bool = True
    enable_resource_profiling: bool = True
    enable_adversarial_inputs: bool = False
    model_policy_override: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class ResourceProfileSample:
    runtime_s: float
    peak_memory_mb: float
    artifact_count: int
    witness_record_count: int
    model_lifecycle_transition_count: int


@dataclass
class EvalRunResult:
    scenario_id: str
    run_id: str
    final_status: str
    success: bool
    commit_hash: Optional[str]
    witness_tip_hash: str
    failed_step: Optional[str]
    runtime_s: float
    peak_memory_mb: float
    artifact_hashes: List[str]
    injected_failures: List[str]
    notes: List[str]
    invariant_violations: List[str] = field(default_factory=list)


@dataclass
class EvalAggregateReport:
    suite_id: str
    generated_at: str
    run_count: int
    pass_rate: float
    mean_runtime_s: float
    median_runtime_s: float
    max_runtime_s: float
    avg_peak_memory_mb: float
    failure_histogram: Dict[str, int]
    final_status_distribution: Dict[str, int]
    rollback_occurrence_count: int
    approval_pause_count: int
    partial_finalization_count: int
    unstable_scenarios: List[str]
    recommendation: str
    readiness_score: Dict[str, Any]
    report_hash: str


class EvalHarness:
    def __init__(self, service_factory: Callable[[EvalScenario], Any], *, pass_threshold: float = 0.95) -> None:
        self.service_factory = service_factory
        self.pass_threshold = pass_threshold

    def run_scenario(self, scenario: EvalScenario, config: EvalRunConfig) -> List[EvalRunResult]:
        results: List[EvalRunResult] = []
        for idx in range(config.iterations):
            run_id = f"{scenario.scenario_id}-run-{idx + 1}"
            start = time.perf_counter()
            service = self.service_factory(scenario)
            out = execute_task_api(service, scenario.objective, strict=scenario.strict_mode)
            elapsed = time.perf_counter() - start
            result = out["result"]
            profile = self._profile(service, elapsed)
            artifact_hashes = [out.get("result_bundle_hash", ""), out.get("session_snapshot_hash", "")]
            injected = self._injected_failures(scenario, config)
            invariants = self._check_invariants(out)
            success = result.final_status == scenario.expected_outcome and not invariants
            results.append(
                EvalRunResult(
                    scenario_id=scenario.scenario_id,
                    run_id=run_id,
                    final_status=result.final_status,
                    success=success,
                    commit_hash=result.commit_hash,
                    witness_tip_hash=result.witness_tip_hash,
                    failed_step=result.failed_step,
                    runtime_s=profile.runtime_s,
                    peak_memory_mb=profile.peak_memory_mb,
                    artifact_hashes=[x for x in artifact_hashes if x],
                    injected_failures=injected,
                    notes=[f"expected={scenario.expected_outcome}", f"actual={result.final_status}"],
                    invariant_violations=invariants,
                )
            )
        return results

    def run_suite(self, scenarios: List[EvalScenario], config: EvalRunConfig, suite_id: str = "basic") -> EvalAggregateReport:
        all_results: List[EvalRunResult] = []
        for scenario in scenarios:
            all_results.extend(self.run_scenario(scenario, config))
        return self.aggregate(all_results, suite_id=suite_id)

    def aggregate(self, results: List[EvalRunResult], *, suite_id: str) -> EvalAggregateReport:
        runtimes = [item.runtime_s for item in results] or [0.0]
        peaks = [item.peak_memory_mb for item in results] or [0.0]
        pass_rate = sum(1 for item in results if item.success) / max(1, len(results))
        failure_histogram: Dict[str, int] = {}
        status_dist: Dict[str, int] = {}
        unstable = set()
        rollback_count = 0
        approval_pause_count = 0
        partial_count = 0
        for item in results:
            status_dist[item.final_status] = status_dist.get(item.final_status, 0) + 1
            if item.invariant_violations:
                for violation in item.invariant_violations:
                    failure_histogram[violation] = failure_histogram.get(violation, 0) + 1
                unstable.add(item.scenario_id)
            if item.final_status != TaskStatus.COMPLETED:
                unstable.add(item.scenario_id)
            if "rollback" in " ".join(item.notes).lower():
                rollback_count += 1
            if item.final_status == TaskStatus.AWAITING_APPROVAL:
                approval_pause_count += 1
            if item.final_status == TaskStatus.PARTIAL:
                partial_count += 1

        readiness = self._readiness_score(results, pass_rate)
        recommendation = "pass_for_bounded_use" if pass_rate >= self.pass_threshold and not failure_histogram else "investigate"
        if pass_rate < 0.7:
            recommendation = "not_ready"
        payload = {
            "suite_id": suite_id,
            "run_count": len(results),
            "pass_rate": pass_rate,
            "status_dist": status_dist,
            "failure_histogram": failure_histogram,
            "readiness": readiness,
        }
        report_hash = sha256_bytes(json.dumps(payload, sort_keys=True).encode("utf-8"))
        return EvalAggregateReport(
            suite_id=suite_id,
            generated_at=_utc_now(),
            run_count=len(results),
            pass_rate=pass_rate,
            mean_runtime_s=statistics.mean(runtimes),
            median_runtime_s=statistics.median(runtimes),
            max_runtime_s=max(runtimes),
            avg_peak_memory_mb=statistics.mean(peaks),
            failure_histogram=failure_histogram,
            final_status_distribution=status_dist,
            rollback_occurrence_count=rollback_count,
            approval_pause_count=approval_pause_count,
            partial_finalization_count=partial_count,
            unstable_scenarios=sorted(unstable),
            recommendation=recommendation,
            readiness_score=readiness,
            report_hash=report_hash,
        )

    @staticmethod
    def _profile(service: Any, runtime_s: float) -> ResourceProfileSample:
        peak_memory_mb = 0.0
        if resource is not None:
            usage = resource.getrusage(resource.RUSAGE_SELF)
            peak_memory_mb = float(usage.ru_maxrss) / 1024.0
        ledger = service.engine.tool_runner.ledger
        artifact_count = len(getattr(ledger, "artifacts", []))
        witness_count = len(getattr(ledger, "events", []))
        transitions = sum(1 for event in getattr(ledger, "events", []) if event.get("phase") == "runtime_model_state")
        return ResourceProfileSample(runtime_s, peak_memory_mb, artifact_count, witness_count, transitions)

    @staticmethod
    def _injected_failures(scenario: EvalScenario, config: EvalRunConfig) -> List[str]:
        if not config.enable_failure_injection:
            return []
        fields = asdict(scenario.failure_injection)
        return sorted([name for name, enabled in fields.items() if enabled])

    @staticmethod
    def _check_invariants(output: Dict[str, Any]) -> List[str]:
        result = output["result"]
        violations: List[str] = []
        if result.final_status == TaskStatus.COMPLETED and not result.witness_tip_hash:
            violations.append("missing_witness_tip")
        if result.final_status == TaskStatus.COMPLETED and not output.get("result_bundle_hash"):
            violations.append("missing_result_bundle")
        if result.final_status == TaskStatus.COMPLETED and not output.get("operator_summary"):
            violations.append("missing_operator_summary")
        summary = output.get("operator_summary", {})
        if summary.get("final_status") and summary.get("final_status") != result.final_status:
            violations.append("summary_mismatch")
        return violations

    @staticmethod
    def _readiness_score(results: List[EvalRunResult], pass_rate: float) -> Dict[str, Any]:
        invariants_ok = 1.0 if all(not r.invariant_violations for r in results) else 0.0
        approval = 1.0 if all(r.final_status != TaskStatus.HALTED for r in results) else 0.5
        partial_penalty = max(0.0, 1.0 - (sum(1 for r in results if r.final_status == TaskStatus.PARTIAL) / max(1, len(results))))
        dimensions = {
            "correctness": round(pass_rate, 4),
            "rollback_reliability": round(invariants_ok, 4),
            "provenance_reliability": round(partial_penalty, 4),
            "runtime_stability": round(pass_rate, 4),
            "approval_handling": round(approval, 4),
            "policy_enforcement": round(invariants_ok, 4),
            "memory_discipline": 1.0,
        }
        overall = sum(dimensions.values()) / len(dimensions)
        verdict = "prototype"
        if overall >= 0.75:
            verdict = "build-ready"
        if overall >= 0.9:
            verdict = "bounded-use ready"
        if invariants_ok < 1.0:
            verdict = "needs hardening"
        return {"dimensions": dimensions, "overall": round(overall, 4), "verdict": verdict}


def basic_scenarios() -> List[EvalScenario]:
    return [
        EvalScenario("simple_python_edit", "Simple Python edit", "Update one function", "fixture_simple", TaskStatus.COMPLETED, True, False, ["simple"]),
        EvalScenario("ambiguous_selector", "Ambiguous selector", "Trigger ambiguity", "fixture_ambiguous", TaskStatus.HALTED, False, False, ["adversarial"], FailureInjectionConfig(ambiguous_selector=True)),
        EvalScenario("rollback_pytest_fail", "Rollback on pytest fail", "Run failing verification", "fixture_fail", TaskStatus.FAILED, False, True, ["rollback"], FailureInjectionConfig(pytest_failure=True)),
        EvalScenario("approval_required", "Approval pause", "Needs approval", "fixture_approval", TaskStatus.AWAITING_APPROVAL, True, False, ["approval"]),
        EvalScenario("partial_notes_fail", "Partial finalization", "Notes fail", "fixture_partial", TaskStatus.PARTIAL, True, False, ["provenance"], FailureInjectionConfig(git_notes_failure=True)),
        EvalScenario("preflight_fail", "Preflight failure", "Missing git repo", "fixture_preflight", TaskStatus.HALTED, False, False, ["preflight"], FailureInjectionConfig(policy_limit_exceeded=True)),
    ]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run distrustful runtime evaluation harness")
    parser.add_argument("--suite", default="basic")
    parser.add_argument("--scenario", default=None)
    parser.add_argument("--iterations", type=int, default=1)
    args = parser.parse_args(argv)

    scenarios = basic_scenarios()
    if args.scenario:
        scenarios = [item for item in scenarios if item.scenario_id == args.scenario]
        if not scenarios:
            print("scenario_not_found")
            return 2

    # Runtime service factory intentionally left explicit for integrators.
    def _unsupported_factory(scenario: EvalScenario):
        raise RuntimeError("Evaluation CLI requires integrator-provided TaskRuntimeService factory")

    harness = EvalHarness(_unsupported_factory)
    try:
        report = harness.run_suite(scenarios, EvalRunConfig(iterations=args.iterations), suite_id=args.suite)
    except RuntimeError as exc:
        print(f"evaluation_error={exc}")
        return 2

    print(f"suite={report.suite_id}")
    print(f"pass_rate={report.pass_rate:.4f}")
    print(f"avg_runtime_s={report.mean_runtime_s:.4f}")
    print(f"avg_peak_memory_mb={report.avg_peak_memory_mb:.2f}")
    print(f"unstable_scenarios={','.join(report.unstable_scenarios)}")
    print(f"aggregate_report_hash={report.report_hash}")

    if report.pass_rate < harness.pass_threshold or report.failure_histogram:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
