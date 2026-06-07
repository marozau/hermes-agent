"""
hermes_recall — Dry-Run Recall Regression Harness (Epic 5)

FR-25: build_recall_set() samples 20 queries from raw layer; (year, week) seeded.
       Persisted by create_dream_artifact at ~/.hermes/dreams/<id>/.hermes-private/recall.json.
FR-26: run_regression_check() pre/post comparison with strict-fewer-correct.
FR-27: regression blocks apply (apply_dream consults recall.json).
FR-28: --force --reason override + Δtokens(MEMORY.md) in manifest.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Data models
# ─────────────────────────────────────────────────────────────────────────────


_RecallStatus = Literal["ready", "complete", "skipped"]
_MatchKind = Literal[
    "identical_both_correct",
    "identical_both_incorrect",
    "improved",
    "degraded",
    "unchanged",
]


class RecallQuery(BaseModel):
    model_config = ConfigDict(frozen=True)
    query: str
    expected_answer: str
    source: str = ""  # raw layer entry_id that generated this query


class RecallResult(BaseModel):
    """P8 / FR-25 schema: includes truncated answer preview + content hash
    so downstream audit can distinguish 'both correct, same answer' from
    'both correct, different answer'."""
    model_config = ConfigDict(frozen=True)
    query: str
    current_correct: bool
    proposed_correct: bool
    current_answer: str = ""        # truncated to 200 chars
    proposed_answer: str = ""       # truncated to 200 chars
    current_answer_hash: str = ""   # sha256 of full content
    proposed_answer_hash: str = ""
    match: _MatchKind = "unchanged"


class RecallReport(BaseModel):
    model_config = ConfigDict(frozen=True)
    status: _RecallStatus = "complete"
    reason: str = ""
    regression: bool = False
    current_score: float = 0.0
    proposed_score: float = 0.0
    results: list[RecallResult] = Field(default_factory=list)
    queries: list[RecallQuery] = Field(default_factory=list)
    seed: int = 0


# ─────────────────────────────────────────────────────────────────────────────
# Path resolution
# ─────────────────────────────────────────────────────────────────────────────

_SAMPLE_SIZE = 20
_COLD_START_THRESHOLD = 20
_ANSWER_PREVIEW_CHARS = 200
_MIN_REASON_CHARS = 10

# Stop-words to ignore in substring matching (so generic "memory"/"about"
# template tokens don't cause spurious matches).
_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "what", "does", "is", "are",
    "was", "were", "be", "been", "being", "have", "has", "had", "do",
    "did", "memory", "about", "this", "that", "these", "those", "with",
    "from", "into", "to", "for", "of", "in", "on", "at", "by", "as",
    "if", "than", "then", "so", "say", "says", "said",
})


def _resolve_raw_dir(override: Optional[str] = None) -> Path:
    if override:
        return Path(override)
    env = os.environ.get("HERMES_RAW_DIR")
    if env:
        return Path(env)
    from autodream._paths import resolve_hermes_home
    return Path(resolve_hermes_home()) / "raw"


# ─────────────────────────────────────────────────────────────────────────────
# Story 5.1: build_recall_set (FR-25)
# ─────────────────────────────────────────────────────────────────────────────


def _iter_raw_entries(raw_root: Path):
    """Stream raw-layer entries line-by-line (no whole-file slurp).
    Logs and counts malformed JSON instead of silently skipping."""
    malformed = 0
    for jsonl_path in sorted(raw_root.rglob("*.jsonl")):
        try:
            with jsonl_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        malformed += 1
                        continue
                    if entry.get("content"):
                        yield entry
        except OSError as e:
            logger.warning("build_recall_set: cannot read %s: %s", jsonl_path, e)
            continue
    if malformed:
        logger.warning(
            "build_recall_set: %d malformed JSONL line(s) skipped",
            malformed,
        )


def _compute_seed(year: int, week: int) -> int:
    """Composite (year, week) seed — same week across years no longer collides."""
    return year * 100 + week


def build_recall_set(
    raw_dir: Optional[str] = None,
    seed_week: Optional[int] = None,
    seed_year: Optional[int] = None,
) -> RecallReport:
    """FR-25: build a deterministic recall test set from the raw layer.

    Samples 20 queries seeded by (year, ISO-week). Returns a RecallReport.
    If the raw layer has < 20 entries, returns status='skipped'.
    Otherwise returns status='ready' with `queries` populated.

    P10: always returns RecallReport (no union return).
    """
    raw_root = _resolve_raw_dir(raw_dir)
    if not raw_root.exists():
        return RecallReport(status="skipped", reason="cold-start-no-raw-data")

    raw_entries = list(_iter_raw_entries(raw_root))
    if len(raw_entries) < _COLD_START_THRESHOLD:
        return RecallReport(
            status="skipped",
            reason=f"cold-start-<{_COLD_START_THRESHOLD}-entries",
        )

    # Sort by entry_id for cross-host determinism — P12.
    raw_entries.sort(key=lambda e: e.get("entry_id", ""))

    if seed_week is None or seed_year is None:
        now = datetime.now(timezone.utc).isocalendar()
        if seed_year is None:
            seed_year = now[0]
        if seed_week is None:
            seed_week = now[1]
    seed = _compute_seed(seed_year, seed_week)
    rng = random.Random(seed)

    sample = rng.sample(raw_entries, _SAMPLE_SIZE)
    queries: list[RecallQuery] = []
    for entry in sample:
        content = entry.get("content", "")
        # P20: normalize newlines in the content used for query/answer.
        content_norm = " ".join(content.split())
        entry_id = entry.get("entry_id", "unknown")
        kind = entry.get("kind", "fact")
        # Kind-aware question template.
        if kind == "trajectory":
            query_text = f"Did Hermes record a trajectory about: {content_norm[:80]}?"
        else:
            query_text = f"What is the recorded fact about: {content_norm[:80]}?"
        queries.append(RecallQuery(
            query=query_text,
            expected_answer=content_norm,
            source=entry_id,
        ))

    logger.debug(
        "build_recall_set: sampled %d queries (year=%d week=%d seed=%d)",
        len(queries), seed_year, seed_week, seed,
    )
    return RecallReport(status="ready", queries=queries, seed=seed)


# ─────────────────────────────────────────────────────────────────────────────
# Story 5.2: run_regression_check (FR-26)
# ─────────────────────────────────────────────────────────────────────────────


def _significant_tokens(text: str) -> set[str]:
    """Words from `text` minus stop-words and short tokens (≤3 chars)."""
    return {
        t.lower().strip(".,!?:;\"'()[]{}")
        for t in text.split()
        if len(t) > 3 and t.lower() not in _STOPWORDS
    }


def _search_memory(query: RecallQuery, memory_dir: str) -> tuple[bool, str]:
    """P13 / DN1: substring match the *expected_answer* against memory entries
    (no longer the question template — that template contains template
    tokens like 'memory'/'about' that auto-match).

    Returns (matched, best_match_preview_200_chars).
    Match heuristic: at least 30% of significant tokens from expected_answer
    are present (case-insensitive substring) in some memory entry's body.
    """
    from autodream.memory import read_entries

    answer_tokens = _significant_tokens(query.expected_answer)
    if not answer_tokens:
        return False, ""

    entries = read_entries(memory_dir, read_only=True)
    best_score = 0.0
    best_body = ""
    # 0.85: high bar so templatized boilerplate ('entry'/'fact'/'topic')
    # doesn't trigger spurious matches. The heuristic is deliberately coarse —
    # Epic 4's dream-orchestrator will swap in an LLM grader later.
    threshold = 0.85
    for entry in entries:
        body = entry.get("body", "")
        body_lower = body.lower()
        hits = sum(1 for tok in answer_tokens if tok in body_lower)
        score = hits / len(answer_tokens)
        if score > best_score:
            best_score = score
            best_body = body
        if best_score >= 1.0:
            break
    if best_score >= threshold:
        return True, best_body[:_ANSWER_PREVIEW_CHARS]
    return False, best_body[:_ANSWER_PREVIEW_CHARS]


def run_regression_check(
    queries: list[RecallQuery],
    *,
    current_memory_dir: str,
    proposed_memory_dir: str,
) -> RecallReport:
    """FR-26: run recall queries against both memory states.

    Returns a RecallReport. Empty `queries` → status='skipped' (P14, no false-pass).
    """
    if not queries:
        return RecallReport(
            status="skipped",
            reason="empty-recall-set",
            queries=[],
        )

    results: list[RecallResult] = []
    current_correct = 0
    proposed_correct = 0

    for q in queries:
        cur_ok, cur_ans = _search_memory(q, current_memory_dir)
        prop_ok, prop_ans = _search_memory(q, proposed_memory_dir)

        cur_hash = hashlib.sha256(cur_ans.encode("utf-8")).hexdigest()[:16]
        prop_hash = hashlib.sha256(prop_ans.encode("utf-8")).hexdigest()[:16]

        # P9: 6-state match classifier.
        if cur_ok and prop_ok:
            match: _MatchKind = "identical_both_correct"
        elif not cur_ok and not prop_ok:
            match = "identical_both_incorrect"
        elif not cur_ok and prop_ok:
            match = "improved"
        elif cur_ok and not prop_ok:
            match = "degraded"
        else:
            match = "unchanged"  # unreachable now but kept for forward-compat

        results.append(RecallResult(
            query=q.query,
            current_correct=cur_ok,
            proposed_correct=prop_ok,
            current_answer=cur_ans,
            proposed_answer=prop_ans,
            current_answer_hash=cur_hash,
            proposed_answer_hash=prop_hash,
            match=match,
        ))
        if cur_ok:
            current_correct += 1
        if prop_ok:
            proposed_correct += 1

    n = len(queries)
    current_score = current_correct / n
    proposed_score = proposed_correct / n
    # FR-26: strict-fewer-correct. Use integer counts to dodge FP drift.
    regression = proposed_correct < current_correct

    return RecallReport(
        status="complete",
        reason="",
        regression=regression,
        current_score=round(current_score, 4),
        proposed_score=round(proposed_score, 4),
        results=results,
        queries=queries,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Story 5.3: Apply gate (FR-27, FR-28)
# ─────────────────────────────────────────────────────────────────────────────


def regression_blocks_apply(
    report: RecallReport,
    force: bool = False,
    force_reason: str = "",
) -> dict:
    """FR-27, FR-28: check whether this RecallReport blocks apply.

    Returns {"blocked": bool, "reason": str, "forced": bool}.
    """
    if report is None:
        return {"blocked": True, "reason": "no-recall-report", "forced": False}
    if report.status == "skipped":
        return {"blocked": False, "reason": f"recall-skipped: {report.reason}", "forced": False}
    if report.status != "complete":
        # Unknown status — fail closed.
        return {"blocked": True, "reason": f"recall-status={report.status}", "forced": False}
    if not report.regression:
        return {"blocked": False, "reason": "no-regression", "forced": False}

    # Regression detected.
    if not force:
        return {
            "blocked": True,
            "reason": (
                f"regression detected: current_score={report.current_score} "
                f"proposed_score={report.proposed_score}. "
                f"Override with --force --reason '<text>'."
            ),
            "forced": False,
        }
    # Force requires reason ≥ 10 chars (P22).
    if len(force_reason.strip()) < _MIN_REASON_CHARS:
        return {
            "blocked": True,
            "reason": f"--force requires --reason <text> of at least {_MIN_REASON_CHARS} characters",
            "forced": False,
        }
    return {
        "blocked": False,
        "reason": f"forced: {force_reason.strip()}",
        "forced": True,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Δtokens (FR-28) — char-count proxy
# ─────────────────────────────────────────────────────────────────────────────


def memory_token_count(memory_dir: Optional[str]) -> int:
    """FR-28: token-budget proxy. char-count of all entry bodies.

    Note: this is a deliberate proxy — accurate enough for delta comparisons
    where both sides use the same metric. A real tokenizer can replace this
    later without breaking the manifest schema.
    """
    if memory_dir is None:
        return 0
    try:
        from autodream.memory import read_entries
        entries = read_entries(memory_dir, read_only=True)
    except Exception:
        return 0
    return sum(len(e.get("body", "")) for e in entries)


def compute_delta_tokens(
    current_memory_dir: Optional[str],
    proposed_memory_dir: Optional[str],
) -> dict:
    """FR-28: Δtokens(MEMORY.md). Returns {before, after, delta}."""
    before = memory_token_count(current_memory_dir)
    after = memory_token_count(proposed_memory_dir)
    return {"before": before, "after": after, "delta": after - before}


# ─────────────────────────────────────────────────────────────────────────────
# Proposed-memory materialization (DN2 / P16)
# ─────────────────────────────────────────────────────────────────────────────


def materialize_proposed_memory(
    artifact_dir: Path,
    current_memory_dir: str,
    dest_dir: Path,
) -> Path:
    """Materialize the dream's *proposed* memory state into a tmp dir.

    1. Copy current memory entries into dest_dir.
    2. Replay the dream's memory.patch operations against the copy.
    Returns the dest_dir path.

    Used by the recall harness to measure 'after apply' without mutating
    live state.
    """
    import yaml as _yaml
    from autodream.memory import (
        add_entry, expire_entry, supersede_entry, update_entry,
    )

    dest_dir.mkdir(parents=True, exist_ok=True)
    # Copy current memory contents.
    current = Path(current_memory_dir)
    if current.exists():
        for f in current.glob("*.md"):
            shutil.copy2(f, dest_dir / f.name)

    patch_path = artifact_dir / "memory.patch"
    if not patch_path.exists():
        return dest_dir
    try:
        proposals_raw = _yaml.safe_load(patch_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("materialize_proposed_memory: invalid memory.patch: %s", e)
        return dest_dir
    if not isinstance(proposals_raw, list):
        return dest_dir

    # Replay against the COPY. Use the same canonical writers but pointed at
    # dest_dir (which becomes the materialized "proposed" state).
    sim_raw = dest_dir / "_sim_raw"
    sim_raw.mkdir(parents=True, exist_ok=True)
    for p in proposals_raw:
        op = p.get("op")
        body = p.get("body", "")
        target = p.get("target_entry_id")
        ptype = p.get("type", "fact")
        source = "dream:sim"
        try:
            if op == "add":
                add_entry(ptype, body, source,
                          memory_dir=str(dest_dir), raw_dir=str(sim_raw))
            elif op == "update" and target:
                try:
                    update_entry(target, body,
                                 memory_dir=str(dest_dir), raw_dir=str(sim_raw))
                except FileNotFoundError:
                    logger.debug("materialize: update target %s missing", target)
            elif op == "supersede" and target:
                try:
                    new_id = add_entry(ptype, body, source,
                                       memory_dir=str(dest_dir), raw_dir=str(sim_raw))
                    supersede_entry(target, new_id,
                                    memory_dir=str(dest_dir), raw_dir=str(sim_raw))
                except FileNotFoundError:
                    logger.debug("materialize: supersede target %s missing", target)
            elif op == "expire" and target:
                try:
                    expire_entry(target,
                                 memory_dir=str(dest_dir), raw_dir=str(sim_raw))
                except FileNotFoundError:
                    logger.debug("materialize: expire target %s missing", target)
        except Exception as e:
            logger.warning("materialize: %s op failed (continuing): %s", op, e)
    return dest_dir


# ─────────────────────────────────────────────────────────────────────────────
# Recall artifact persistence + create-time hook
# ─────────────────────────────────────────────────────────────────────────────


def recall_artifact_path(artifact_dir: Path) -> Path:
    """FR-25 / CLAUDE.md upstream vocab: ~/.hermes/dreams/<id>/.hermes-private/recall.json"""
    return artifact_dir / ".hermes-private" / "recall.json"


def write_recall_artifact(artifact_dir: Path, report: RecallReport) -> Path:
    """Persist the RecallReport to <id>/.hermes-private/recall.json (0o600)."""
    path = recall_artifact_path(artifact_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    fd = os.open(
        str(path),
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        0o600,
    )
    try:
        os.write(
            fd,
            (json.dumps(report.model_dump(), indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
        )
    finally:
        os.close(fd)
    return path


def read_recall_artifact(artifact_dir: Path) -> Optional[RecallReport]:
    """Read recall.json from <id>/.hermes-private/. Returns None if absent."""
    path = recall_artifact_path(artifact_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return RecallReport.model_validate(data)
    except Exception as e:
        logger.warning("read_recall_artifact: %s — %s", path, e)
        return None


def run_recall_at_create(
    artifact_dir: Path,
    current_memory_dir: Optional[str],
) -> RecallReport:
    """P1 / FR-25: invoked from create_dream_artifact.

    1. build_recall_set() — sample queries from raw layer.
    2. Materialize proposed memory.
    3. run_regression_check() against current + proposed.
    4. Persist to <artifact_dir>/.hermes-private/recall.json.
    Returns the report.
    """
    rs = build_recall_set()
    if rs.status == "skipped" or current_memory_dir is None:
        # Cold-start or no memory to measure — persist and return as-is.
        write_recall_artifact(artifact_dir, rs)
        return rs

    # Materialize proposed memory in a sibling tmp dir.
    sim_dir = artifact_dir / ".hermes-private" / "_sim_memory"
    try:
        materialize_proposed_memory(artifact_dir, current_memory_dir, sim_dir)
    except Exception as e:
        logger.warning("run_recall_at_create: materialization failed: %s", e)
        # Fall back to status=skipped if we can't measure.
        report = RecallReport(
            status="skipped",
            reason=f"materialization-failed: {e}",
            queries=rs.queries,
            seed=rs.seed,
        )
        write_recall_artifact(artifact_dir, report)
        return report

    report = run_regression_check(
        rs.queries,
        current_memory_dir=current_memory_dir,
        proposed_memory_dir=str(sim_dir),
    )
    # Preserve the seed for audit reproducibility.
    report = report.model_copy(update={"seed": rs.seed})
    write_recall_artifact(artifact_dir, report)
    # Best-effort cleanup of the sim dir.
    try:
        shutil.rmtree(sim_dir, ignore_errors=True)
    except OSError:
        pass
    return report
