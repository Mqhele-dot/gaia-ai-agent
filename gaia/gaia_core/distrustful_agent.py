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


@dataclass
class DirtyStateTracker:
    patch_check_passed: bool = False
    apply_patch_succeeded: bool = False
    working_tree_changed: bool = False
    rollback_snapshot_created: bool = False
    rollback_succeeded: bool = False
    rollback_failed: bool = False
    pre_existing_dirty_state: bool = False
    agent_introduced_dirty_state: bool = False
    rollback_checkpoint_id: Optional[str] = None
    rollback_strategy: Optional[str] = None


def detect_dirty_state(tool_runner: ToolWitnessRunner, repo_root: Path) -> Dict[str, Any]:
    status = tool_runner.run(["git", "status", "--porcelain"], cwd=repo_root)
    output = status.stdout.decode("utf-8", errors="replace")
    return {"is_dirty": bool(output.strip()), "status": output}


def create_rollback_checkpoint(
    tool_runner: ToolWitnessRunner,
    repo_root: Path,
    *,
    session_id: str,
) -> Dict[str, Any]:
    dirty = detect_dirty_state(tool_runner, repo_root)
    checkpoint_label = f"gaia-agent-{session_id}"
    stash = tool_runner.run(
        ["git", "stash", "push", "--include-untracked", "-m", checkpoint_label],
        cwd=repo_root,
    )
    stash_stdout = stash.stdout.decode("utf-8", errors="replace")
    stash_created = stash.exit_code == 0 and "No local changes to save" not in stash_stdout
    strategy = "stash" if stash_created else "artifact_snapshot"

    artifact_payload = json.dumps(
        {
            "session_id": session_id,
            "pre_status": dirty["status"],
            "stash_output": stash_stdout,
            "stash_exit_code": stash.exit_code,
        },
        sort_keys=True,
    ).encode("utf-8")
    checkpoint_id = tool_runner.ledger.store_artifact(artifact_payload, suffix=".snapshot.json")
    event = tool_runner.ledger.append(
        {
            "phase": "snapshot",
            "controller_decision": "checkpoint_created",
            "failure_class": "pre_existing_dirty_state_blocked" if dirty["is_dirty"] else "",
            "rationale_hash": checkpoint_id,
            "verification_status": "pre_existing_dirty" if dirty["is_dirty"] else "clean",
        }
    )
    return {
        "checkpoint_id": checkpoint_id,
        "checkpoint_label": checkpoint_label,
        "strategy": strategy,
        "pre_existing_dirty_state": dirty["is_dirty"],
        "initial_status": dirty["status"],
        "stash_created": stash_created,
        "witness_event_id": event.get("event_id"),
    }


def restore_rollback_checkpoint(
    tool_runner: ToolWitnessRunner,
    repo_root: Path,
    checkpoint: Dict[str, Any],
) -> Dict[str, Any]:
    if not checkpoint:
        return {"outcome": "rollback_not_needed"}
    if checkpoint.get("strategy") == "stash" and checkpoint.get("stash_created"):
        list_result = tool_runner.run(["git", "stash", "list"], cwd=repo_root)
        listing = list_result.stdout.decode("utf-8", errors="replace").splitlines()
        stash_ref = ""
        label = str(checkpoint.get("checkpoint_label", ""))
        for line in listing:
            if label and label in line:
                stash_ref = line.split(":", 1)[0]
                break
        if stash_ref:
            tool_runner.run(["git", "restore", "--staged", "--worktree", "."], cwd=repo_root)
            pop = tool_runner.run(["git", "stash", "pop", stash_ref], cwd=repo_root)
            if pop.exit_code != 0:
                return {"outcome": "rollback_failed", "failure_class": "rollback_failed_dirty_repo"}
    else:
        restore = tool_runner.run(["git", "restore", "--staged", "--worktree", "."], cwd=repo_root)
        if restore.exit_code != 0:
            return {"outcome": "rollback_failed", "failure_class": "rollback_failed_dirty_repo"}

    after = detect_dirty_state(tool_runner, repo_root)
    initial_status = str(checkpoint.get("initial_status", "")).strip()
    restored_status = str(after.get("status", "")).strip()
    expected_dirty = bool(checkpoint.get("pre_existing_dirty_state"))
    rollback_ok = (restored_status == initial_status) or (not expected_dirty and not restored_status)
    if rollback_ok:
        return {"outcome": "rollback_succeeded", "dirty": after.get("is_dirty", False)}
    return {"outcome": "rollback_failed", "failure_class": "rollback_failed_dirty_repo"}


def build_provenance_note(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def finalize_commit(
    tool_runner: ToolWitnessRunner,
    repo_root: Path,
    *,
    task_id: str,
) -> Dict[str, Any]:
    add = tool_runner.run(["git", "add", "-A"], cwd=repo_root)
    if add.exit_code != 0:
        return {"ok": False, "failure_class": "commit_finalization_failed"}
    message = f"chore(agent): apply witnessed patch for {task_id}"
    commit = tool_runner.run(["git", "commit", "-m", message], cwd=repo_root)
    if commit.exit_code != 0:
        return {"ok": False, "failure_class": "commit_finalization_failed"}
    rev = tool_runner.run(["git", "rev-parse", "HEAD"], cwd=repo_root)
    if rev.exit_code != 0:
        return {"ok": False, "failure_class": "commit_finalization_failed"}
    commit_hash = rev.stdout.decode("utf-8", errors="replace").strip()
    return {"ok": True, "commit_hash": commit_hash, "message": message}


def attach_git_note(
    tool_runner: ToolWitnessRunner,
    repo_root: Path,
    *,
    commit_hash: str,
    note_payload: str,
) -> Dict[str, Any]:
    result = tool_runner.run(["git", "notes", "add", "-f", "-m", note_payload, commit_hash], cwd=repo_root)
    if result.exit_code != 0:
        return {"ok": False, "failure_class": "git_notes_failed"}
    tool_runner.ledger.append({"phase": "finalization", "controller_decision": "git_note_attached"})
    return {"ok": True}


def verify_finalization(
    tool_runner: ToolWitnessRunner,
    repo_root: Path,
    *,
    commit_hash: str,
    expected_witness_tip_hash: str,
) -> Dict[str, Any]:
    commit_exists = tool_runner.run(["git", "cat-file", "-e", f"{commit_hash}^{{commit}}"], cwd=repo_root)
    note_show = tool_runner.run(["git", "notes", "show", commit_hash], cwd=repo_root)
    if commit_exists.exit_code != 0:
        return {"ok": False, "failure_class": "commit_finalization_failed"}
    if note_show.exit_code != 0:
        return {"ok": False, "failure_class": "partial_finalization_failure"}
    try:
        note_payload = json.loads(note_show.stdout.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return {"ok": False, "failure_class": "partial_finalization_failure"}
    if note_payload.get("witness_tip_hash") != expected_witness_tip_hash:
        return {"ok": False, "failure_class": "partial_finalization_failure"}
    tool_runner.ledger.append({"phase": "finalization", "verification_status": "finalization_success"})
    return {"ok": True}


class VerificationRunner:
    """Deterministic verification sequence with failure classification."""

    def __init__(self, tool_runner: ToolWitnessRunner, retry_manager: RetryManager) -> None:
        self.tool_runner = tool_runner
        self.retry_manager = retry_manager

    def run(self, repo_root: Path, patch_text: str, strict: bool = False) -> List[VerificationResult]:
        session_id = uuid.uuid4().hex[:12]
        dirty_state = DirtyStateTracker()
        patch_bytes = patch_text.encode("utf-8")
        patch_digest = self.tool_runner.ledger.store_artifact(patch_bytes, suffix=".patch")
        self.tool_runner.ledger.append({"phase": "proposal", "rationale_hash": patch_digest})
        checkpoint = create_rollback_checkpoint(self.tool_runner, repo_root, session_id=session_id)
        dirty_state.rollback_snapshot_created = True
        dirty_state.rollback_checkpoint_id = checkpoint.get("checkpoint_id")
        dirty_state.rollback_strategy = checkpoint.get("strategy")
        dirty_state.pre_existing_dirty_state = bool(checkpoint.get("pre_existing_dirty_state"))

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
                if step.name == "patch_check":
                    dirty_state.patch_check_passed = True
                if step.name == "apply_patch":
                    dirty_state.apply_patch_succeeded = True
                if step.name == "change_detect":
                    dirty_state.working_tree_changed = True
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
                if dirty_state.apply_patch_succeeded:
                    rollback_result = restore_rollback_checkpoint(self.tool_runner, repo_root, checkpoint)
                    outcome = rollback_result.get("outcome", "rollback_not_needed")
                    self.tool_runner.ledger.append(
                        {
                            "phase": "rollback",
                            "verification_status": outcome,
                            "failure_class": rollback_result.get("failure_class", ""),
                        }
                    )
                    dirty_state.rollback_succeeded = outcome == "rollback_succeeded"
                    dirty_state.rollback_failed = outcome == "rollback_failed"
                    dirty_check = detect_dirty_state(self.tool_runner, repo_root)
                    dirty_state.agent_introduced_dirty_state = bool(dirty_check["is_dirty"]) and not dirty_state.pre_existing_dirty_state
                    if dirty_state.rollback_failed:
                        self.tool_runner.ledger.append(
                            {
                                "phase": "rollback",
                                "verification_status": "failed",
                                "controller_decision": "halt",
                                "failure_class": "rollback_failed_dirty_repo",
                            }
                        )
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
    SNAPSHOT = "SNAPSHOT"
    VERIFY = "VERIFY"
    APPROVAL = "APPROVAL"
    FINALIZE = "FINALIZE"
    ROLLBACK = "ROLLBACK"
    COMMIT = "COMMIT"
    HALT = "HALT"


class DeterministicController:
    """Code-driven FSM for bounded workflows (no model for state transitions)."""

    TRANSITIONS = {
        ControllerState.IDLE: {"task_received": ControllerState.PLAN},
        ControllerState.PLAN: {"intent_valid": ControllerState.RESOLVE_EDIT, "intent_invalid": ControllerState.HALT},
        ControllerState.RESOLVE_EDIT: {"diff_ready": ControllerState.SNAPSHOT, "selector_ambiguous": ControllerState.HALT},
        ControllerState.SNAPSHOT: {"snapshot_ready": ControllerState.VERIFY, "snapshot_failed": ControllerState.HALT},
        ControllerState.VERIFY: {"verification_passed": ControllerState.APPROVAL, "verification_failed": ControllerState.ROLLBACK},
        ControllerState.ROLLBACK: {"rollback_succeeded": ControllerState.HALT, "rollback_failed": ControllerState.HALT},
        ControllerState.APPROVAL: {"approval_granted": ControllerState.FINALIZE, "approval_denied": ControllerState.HALT},
        ControllerState.FINALIZE: {"finalization_passed": ControllerState.IDLE, "finalization_failed": ControllerState.HALT},
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


class TaskStatus:
    PENDING = "PENDING"
    PLANNED = "PLANNED"
    RUNNING = "RUNNING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    COMPLETED = "COMPLETED"
    HALTED = "HALTED"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"


@dataclass(frozen=True)
class TaskArtifactSummary:
    artifact_hash: str
    kind: str
    note: str = ""


@dataclass(frozen=True)
class TaskRequest:
    task_id: str
    user_objective: str
    repo_root: str
    strict_mode: bool
    approval_required: bool
    created_at: str


@dataclass
class TaskStep:
    step_id: str
    step_type: str
    description: str
    expected_output: str
    dependencies: List[str]
    risk_level: str
    requires_approval: bool
    status: str = "PENDING"
    inputs: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskPlan:
    task_id: str
    objective_summary: str
    assumptions: List[str]
    bounded_steps: List[TaskStep]
    success_criteria: List[str]
    stop_conditions: List[str]


@dataclass
class TaskExecutionResult:
    task_id: str
    final_status: str
    completed_steps: List[str]
    failed_step: Optional[str]
    commit_hash: Optional[str]
    witness_tip_hash: str
    summary: str
    artifacts: List[TaskArtifactSummary]


@dataclass(frozen=True)
class TaskPolicyLimits:
    max_task_steps: int = 7
    max_refinement_attempts: int = 2
    max_files_read_per_step: int = 3
    max_context_bytes: int = 32_000
    max_task_runtime_s: int = 120
    max_approvals_per_task: int = 1


class BoundedPlanner:
    SUPPORTED_STEP_TYPES = {
        "inspect_repo",
        "read_file",
        "plan_edit",
        "apply_edit",
        "run_verification",
        "finalize_change",
        "summarize_result",
    }

    def __init__(self, max_steps: int = 7) -> None:
        self.max_steps = max_steps

    def build_plan(self, request: TaskRequest, proposed_plan: Optional[TaskPlan] = None) -> TaskPlan:
        plan = proposed_plan or self._default_plan(request)
        self.validate_plan(plan)
        return plan

    def validate_plan(self, plan: TaskPlan) -> None:
        if not plan.success_criteria:
            raise ValueError("TaskPlan requires success_criteria")
        if len(plan.bounded_steps) == 0 or len(plan.bounded_steps) > self.max_steps:
            raise ValueError("TaskPlan step count exceeds bounded limit")
        for step in plan.bounded_steps:
            if step.step_type not in self.SUPPORTED_STEP_TYPES:
                raise ValueError(f"Unsupported step type: {step.step_type}")
            if any(token in step.description.lower() for token in ("loop until", "while true", "repeat forever")):
                raise ValueError("Unbounded loop language is not allowed in step descriptions")
            if step.step_type == "apply_edit" and " and " in step.description.lower():
                raise ValueError("Risky multi-action step must be split")

    @staticmethod
    def _default_plan(request: TaskRequest) -> TaskPlan:
        return TaskPlan(
            task_id=request.task_id,
            objective_summary=request.user_objective,
            assumptions=["single-edit reliability-first workflow"],
            bounded_steps=[
                TaskStep("s1", "inspect_repo", "Inspect repository state", "Repo metadata", [], "low", False),
                TaskStep("s2", "plan_edit", "Produce one structured edit intent", "Valid EditIntent", ["s1"], "medium", False),
                TaskStep("s3", "apply_edit", "Apply one deterministic edit", "Verified patch", ["s2"], "high", False),
                TaskStep("s4", "finalize_change", "Finalize commit and provenance", "Commit + note", ["s3"], "high", True),
                TaskStep("s5", "summarize_result", "Summarize witnessed outcomes", "Deterministic summary", ["s4"], "low", False),
            ],
            success_criteria=["verification passed", "commit and note verified"],
            stop_conditions=["selector ambiguity", "verification failure", "approval denied"],
        )


class TaskExecutionEngine:
    """Bounded task loop that routes all risky operations through distrustful primitives."""

    def __init__(
        self,
        *,
        planner: BoundedPlanner,
        diff_emitter: DeterministicDiffEmitter,
        verification_runner: VerificationRunner,
        tool_runner: ToolWitnessRunner,
        approval_tokens: ApprovalTokenManager,
        sanitizer: InjectionSanitizer,
        policy: TaskPolicyLimits = TaskPolicyLimits(),
        intent_provider: Optional[Any] = None,
        refinement_provider: Optional[Any] = None,
    ) -> None:
        self.planner = planner
        self.diff_emitter = diff_emitter
        self.verification_runner = verification_runner
        self.tool_runner = tool_runner
        self.approval_tokens = approval_tokens
        self.sanitizer = sanitizer
        self.policy = policy
        self.intent_provider = intent_provider
        self.refinement_provider = refinement_provider

    def execute(
        self,
        request: TaskRequest,
        *,
        proposed_plan: Optional[TaskPlan] = None,
        approval_token: Optional[str] = None,
    ) -> TaskExecutionResult:
        started = time.time()
        completed_steps: List[str] = []
        artifacts: List[TaskArtifactSummary] = []
        commit_hash: Optional[str] = None
        current_patch = ""
        intent: Optional[EditIntent] = None
        status = TaskStatus.PENDING
        failed_step: Optional[str] = None

        plan = self.planner.build_plan(request, proposed_plan=proposed_plan)
        self._task_event("task_plan_created", request.task_id, None, "plan_ready")
        status = TaskStatus.PLANNED

        for step in plan.bounded_steps[: self.policy.max_task_steps]:
            if time.time() - started > self.policy.max_task_runtime_s:
                failed_step = step.step_id
                status = TaskStatus.HALTED
                self._task_event("task_halted", request.task_id, step.step_id, "runtime_limit")
                break

            status = TaskStatus.RUNNING
            self._task_event("task_step_started", request.task_id, step.step_id, "start")
            try:
                if step.step_type == "inspect_repo":
                    dirty = detect_dirty_state(self.tool_runner, Path(request.repo_root))
                    payload = json.dumps(dirty, sort_keys=True).encode("utf-8")
                    digest = self.tool_runner.ledger.store_artifact(payload, suffix=".inspect.json")
                    artifacts.append(TaskArtifactSummary(digest, "inspect_repo", "dirty-state snapshot"))
                elif step.step_type == "read_file":
                    step_artifacts = self._execute_read_file_step(request, step)
                    artifacts.extend(step_artifacts)
                elif step.step_type == "plan_edit":
                    intent = self._get_edit_intent(request, step)
                elif step.step_type == "apply_edit":
                    if intent is None:
                        raise RuntimeError("apply_edit requires prior plan_edit intent")
                    current_patch = self._execute_apply_edit(request, step, intent)
                elif step.step_type == "run_verification":
                    verify_results = self.verification_runner.run(Path(request.repo_root), current_patch, strict=request.strict_mode)
                    if any((not result.passed) and result.step in {"patch_check", "apply_patch", "change_detect", "tests"} for result in verify_results):
                        raise RuntimeError("verification_failed")
                elif step.step_type == "finalize_change":
                    status, commit_hash = self._execute_finalize_step(request, step, current_patch, approval_token)
                    if status in {TaskStatus.AWAITING_APPROVAL, TaskStatus.HALTED, TaskStatus.PARTIAL, TaskStatus.FAILED}:
                        failed_step = step.step_id
                        self._task_event("task_step_failed", request.task_id, step.step_id, status.lower())
                        break
                elif step.step_type == "summarize_result":
                    pass
                else:
                    raise ValueError(f"Unsupported step type: {step.step_type}")
            except SelectorAmbiguityError:
                refined_intent = self._attempt_refinement(request, intent)
                if refined_intent is None:
                    failed_step = step.step_id
                    status = TaskStatus.HALTED
                    self._task_event("task_halted", request.task_id, step.step_id, "selector_ambiguous")
                    break
                intent = refined_intent
                current_patch = self._execute_apply_edit(request, step, intent)
            except Exception as exc:
                failed_step = step.step_id
                status = TaskStatus.FAILED
                self._task_event("task_step_failed", request.task_id, step.step_id, str(exc), failure_class="task_step_failed")
                break

            step.status = "COMPLETED"
            completed_steps.append(step.step_id)
            self._task_event("task_step_completed", request.task_id, step.step_id, "completed")

        if status == TaskStatus.RUNNING:
            status = TaskStatus.COMPLETED

        summary = self._build_summary_from_witness(status, completed_steps, failed_step, commit_hash)
        final_event = "task_completed" if status == TaskStatus.COMPLETED else "task_partial" if status == TaskStatus.PARTIAL else "task_halted"
        self._task_event(final_event, request.task_id, failed_step, status.lower())
        return TaskExecutionResult(
            task_id=request.task_id,
            final_status=status,
            completed_steps=completed_steps,
            failed_step=failed_step,
            commit_hash=commit_hash,
            witness_tip_hash=self.tool_runner.ledger.tip_hash,
            summary=summary,
            artifacts=artifacts,
        )

    def _execute_read_file_step(self, request: TaskRequest, step: TaskStep) -> List[TaskArtifactSummary]:
        targets = list(step.inputs.get("files", []))[: self.policy.max_files_read_per_step]
        summaries: List[TaskArtifactSummary] = []
        for rel_path in targets:
            file_path = (Path(request.repo_root) / rel_path).resolve()
            text = file_path.read_text(encoding="utf-8")
            bounded = text[: self.policy.max_context_bytes]
            sanitized = self.sanitizer.sanitize(bounded, provenance=f"file:{rel_path}")
            digest = self.tool_runner.ledger.store_artifact(sanitized.sanitized_text.encode("utf-8"), suffix=".sanitized.txt")
            summaries.append(TaskArtifactSummary(digest, "read_file", rel_path))
        return summaries

    def _get_edit_intent(self, request: TaskRequest, step: TaskStep) -> EditIntent:
        if self.intent_provider is None:
            raise RuntimeError("No intent provider configured")
        proposed = self.intent_provider(request, step)
        if isinstance(proposed, EditIntent):
            intent = proposed
        else:
            intent = EditIntent(**proposed)
        intent.validate()
        return intent

    def _execute_apply_edit(self, request: TaskRequest, step: TaskStep, intent: EditIntent) -> str:
        resolved = self.diff_emitter.resolve(Path(request.repo_root), intent)
        patch = self.diff_emitter.emit_diff(Path(request.repo_root), resolved)
        verify_results = self.verification_runner.run(Path(request.repo_root), patch, strict=request.strict_mode)
        if any((not result.passed) and result.step in {"patch_check", "apply_patch", "change_detect", "tests"} for result in verify_results):
            raise RuntimeError("verification_failed")
        return patch

    def _attempt_refinement(self, request: TaskRequest, current_intent: Optional[EditIntent]) -> Optional[EditIntent]:
        if current_intent is None or self.refinement_provider is None:
            return None
        for attempt in range(self.policy.max_refinement_attempts):
            refined = self.refinement_provider(request, current_intent, attempt)
            if not refined:
                continue
            candidate = refined if isinstance(refined, EditIntent) else EditIntent(**refined)
            candidate.validate()
            return candidate
        return None

    def _execute_finalize_step(
        self,
        request: TaskRequest,
        step: TaskStep,
        patch_text: str,
        approval_token: Optional[str],
    ) -> Tuple[str, Optional[str]]:
        if request.approval_required or step.requires_approval:
            if not approval_token:
                return TaskStatus.AWAITING_APPROVAL, None
            try:
                approval_payload = self.approval_tokens.verify(approval_token, "git.commit")
                approval_ref = approval_payload.get("jti", "")
            except Exception:
                return TaskStatus.HALTED, None
        else:
            approval_ref = ""

        commit = finalize_commit(self.tool_runner, Path(request.repo_root), task_id=request.task_id)
        if not commit.get("ok"):
            return TaskStatus.FAILED, None
        commit_hash = str(commit["commit_hash"])
        note_payload = build_provenance_note(
            {
                "task_id": request.task_id,
                "timestamp": _utc_now(),
                "model_fingerprint": self.tool_runner.model_fingerprint,
                "witness_tip_hash": self.tool_runner.ledger.tip_hash,
                "patch_artifact_hash": sha256_bytes(patch_text.encode("utf-8")),
                "approval_ref": approval_ref,
                "prompt_schema_hash": sha256_bytes(json.dumps(EDIT_INTENT_JSON_SCHEMA, sort_keys=True).encode("utf-8")),
                "verification_summary_hash": sha256_bytes(f"{request.task_id}|{commit_hash}".encode("utf-8")),
            }
        )
        note = attach_git_note(self.tool_runner, Path(request.repo_root), commit_hash=commit_hash, note_payload=note_payload)
        if not note.get("ok"):
            return TaskStatus.PARTIAL, commit_hash
        verified = verify_finalization(
            self.tool_runner,
            Path(request.repo_root),
            commit_hash=commit_hash,
            expected_witness_tip_hash=self.tool_runner.ledger.tip_hash,
        )
        if not verified.get("ok"):
            return TaskStatus.PARTIAL, commit_hash
        return TaskStatus.RUNNING, commit_hash

    def _task_event(
        self,
        phase: str,
        task_id: str,
        step_id: Optional[str],
        decision: str,
        *,
        failure_class: str = "",
    ) -> None:
        self.tool_runner.ledger.append(
            {
                "phase": phase,
                "controller_decision": decision,
                "failure_class": failure_class,
                "tool_name": "task_engine",
                "args_hash": sha256_bytes(f"{task_id}:{step_id or ''}:{decision}".encode("utf-8")),
                "verification_status": "",
            }
        )

    @staticmethod
    def _build_summary_from_witness(status: str, completed: List[str], failed_step: Optional[str], commit_hash: Optional[str]) -> str:
        parts = [f"status={status}", f"completed_steps={len(completed)}"]
        if failed_step:
            parts.append(f"failed_step={failed_step}")
        if commit_hash:
            parts.append(f"commit={commit_hash}")
        return "; ".join(parts)
