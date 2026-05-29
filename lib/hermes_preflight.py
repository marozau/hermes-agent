"""
hermes_preflight — Pre-task context injection (Epic 7)

FR-29: Plugin scaffolding for `pre_llm_call` (handler in ~/.hermes/plugins/preflight/).
FR-30: FTS5 retrieval + 4-factor ranking (real BM25 from row["rank"]).
FR-31: Sentinel-wrapped user-role addendum, appended AFTER user message.
FR-32: Skip ladder — cheap first; warm-up <3 turns; --preflight=off; etc.
FR-33: valid_until-past trajectories excluded via hermes_memory lookup.
FR-34: One JSONL per invocation (fire OR skip) to preflight/log/<date>.jsonl.
FR-35: /preflight [task] force-fires via the standalone CLI.

CLAUDE.md staged rollout: `mode: shadow` (telemetry only) → `mode: live` (inject).
Hard Invariant #12: heads-up appends BELOW the cache breakpoints to preserve cache.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Data models
# ─────────────────────────────────────────────────────────────────────────────


class SkipReason(str, Enum):
    DISABLED = "disabled"
    SHADOW_MODE_DO_NOT_INJECT = "shadow-mode"  # telemetry written; no injection
    ALREADY_FIRED = "already-fired-for-this-message"
    USER_DISABLED = "user-disabled"
    SMALL_NO_DOMAIN = "small-and-no-domain"
    NO_TRAJECTORIES = "no-trajectories-found"
    WITHIN_SKIP_WINDOW = "within-skip-window"
    WARM_UP = "warm-up-less-than-3-turns"


@dataclass
class IntentResult:
    domains: list[str] = field(default_factory=list)
    complexity: bool = False
    intent_hash: str = ""


@dataclass
class TrajectoryHit:
    id: str
    content: str
    category: str = "unknown"
    domain: str = "unknown"
    bm25_score: float = 0.0
    timestamp: float = 0.0  # Unix timestamp
    has_resolution: bool = False
    score: float = 0.0
    entry_id: str = ""


@dataclass
class PreflightGate:
    enabled: bool = True
    session_id: str = ""
    turn_count: int = 0          # P12 / FR-32 warm-up tracking
    _fired_hashes: OrderedDict = field(default_factory=OrderedDict)  # P17 bounded
    _last_fired_at: float = 0.0
    _last_invoked_at: float = 0.0  # P17b: monotonic timestamp of last increment_turn()
    _MAX_FIRED_HASHES = 256

    def mark_fired(self, message_hash: str) -> None:
        self._fired_hashes[message_hash] = time.time()
        if len(self._fired_hashes) > self._MAX_FIRED_HASHES:
            self._fired_hashes.popitem(last=False)
        self._last_fired_at = time.time()

    def increment_turn(self) -> None:
        self.turn_count += 1
        self._last_invoked_at = time.time()


@dataclass
class PreflightTelemetry:
    session_id: str
    intent_hash: str
    domains: list[str]
    complexity_hit: bool
    skip_reason: Optional[str]
    raw_hits: int
    top_ids: list[str]
    scores: list[float]
    elapsed_ms: float
    mode: str = "live"       # P6: telemetry knows whether injection happened
    cited_entry_ids: list[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Path resolution + config
# ─────────────────────────────────────────────────────────────────────────────


def _hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes"))


def _preflight_dir() -> Path:
    return _hermes_home() / "preflight"


def _resolve_mode() -> str:
    """DN4: HERMES_PREFLIGHT_MODE env > config.yaml > 'shadow' default.
    Returns 'shadow' or 'live'."""
    env = os.environ.get("HERMES_PREFLIGHT_MODE", "").strip().lower()
    if env in ("shadow", "live"):
        return env
    cfg = _load_config()
    mode = str(cfg.get("mode", "shadow")).strip().lower()
    return mode if mode in ("shadow", "live") else "shadow"


def _load_config() -> dict:
    try:
        import yaml as _yaml
    except ImportError:
        return {}
    cfg_path = _preflight_dir() / "config.yaml"
    if not cfg_path.exists():
        return {}
    try:
        return _yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# Story 7.2: Intent classifier (P15 word-boundary match)
# ─────────────────────────────────────────────────────────────────────────────

_COMPLEXITY_KEYWORDS = {
    "refactor", "migrate", "build", "install", "configure", "set up", "setup",
    "deploy", "release", "rebase", "merge", "restore", "integrate",
    "replace", "rewrite", "debug", "optimize", "upgrade", "downgrade",
}

_SMALL_MESSAGE_LENGTH = 80
_COMPLEX_MESSAGE_LENGTH = 200
_WORD_RE = re.compile(r"\b[\w\-]+\b")


def _tokenize(text: str) -> set[str]:
    return {m.group(0).lower() for m in _WORD_RE.finditer(text)}


def classify_intent(
    message: str,
    vocab_path: Optional[str] = None,
) -> IntentResult:
    """FR-32 / P15: word-boundary match for vocab AND complexity keywords."""
    # Load vocab
    vocab_words: set[str] = set()
    if vocab_path:
        vp = Path(vocab_path)
        if vp.exists():
            try:
                vocab_words = {
                    w.strip().lower()
                    for w in vp.read_text(encoding="utf-8", errors="replace").splitlines()
                    if w.strip() and not w.lstrip().startswith("#")
                }
            except OSError:
                vocab_words = set()

    msg_tokens = _tokenize(message)

    # P15: word-boundary intersection
    domains = sorted(vocab_words & msg_tokens)

    # Complexity check (word-boundary).
    msg_lower = message.lower()
    complexity_hits = 0
    for kw in _COMPLEXITY_KEYWORDS:
        if " " in kw:
            # Multi-word kw — exact phrase match.
            if re.search(r"\b" + re.escape(kw) + r"\b", msg_lower):
                complexity_hits += 1
        else:
            if kw in msg_tokens:
                complexity_hits += 1

    complexity = len(message) > _COMPLEX_MESSAGE_LENGTH or complexity_hits >= 2
    intent_hash = hashlib.sha256(message.encode("utf-8")).hexdigest()[:16]

    return IntentResult(domains=domains, complexity=complexity, intent_hash=intent_hash)


# ─────────────────────────────────────────────────────────────────────────────
# Story 7.6 / FR-32: Skip ladder (cheap-first; P12 warm-up; P14 token match)
# ─────────────────────────────────────────────────────────────────────────────


_PREFLIGHT_OFF_RE = re.compile(r"--preflight=off\b", re.IGNORECASE)


def evaluate_skip_ladder(
    gate: PreflightGate,
    message: str,
    *,
    message_hash: Optional[str] = None,
    force: bool = False,
    intent: Optional[IntentResult] = None,
    vocab_path: Optional[str] = None,
    session_search_fn: Optional[Callable] = None,
) -> Optional[SkipReason]:
    """Cheap-first ladder; returns None if preflight should proceed.

    P16 / P12: warm-up + bounded-state checks; P14 token-match on --preflight=off.
    """
    if force:
        return None  # /preflight bypasses

    if not gate.enabled:
        return SkipReason.DISABLED

    # P14: token-match — avoids `--preflight=offline` false-positive.
    if _PREFLIGHT_OFF_RE.search(message):
        return SkipReason.USER_DISABLED

    if message_hash and message_hash in gate._fired_hashes:
        return SkipReason.ALREADY_FIRED

    # P12: warm-up. Read threshold from config, default 3.
    warmup = int(_load_config().get("warmup_turns", 3))
    if gate.turn_count < warmup:
        return SkipReason.WARM_UP

    # Within-skip-window (10 min).
    if gate._last_fired_at > 0:
        if (time.time() - gate._last_fired_at) < 600:
            return SkipReason.WITHIN_SKIP_WINDOW

    # Classify only if not already done by caller (P16: dedupe).
    if intent is None:
        intent = classify_intent(message, vocab_path)
    if not intent.complexity and not intent.domains and len(message) < _SMALL_MESSAGE_LENGTH:
        return SkipReason.SMALL_NO_DOMAIN

    if intent.domains and session_search_fn:
        hits = retrieve_trajectories(intent.domains, session_search_fn, limit=1)
        if not hits:
            return SkipReason.NO_TRAJECTORIES

    return None


# ─────────────────────────────────────────────────────────────────────────────
# Story 7.3: FTS5 retrieval + 4-factor ranking (P9 real BM25, P10 timestamp)
# ─────────────────────────────────────────────────────────────────────────────


def _resolve_entry_timestamp(entry_id: str) -> Optional[float]:
    """P10 / DN5: look up trajectory entry's created_at via hermes_memory."""
    if not entry_id:
        return None
    try:
        from lib.hermes_memory import read_entries
    except Exception:
        return None
    try:
        for e in read_entries(read_only=True):
            if e.get("id") == entry_id:
                ca = e.get("created_at")
                if ca:
                    return datetime.fromisoformat(str(ca).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None
    return None


def _fts5_safe_term(term: str) -> str:
    """P24: quote FTS5 terms to neutralize metacharacters."""
    return '"' + term.replace('"', '""') + '"'


def retrieve_trajectories(
    domains: list[str],
    session_search_fn: Callable,
    limit: int = 20,
) -> list[TrajectoryHit]:
    """FR-30 / P9 / P10: FTS5 OR-query + real rank + timestamp resolution."""
    if not domains:
        return []

    safe_terms = " OR ".join(_fts5_safe_term(d) for d in domains)
    query = f"TRAJECTORY ({safe_terms})"
    try:
        results = session_search_fn(query, limit)
    except Exception as e:
        logger.warning("retrieve_trajectories: search failed: %s", e)
        return []

    hits: list[TrajectoryHit] = []
    for row in results:
        content = row.get("content", "") or row.get("snippet", "")
        # Category extraction (canonical 6).
        category = "unknown"
        cat_match = re.search(
            r"\b(tool-misuse|context-overflow|hallucinated-api|"
            r"incomplete-context|edit-error|requirement-drift)\b",
            content,
        )
        if cat_match:
            category = cat_match.group(1)
        # Domain attribution
        domain = "unknown"
        cl = content.lower()
        for d in domains:
            if re.search(r"\b" + re.escape(d) + r"\b", cl):
                domain = d
                break

        # P9: real BM25 from FTS5 rank (lower abs() = better, so invert).
        raw_rank = row.get("rank")
        if raw_rank is not None:
            try:
                # SQLite FTS5 rank is negative; smaller = better. Map to (0,1].
                r = float(raw_rank)
                bm25_score = 1.0 / (1.0 + abs(r))
            except (TypeError, ValueError):
                bm25_score = 0.5
        else:
            bm25_score = 0.5

        # P10: timestamp resolution. Try row, fall back to entry_id lookup, else None.
        entry_id = row.get("entry_id", "") or row.get("id", "")
        ts_raw = row.get("timestamp")
        ts: Optional[float] = None
        if isinstance(ts_raw, (int, float)) and ts_raw > 0:
            ts = float(ts_raw)
        if ts is None and entry_id:
            ts = _resolve_entry_timestamp(entry_id)
        # Unknown timestamp → recency neutral (no boost).
        ts_for_hit = ts if ts is not None else 0.0

        hits.append(TrajectoryHit(
            id=entry_id or row.get("id", f"hit-{len(hits)}"),
            entry_id=entry_id,
            content=content[:500],
            category=category,
            domain=domain,
            bm25_score=bm25_score,
            timestamp=ts_for_hit,
            has_resolution=(("→" in content) or ("->" in content)) and "fix" in content.lower(),
        ))

    return hits


def _filter_stale_trajectories(hits: list[TrajectoryHit]) -> list[TrajectoryHit]:
    """P11 / FR-33: drop hits whose source entry has past valid_until."""
    try:
        from lib.hermes_memory import read_entries
    except Exception:
        return hits
    try:
        entries = {e["id"]: e for e in read_entries(read_only=True) if e.get("id")}
    except Exception:
        return hits

    now = datetime.now(timezone.utc)
    keep: list[TrajectoryHit] = []
    for h in hits:
        if not h.entry_id:
            keep.append(h)
            continue
        e = entries.get(h.entry_id)
        if e is None:
            keep.append(h)
            continue
        vu = e.get("valid_until")
        if vu in (None, ""):
            keep.append(h)
            continue
        try:
            dt = datetime.fromisoformat(str(vu).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt >= now:
                keep.append(h)
            # else: stale, drop
        except ValueError:
            keep.append(h)  # malformed → keep, audit elsewhere
    return keep


def rank_trajectories(
    hits: list[TrajectoryHit],
    config_path: Optional[str] = None,
) -> list[TrajectoryHit]:
    """FR-30 four-factor scoring. P18: math.exp + recency clamped [0, 1]."""
    weights = {"bm25": 0.45, "recency": 0.25, "category": 0.20, "resolution": 0.10}
    category_weights: dict[str, float] = {
        "tool-misuse": 1.0,
        "incomplete-context": 0.8,
        "edit-error": 0.7,
        "context-overflow": 0.7,
        "hallucinated-api": 0.6,
        "requirement-drift": 0.6,
    }

    if config_path:
        cfg_path = Path(config_path)
        if cfg_path.exists():
            try:
                import yaml as _yaml
                cfg = _yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
                if isinstance(cfg.get("weights"), dict):
                    for k, v in cfg["weights"].items():
                        if isinstance(v, (int, float)):
                            weights[k] = float(v)
                if isinstance(cfg.get("category_weights"), dict):
                    for k, v in cfg["category_weights"].items():
                        if isinstance(v, (int, float)):
                            category_weights[k] = float(v)
            except Exception as e:
                logger.warning("rank_trajectories: bad config: %s", e)

    now = time.time()
    for h in hits:
        bm25_c = weights["bm25"] * h.bm25_score
        # P18: math.exp; clamp recency to [0, 1] — no future-timestamp boost.
        if h.timestamp > 0:
            age_days = max(0.0, (now - h.timestamp) / 86400.0)
            recency = min(1.0, max(0.0, math.exp(-age_days / 30.0)))
        else:
            recency = 0.0  # unknown timestamp → neutral
        rec_c = weights["recency"] * recency
        cat_c = weights["category"] * category_weights.get(h.category, 0.5)
        res_c = weights["resolution"] * (1.0 if h.has_resolution else 0.0)
        h.score = bm25_c + rec_c + cat_c + res_c

    return sorted(hits, key=lambda h: h.score, reverse=True)


# ─────────────────────────────────────────────────────────────────────────────
# Story 7.4: Dedupe + top-K (P21: enforce ≥1 same-domain entry)
# ─────────────────────────────────────────────────────────────────────────────


def dedupe_and_cap(
    ranked: list[TrajectoryHit],
    k: int = 3,
    *,
    primary_domain: Optional[str] = None,
) -> list[TrajectoryHit]:
    """Dedupe by (category, domain); apply dilution cap.

    P21: when capping, guarantee ≥1 entry whose domain matches `primary_domain`
    (the user's top classified domain), if one exists among ranked hits.
    """
    buckets: dict[tuple[str, str], list[TrajectoryHit]] = {}
    for h in ranked:
        buckets.setdefault((h.category, h.domain), []).append(h)
    deduped = [sorted(v, key=lambda x: x.score, reverse=True)[0] for v in buckets.values()]
    deduped.sort(key=lambda x: x.score, reverse=True)

    unique_domains = {h.domain for h in deduped}
    if len(unique_domains) > k:
        max_domains = max(1, 2 * k // 3)
        seen: set[str] = set()
        # P21: pin a same-domain entry first if available.
        pinned: list[TrajectoryHit] = []
        if primary_domain:
            for h in deduped:
                if h.domain == primary_domain:
                    pinned.append(h)
                    seen.add(h.domain)
                    break
        capped: list[TrajectoryHit] = list(pinned)
        for h in deduped:
            if h in capped:
                continue
            if h.domain not in seen:
                if len(seen) >= max_domains:
                    continue
                seen.add(h.domain)
            capped.append(h)
        deduped = capped

    return deduped[:k]


# ─────────────────────────────────────────────────────────────────────────────
# Story 7.5: Sentinel formatter (FR-31)
# ─────────────────────────────────────────────────────────────────────────────


def _extract_afx(content: str) -> str:
    """Attempt → failure → fix extractor. Preserves middle segment for 3-arrow chains."""
    parts = [p.strip() for p in re.split(r"\s*(?:→|->)\s*", content) if p.strip()]
    parts = [p.replace("TRAJECTORY:", "").strip() for p in parts]
    if not parts:
        return content[:120]
    if len(parts) >= 3:
        return f"{parts[0][:60]} → {parts[1][:60]} → {parts[-1][:60]}"
    if len(parts) == 2:
        return f"{parts[0][:80]} → {parts[1][:80]}"
    return parts[0][:120]


def format_heads_up(hits: list[TrajectoryHit]) -> str:
    """FR-31: sentinel-wrapped user-role addendum."""
    if not hits:
        return (
            "<preflight-heads-up>\n"
            "No relevant past failures found for this task.\n"
            "Apply standard verification patterns. If they don't fit, ignore them.\n"
            "</preflight-heads-up>"
        )
    lines = ["<preflight-heads-up>", ""]
    for h in hits:
        if h.timestamp > 0:
            date_str = datetime.fromtimestamp(h.timestamp, tz=timezone.utc).strftime("%Y-%m-%d")
        else:
            date_str = "unknown"
        afx = _extract_afx(h.content)
        lines.append(
            f"[{h.category} · {h.domain}] {afx}. "
            f"(last seen: {date_str}, similarity {h.score:.2f})"
        )
    lines.append("\nTrajectory IDs: " + ", ".join(str(h.id) for h in hits))
    lines.append(
        "\nApply these patterns proactively. "
        "If they don't fit the actual task, ignore them."
    )
    lines.append("</preflight-heads-up>")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Story 7.7: Telemetry + citation persistence (P19, P25, DN3)
# ─────────────────────────────────────────────────────────────────────────────


def _atomic_append(path: Path, line: str) -> None:
    """P19: atomic owner-only append."""
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


def write_preflight_telemetry(
    telemetry: PreflightTelemetry,
    log_dir: Optional[str] = None,
) -> None:
    """FR-34, NFR-17: one JSONL row per invocation (fire OR skip)."""
    if log_dir is None:
        log_dir = str(_preflight_dir() / "log")
    log_path = Path(log_dir)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    row = json.dumps({
        "ts": datetime.now(timezone.utc).isoformat(),
        "session_id": telemetry.session_id,
        "intent_hash": telemetry.intent_hash,
        "domains": telemetry.domains,
        "complexity_hit": telemetry.complexity_hit,
        "skip_reason": telemetry.skip_reason,
        "raw_hits": telemetry.raw_hits,
        "top_ids": telemetry.top_ids,
        "scores": telemetry.scores,
        "elapsed_ms": round(telemetry.elapsed_ms, 2),
        "mode": telemetry.mode,
        "cited_entry_ids": telemetry.cited_entry_ids,
    }, ensure_ascii=False, sort_keys=True) + "\n"
    _atomic_append(log_path / f"{today}.jsonl", row)


def record_verify_citations(
    session_id: str,
    intent_hash: str,
    cited_ids: list[str],
    log_dir: Optional[str] = None,
) -> None:
    """Append a verify_citation event row keyed on (session_id, intent_hash).

    Item-7 hit rate is computed by joining each preflight row with the
    verify_citation event for the same (session_id, intent_hash) and asking
    whether `preflight_row.top_ids[0]` appears in `verify_row.cited_ids`.

    Append-only: never mutates the original preflight row. An empty
    `cited_ids` is a meaningful signal (verify ran but consulted nothing
    from preflight's top-K) and is recorded as such.
    """
    if log_dir is None:
        log_dir = str(_preflight_dir() / "log")
    log_path = Path(log_dir)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    row = json.dumps({
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": "verify_citation",
        "session_id": session_id,
        "intent_hash": intent_hash,
        "cited_ids": list(cited_ids),
    }, ensure_ascii=False, sort_keys=True) + "\n"
    _atomic_append(log_path / f"{today}.jsonl", row)


def persist_citations(session_id: str, entry_ids: list[str]) -> None:
    """DN3 / Story 7.7: persist preflight citations for trajectory writer."""
    if not entry_ids:
        return
    p = _preflight_dir() / "last-cited.json"
    p.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    try:
        existing = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except Exception:
        existing = {}
    existing[session_id] = {
        "entry_ids": entry_ids,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    tmp = p.with_suffix(p.suffix + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, (json.dumps(existing, indent=2) + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    os.replace(tmp, p)


def read_citations(session_id: str) -> list[str]:
    """Trajectory writer (or verify) reads citations to compute `match:` field."""
    p = _preflight_dir() / "last-cited.json"
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    return list(data.get(session_id, {}).get("entry_ids", []))


# ─────────────────────────────────────────────────────────────────────────────
# Plugin entry point (Story 7.1, FR-29)
# ─────────────────────────────────────────────────────────────────────────────

_gates: "OrderedDict[str, PreflightGate]" = OrderedDict()
_GATES_MAX = 1024  # P17 LRU bound


def _materialize_gate_from_log(session_id: str, enabled: bool) -> PreflightGate:
    """Reconstruct gate state from telemetry log (Bug 2 fix).
    Telemetry log is the source of truth — gates.json removed."""
    gate = PreflightGate(enabled=enabled, session_id=session_id)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_path = _preflight_dir() / "log" / f"{today}.jsonl"
    if not log_path.exists():
        return gate
    try:
        for line in log_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("session_id") != session_id:
                continue
            gate.turn_count += 1
            if not row.get("skip_reason"):  # fired
                h = row.get("intent_hash") or ""
                if h:
                    gate._fired_hashes[h] = 0.0
                try:
                    fired_dt = datetime.fromisoformat(row["ts"])
                    gate._last_fired_at = max(gate._last_fired_at, fired_dt.timestamp())
                except (KeyError, ValueError):
                    pass
    except OSError:
        pass
    return gate


def get_or_create_gate(session_id: str, enabled: bool = True) -> PreflightGate:
    if session_id in _gates:
        gate = _gates[session_id]
        gate.enabled = enabled
        _gates.move_to_end(session_id)
        return gate
    # Bug 2 fix: derive gate state from telemetry log instead of gates.json.
    gate = _materialize_gate_from_log(session_id, enabled)
    if len(_gates) >= _GATES_MAX:
        _gates.popitem(last=False)

    # P17b: carry forward turn_count from recently-active gates.
    # Context compression creates a new session_id mid-conversation.
    # Without this, the warm-up gate resets to 0 on every compression.
    carried = 0
    _now = time.monotonic()
    for _sid, _gate in reversed(list(_gates.items())):
        if _gate._last_invoked_at > 0 and (_now - _gate._last_invoked_at) < 30.0:
            carried = _gate.turn_count
            break

    gate = PreflightGate(enabled=enabled, session_id=session_id)
    if carried > 0:
        gate.turn_count = carried
    _gates[session_id] = gate
    return gate


def should_run_preflight(
    session_id: str,
    message: str,
    *,
    vocab_path: Optional[str] = None,
    config_path: Optional[str] = None,
    session_search_fn: Optional[Callable] = None,
    force: bool = False,
    log_dir: Optional[str] = None,
) -> "tuple[PreflightGate, Optional[SkipReason], Optional[str]]":
    """FR-29 entry point. Returns (gate, skip_reason, heads_up_text_or_None).

    In shadow mode, telemetry is written but heads_up is None (no injection).
    Telemetry is emitted on EVERY call (fire OR skip; P13 / FR-34).
    """
    # Default paths if not provided.
    if vocab_path is None:
        v = _preflight_dir() / "domain-vocab.txt"
        if v.exists():
            vocab_path = str(v)
    if config_path is None:
        c = _preflight_dir() / "config.yaml"
        if c.exists():
            config_path = str(c)

    cfg = _load_config()
    enabled_default = bool(cfg.get("enabled", True))
    mode = _resolve_mode()

    gate = get_or_create_gate(session_id, enabled=enabled_default)
    gate.increment_turn()  # P12: count every invocation

    message_hash = hashlib.sha256(message.encode("utf-8")).hexdigest()[:16]

    # P16: classify once and reuse.
    intent = classify_intent(message, vocab_path)

    t0 = time.perf_counter()
    reason = evaluate_skip_ladder(
        gate, message, message_hash=message_hash,
        force=force, intent=intent, vocab_path=vocab_path,
        session_search_fn=session_search_fn,
    )

    if reason is not None:
        elapsed = (time.perf_counter() - t0) * 1000
        # P13 / FR-34: emit telemetry on skip path.
        write_preflight_telemetry(PreflightTelemetry(
            session_id=session_id,
            intent_hash=intent.intent_hash,
            domains=intent.domains,
            complexity_hit=intent.complexity,
            skip_reason=reason.value,
            raw_hits=0,
            top_ids=[],
            scores=[],
            elapsed_ms=elapsed,
            mode=mode,
        ), log_dir=log_dir)
        return gate, reason, None

    # Fire path.
    hits = retrieve_trajectories(
        intent.domains, session_search_fn or _noop_search,
    )
    hits = _filter_stale_trajectories(hits)  # P11 / FR-33
    ranked = rank_trajectories(hits, config_path)
    primary_domain = intent.domains[0] if intent.domains else None
    deduped = dedupe_and_cap(ranked, primary_domain=primary_domain)
    heads_up = format_heads_up(deduped)

    elapsed = (time.perf_counter() - t0) * 1000
    cited_ids = [h.entry_id or h.id for h in deduped]

    # P25 / P20: telemetry BEFORE mark_fired (so a telemetry failure leaves
    # the gate unfired and retry replays cleanly).
    #
    # cited_entry_ids stays empty at preflight emit time. The verify-cited
    # follow-through (record_verify_citations) populates it later by
    # appending a separate `verify_citation` event row keyed on
    # (session_id, intent_hash). Item-7 hit rate is then computed by
    # joining preflight rows with verify_citation events, NOT by reading
    # cited_entry_ids on the original row (that field is reserved for an
    # in-process verify hook that may land in a later iteration).
    write_preflight_telemetry(PreflightTelemetry(
        session_id=session_id,
        intent_hash=intent.intent_hash,
        domains=intent.domains,
        complexity_hit=intent.complexity,
        skip_reason=None,
        raw_hits=len(hits),
        top_ids=cited_ids,
        scores=[h.score for h in deduped],
        elapsed_ms=elapsed,
        mode=mode,
        cited_entry_ids=[],
    ), log_dir=log_dir)

    if cited_ids:
        persist_citations(session_id, cited_ids)

    gate.mark_fired(message_hash)

    # DN4: shadow mode → telemetry-only; do not return heads_up for injection.
    if mode == "shadow":
        return gate, SkipReason.SHADOW_MODE_DO_NOT_INJECT, None

    return gate, None, heads_up


def _noop_search(query: str, limit: int = 20) -> list[dict]:
    return []
