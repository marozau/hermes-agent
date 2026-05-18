"""
hermes_dream — Staged Dream Artifact orchestrator (Epic 4)

Spec references:
  FR-13 hermes dream {create|status|diff|apply|discard} CLI verbs
        (CLI surface = ~/.hermes/bin/hermes-dream; deferred hermes_cli wiring = Story 4.8).
  FR-14 create writes ONLY to ~/.hermes/dreams/<dream_id>/; never mutates live state.
  FR-15 Artifact layout: manifest.json, REPORT.md, memory.patch, sources.jsonl,
        + optional user.patch / facts.proposed.jsonl / skills.proposed/*.patch (deferred).
  FR-16 Each patch proposal carries op, target_entry_id, body, type, rationale,
        confidence (Literal), risk_class (Literal), source_refs.
  FR-17 status lists existing dreams with scope, age, regression verdict, eligibility.
  FR-18 diff renders REPORT.md + memory.patch + sources summary (truncated at 1 MB/file).
  FR-19 apply runs each patch through canonical writer (per-op undo journal).
  FR-20 apply --only <patch-glob> filters proposals by fnmatch glob.
  FR-21 discard removes artifact dir; emits `op: discard` audit row; idempotent.
  FR-22 apply is idempotent by sha256(memory.patch + manifest.json).
  FR-24 Lock at ~/.hermes/dreams/.create.lock — atomic O_EXCL acquire + release.
  NFR-8  Lock auto-released on crash (context manager + try/finally).
  NFR-9  apply rolls back on partial failure via undo journal.
  NFR-14 Dream artifacts mode 0o700 at creation (not chmod-after).
  NFR-19 Hash-chained audit log at ~/.hermes/dreams/audit.jsonl.
  Hard Invariant #4 apply requires force_apply=True (manual ack).
  Hard Invariant #5 soul-guardian carve-out check at create start (stub seam).
  Hard Invariant #9 Content-hashed idempotency.
"""
from __future__ import annotations

import errno
import fnmatch
import hashlib
import json
import logging
import os
import shutil
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from lib.hermes_memory import _generate_ulid, read_entries

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Data models
# ─────────────────────────────────────────────────────────────────────────────

EntryType = Literal[
    "preference", "fact", "procedure", "episode",
    "superseded", "trajectory", "unknown",
]

ProposalOp = Literal["add", "update", "supersede", "expire"]
Confidence = Literal["low", "medium", "high"]
RiskClass = Literal["additive", "corrective", "deprecating"]


class CostInfo(BaseModel):
    model_config = ConfigDict(frozen=True)
    tokens_in: int = 0
    tokens_out: int = 0
    cache_read_tokens: int = 0


class PatchProposal(BaseModel):
    """FR-16: every proposal carries op, target_entry_id (or marker `new`),
    rationale, confidence, risk_class, source_refs. P7: includes type so the
    apply path can route to the right canonical-writer call."""
    model_config = ConfigDict(frozen=True)

    op: ProposalOp
    type: EntryType = "fact"        # P7: required for `add` ops; default keeps tests simple
    target_entry_id: Optional[str] = None
    body: str = ""
    rationale: str = ""
    confidence: Confidence = "low"
    risk_class: RiskClass = "additive"
    source_refs: list[str] = Field(default_factory=list)


class DreamManifest(BaseModel):
    model_config = ConfigDict(frozen=True)
    scope: str
    started_at: str
    finished_at: str
    model_used: str = "none"
    signal_density_score: float = 0.0
    recall_regression_verdict: str = "skipped"
    cost: CostInfo = Field(default_factory=CostInfo)
    signature_anchors: list[str] = Field(default_factory=list)
    # Epic 5 / FR-28: Δtokens(MEMORY.md) — char-count proxy.
    delta_tokens: dict = Field(default_factory=lambda: {"before": 0, "after": 0, "delta": 0})


# ─────────────────────────────────────────────────────────────────────────────
# Path resolution + atomic file write (P13, P14, P15)
# ─────────────────────────────────────────────────────────────────────────────


def _resolve_dreams_dir(override: Optional[str] = None) -> Path:
    """P14: honors HERMES_DREAMS_DIR (parity with HERMES_MEMORY_DIR/RAW_DIR)."""
    if override:
        return Path(override)
    env = os.environ.get("HERMES_DREAMS_DIR")
    if env:
        return Path(env)
    home = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
    return Path(home) / "dreams"


def _ensure_dir(path: Path, mode: int = 0o700) -> None:
    """P15: mkdir with explicit mode; never relies on chmod-after race."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.mkdir(mode=mode, exist_ok=True)


def _write_file_atomic(path: Path, content: str, mode: int = 0o600) -> None:
    """P13: actually atomic — tmp + os.replace, never visible at the final path
    in a partial state."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        os.write(fd, content.encode("utf-8"))
    finally:
        os.close(fd)
    os.replace(tmp, path)


# ─────────────────────────────────────────────────────────────────────────────
# Lock mechanics (DN2 / FR-24 / NFR-8 / Hard Invariant #7) — atomic + released
# ─────────────────────────────────────────────────────────────────────────────

_LOCK_STALE_SECONDS = 3600  # 1 hour


def generate_dream_id() -> str:
    """ULID-based, 26 chars. Reuses the in-process monotonic state from
    hermes_memory; this keeps dream IDs lex-sortable across creation order."""
    return _generate_ulid()


def _try_atomic_create(lock_path: Path) -> Optional[int]:
    """O_CREAT|O_EXCL create. Returns the open fd or None if file exists."""
    try:
        return os.open(
            str(lock_path),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError:
        return None


def acquire_lock(dreams_dir: Optional[str] = None) -> bool:
    """P2 + DN2: atomic O_EXCL create, owner-only mode. Returns True on
    acquired, False on held-by-another-live-process.

    If a lock exists but its mtime is stale (>1h) AND the PID is dead
    (os.kill(pid, 0) raises ProcessLookupError), reclaim it. NEVER reclaim
    a fresh lock.
    """
    ddir = _resolve_dreams_dir(dreams_dir)
    _ensure_dir(ddir, mode=0o700)
    lock_path = ddir / ".create.lock"

    fd = _try_atomic_create(lock_path)
    if fd is None:
        # Lock present — evaluate liveness.
        try:
            age = datetime.now(timezone.utc).timestamp() - lock_path.stat().st_mtime
        except OSError:
            return False
        if age < _LOCK_STALE_SECONDS:
            return False  # Fresh lock; respect it.

        # Stale by mtime — check whether the holder PID is alive.
        try:
            body = lock_path.read_text(encoding="utf-8")
        except OSError:
            return False
        first_line = body.split("\n", 1)[0].strip()
        if not first_line:
            # Empty/partial body — refuse to reclaim (could be a slow flush
            # from a healthy writer; see TOCTOU-by-truncate review finding).
            return False
        try:
            pid = int(first_line)
        except ValueError:
            return False
        # P1: os.kill(pid, 0) is the canonical POSIX liveness check.
        pid_alive = True
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            pid_alive = False
        except PermissionError:
            pid_alive = True  # owned by another user → alive
        except OSError as e:
            pid_alive = e.errno != errno.ESRCH
        if pid_alive:
            return False

        # PID dead — reclaim.
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            return False
        fd = _try_atomic_create(lock_path)
        if fd is None:
            return False

    # We hold the fd — write the body.
    try:
        body = f"{os.getpid()}\n{datetime.now(timezone.utc).isoformat()}\n"
        os.write(fd, body.encode("utf-8"))
    finally:
        os.close(fd)
    return True


def release_lock(dreams_dir: Optional[str] = None) -> bool:
    """P2: explicit release. Removes the lock file. Returns True if removed."""
    ddir = _resolve_dreams_dir(dreams_dir)
    lock_path = ddir / ".create.lock"
    try:
        lock_path.unlink()
        return True
    except FileNotFoundError:
        return False


@contextmanager
def dream_lock(dreams_dir: Optional[str] = None):
    """P3 / DN2: context manager. Releases on completion AND on exception
    (NFR-8 crash auto-release)."""
    acquired = acquire_lock(dreams_dir)
    if not acquired:
        raise RuntimeError(
            "dream_lock: another `create` is in progress (lock held). "
            "If you're sure no other run is active, wait 1h or remove "
            f"{_resolve_dreams_dir(dreams_dir) / '.create.lock'} manually."
        )
    try:
        yield
    finally:
        release_lock(dreams_dir)


# ─────────────────────────────────────────────────────────────────────────────
# Hash-chained audit log (P11 / NFR-19)
# ─────────────────────────────────────────────────────────────────────────────


def _audit_path(dreams_dir: Optional[str] = None) -> Path:
    return _resolve_dreams_dir(dreams_dir) / "audit.jsonl"


def _last_audit_hash(audit_file: Path) -> str:
    """Read the previous row's `hash` field — empty string if no prior rows."""
    if not audit_file.exists():
        return ""
    try:
        with audit_file.open("rb") as f:
            try:
                f.seek(-4096, os.SEEK_END)
            except OSError:
                f.seek(0)
            tail = f.read().decode("utf-8", errors="ignore")
    except OSError:
        return ""
    last = ""
    for line in tail.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            last = row.get("hash", "") or last
        except json.JSONDecodeError:
            continue
    return last


def write_audit(
    op: str,
    *,
    dream_id: str,
    actor: Optional[str] = None,
    extra: Optional[dict] = None,
    dreams_dir: Optional[str] = None,
) -> str:
    """NFR-19: append a hash-chained audit row. Returns the new row's hash."""
    ddir = _resolve_dreams_dir(dreams_dir)
    _ensure_dir(ddir, mode=0o700)
    audit_file = ddir / "audit.jsonl"

    prev = _last_audit_hash(audit_file)
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "op": op,
        "dream_id": dream_id,
        "actor": actor or os.environ.get("USER") or "unknown",
        "prev_hash": prev,
    }
    if extra:
        if "hash" in extra:
            raise ValueError(
                "write_audit: 'hash' is reserved for the audit-chain digest. "
                "Use 'content_hash' or another name in `extra`."
            )
        row.update(extra)

    canonical = json.dumps(row, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256((prev + canonical).encode("utf-8")).hexdigest()
    row["hash"] = digest

    line = (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    fd = os.open(str(audit_file), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)
    return digest


def verify_audit_chain(dreams_dir: Optional[str] = None) -> bool:
    """Verify the hash chain. Returns True if valid (or empty)."""
    audit_file = _audit_path(dreams_dir)
    if not audit_file.exists():
        return True
    prev = ""
    for line in audit_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        claimed = row.pop("hash")
        canonical = json.dumps(row, ensure_ascii=False, sort_keys=True)
        expected = hashlib.sha256((prev + canonical).encode("utf-8")).hexdigest()
        if claimed != expected:
            return False
        prev = claimed
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Hard Invariant #5: soul-guardian carve-out check (stub seam for Epic 6)
# ─────────────────────────────────────────────────────────────────────────────


def _check_soul_guardian_carve_out(memory_dir: Optional[str] = None) -> None:
    """Epic 6 P2: delegates to hermes_trust.check_soul_guardian_carveout.
    Raises RuntimeError if the carve-out is violated (mis-config = abort,
    Hard Invariant #5 / FR-43)."""
    from lib.hermes_trust import check_soul_guardian_carveout
    result = check_soul_guardian_carveout()
    if not result["ok"]:
        raise RuntimeError(
            f"soul-guardian carve-out violated (FR-43): {result['reason']}. "
            f"Offenders: {result.get('offenders', [])}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# create_dream_artifact (FR-13, FR-14, NFR-14)
# ─────────────────────────────────────────────────────────────────────────────


def create_dream_artifact(
    scope: str = "default",
    *,
    memory_dir: Optional[str] = None,
    dreams_dir: Optional[str] = None,
    dry_run: bool = False,
    use_lock: bool = True,
    model_used: str = "none",                            # P24
    cost: Optional[CostInfo] = None,                     # P24
    recall_regression_verdict: str = "skipped",          # P24
) -> str:
    """Create a dream artifact directory (FR-13, FR-14, NFR-14).

    DN2 / P3: wraps body in `with dream_lock():` so crash mid-create
    auto-releases the lock.
    """
    import yaml as _yaml

    # Epic 6 P2 / FR-43: soul-guardian carve-out check (raises on misconfig).
    _check_soul_guardian_carve_out(memory_dir)

    # Epic 6 P1 / FR-41: attestation pre-flight BEFORE lock acquire.
    # Critical drift aborts before any state mutation; advisory is emitted.
    from lib.hermes_trust import run_attestation_preflight
    attest = run_attestation_preflight()
    if not attest.passed:
        raise RuntimeError(
            f"attestation pre-flight failed (FR-41): severity={attest.severity}, "
            f"details={attest.details}"
        )

    def _do_create() -> str:
        dream_id = generate_dream_id()
        started = datetime.now(timezone.utc).isoformat()

        dreams_path = _resolve_dreams_dir(dreams_dir)
        _ensure_dir(dreams_path, mode=0o700)
        artifact_dir = dreams_path / dream_id
        artifact_dir.mkdir(mode=0o700, exist_ok=False)

        # FR-14: read live memory in read-only mode (NEVER mutates).
        entries = read_entries(memory_dir, read_only=True) if memory_dir else []

        proposals: list[PatchProposal] = []
        source_rows: list[dict] = []

        if dry_run:
            for i, entry in enumerate(entries[:3]):
                proposals.append(PatchProposal(
                    op="add" if i == 0 else "update",
                    type=entry.get("type", "fact"),
                    target_entry_id=entry["id"],
                    body=entry.get("body", ""),
                    rationale=f"Dry-run proposal {i+1}",
                    confidence="low" if i == 2 else "medium",
                    risk_class="additive",
                    source_refs=[f"sources:{i+1}"],
                ))
                source_rows.append({
                    "id": f"sources:{i+1}",
                    "source": entry.get("source", "unknown"),
                    "entry_id": entry["id"],
                })

        # memory.patch (FR-16) — written FIRST so the recall harness can replay it.
        if proposals:
            patches_yaml = _yaml.dump(
                [p.model_dump() for p in proposals],
                default_flow_style=False, allow_unicode=True, sort_keys=False,
            )
            _write_file_atomic(artifact_dir / "memory.patch", patches_yaml)

        # sources.jsonl
        if source_rows:
            sources_text = "\n".join(
                json.dumps(row, ensure_ascii=False) for row in source_rows
            ) + "\n"
            _write_file_atomic(artifact_dir / "sources.jsonl", sources_text)

        # ── Epic 5 / P1 + P7: recall harness + Δtokens ──
        # P1: run the recall harness; persists <id>/.hermes-private/recall.json.
        # The harness materializes proposed memory (copy of current + replay
        # patches) so live state is never mutated (FR-14 / Hard Invariant #4).
        from lib.hermes_recall import (
            compute_delta_tokens, materialize_proposed_memory,
            recall_artifact_path, run_recall_at_create,
        )
        # Run recall (idempotent on cold-start; writes recall.json).
        recall_report = run_recall_at_create(artifact_dir, memory_dir)
        # P7 / FR-28: Δtokens(MEMORY.md). Re-materialize for the delta calc;
        # cheap and ensures the manifest carries the value regardless of
        # whether the recall report was skipped.
        delta = {"before": 0, "after": 0, "delta": 0}
        if memory_dir is not None and (artifact_dir / "memory.patch").exists():
            sim_dir = artifact_dir / ".hermes-private" / "_sim_memory_delta"
            try:
                materialize_proposed_memory(artifact_dir, memory_dir, sim_dir)
                delta = compute_delta_tokens(memory_dir, str(sim_dir))
            except Exception as e:
                logger.debug("delta_tokens calc failed: %s", e)
            finally:
                try:
                    shutil.rmtree(sim_dir, ignore_errors=True)
                except OSError:
                    pass

        # manifest.json (written AFTER the recall harness so its verdict can be
        # threaded into the manifest).
        finished = datetime.now(timezone.utc).isoformat()
        # Verdict precedence: the harness wins when it actually ran;
        # otherwise the caller's `recall_regression_verdict` argument is kept
        # (so explicit test/operator overrides survive cold-start cases).
        verdict = recall_regression_verdict
        if recall_report.status == "complete":
            verdict = "fail" if recall_report.regression else "pass"
        manifest = DreamManifest(
            scope=scope,
            started_at=started,
            finished_at=finished,
            model_used=model_used,
            cost=cost or CostInfo(),
            recall_regression_verdict=verdict,
            delta_tokens=delta,
        )
        _write_file_atomic(
            artifact_dir / "manifest.json",
            json.dumps(manifest.model_dump(), indent=2, ensure_ascii=False) + "\n",
        )

        # REPORT.md
        report_lines = [
            f"# Dream Report — {dream_id}", "",
            f"**Scope:** {scope}",
            f"**Started:** {started}",
            f"**Finished:** {finished}", "",
            "## Summary", "",
            f"{len(proposals)} proposal(s) generated.",
            "",
            f"**Recall:** {recall_report.status} "
            f"(current={recall_report.current_score} proposed={recall_report.proposed_score} "
            f"regression={recall_report.regression})",
            f"**Δtokens:** {delta['delta']:+d} (before={delta['before']}, after={delta['after']})",
        ]
        if dry_run:
            report_lines += ["", "*This is a dry-run artifact for pipeline validation.*"]
        _write_file_atomic(
            artifact_dir / "REPORT.md",
            "\n".join(report_lines) + "\n",
        )

        logger.info(
            "Dream artifact created: %s (%d proposals, recall=%s, Δtok=%+d)",
            dream_id, len(proposals), verdict, delta["delta"],
        )
        return dream_id

    if use_lock:
        with dream_lock(dreams_dir):
            return _do_create()
    return _do_create()


# ─────────────────────────────────────────────────────────────────────────────
# Content-hash idempotency (P12 / DN4)
# ─────────────────────────────────────────────────────────────────────────────


def _compute_dream_hash(artifact_dir: Path) -> str:
    """sha256(memory.patch + manifest.json) — content-hash idempotency."""
    h = hashlib.sha256()
    for name in ("manifest.json", "memory.patch"):
        p = artifact_dir / name
        if p.exists():
            h.update(p.read_bytes())
        h.update(b"|")
    return h.hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# CLI verbs (Stories 4.3-4.7)
# ─────────────────────────────────────────────────────────────────────────────


_ULID_NAME_RE = None  # populated lazily


def list_dreams(dreams_dir: Optional[str] = None) -> list[dict]:
    """FR-17: list all dream artifacts with metadata.
    P18: apply_eligible derived from recall_regression_verdict."""
    ddir = _resolve_dreams_dir(dreams_dir)
    if not ddir.exists():
        return []

    dreams = []
    for artifact_dir in sorted(ddir.iterdir(), reverse=True):
        if not artifact_dir.is_dir() or artifact_dir.name.startswith("."):
            continue
        manifest_path = artifact_dir / "manifest.json"
        if not manifest_path.exists():
            logger.warning("list_dreams: skipping %s (no manifest.json)", artifact_dir.name)
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("list_dreams: corrupt manifest at %s: %s", artifact_dir.name, e)
            continue

        verdict = manifest.get("recall_regression_verdict", "skipped")
        # P18: eligibility derived from verdict.
        if verdict in ("pass", "ok"):
            eligible = "yes"
        elif verdict in ("fail", "regression"):
            eligible = "no"
        else:
            eligible = "manual"  # skipped/unknown → require explicit force_apply

        dreams.append({
            "dream_id": artifact_dir.name,
            "scope": manifest.get("scope", "unknown"),
            "created": manifest.get("started_at", ""),
            "regression": verdict,
            "apply_eligible": eligible,
            "applied": (artifact_dir / ".applied").exists(),
        })
    return dreams


_DIFF_MAX_BYTES = 1_048_576  # P21: 1 MB per file


def _read_truncated(path: Path) -> str:
    """P21: read a file but cap at _DIFF_MAX_BYTES with a truncation marker."""
    try:
        size = path.stat().st_size
    except OSError:
        return ""
    if size <= _DIFF_MAX_BYTES:
        return path.read_text(encoding="utf-8", errors="replace")
    with path.open("r", encoding="utf-8", errors="replace") as f:
        data = f.read(_DIFF_MAX_BYTES)
    return data + f"\n[... truncated — {size - _DIFF_MAX_BYTES} more bytes elided]\n"


def dream_diff(dream_id: str, dreams_dir: Optional[str] = None) -> str:
    """FR-18: render REPORT.md + memory.patch + sources summary."""
    ddir = _resolve_dreams_dir(dreams_dir)
    artifact_dir = ddir / dream_id
    if not artifact_dir.is_dir():
        raise FileNotFoundError(f"Dream '{dream_id}' not found at {artifact_dir}")

    parts = []
    report = artifact_dir / "REPORT.md"
    if report.exists():
        parts.append(_read_truncated(report))

    patch = artifact_dir / "memory.patch"
    if patch.exists():
        parts.append("\n--- memory.patch ---")
        parts.append(_read_truncated(patch))

    sources = artifact_dir / "sources.jsonl"
    if sources.exists():
        parts.append("\n--- sources.jsonl (summary) ---")
        try:
            n = sum(1 for ln in sources.read_text(encoding="utf-8").splitlines() if ln.strip())
        except OSError:
            n = 0
        parts.append(f"{n} source(s)")
    return "\n".join(parts)


# ── apply_dream — transactional with per-op undo journal (DN3 / NFR-9) ──


_JOURNAL_NAME = ".apply-journal.jsonl"
_APPLIED_NAME = ".applied"


def _journal_append(artifact_dir: Path, entry: dict) -> None:
    """Append one undo-journal row; written ATOMICALLY-PER-LINE."""
    fd = os.open(
        str(artifact_dir / _JOURNAL_NAME),
        os.O_WRONLY | os.O_CREAT | os.O_APPEND,
        0o600,
    )
    try:
        os.write(fd, (json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
    finally:
        os.close(fd)


def _journal_load(artifact_dir: Path) -> list[dict]:
    j = artifact_dir / _JOURNAL_NAME
    if not j.exists():
        return []
    return [
        json.loads(line)
        for line in j.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _undo_journal(
    artifact_dir: Path, memory_dir: Optional[str], raw_dir: Optional[str] = None,
) -> int:
    """Reverse-apply journal entries to roll back partial state."""
    from lib.hermes_memory import expire_entry  # noqa: F401 — kept for symmetry
    entries = _journal_load(artifact_dir)
    undone = 0
    # Reverse order: undo most-recent first.
    for je in reversed(entries):
        new_id = je.get("written_id")
        if not new_id:
            continue
        # Best-effort delete the typed file written by add/update.
        # NOTE: Memory mutations also went into the raw layer (immutable);
        # they remain as the audit record of the attempted apply (by design).
        from lib.hermes_memory import _resolve_memory_dir as _r
        mpath = _r(memory_dir) / f"{new_id}.md"
        try:
            mpath.unlink(missing_ok=True)
            undone += 1
        except OSError:
            pass
    # Clear the journal so re-apply starts fresh.
    try:
        (artifact_dir / _JOURNAL_NAME).unlink(missing_ok=True)
    except OSError:
        pass
    return undone


def apply_dream(
    dream_id: str,
    dreams_dir: Optional[str] = None,
    *,
    memory_dir: Optional[str] = None,
    only: Optional[str] = None,
    force_apply: bool = False,
    force_recall: bool = False,
    force_reason: str = "",
    actor: Optional[str] = None,
) -> dict:
    """FR-19 + FR-20 + FR-22 + FR-27 + FR-28 + NFR-9 + Hard Invariant #4/#9.

    force_apply:  manual ack of the apply itself (Epic 4, Hard Invariant #4).
    force_recall: override the recall-regression gate (Epic 5, FR-27/28).
    force_reason: required when force_recall=True (≥10 chars).

    Returns {"status": "applied"|"no_changes"|"refused"|"regression_blocked",
             "operations": N, ...}.
    """
    from lib.hermes_memory import (
        add_entry, expire_entry, supersede_entry, update_entry,
    )
    from lib.hermes_recall import read_recall_artifact, regression_blocks_apply
    import yaml as _yaml

    ddir = _resolve_dreams_dir(dreams_dir)
    artifact_dir = ddir / dream_id
    if not artifact_dir.is_dir():
        raise FileNotFoundError(f"Dream '{dream_id}' not found")

    # DN5 / Hard Invariant #4: manual ack required.
    if not force_apply:
        return {
            "status": "refused",
            "operations": 0,
            "reason": (
                f"apply_dream requires explicit force_apply=True (Hard Invariant #4). "
                f"Review the artifact at {artifact_dir} first, then call again "
                f"with force_apply=True or `hermes dream apply {dream_id} --accept`."
            ),
        }

    # ── Epic 5 / FR-27 + FR-28: recall regression gate ──
    # Consult <id>/.hermes-private/recall.json. If regression=true and the
    # caller hasn't passed force_recall=True with a reason ≥10 chars, refuse
    # with status='regression_blocked'.
    recall_report = read_recall_artifact(artifact_dir)
    recall_gate = regression_blocks_apply(
        recall_report,
        force=force_recall,
        force_reason=force_reason,
    ) if recall_report is not None else {
        "blocked": False, "reason": "no-recall-artifact", "forced": False,
    }
    if recall_gate["blocked"]:
        return {
            "status": "regression_blocked",
            "operations": 0,
            "reason": recall_gate["reason"],
        }
    forced = recall_gate.get("forced", False)

    # P12 / DN4: content-hash idempotency.
    current_hash = _compute_dream_hash(artifact_dir)
    applied_marker = artifact_dir / _APPLIED_NAME
    if applied_marker.exists():
        try:
            prior_hash = applied_marker.read_text(encoding="utf-8").strip()
        except OSError:
            prior_hash = ""
        if prior_hash == current_hash:
            return {"status": "no_changes", "operations": 0, "hash": current_hash}
        # Hash differs — the artifact was modified after a prior apply.
        # Refuse rather than silently re-apply (this would be a hash-chain break).
        return {
            "status": "refused",
            "operations": 0,
            "reason": (
                "Artifact content has changed since prior apply "
                "(hash mismatch). Re-create the dream rather than re-applying."
            ),
        }

    patch_path = artifact_dir / "memory.patch"
    if not patch_path.exists():
        return {"status": "no_changes", "operations": 0, "hash": current_hash}

    proposals_raw = _yaml.safe_load(patch_path.read_text(encoding="utf-8"))
    if not isinstance(proposals_raw, list):
        return {"status": "no_changes", "operations": 0, "hash": current_hash}

    # P17: validate via PatchProposal schema. Refuses malformed proposals.
    proposals: list[PatchProposal] = []
    for raw in proposals_raw:
        try:
            proposals.append(PatchProposal.model_validate(raw))
        except ValidationError as e:
            return {
                "status": "refused",
                "operations": 0,
                "reason": f"Invalid proposal in memory.patch: {e}",
            }

    # P6 / FR-20: --only glob filter.
    if only:
        proposals = [p for p in proposals if p.target_entry_id and fnmatch.fnmatch(p.target_entry_id, only)]

    # P9: source attribution is canonical.
    source = f"dream:{dream_id}"

    # P4 / DN3 / NFR-9: per-op undo journal + rollback on failure.
    ops = 0
    try:
        for p in proposals:
            if p.op == "add":
                new_id = add_entry(p.type, p.body, source, memory_dir=memory_dir)
                _journal_append(artifact_dir, {
                    "op": "add", "written_id": new_id, "type": p.type,
                })
                ops += 1
            elif p.op == "update":
                if not p.target_entry_id:
                    raise ValueError(f"update proposal missing target_entry_id")
                # P10: NO silent fallback to add — target missing = LookupError.
                try:
                    update_entry(p.target_entry_id, p.body, memory_dir=memory_dir)
                except FileNotFoundError:
                    raise LookupError(
                        f"update target {p.target_entry_id} not found "
                        f"in live memory; refusing silent fallback"
                    )
                _journal_append(artifact_dir, {
                    "op": "update", "target_entry_id": p.target_entry_id,
                })
                ops += 1
            elif p.op == "supersede":
                # P5: supersede branch (was silently dropped).
                if not p.target_entry_id:
                    raise ValueError("supersede proposal missing target_entry_id")
                new_id = add_entry(p.type, p.body, source, memory_dir=memory_dir)
                supersede_entry(p.target_entry_id, new_id, memory_dir=memory_dir)
                _journal_append(artifact_dir, {
                    "op": "supersede",
                    "old_id": p.target_entry_id,
                    "written_id": new_id,
                })
                ops += 1
            elif p.op == "expire":
                if not p.target_entry_id:
                    raise ValueError("expire proposal missing target_entry_id")
                expire_entry(p.target_entry_id, memory_dir=memory_dir)
                _journal_append(artifact_dir, {
                    "op": "expire", "target_entry_id": p.target_entry_id,
                })
                ops += 1
    except BaseException as e:
        # P4: rollback partial state via the undo journal.
        undone = _undo_journal(artifact_dir, memory_dir)
        write_audit(
            "apply_failed", dream_id=dream_id, actor=actor,
            extra={"error": repr(e), "undone": undone, "applied_before_fail": ops},
            dreams_dir=dreams_dir,
        )
        raise

    # P11 / NFR-19: audit row on success. Note: `content_hash` not `hash` —
    # the `hash` key is reserved for the audit-chain digest itself.
    # FR-28: when the recall gate was force-overridden, the audit row
    # carries `forced: true` + `reason: <text>` for the override trail.
    audit_extra = {
        "operations": ops,
        "content_hash": current_hash,
        "only": only,
        "forced": forced,
    }
    if forced:
        audit_extra["reason"] = force_reason.strip()
    write_audit(
        "apply", dream_id=dream_id, actor=actor,
        extra=audit_extra,
        dreams_dir=dreams_dir,
    )

    # P12: applied marker carries the content hash.
    _write_file_atomic(applied_marker, current_hash + "\n")
    # Clear journal — apply succeeded.
    try:
        (artifact_dir / _JOURNAL_NAME).unlink(missing_ok=True)
    except OSError:
        pass

    # ── Epic 6 P3 + P4: sign + rebaseline after successful apply ──
    # FR-44: sign_patches writes <id>/.hermes-private/sign.json (Ed25519).
    # FR-42: rebaseline_attestation re-hashes protected files.
    # Both are best-effort: if signing key isn't configured (NFR-15 advisory)
    # we log and continue rather than rolling back a successful apply.
    sign_result: Optional[dict] = None
    rebaseline_result: Optional[dict] = None
    try:
        from lib.hermes_trust import rebaseline_attestation, sign_patches
        try:
            sign_result = sign_patches(str(artifact_dir))
        except ValueError as e:
            # NFR-15: no signing key configured. Log but don't roll back.
            logger.warning("sign_patches skipped (FR-44): %s", e)
        except FileExistsError as e:
            logger.warning("sign_patches: sign.json already exists: %s", e)
        try:
            rebaseline_result = rebaseline_attestation(str(artifact_dir))
        except Exception as e:
            logger.warning("rebaseline_attestation failed (FR-42): %s", e)
    except ImportError as e:
        logger.warning("hermes_trust unavailable: %s", e)

    return {
        "status": "applied",
        "operations": ops,
        "hash": current_hash,
        "signed": sign_result is not None,
        "rebaselined": bool(rebaseline_result and rebaseline_result.get("ok")),
    }


def discard_dream(
    dream_id: str,
    dreams_dir: Optional[str] = None,
    *,
    actor: Optional[str] = None,
) -> dict:
    """FR-21: remove artifact dir + emit `op: discard` audit (NFR-19).
    P20: refuses to follow symlinks. Idempotent on missing artifact."""
    ddir = _resolve_dreams_dir(dreams_dir)
    artifact_dir = ddir / dream_id

    if not artifact_dir.exists():
        return {"status": "not_found"}

    if artifact_dir.is_symlink():
        raise RuntimeError(
            f"Refusing to discard '{dream_id}': artifact is a symlink. "
            f"Inspect the link manually."
        )

    # NFR-19: audit FIRST (so the event is durable even if rmtree fails partway).
    write_audit("discard", dream_id=dream_id, actor=actor, dreams_dir=dreams_dir)

    shutil.rmtree(artifact_dir)
    return {"status": "discarded"}
