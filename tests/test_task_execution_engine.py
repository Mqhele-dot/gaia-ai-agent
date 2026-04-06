from __future__ import annotations

from pathlib import Path

from gaia.gaia_core.distrustful_agent import (
    ApprovalTokenManager,
    BoundedPlanner,
    DeterministicDiffEmitter,
    EditIntent,
    InjectionSanitizer,
    TaskExecutionEngine,
    TaskPlan,
    TaskPolicyLimits,
    TaskRequest,
    TaskStatus,
    TaskStep,
    VerificationResult,
)


class FakeLedger:
    def __init__(self) -> None:
        self.events = []
        self.artifacts = []
        self.tip_hash = "tip-hash"

    def append(self, record):
        self.events.append(record)
        return {"event_id": f"evt-{len(self.events)}", **record}

    def store_artifact(self, payload: bytes, suffix: str = ".bin") -> str:
        self.artifacts.append((payload, suffix))
        return f"artifact-{len(self.artifacts)}"


class FakeToolRunner:
    def __init__(self, *, notes_fail: bool = False) -> None:
        self.ledger = FakeLedger()
        self.model_fingerprint = "model:test"
        self.notes_fail = notes_fail

    def run(self, command, cwd, max_capture=200000, input_data=None):
        cmd = list(command)
        if cmd[:3] == ["git", "status", "--porcelain"]:
            return _tool_result(cmd, 0, b"", b"")
        if cmd[:2] == ["git", "stash"]:
            return _tool_result(cmd, 0, b"No local changes to save\n", b"")
        if cmd[:2] == ["git", "add"]:
            return _tool_result(cmd, 0, b"", b"")
        if cmd[:2] == ["git", "commit"]:
            return _tool_result(cmd, 0, b"[main 123] commit\n", b"")
        if cmd[:3] == ["git", "rev-parse", "HEAD"]:
            return _tool_result(cmd, 0, b"abc123\n", b"")
        if cmd[:3] == ["git", "notes", "add"]:
            return _tool_result(cmd, 1 if self.notes_fail else 0, b"", b"notes error" if self.notes_fail else b"")
        if cmd[:3] == ["git", "cat-file", "-e"]:
            return _tool_result(cmd, 0, b"", b"")
        if cmd[:3] == ["git", "notes", "show"]:
            return _tool_result(cmd, 0, b'{"witness_tip_hash":"tip-hash"}', b"")
        return _tool_result(cmd, 0, b"ok\n", b"")


class FakeVerificationRunner:
    def __init__(self, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.calls = []

    def run(self, repo_root: Path, patch_text: str, strict: bool = False):
        self.calls.append((str(repo_root), patch_text, strict))
        if self.should_fail:
            return [VerificationResult(step="tests", passed=False, exit_code=1, failure_class="flaky_or_environmental")]
        return [
            VerificationResult(step="patch_check", passed=True, exit_code=0),
            VerificationResult(step="apply_patch", passed=True, exit_code=0),
            VerificationResult(step="change_detect", passed=True, exit_code=0),
            VerificationResult(step="tests", passed=True, exit_code=0),
        ]


def _tool_result(command, exit_code, stdout, stderr):
    class _R:
        pass

    r = _R()
    r.command = command
    r.exit_code = exit_code
    r.stdout = stdout
    r.stderr = stderr
    r.wall_ms = 1
    return r


def _request(tmp_path: Path, approval_required: bool = False) -> TaskRequest:
    return TaskRequest(
        task_id="task-1",
        user_objective="update function",
        repo_root=str(tmp_path),
        strict_mode=False,
        approval_required=approval_required,
        created_at="2026-04-06T00:00:00.000000Z",
    )


def _intent_provider(request: TaskRequest, step: TaskStep):
    return EditIntent(
        file_path="sample.py",
        language="python",
        selector_kind="symbol",
        symbol_name="target",
        anchor_text="",
        scope_hint="module",
        ast_path=None,
        replacement_snippet="def target():\n    return 2\n",
        postconditions=["function returns 2"],
        rationale_summary="update target",
    )


def _plan(task_id: str, approval: bool = True) -> TaskPlan:
    return TaskPlan(
        task_id=task_id,
        objective_summary="obj",
        assumptions=["single edit"],
        bounded_steps=[
            TaskStep("s1", "inspect_repo", "inspect", "meta", [], "low", False),
            TaskStep("s2", "plan_edit", "plan", "intent", ["s1"], "medium", False),
            TaskStep("s3", "apply_edit", "apply", "patch", ["s2"], "high", False),
            TaskStep("s4", "finalize_change", "finalize", "commit", ["s3"], "high", approval),
            TaskStep("s5", "summarize_result", "summarize", "summary", ["s4"], "low", False),
        ],
        success_criteria=["done"],
        stop_conditions=["fail"],
    )


def test_planner_rejects_unsupported_step_type() -> None:
    planner = BoundedPlanner(max_steps=3)
    bad_plan = TaskPlan(
        task_id="t",
        objective_summary="x",
        assumptions=[],
        bounded_steps=[TaskStep("s", "unknown", "x", "x", [], "low", False)],
        success_criteria=["ok"],
        stop_conditions=["halt"],
    )
    try:
        planner.validate_plan(bad_plan)
        assert False, "expected unsupported step rejection"
    except ValueError:
        pass


def test_planning_produces_bounded_steps(tmp_path: Path) -> None:
    planner = BoundedPlanner(max_steps=5)
    plan = planner.build_plan(_request(tmp_path))
    assert len(plan.bounded_steps) <= 5


def test_execution_halts_on_ambiguity_after_refinement_budget(tmp_path: Path) -> None:
    (tmp_path / "sample.py").write_text("def target():\n    return 1\n\ndef target():\n    return 2\n", encoding="utf-8")
    verification = FakeVerificationRunner()
    engine = TaskExecutionEngine(
        planner=BoundedPlanner(max_steps=5),
        diff_emitter=DeterministicDiffEmitter(),
        verification_runner=verification,
        tool_runner=FakeToolRunner(),
        approval_tokens=ApprovalTokenManager(secret=b"k" * 32),
        sanitizer=InjectionSanitizer(),
        policy=TaskPolicyLimits(max_refinement_attempts=1),
        intent_provider=lambda *_: {
            "file_path": "sample.py",
            "language": "python",
            "selector_kind": "symbol",
            "symbol_name": "target",
            "anchor_text": "",
            "scope_hint": "module",
            "ast_path": None,
            "replacement_snippet": "def target():\n    return 9\n",
            "postconditions": [],
            "rationale_summary": "x",
        },
        refinement_provider=lambda *_: None,
    )
    result = engine.execute(_request(tmp_path), proposed_plan=_plan("task-1", approval=False))
    assert result.final_status == TaskStatus.HALTED


def test_successful_single_edit_task_completes(tmp_path: Path) -> None:
    (tmp_path / "sample.py").write_text("def target():\n    return 1\n", encoding="utf-8")
    verification = FakeVerificationRunner()
    token_mgr = ApprovalTokenManager(secret=b"k" * 32)
    token = token_mgr.issue(["git.commit"], ttl_s=60)
    tool_runner = FakeToolRunner()
    engine = TaskExecutionEngine(
        planner=BoundedPlanner(max_steps=5),
        diff_emitter=DeterministicDiffEmitter(),
        verification_runner=verification,
        tool_runner=tool_runner,
        approval_tokens=token_mgr,
        sanitizer=InjectionSanitizer(),
        intent_provider=_intent_provider,
    )
    result = engine.execute(_request(tmp_path, approval_required=True), proposed_plan=_plan("task-1", approval=True), approval_token=token)
    assert result.final_status == TaskStatus.COMPLETED
    assert result.commit_hash == "abc123"


def test_approval_required_task_pauses(tmp_path: Path) -> None:
    (tmp_path / "sample.py").write_text("def target():\n    return 1\n", encoding="utf-8")
    engine = TaskExecutionEngine(
        planner=BoundedPlanner(max_steps=5),
        diff_emitter=DeterministicDiffEmitter(),
        verification_runner=FakeVerificationRunner(),
        tool_runner=FakeToolRunner(),
        approval_tokens=ApprovalTokenManager(secret=b"k" * 32),
        sanitizer=InjectionSanitizer(),
        intent_provider=_intent_provider,
    )
    result = engine.execute(_request(tmp_path, approval_required=True), proposed_plan=_plan("task-1", approval=True))
    assert result.final_status == TaskStatus.AWAITING_APPROVAL


def test_denied_approval_halts(tmp_path: Path) -> None:
    (tmp_path / "sample.py").write_text("def target():\n    return 1\n", encoding="utf-8")
    engine = TaskExecutionEngine(
        planner=BoundedPlanner(max_steps=5),
        diff_emitter=DeterministicDiffEmitter(),
        verification_runner=FakeVerificationRunner(),
        tool_runner=FakeToolRunner(),
        approval_tokens=ApprovalTokenManager(secret=b"k" * 32),
        sanitizer=InjectionSanitizer(),
        intent_provider=_intent_provider,
    )
    result = engine.execute(_request(tmp_path, approval_required=True), proposed_plan=_plan("task-1", approval=True), approval_token="bad-token")
    assert result.final_status == TaskStatus.HALTED


def test_summarize_uses_deterministic_status_text(tmp_path: Path) -> None:
    (tmp_path / "sample.py").write_text("def target():\n    return 1\n", encoding="utf-8")
    token_mgr = ApprovalTokenManager(secret=b"k" * 32)
    token = token_mgr.issue(["git.commit"], ttl_s=60)
    engine = TaskExecutionEngine(
        planner=BoundedPlanner(max_steps=5),
        diff_emitter=DeterministicDiffEmitter(),
        verification_runner=FakeVerificationRunner(),
        tool_runner=FakeToolRunner(),
        approval_tokens=token_mgr,
        sanitizer=InjectionSanitizer(),
        intent_provider=_intent_provider,
    )
    result = engine.execute(_request(tmp_path, approval_required=True), proposed_plan=_plan("task-1", approval=True), approval_token=token)
    assert "status=" in result.summary
    assert "completed_steps=" in result.summary


def test_engine_uses_verification_runner_for_edits(tmp_path: Path) -> None:
    (tmp_path / "sample.py").write_text("def target():\n    return 1\n", encoding="utf-8")
    verification = FakeVerificationRunner()
    token_mgr = ApprovalTokenManager(secret=b"k" * 32)
    token = token_mgr.issue(["git.commit"], ttl_s=60)
    engine = TaskExecutionEngine(
        planner=BoundedPlanner(max_steps=5),
        diff_emitter=DeterministicDiffEmitter(),
        verification_runner=verification,
        tool_runner=FakeToolRunner(),
        approval_tokens=token_mgr,
        sanitizer=InjectionSanitizer(),
        intent_provider=_intent_provider,
    )
    engine.execute(_request(tmp_path, approval_required=True), proposed_plan=_plan("task-1", approval=True), approval_token=token)
    assert len(verification.calls) >= 1


def test_commit_hash_and_witness_tip_propagate(tmp_path: Path) -> None:
    (tmp_path / "sample.py").write_text("def target():\n    return 1\n", encoding="utf-8")
    token_mgr = ApprovalTokenManager(secret=b"k" * 32)
    token = token_mgr.issue(["git.commit"], ttl_s=60)
    tool_runner = FakeToolRunner()
    engine = TaskExecutionEngine(
        planner=BoundedPlanner(max_steps=5),
        diff_emitter=DeterministicDiffEmitter(),
        verification_runner=FakeVerificationRunner(),
        tool_runner=tool_runner,
        approval_tokens=token_mgr,
        sanitizer=InjectionSanitizer(),
        intent_provider=_intent_provider,
    )
    result = engine.execute(_request(tmp_path, approval_required=True), proposed_plan=_plan("task-1", approval=True), approval_token=token)
    assert result.commit_hash == "abc123"
    assert result.witness_tip_hash == tool_runner.ledger.tip_hash


def test_partial_finalization_failure_when_notes_fail(tmp_path: Path) -> None:
    (tmp_path / "sample.py").write_text("def target():\n    return 1\n", encoding="utf-8")
    token_mgr = ApprovalTokenManager(secret=b"k" * 32)
    token = token_mgr.issue(["git.commit"], ttl_s=60)
    engine = TaskExecutionEngine(
        planner=BoundedPlanner(max_steps=5),
        diff_emitter=DeterministicDiffEmitter(),
        verification_runner=FakeVerificationRunner(),
        tool_runner=FakeToolRunner(notes_fail=True),
        approval_tokens=token_mgr,
        sanitizer=InjectionSanitizer(),
        intent_provider=_intent_provider,
    )
    result = engine.execute(_request(tmp_path, approval_required=True), proposed_plan=_plan("task-1", approval=True), approval_token=token)
    assert result.final_status == TaskStatus.PARTIAL
