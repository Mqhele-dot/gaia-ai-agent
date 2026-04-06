"""Deterministic, distrustful orchestration primitives for local AI workflows.

This module intentionally treats model output as *untrusted proposals* that must
be verified at tool boundaries before the controller can advance.
"""
from __future__ import annotations

import ast
import base64
import difflib
import hashlib
import hmac
import json
import os
import random
import re
import shlex
import socket
import sqlite3
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ISO_UTC = "%Y-%m-%dT%H:%M:%S.%fZ"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime(ISO_UTC)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_json(payload: Dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256_bytes(encoded)


# ---------------------------------------------------------------------------
# Edit intent schema + deterministic selector resolution
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EditIntent:
    """Structured edit proposal emitted by the reasoning model."""

    file_path: str
    language: str
    selector_kind: str
    symbol_name: str
    anchor_text: str
    scope_hint: str
    replacement_snippet: str
    postconditions: List[str] = field(default_factory=list)
    rationale_summary: str = ""
    ast_path: Optional[str] = None

    def validate(self) -> None:
        required = {
            "file_path": self.file_path,
            "language": self.language,
            "selector_kind": self.selector_kind,
            "replacement_snippet": self.replacement_snippet,
        }
        missing = [key for key, value in required.items() if not str(value).strip()]
        if missing:
            raise ValueError(f"EditIntent missing required fields: {', '.join(sorted(missing))}")
        if self.selector_kind not in {"symbol", "anchor", "ast_path"}:
            raise ValueError(f"Unsupported selector_kind={self.selector_kind}")
        if self.selector_kind == "symbol" and not self.symbol_name.strip():
            raise ValueError("symbol selector requires symbol_name")
        if self.selector_kind == "anchor" and not self.anchor_text.strip():
            raise ValueError("anchor selector requires anchor_text")
        if self.selector_kind == "ast_path" and not (self.ast_path or "").strip():
            raise ValueError("ast_path selector requires ast_path")


@dataclass(frozen=True)
class ResolvedEdit:
    file_path: str
    start_line: int
    end_line: int
    replacement_snippet: str


class SelectorAmbiguityError(RuntimeError):
    """Raised when selector resolution is not unique."""


class DeterministicDiffEmitter:
    """Generate unified diffs from structured intent and deterministic resolution."""

    COMMENT_PATTERNS = {
        "python": re.compile(r"^\s*#"),
        "javascript": re.compile(r"^\s*//"),
        "typescript": re.compile(r"^\s*//"),
        "rust": re.compile(r"^\s*//"),
    }

    def resolve(self, root: Path, intent: EditIntent) -> ResolvedEdit:
        intent.validate()
        target = (root / intent.file_path).resolve()
        root_resolved = root.resolve()
        try:
            target.relative_to(root_resolved)
        except ValueError as exc:
            raise ValueError("file_path escapes repository root") from exc

        source = target.read_text(encoding="utf-8")
        lines = source.splitlines()

        if intent.language.lower() == "python" and intent.selector_kind in {"symbol", "ast_path"}:
            return self._resolve_python_symbol(intent, lines)

        if intent.selector_kind == "anchor":
            return self._resolve_anchor(intent, lines)

        if intent.selector_kind == "symbol":
            return self._resolve_symbol_plaintext(intent, lines)

        raise ValueError("Unsupported selector strategy for language")

    def emit_diff(self, root: Path, resolved: ResolvedEdit) -> str:
        target = root / resolved.file_path
        before = target.read_text(encoding="utf-8").splitlines(keepends=True)
        replacement = resolved.replacement_snippet
        if replacement and not replacement.endswith("\n"):
            replacement += "\n"
        replacement_lines = replacement.splitlines(keepends=True)

        start = resolved.start_line - 1
        end = resolved.end_line
        after = before[:start] + replacement_lines + before[end:]

        diff = "".join(
            difflib.unified_diff(
                before,
                after,
                fromfile=f"a/{resolved.file_path}",
                tofile=f"b/{resolved.file_path}",
                n=3,
            )
        )
        return diff

    def _resolve_python_symbol(self, intent: EditIntent, lines: Sequence[str]) -> ResolvedEdit:
        src = "\n".join(lines)
        tree = ast.parse(src)
        matches: List[Tuple[int, int]] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.name == intent.symbol_name:
                    end_lineno = getattr(node, "end_lineno", None)
                    if end_lineno is None:
                        continue
                    matches.append((node.lineno, end_lineno))

        if len(matches) != 1:
            raise SelectorAmbiguityError(f"Expected exactly one symbol match, got {len(matches)}")
        start, end = matches[0]
        return ResolvedEdit(
            file_path=intent.file_path,
            start_line=start,
            end_line=end,
            replacement_snippet=intent.replacement_snippet,
        )

    def _resolve_anchor(self, intent: EditIntent, lines: Sequence[str]) -> ResolvedEdit:
        matches: List[int] = []
        comment_pat = self.COMMENT_PATTERNS.get(intent.language.lower())
        for idx, line in enumerate(lines, start=1):
            if comment_pat and comment_pat.match(line):
                continue
            if intent.anchor_text in line:
                if "dead code" in line.lower() or "disabled" in line.lower():
                    continue
                matches.append(idx)

        if len(matches) != 1:
            raise SelectorAmbiguityError(f"Expected unique anchor match, got {len(matches)}")

        line_no = matches[0]
        return ResolvedEdit(
            file_path=intent.file_path,
            start_line=line_no,
            end_line=line_no,
            replacement_snippet=intent.replacement_snippet,
        )

    def _resolve_symbol_plaintext(self, intent: EditIntent, lines: Sequence[str]) -> ResolvedEdit:
        pattern = re.compile(rf"\b{re.escape(intent.symbol_name)}\b")
        matches = [idx for idx, line in enumerate(lines, start=1) if pattern.search(line)]
        if len(matches) != 1:
            raise SelectorAmbiguityError(f"Expected unique plaintext symbol match, got {len(matches)}")
        line_no = matches[0]
        return ResolvedEdit(
            file_path=intent.file_path,
            start_line=line_no,
            end_line=line_no,
            replacement_snippet=intent.replacement_snippet,
        )


# ---------------------------------------------------------------------------
# Witness layer (JSONL hash-chain + SQLite WAL + content-addressed artifacts)
# ---------------------------------------------------------------------------


WITNESS_SQL_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA journal_size_limit=268435456;
CREATE TABLE IF NOT EXISTS witness_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT UNIQUE NOT NULL,
    ts_utc TEXT NOT NULL,
    phase TEXT NOT NULL,
    prev_hash TEXT,
    record_hash TEXT NOT NULL,
    tool_name TEXT,
    args_hash TEXT,
    exit_code INTEGER,
    stdout_hash TEXT,
    stderr_hash TEXT,
    stdout_bytes INTEGER,
    stderr_bytes INTEGER,
    stdout_truncated INTEGER,
    stderr_truncated INTEGER,
    wall_ms INTEGER,
    host_fingerprint TEXT,
    model_fingerprint TEXT,
    rationale_hash TEXT,
    verification_status TEXT,
    controller_decision TEXT,
    failure_class TEXT
);
CREATE INDEX IF NOT EXISTS idx_witness_phase ON witness_records(phase);
CREATE INDEX IF NOT EXISTS idx_witness_ts ON witness_records(ts_utc);
""".strip()


class WitnessLedger:
    """Append-only witness logger with hash-chaining and SQLite index."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.ledger_dir = root / "gaia" / "data" / "witness"
        self.ledger_dir.mkdir(parents=True, exist_ok=True)
        self.jsonl_path = self.ledger_dir / "witness.jsonl"
        self.sqlite_path = self.ledger_dir / "witness.sqlite"
        self.artifacts_dir = self.ledger_dir / "artifacts"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.sqlite_path)
        self._conn.executescript(WITNESS_SQL_SCHEMA)
        self._conn.commit()
        self._tip_hash = self._load_tip_hash()

    def _load_tip_hash(self) -> str:
        if not self.jsonl_path.exists():
            return "0" * 64
        last_line = ""
        with self.jsonl_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    last_line = line
        if not last_line:
            return "0" * 64
        payload = json.loads(last_line)
        return str(payload.get("record_hash", "0" * 64))

    def store_artifact(self, payload: bytes, suffix: str = ".bin") -> str:
        digest = sha256_bytes(payload)
        target = self.artifacts_dir / f"{digest}{suffix}"
        if not target.exists():
            target.write_bytes(payload)
        return digest

    def append(self, record: Dict[str, Any]) -> Dict[str, Any]:
        event = dict(record)
        event.setdefault("event_id", f"evt-{uuid.uuid4().hex}")
        event.setdefault("ts_utc", _utc_now())
        event["prev_hash"] = self._tip_hash

        canonical = dict(event)
        canonical.pop("record_hash", None)
        event["record_hash"] = sha256_json(canonical)

        with self.jsonl_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True) + "\n")

        self._index_record(event)
        self._tip_hash = event["record_hash"]
        return event

    def _index_record(self, event: Dict[str, Any]) -> None:
        cols = {
            "event_id": event.get("event_id"),
            "ts_utc": event.get("ts_utc"),
            "phase": event.get("phase", "unknown"),
            "prev_hash": event.get("prev_hash"),
            "record_hash": event.get("record_hash"),
            "tool_name": event.get("tool_name"),
            "args_hash": event.get("args_hash"),
            "exit_code": event.get("exit_code"),
            "stdout_hash": event.get("stdout_hash"),
            "stderr_hash": event.get("stderr_hash"),
            "stdout_bytes": event.get("stdout_bytes"),
            "stderr_bytes": event.get("stderr_bytes"),
            "stdout_truncated": int(bool(event.get("stdout_truncated"))),
            "stderr_truncated": int(bool(event.get("stderr_truncated"))),
            "wall_ms": event.get("wall_ms"),
            "host_fingerprint": event.get("host_fingerprint"),
            "model_fingerprint": event.get("model_fingerprint"),
            "rationale_hash": event.get("rationale_hash"),
            "verification_status": event.get("verification_status"),
            "controller_decision": event.get("controller_decision"),
            "failure_class": event.get("failure_class"),
        }
        names = ", ".join(cols.keys())
        placeholders = ", ".join(["?"] * len(cols))
        self._conn.execute(
            f"INSERT INTO witness_records ({names}) VALUES ({placeholders})",
            tuple(cols.values()),
        )
        self._conn.commit()

    @property
    def tip_hash(self) -> str:
        return self._tip_hash

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------------------
# Prompt-injection sanitizer
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SanitizedContent:
    provenance: str
    raw_sha256: str
    sanitized_text: str
    high_risk_flags: List[str]


class InjectionSanitizer:
    """Neutralize indirect prompt injection patterns from untrusted content."""

    RISK_PATTERNS = {
        "override_instructions": re.compile(r"ignore (all|previous|prior) instructions", re.I),
        "fake_tool_call": re.compile(r"<(tool|function)_call>|```(json|xml)", re.I),
        "credential_exfil": re.compile(r"(api[_-]?key|token|secret|password)", re.I),
    }

    TOOL_CALL_SYNTAX = re.compile(r"\b(call_tool|tool_call|function_call)\b", re.I)

    def sanitize(self, text: str, provenance: str) -> SanitizedContent:
        flags: List[str] = []
        for name, pattern in self.RISK_PATTERNS.items():
            if pattern.search(text):
                flags.append(name)

        neutralized = self.TOOL_CALL_SYNTAX.sub("[neutralized_call_token]", text)
        neutralized = neutralized.replace("<", "⟨").replace(">", "⟩")
        neutralized = f"[UNTRUSTED:{provenance}]\n{neutralized}"

        return SanitizedContent(
            provenance=provenance,
            raw_sha256=sha256_bytes(text.encode("utf-8")),
            sanitized_text=neutralized,
            high_risk_flags=flags,
        )


# ---------------------------------------------------------------------------
# Retry manager
# ---------------------------------------------------------------------------


@dataclass
class RetryPolicy:
    max_attempts: int
    backoff_base_s: float = 0.2
    backoff_cap_s: float = 2.0
    jitter_s: float = 0.05


class RetryBudgetExceeded(RuntimeError):
    pass


class RetryManager:
    """Budgeted retry controller with per-class limits and hard entropy cap."""

    def __init__(self, entropy_cap: int = 8) -> None:
        self.entropy_cap = entropy_cap
        self.failures_total = 0
        self.failures: Dict[str, int] = {}

    def register_failure(self, failure_class: str, policy: RetryPolicy) -> None:
        self.failures_total += 1
        self.failures[failure_class] = self.failures.get(failure_class, 0) + 1
        if self.failures_total > self.entropy_cap:
            raise RetryBudgetExceeded("Global entropy budget exceeded")
        if self.failures[failure_class] > policy.max_attempts:
            raise RetryBudgetExceeded(f"Retry budget exceeded for class={failure_class}")

    def compute_sleep_s(self, failure_class: str, policy: RetryPolicy) -> float:
        attempt = self.failures.get(failure_class, 0)
        raw = min(policy.backoff_base_s * (2 ** max(0, attempt - 1)), policy.backoff_cap_s)
        jitter = random.uniform(0, policy.jitter_s)
        return raw + jitter

    @staticmethod
    def is_retryable(failure_class: str) -> bool:
        """Only infra/tool failures get backoff-based retries."""

        return failure_class == "infra_tool_failure"


# ---------------------------------------------------------------------------
# Approval token design (scoped HMAC token)
# ---------------------------------------------------------------------------


class ApprovalTokenManager:
    """Issue and validate short-lived scoped approval tokens."""

    def __init__(self, secret: Optional[bytes] = None) -> None:
        self.secret = secret or os.urandom(32)

    def issue(self, scopes: Sequence[str], ttl_s: int = 600) -> str:
        now = datetime.now(timezone.utc)
        payload = {
            "scopes": sorted(set(scopes)),
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=ttl_s)).timestamp()),
            "jti": uuid.uuid4().hex,
        }
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        sig = hmac.new(self.secret, body, hashlib.sha256).hexdigest().encode("ascii")
        return base64.urlsafe_b64encode(body + b"." + sig).decode("ascii")

    def verify(self, token: str, required_scope: str, now_ts: Optional[int] = None) -> Dict[str, Any]:
        raw = base64.urlsafe_b64decode(token.encode("ascii"))
        body, sig = raw.rsplit(b".", 1)
        expected = hmac.new(self.secret, body, hashlib.sha256).hexdigest().encode("ascii")
        if not hmac.compare_digest(sig, expected):
            raise PermissionError("Invalid approval token signature")
        payload = json.loads(body)
        current = now_ts if now_ts is not None else int(datetime.now(timezone.utc).timestamp())
        if current > int(payload["exp"]):
            raise PermissionError("Approval token expired")
        if required_scope not in set(payload.get("scopes", [])):
            raise PermissionError("Approval token missing required scope")
        return payload


# ---------------------------------------------------------------------------
# Tool witness wrapper + deterministic verification pipeline
# ---------------------------------------------------------------------------


@dataclass
class ToolResult:
    command: List[str]
    exit_code: int
    stdout: bytes
    stderr: bytes
    wall_ms: int
    stdout_truncated: bool = False
    stderr_truncated: bool = False


class ToolWitnessRunner:
    """Execute tools and write authoritative witness records."""

    def __init__(self, ledger: WitnessLedger, model_fingerprint: str) -> None:
        self.ledger = ledger
        self.model_fingerprint = model_fingerprint
        self.host_fingerprint = sha256_bytes(
            f"{socket.gethostname()}|{os.uname().sysname}|{os.uname().release}".encode("utf-8")
        )

    def run(
        self,
        command: Sequence[str],
        cwd: Path,
        max_capture: int = 200_000,
        input_data: Optional[bytes] = None,
    ) -> ToolResult:
        started = time.perf_counter()
        try:
            proc = subprocess.run(
                list(command),
                cwd=str(cwd),
                capture_output=True,
                check=False,
                input=input_data,
            )
            exit_code = proc.returncode
            full_stdout = proc.stdout
            full_stderr = proc.stderr
        except OSError as exc:
            exit_code = 127
            full_stdout = b""
            full_stderr = str(exc).encode("utf-8", errors="replace")
        wall_ms = int((time.perf_counter() - started) * 1000)
        stdout = full_stdout[:max_capture]
        stderr = full_stderr[:max_capture]
        result = ToolResult(
            command=list(command),
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            wall_ms=wall_ms,
            stdout_truncated=len(full_stdout) > max_capture,
            stderr_truncated=len(full_stderr) > max_capture,
        )
        self._record_tool_result(result, full_stdout=full_stdout, full_stderr=full_stderr)
        return result

    def _record_tool_result(self, result: ToolResult, *, full_stdout: bytes, full_stderr: bytes) -> None:
        stdout_hash = self.ledger.store_artifact(full_stdout, suffix=".stdout")
        stderr_hash = self.ledger.store_artifact(full_stderr, suffix=".stderr")
        args_hash = sha256_bytes(" ".join(shlex.quote(part) for part in result.command).encode("utf-8"))
        self.ledger.append(
            {
                "phase": "tool",
                "tool_name": result.command[0] if result.command else "",
                "args_hash": args_hash,
                "exit_code": result.exit_code,
                "stdout_hash": stdout_hash,
                "stderr_hash": stderr_hash,
                "stdout_bytes": len(full_stdout),
                "stderr_bytes": len(full_stderr),
                "stdout_truncated": result.stdout_truncated,
                "stderr_truncated": result.stderr_truncated,
                "wall_ms": result.wall_ms,
                "host_fingerprint": self.host_fingerprint,
                "model_fingerprint": self.model_fingerprint,
            }
        )


@dataclass
class VerificationStep:
    name: str
    command: List[str]
    required: bool = True


@dataclass
class VerificationResult:
    step: str
    passed: bool
    exit_code: int
    failure_class: Optional[str] = None


class VerificationRunner:
    """Deterministic verification sequence with failure classification."""

    def __init__(self, tool_runner: ToolWitnessRunner, retry_manager: RetryManager) -> None:
        self.tool_runner = tool_runner
        self.retry_manager = retry_manager

    def run(self, repo_root: Path, patch_text: str, strict: bool = False) -> List[VerificationResult]:
        patch_bytes = patch_text.encode("utf-8")
        patch_digest = self.tool_runner.ledger.store_artifact(patch_bytes, suffix=".patch")
        self.tool_runner.ledger.append({"phase": "proposal", "rationale_hash": patch_digest})

        steps: List[VerificationStep] = [
            VerificationStep("patch_check", ["git", "apply", "--check", "--whitespace=nowarn", "-"], required=True),
            VerificationStep("apply_patch", ["git", "apply", "--whitespace=nowarn", "-"], required=True),
            VerificationStep("change_detect", ["git", "status", "--porcelain"], required=True),
            VerificationStep("format_lint", ["ruff", "check", "."], required=False),
            VerificationStep("type_check", ["mypy", "."], required=False),
            VerificationStep("tests", ["pytest", "-q"], required=True),
        ]
        if strict:
            steps.append(VerificationStep("security", ["bandit", "-q", "-r", "."], required=False))

        results: List[VerificationResult] = []
        for step in steps:
            run = self._execute_step_with_retries(
                step=step,
                repo_root=repo_root,
                patch_bytes=patch_bytes,
            )
            exit_code = run.exit_code
            stdout = run.stdout.decode("utf-8", errors="replace")

            if step.name == "change_detect" and not stdout.strip():
                exit_code = 1

            if exit_code == 0:
                results.append(VerificationResult(step=step.name, passed=True, exit_code=0))
                continue

            failure_class = self._classify_failure(step.name, exit_code)
            results.append(
                VerificationResult(
                    step=step.name,
                    passed=False,
                    exit_code=exit_code,
                    failure_class=failure_class,
                )
            )
            self.tool_runner.ledger.append(
                {
                    "phase": "verification",
                    "verification_status": "failed",
                    "controller_decision": "halt",
                    "failure_class": failure_class,
                }
            )
            if step.required:
                break

        return results

    def _execute_step_with_retries(self, step: VerificationStep, repo_root: Path, patch_bytes: bytes) -> ToolResult:
        retry_policy = RetryPolicy(max_attempts=2)
        while True:
            run = self.tool_runner.run(
                step.command,
                cwd=repo_root,
                input_data=patch_bytes if step.name in {"patch_check", "apply_patch"} else None,
            )
            failure_class = self._classify_failure(step.name, run.exit_code)
            if run.exit_code == 0:
                return run
            if not self.retry_manager.is_retryable(failure_class):
                return run
            try:
                self.retry_manager.register_failure(failure_class, retry_policy)
            except RetryBudgetExceeded:
                self.tool_runner.ledger.append(
                    {
                        "phase": "verification",
                        "verification_status": "failed",
                        "controller_decision": "halt",
                        "failure_class": "infra_tool_failure_budget_exhausted",
                    }
                )
                return run
            time.sleep(self.retry_manager.compute_sleep_s(failure_class, retry_policy))

    @staticmethod
    def _classify_failure(step: str, exit_code: int) -> str:
        if step in {"patch_check", "apply_patch", "change_detect"}:
            return "deterministic_failure"
        if exit_code in {2, 127}:
            return "infra_tool_failure"
        return "flaky_or_environmental"


# ---------------------------------------------------------------------------
# Deterministic FSM controller
# ---------------------------------------------------------------------------


class ControllerState:
    IDLE = "IDLE"
    PLAN = "PLAN"
    RESOLVE_EDIT = "RESOLVE_EDIT"
    VERIFY = "VERIFY"
    APPROVAL = "APPROVAL"
    COMMIT = "COMMIT"
    HALT = "HALT"


class DeterministicController:
    """Code-driven FSM for bounded workflows (no model for state transitions)."""

    TRANSITIONS = {
        ControllerState.IDLE: {"task_received": ControllerState.PLAN},
        ControllerState.PLAN: {"intent_valid": ControllerState.RESOLVE_EDIT, "intent_invalid": ControllerState.HALT},
        ControllerState.RESOLVE_EDIT: {"diff_ready": ControllerState.VERIFY, "selector_ambiguous": ControllerState.HALT},
        ControllerState.VERIFY: {"verification_passed": ControllerState.APPROVAL, "verification_failed": ControllerState.HALT},
        ControllerState.APPROVAL: {"approval_granted": ControllerState.COMMIT, "approval_denied": ControllerState.HALT},
        ControllerState.COMMIT: {"committed": ControllerState.IDLE, "commit_failed": ControllerState.HALT},
        ControllerState.HALT: {},
    }

    def __init__(self) -> None:
        self.state = ControllerState.IDLE
        self.history: List[Tuple[str, str, str]] = []

    def advance(self, event: str, reason: str) -> str:
        allowed = self.TRANSITIONS.get(self.state, {})
        if event not in allowed:
            raise RuntimeError(f"Illegal transition: state={self.state} event={event}")
        prev = self.state
        self.state = allowed[event]
        self.history.append((prev, event, self.state))
        return self.state


EDIT_INTENT_JSON_SCHEMA: Dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "EditIntent",
    "type": "object",
    "required": [
        "file_path",
        "language",
        "selector_kind",
        "symbol_name",
        "anchor_text",
        "scope_hint",
        "ast_path",
        "replacement_snippet",
        "postconditions",
        "rationale_summary",
    ],
    "properties": {
        "file_path": {"type": "string", "minLength": 1},
        "language": {"type": "string", "minLength": 1},
        "selector_kind": {"type": "string", "enum": ["symbol", "anchor", "ast_path"]},
        "symbol_name": {"type": "string"},
        "anchor_text": {"type": "string"},
        "scope_hint": {"type": "string"},
        "ast_path": {"type": ["string", "null"]},
        "replacement_snippet": {"type": "string", "minLength": 1},
        "postconditions": {"type": "array", "items": {"type": "string"}},
        "rationale_summary": {"type": "string"},
    },
    "additionalProperties": False,
}


WITNESS_JSON_SCHEMA: Dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "WitnessRecord",
    "type": "object",
    "required": ["event_id", "ts_utc", "phase", "prev_hash", "record_hash"],
    "properties": {
        "event_id": {"type": "string"},
        "ts_utc": {"type": "string"},
        "phase": {"type": "string"},
        "prev_hash": {"type": "string", "minLength": 64, "maxLength": 64},
        "record_hash": {"type": "string", "minLength": 64, "maxLength": 64},
        "tool_name": {"type": "string"},
        "args_hash": {"type": "string"},
        "exit_code": {"type": "integer"},
        "stdout_hash": {"type": "string"},
        "stderr_hash": {"type": "string"},
        "stdout_bytes": {"type": "integer", "minimum": 0},
        "stderr_bytes": {"type": "integer", "minimum": 0},
        "stdout_truncated": {"type": "boolean"},
        "stderr_truncated": {"type": "boolean"},
        "wall_ms": {"type": "integer", "minimum": 0},
        "host_fingerprint": {"type": "string"},
        "model_fingerprint": {"type": "string"},
        "rationale_hash": {"type": "string"},
        "verification_status": {"type": "string"},
        "controller_decision": {"type": "string"},
        "failure_class": {"type": "string"},
    },
    "additionalProperties": True,
}


def memory_profile_notes() -> Dict[str, Any]:
    """Guidance values for keeping operation inside a ~16 GB RAM budget."""

    return {
        "budget_target_gb": 16,
        "controller_overhead_mb": 120,
        "sqlite_wal_cap_mb": 256,
        "artifact_streaming": "chunk outputs >200KB into CAS to avoid RAM spikes",
        "main_model_policy": "single Ollama reasoning model active; no parallel medium models",
        "retrieval_policy": "top-k bounded to <=8 chunks, <=32KB per chunk",
        "kv_cache_hint": "keep_alive only during active reasoning window",
    }
