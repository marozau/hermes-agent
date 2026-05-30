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
    # Story 8.3: type and source for scoring
    entry_type: str = "unknown"     # typed entry type (preference, fact, etc.)
    entry_source: str = ""          # frontmatter source (e.g. "user-correction")
    # Story 9.1: access count for strength factor
    access_count: int = 0           # from frontmatter; 0 if missing


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
    # Story 8.1: YAKE enrichment telemetry
    intent_source: str = ""       # "rule-based" | "yake-fallback"
    yake_terms: list[str] = field(default_factory=list)
    # Story 8.5: hard cap telemetry
    truncated_count: int = 0      # entries suppressed beyond top-K cap
    # Story 8.4: embedding telemetry
    embedding_source: str = ""     # "deepseek" | "openai" | "cache" | "failed" | ""
    # Story 8.6: reranker telemetry
    rerank_outcome: str = ""       # "disabled" | "ok" | "parse-failed" | "failed" | ""


# ─────────────────────────────────────────────────────────────────────────────
# Path resolution + config
# ─────────────────────────────────────────────────────────────────────────────


def _hermes_home() -> Path:
    from lib._hermes_paths import resolve_hermes_home
    return Path(resolve_hermes_home())


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
# Story 8.1: YAKE keyword enrichment for FTS queries
# ─────────────────────────────────────────────────────────────────────────────


def enrich_query_with_yake(
    base_domains: list[str],
    message: str,
) -> tuple[list[str], list[str]]:
    """Story 8.1 / AC3: OR YAKE keywords into the recall query.

    Returns (enriched_domains, yake_terms) where enriched_domains includes
    both the original domain terms and the YAKE-derived terms.
    yake_terms is the list of what was added (for telemetry).
    """
    try:
        from lib._yake import extract_keywords
        yake_terms = extract_keywords(message)[:8]
    except Exception as e:
        logger.debug("YAKE enrichment failed: %s", e)
        return base_domains, []

    if not yake_terms:
        return base_domains, []

    # Deduplicate: don't add terms already in base_domains
    existing = {d.lower() for d in base_domains}
    new_terms = [t for t in yake_terms if t.lower() not in existing]
    enriched = base_domains + new_terms
    return enriched, new_terms


# ─────────────────────────────────────────────────────────────────────────────
# Story 8.4: Hybrid recall base-score (BM25 + embedding cosine)
# ─────────────────────────────────────────────────────────────────────────────

# In-process LRU embedding cache — OrderedDict[sha256(text), vec]
_embedding_cache: "OrderedDict[str, list[float]]" = OrderedDict()
_EMBEDDING_CACHE_MAX = 1024

# Cosine similarity weights for hybrid scoring
_HYBRID_COSINE_WEIGHT = 0.7
_HYBRID_BM25_WEIGHT = 0.3


def _text_hash(text: str) -> str:
    """SHA-256 of text for cache key."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _get_cached_embedding(text: str) -> Optional[list[float]]:
    """Check LRU cache for existing embedding."""
    key = _text_hash(text)
    if key in _embedding_cache:
        _embedding_cache.move_to_end(key)
        return _embedding_cache[key]
    return None


def _cache_embedding(text: str, vec: list[float]) -> None:
    """Store embedding in LRU cache."""
    key = _text_hash(text)
    _embedding_cache[key] = vec
    if len(_embedding_cache) > _EMBEDDING_CACHE_MAX:
        _embedding_cache.popitem(last=False)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _get_embedding(text: str, workload: str = "recall_embed") -> Optional[list[float]]:
    """Get embedding for text, using cache + llm_embed. Returns None on failure."""
    cached = _get_cached_embedding(text)
    if cached is not None:
        return cached
    try:
        from lib.hermes_llm import llm_embed
        vec = llm_embed(text, workload)
        if vec is not None:
            _cache_embedding(text, vec)
        return vec
    except Exception as e:
        logger.debug("Embedding failed for text[:50]: %s", e)
        return None


def _normalize_bm25_scores(hits: list[TrajectoryHit]) -> list[float]:
    """Normalize BM25 scores to [0, 1] across the candidate set."""
    if not hits:
        return []
    scores = [h.bm25_score for h in hits]
    min_s = min(scores)
    max_s = max(scores)
    if max_s == min_s:
        return [1.0] * len(scores)
    return [(s - min_s) / (max_s - min_s) for s in scores]


def apply_hybrid_scoring(
    hits: list[TrajectoryHit],
    query_text: str,
    config: Optional[dict] = None,
) -> list[TrajectoryHit]:
    """Story 8.4: Apply hybrid BM25 + cosine scoring to hits.

    When embeddings are available:
        base = 0.7 * cosine_sim(query, candidate) + 0.3 * bm25_normalized
    When embeddings fail:
        base = bm25_normalized (fail-open)

    Returns hits with updated bm25_score (which now reflects the hybrid base).
    """
    # Check config flag
    use_embeddings = True
    if config:
        recall_cfg = config.get("recall", {})
        if isinstance(recall_cfg, dict):
            use_embeddings = bool(recall_cfg.get("use_embeddings", True))

    if not use_embeddings or not hits:
        return hits

    # Get query embedding
    query_vec = _get_embedding(query_text)
    if query_vec is None:
        # Fail-open: embedding unavailable, use pure BM25
        logger.debug("Hybrid scoring: embedding unavailable, falling back to BM25")
        return hits

    # Get candidate embeddings
    bm25_normalized = _normalize_bm25_scores(hits)
    for i, h in enumerate(hits):
        cand_vec = _get_embedding(h.content[:500])
        if cand_vec is not None:
            cos_sim = _cosine_similarity(query_vec, cand_vec)
            h.bm25_score = (
                _HYBRID_COSINE_WEIGHT * cos_sim
                + _HYBRID_BM25_WEIGHT * bm25_normalized[i]
            )
        else:
            # Candidate embedding failed — use pure BM25 for this one
            h.bm25_score = _HYBRID_BM25_WEIGHT * bm25_normalized[i]

    return hits


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


# Cache for entry metadata (type, source, access_count) — avoids repeated reads
_entry_meta_cache: "OrderedDict[str, dict]" = OrderedDict()
_ENTRY_META_CACHE_MAX = 512


def _resolve_entry_metadata(entry_id: str) -> dict:
    """Story 8.3: look up entry type, source, access_count for scoring."""
    if not entry_id:
        return {}
    if entry_id in _entry_meta_cache:
        _entry_meta_cache.move_to_end(entry_id)
        return _entry_meta_cache[entry_id]
    try:
        from lib.hermes_memory import read_entries
    except Exception:
        return {}
    try:
        for e in read_entries(read_only=True):
            if e.get("id") == entry_id:
                meta = {
                    "type": e.get("type", "unknown"),
                    "source": e.get("source", ""),
                    "access_count": e.get("access_count", 0) or 0,
                }
                if len(_entry_meta_cache) >= _ENTRY_META_CACHE_MAX:
                    _entry_meta_cache.popitem(last=False)
                _entry_meta_cache[entry_id] = meta
                return meta
    except Exception:
        pass
    return {}


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

        # Story 8.3: resolve entry metadata for type-boost scoring
        meta = _resolve_entry_metadata(entry_id) if entry_id else {}

        hits.append(TrajectoryHit(
            id=entry_id or row.get("id", f"hit-{len(hits)}"),
            entry_id=entry_id,
            content=content[:500],
            category=category,
            domain=domain,
            bm25_score=bm25_score,
            timestamp=ts_for_hit,
            has_resolution=(("→" in content) or ("->" in content)) and "fix" in content.lower(),
            # Story 8.3: entry metadata for type-boost
            entry_type=meta.get("type", "unknown"),
            entry_source=meta.get("source", ""),
            # Story 9.1: access count for strength factor
            access_count=meta.get("access_count", 0) or 0,
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
    """FR-30 four-factor scoring. P18: math.exp + recency clamped [0, 1].

    Story 8.2: recency switched from exp(-Δdays/30) to power-law
    (1 + hours_since_update) ** exponent, configurable via
    recency: { form: power_law, exponent: -0.3 } in config.yaml.
    """
    weights = {"bm25": 0.45, "recency": 0.25, "category": 0.20, "resolution": 0.10}
    category_weights: dict[str, float] = {
        "tool-misuse": 1.0,
        "incomplete-context": 0.8,
        "edit-error": 0.7,
        "context-overflow": 0.7,
        "hallucinated-api": 0.6,
        "requirement-drift": 0.6,
    }
    # Story 8.2: recency config defaults
    recency_form = "power_law"
    recency_exponent = -0.3
    # Story 8.3: type boost map defaults
    type_boosts: dict[str, float] = {
        "preference": 1.2,
        "procedure": 1.1,
        "fact": 1.0,
        "trajectory": 1.0,
        "episode": 0.8,
        "superseded": 0.2,
        "unknown": 0.6,
    }
    # Story 8.3: source boost for user-corrections
    correction_source_boost = 0.3

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
                # Story 8.2: read recency config
                rec_cfg = cfg.get("recency", {})
                if isinstance(rec_cfg, dict):
                    if rec_cfg.get("form") == "power_law":
                        recency_form = "power_law"
                    if isinstance(rec_cfg.get("exponent"), (int, float)):
                        recency_exponent = float(rec_cfg["exponent"])
                # Story 8.3: read type_boosts config
                tb = cfg.get("type_boosts")
                if isinstance(tb, dict):
                    for k, v in tb.items():
                        if isinstance(v, (int, float)):
                            type_boosts[k] = float(v)
                # Story 8.3: read correction_source_boost
                if isinstance(cfg.get("correction_source_boost"), (int, float)):
                    correction_source_boost = float(cfg["correction_source_boost"])
            except Exception as e:
                logger.warning("rank_trajectories: bad config: %s", e)

    now = time.time()
    for h in hits:
        bm25_c = weights["bm25"] * h.bm25_score

        # Story 8.2: power-law recency
        # P18: clamp recency to [0, 1] — no future-timestamp boost.
        if h.timestamp > 0:
            age_hours = max(0.0, (now - h.timestamp) / 3600.0)
            if recency_form == "power_law":
                recency = min(1.0, max(0.0, (1.0 + age_hours) ** recency_exponent))
            else:
                # Fallback to old exp form for backward compat
                age_days = max(0.0, (now - h.timestamp) / 86400.0)
                recency = min(1.0, max(0.0, math.exp(-age_days / 30.0)))
        else:
            recency = 0.0  # unknown timestamp → neutral
        rec_c = weights["recency"] * recency

        cat_c = weights["category"] * category_weights.get(h.category, 0.5)
        res_c = weights["resolution"] * (1.0 if h.has_resolution else 0.0)
        four_factor = bm25_c + rec_c + cat_c + res_c

        # Story 8.3: apply type boost and source boost
        entry_type = getattr(h, 'entry_type', 'unknown')
        type_w = type_boosts.get(entry_type, 1.0)
        source_boost = 0.0
        entry_source = getattr(h, 'entry_source', '')
        if entry_source == "user-correction":
            source_boost = correction_source_boost

        # Story 9.1: strength factor from access_count
        # (reads defensively — 0 if missing, so 8.3 ships first)
        access_count = getattr(h, 'access_count', 0) or 0
        strength = 1.0 + 0.1 * math.log(1 + access_count)

        h.score = four_factor * type_w * strength + source_boost

    return sorted(hits, key=lambda h: h.score, reverse=True)


# ─────────────────────────────────────────────────────────────────────────────
# Story 8.6: LLM reranker — preflight_rerank workload
# ─────────────────────────────────────────────────────────────────────────────

# Grep-able constant for the rerank prompt
RERANK_PROMPT = """Given this query: {intent_summary}
Which of these memories are most relevant?
Return ONLY the numbers of the top 3 most relevant, as a JSON array.

{candidates}"""

_RERANK_TOP_N = 8  # candidates to feed to reranker
_RERANK_TOP_K = 3  # final selection from reranker


def _build_rerank_candidates(hits: list[TrajectoryHit], max_body: int = 200) -> str:
    """Build the numbered candidate list for the rerank prompt."""
    lines = []
    for i, h in enumerate(hits[:_RERANK_TOP_N]):
        body = h.content[:max_body].replace("\n", " ")
        lines.append(f"[{i}] {body}")
    return "\n".join(lines)


def _parse_rerank_indices(raw_content: str, max_idx: int) -> Optional[list[int]]:
    """Parse reranker response. Returns list of valid indices or None on failure.

    Pydantic-gated: RerankIndices schema (Hard Invariant #11).
    """
    try:
        from pydantic import BaseModel, Field, conint

        class RerankIndices(BaseModel):
            indices: list[conint(ge=0, le=7)] = Field(..., max_length=3)

        # Try to extract JSON array from the response
        import json as _json
        import re as _re
        text = raw_content.strip()
        # Try direct parse first
        try:
            parsed = _json.loads(text)
            if isinstance(parsed, list):
                result = RerankIndices(indices=parsed)
                return [i for i in result.indices if i < max_idx]
        except _json.JSONDecodeError:
            pass
        # Try to find JSON array in text
        match = _re.search(r'\[[\d\s,]+\]', text)
        if match:
            parsed = _json.loads(match.group())
            result = RerankIndices(indices=parsed)
            return [i for i in result.indices if i < max_idx]
    except Exception:
        pass
    return None


def rerank_with_llm(
    hits: list[TrajectoryHit],
    query_text: str,
    *,
    config: Optional[dict] = None,
) -> tuple[list[TrajectoryHit], str]:
    """Story 8.6: LLM reranker via preflight_rerank workload.

    Returns (reranked_hits, rerank_outcome) where rerank_outcome is one of:
    - "disabled" — use_reranker is false
    - "ok" — reranker returned valid indices
    - "parse-failed" — reranker response couldn't be parsed
    - "failed" — LLM call failed (fail-open to score-based)
    - "not-enough-candidates" — fewer than 2 candidates, skip rerank

    Fail-open: on any failure, returns the original top-3 from scoring.
    """
    # Check config flag
    use_reranker = False
    if config:
        recall_cfg = config.get("recall", {})
        if isinstance(recall_cfg, dict):
            use_reranker = bool(recall_cfg.get("use_reranker", False))

    if not use_reranker:
        return hits[:_RERANK_TOP_K], "disabled"

    if len(hits) < 2:
        return hits[:_RERANK_TOP_K], "not-enough-candidates"

    # Build prompt
    candidates = _build_rerank_candidates(hits)
    prompt = RERANK_PROMPT.format(
        intent_summary=query_text[:200],
        candidates=candidates,
    )

    # Call LLM via llm_call
    try:
        from lib.hermes_llm import LLMSpec, llm_call
        spec = LLMSpec(
            workload="preflight_rerank",
            messages=[{"role": "user", "content": prompt}],
            response_model=None,  # Free-text — we parse JSON from it
            idempotency_key=f"rerank-{hashlib.sha256(query_text.encode()).hexdigest()[:16]}",
        )
        result = llm_call(spec)
        content = result.get("content", "") if isinstance(result, dict) else str(result)
    except Exception as e:
        logger.warning("Rerank LLM call failed: %s — falling back to score-based", e)
        return hits[:_RERANK_TOP_K], "failed"

    # Parse response
    indices = _parse_rerank_indices(content, len(hits))
    if indices is None:
        logger.debug("Rerank parse failed for content: %s", content[:200])
        return hits[:_RERANK_TOP_K], "parse-failed"

    # Select reranked hits
    reranked = [hits[i] for i in indices if i < len(hits)]
    if not reranked:
        return hits[:_RERANK_TOP_K], "parse-failed"

    return reranked[:_RERANK_TOP_K], "ok"


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

    Story 8.5: hard ceiling at k=3, validated from config.recall.top_k [1,3].
    """
    # Story 8.5: enforce hard ceiling
    k = min(k, 3)

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


def validate_top_k(value: int) -> int:
    """Story 8.5: validate top_k config at startup. Must be [1, 3]."""
    if not isinstance(value, int) or value < 1 or value > 3:
        raise ValueError(f"top_k must be between 1 and 3, got {value}")
    return value


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
        "intent_source": telemetry.intent_source,
        "yake_terms": telemetry.yake_terms,
        "truncated_count": telemetry.truncated_count,
        "embedding_source": telemetry.embedding_source,
        "rerank_outcome": telemetry.rerank_outcome,
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
_HYDRATED = True  # stub — Bug 2 fix removed disk hydration


# ─�─ Bug 2 fix compat stubs ─────────────────────────────────────────────────
# These functions existed pre-Bug-2 (gates.json persistence). They are now
# no-ops because gate state is derived from telemetry log. Kept only so the
# V1 test file (test_hermes_preflight.py) can import without crashing.

def _gates_path() -> Path:
    """Stub — gates.json no longer used. Returns a path that won't exist."""
    return _hermes_home() / "preflight" / "gates.json"


def _ensure_hydrated() -> None:
    """Stub — hydration now happens lazily in get_or_create_gate()."""
    pass


def _load_gates_from_disk() -> dict:
    """Stub — gates.json no longer used."""
    return {}


def _save_gates_to_disk() -> None:
    """Stub — gates.json no longer used."""
    pass


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

    # Story 8.1 / AC2: YAKE fallback when classify_intent fails
    # (SMALL_NO_DOMAIN means no domain vocab matched AND message is short).
    # If we have complexity or a longer message, try YAKE keywords instead
    # of skipping.
    intent_source = "rule-based"
    yake_terms: list[str] = []
    if reason == SkipReason.SMALL_NO_DOMAIN and intent.complexity:
        # Complexity hit but no domain — try YAKE
        enriched, yake_terms = enrich_query_with_yake(intent.domains, message)
        if enriched:
            intent.domains = enriched
            intent_source = "yake-fallback"
            reason = None  # override the skip

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
            intent_source=intent_source,
            yake_terms=yake_terms,
        ), log_dir=log_dir)
        return gate, reason, None

    # Fire path.
    # Story 8.1 / AC3: enrich query with YAKE keywords
    enriched_domains, fire_yake_terms = enrich_query_with_yake(intent.domains, message)
    if fire_yake_terms and not yake_terms:
        yake_terms = fire_yake_terms
        intent_source = "rule-based+yake"
    hits = retrieve_trajectories(
        enriched_domains, session_search_fn or _noop_search,
    )
    hits = _filter_stale_trajectories(hits)  # P11 / FR-33
    # Story 8.4: apply hybrid BM25 + embedding scoring before ranking
    embedding_source = ""
    if hits:
        cfg = _load_config()
        pre_hits = len(hits)
        hits = apply_hybrid_scoring(hits, message, config=cfg)
        if pre_hits > 0 and hits and hits[0].bm25_score != 0:
            # Check if embeddings were actually used (score changed from pure BM25)
            if any(h.bm25_score > 1.0 for h in hits):
                embedding_source = "provider"
            elif _embedding_cache:
                embedding_source = "cache"
    ranked = rank_trajectories(hits, config_path)
    primary_domain = intent.domains[0] if intent.domains else None
    # Story 8.6: LLM reranker — operate on top-N=8 before dedupe_and_cap
    rerank_outcome = ""
    cfg = _load_config()
    top_n_for_rerank = ranked[:_RERANK_TOP_N]
    if top_n_for_rerank and cfg.get("recall", {}).get("use_reranker", False):
        top_n_for_rerank, rerank_outcome = rerank_with_llm(
            top_n_for_rerank, message, config=cfg,
        )
        # Replace the top-N in ranked with reranked results
        ranked = top_n_for_rerank + ranked[_RERANK_TOP_N:]
    deduped = dedupe_and_cap(ranked, primary_domain=primary_domain)
    # Story 8.5: truncated_count = entries that scored but were capped out
    truncated_count = max(0, len(ranked) - len(deduped))
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
        intent_source=intent_source,
        yake_terms=yake_terms,
        truncated_count=truncated_count,
        embedding_source=embedding_source,
        rerank_outcome=rerank_outcome,
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
