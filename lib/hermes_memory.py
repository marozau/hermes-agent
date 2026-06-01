"""
hermes_memory — Canonical typed memory writer for Hermes Agent.

The ONLY sanctioned path for writing memory entries (FR-1, FR-3, Hard Invariant #1).
All writes go through add_entry() and its siblings (update_entry, supersede_entry,
expire_entry). Direct file-system writes to the memory dir are forbidden.

Each entry is a standalone .md file with YAML frontmatter following the upstream
consensus spec (NousResearch/hermes-agent#10771, @alexzhu0).

Frontmatter fields:
    id: <ULID>           # 26-char unique identifier
    type: preference     # preference | fact | procedure | episode | superseded | trajectory | unknown
    created_at: <ISO8601+TZ>
    last_used_at: <ISO8601+TZ>
    source: user-correction
    valid_until: null    # ISO8601+TZ or null
    supersedes: null     # ULID of superseded entry or null
    evidence: null

Spec references:
    FR-1  add_entry is the canonical writer.
    FR-2  Frontmatter emitted unconditionally.
    FR-3  No direct writes to the memory dir from skills/plugins/LLMs.
    FR-4  valid_until filters at read; never deletes from disk.
    FR-5  last_used_at bumps on recall (debounced ≤1×/entry/min).
    FR-6  Legacy untyped entries are type: unknown.
    FR-7  update_entry / supersede_entry / expire_entry siblings.
    FR-8  Unknown frontmatter keys preserved verbatim.
    FR-12 (deferred) raw-layer pairing arrives with Epic 2.
    NFR-16 Secret-scanner pre-check aborts writes with secrets.
"""
import atexit
import concurrent.futures
import logging
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Async sidecar embedding writer (Story 11.2, NFR-29)
# ─────────────────────────────────────────────────────────────────────────────

_EMBED_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="embed"
)
atexit.register(_EMBED_EXECUTOR.shutdown, wait=False)


def _queue_embedding_write(entry_id: str, body: str, entry_path: Path) -> None:
    """Submit background embedding job; non-blocking (NFR-29)."""
    _EMBED_EXECUTOR.submit(_compute_and_write_sidecar, entry_id, body, entry_path)


def _compute_and_write_sidecar(entry_id: str, body: str, entry_path: Path) -> None:
    """Compute embedding and write .vec sidecar file atomically."""
    try:
        import numpy
        from lib.hermes_llm import llm_embed, load_providers_config

        providers = load_providers_config()
        wl = providers.get("recall_embed")
        if not wl:
            return
        provider = wl.primary.provider
        model = wl.primary.model.lower().replace("/", "-")
        result = llm_embed([body])
        if not result or result[0] is None:
            return
        vec = result[0]
        sidecar = entry_path.parent / f"{entry_id}.{provider}-{model}.vec"
        tmp = sidecar.with_suffix(".vec.tmp")
        numpy.array(vec, dtype=numpy.float32).tofile(str(tmp))
        os.replace(tmp, sidecar)
        logger.debug("Sidecar wrote %s (%d dims)", sidecar.name, len(vec))
    except Exception as e:
        logger.debug("Sidecar write failed for %s: %s", entry_id, e)


# ─────────────────────────────────────────────────────────────────────────────
# Memory dir resolution (DN2: auto-resolve from HERMES_HOME; tests may override)
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_memory_dir(override: Optional[str] = None) -> Path:
    """Production callers leave override=None; the helper resolves to
    $HERMES_MEMORY_DIR or $HERMES_HOME/memory/typed or ~/.hermes/memory/typed.

    Tests pass override (or set HERMES_HOME via monkeypatch.setenv).
    """
    if override is not None:
        return Path(override)
    env_dir = os.environ.get("HERMES_MEMORY_DIR")
    if env_dir:
        return Path(env_dir)
    from lib._hermes_paths import resolve_hermes_home
    return Path(resolve_hermes_home()) / "memory" / "typed"


def _resolve_raw_dir(override: Optional[str] = None) -> Path:
    """Resolve the raw-layer directory: $HERMES_RAW_DIR or $HERMES_HOME/raw.
    Tests pass override; production callers leave it None."""
    if override is not None:
        return Path(override)
    env_dir = os.environ.get("HERMES_RAW_DIR")
    if env_dir:
        return Path(env_dir)
    from lib._hermes_paths import resolve_hermes_home
    return Path(resolve_hermes_home()) / "raw"


def _ensure_raw_dirs(raw_root: Path, project: str, role: str) -> Path:
    """Create the raw-layer day dir with 0o700 mode at-creation (P19).
    Creates each level incrementally; existing dirs are not chmod'd
    (avoids the every-write chmod thrash from the previous implementation)."""
    for path in (raw_root, raw_root / project, raw_root / project / role):
        if not path.exists():
            # Parent may not exist yet (e.g. raw_root itself missing); ensure
            # parents are created first, then this level with mode 0o700.
            path.parent.mkdir(parents=True, exist_ok=True)
            path.mkdir(mode=0o700, exist_ok=True)
    return raw_root / project / role


def _append_raw_line(
    *,
    entry_id: str,
    ts: datetime,
    kind: str,
    content: str,
    evidence: Optional[str],   # DN3: renamed from evidence_span to match typed FM
    model: Optional[str] = None,  # P11 + FR-9: model that produced this fact (None for human)
    raw_dir_override: Optional[str] = None,
) -> None:
    """Append one immutable JSONL line to the raw layer (FR-12).

    Raw layer path: <raw_dir>/<project>/<role>/<YYYY-MM-DD>.jsonl  (date = UTC, DN2).
    File mode 0o600 (set on open via fd, not racy chmod-after).
    Parent dirs 0o700 at creation (no chmod-every-write thrashing — P19).
    """
    raw_root = _resolve_raw_dir(raw_dir_override)
    project = os.environ.get("HERMES_PROJECT", "default")
    role = os.environ.get("HERMES_ROLE", "engineer")
    date_str = ts.strftime("%Y-%m-%d")  # DN2: UTC date sharding (ts is tz-aware UTC)

    raw_day_dir = _ensure_raw_dirs(raw_root, project, role)
    raw_file = raw_day_dir / f"{date_str}.jsonl"

    payload = {
        "ts": ts.isoformat(),
        "entry_id": entry_id,
        "project": project,
        "role": role,
        "model": model,           # P11 / FR-9
        "kind": kind,
        "content": content,
        "evidence": evidence,     # DN3: was evidence_span
    }
    line = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")

    # P12: unconditional append via O_CREAT|O_WRONLY|O_APPEND, mode 0o600 at create.
    # Eliminates the TOCTOU first-write race.
    fd = os.open(
        str(raw_file),
        os.O_WRONLY | os.O_CREAT | os.O_APPEND,
        0o600,
    )
    try:
        os.write(fd, line)
    finally:
        os.close(fd)

    logger.debug(
        "_append_raw_line: %s/%s/%s/%s line written",
        project, role, date_str, entry_id,
    )


# ─────────────────────────────────────────────────────────────────────────────
# ULID generation (stdlib only) — monotonic within a process (P3)
# ─────────────────────────────────────────────────────────────────────────────

_ULID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")

_last_ulid_ms: int = 0
_last_ulid_random_int: int = 0


def _generate_ulid() -> str:
    """Generate a 26-character ULID (Crockford base32).

    Monotonic within a millisecond: if called twice in the same ms, the random
    component is incremented from the previous value instead of regenerated.
    Resilient to clock skew: a backwards-jumping clock reuses the prior ms.
    """
    global _last_ulid_ms, _last_ulid_random_int

    timestamp_ms = int(time.time() * 1000)

    if timestamp_ms <= _last_ulid_ms and _last_ulid_ms > 0:
        # Same ms or clock went backwards → reuse last_ms, increment randomness
        timestamp_ms = _last_ulid_ms
        randomness_int = (_last_ulid_random_int + 1) & ((1 << 80) - 1)
    else:
        randomness_int = int.from_bytes(os.urandom(10), "big")

    _last_ulid_ms = timestamp_ms
    _last_ulid_random_int = randomness_int

    ts_chars = [_ULID_ALPHABET[(timestamp_ms >> (5 * i)) & 0x1F] for i in range(9, -1, -1)]
    rand_chars = [_ULID_ALPHABET[(randomness_int >> (5 * i)) & 0x1F] for i in range(15, -1, -1)]
    return "".join(ts_chars + rand_chars)


def _is_ulid(value: str) -> bool:
    """True if `value` looks like a 26-char Crockford-base32 ULID."""
    return bool(_ULID_RE.match(value))


# ─────────────────────────────────────────────────────────────────────────────
# Secret scanner (NFR-16) — broader pattern coverage (P15)
# ─────────────────────────────────────────────────────────────────────────────

_SECRET_PATTERNS = [
    # OpenAI / Anthropic / Stripe variants (sk-…, sk-proj-…, sk-ant-…, sk_live_…, sk_test_…)
    (re.compile(r'sk[-_](?:proj|ant|live|test|admin)[-_][A-Za-z0-9_\-]{16,}'), "API key (provider-prefixed)"),
    (re.compile(r'\bsk-[A-Za-z0-9]{32,}'), "OpenAI-style API key"),
    # Anthropic legacy
    (re.compile(r'\bsk-ant-[A-Za-z0-9_\-]{20,}'), "Anthropic API key"),
    # Google
    (re.compile(r'\bAIza[0-9A-Za-z\-_]{35}\b'), "Google API key"),
    # AWS
    (re.compile(r'\bAKIA[0-9A-Z]{16}\b'), "AWS access key"),
    # GitHub / GitLab tokens
    (re.compile(r'\bgh[pousr]_[A-Za-z0-9_]{20,}\b'), "GitHub token"),
    (re.compile(r'\bglpat-[A-Za-z0-9_\-]{20,}\b'), "GitLab PAT"),
    # Slack
    (re.compile(r'\bxox[baprs]-[A-Za-z0-9-]{10,}\b'), "Slack token"),
    # HuggingFace
    (re.compile(r'\bhf_[A-Za-z0-9]{30,}\b'), "HuggingFace token"),
    # JWT (case-sensitive — JWT base64 prefix is always `eyJ`)
    (re.compile(r'\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\b'), "JWT token"),
    # Private key headers
    (re.compile(r'-----BEGIN\s+(?:RSA|EC|DSA|OPENSSH|PGP)\s+PRIVATE\s+KEY-----'), "Private key header"),
    # api_key/api-key/apikey assignments with high-entropy-ish RHS
    (re.compile(r'(?:api[_-]?key|apikey|api[_-]?secret)\s*[:=]\s*[\'"][A-Za-z0-9+/=_\-]{24,}[\'"]', re.IGNORECASE), "API key assignment"),
]


def _scan_for_secrets(body: str) -> Optional[str]:
    """Scan body for secret patterns. Returns the secret kind on hit, None if clean."""
    for pattern, kind in _SECRET_PATTERNS:
        if pattern.search(body):
            return kind
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Frontmatter parsing — tolerant of CRLF / BOM / trailing space (P4)
# ─────────────────────────────────────────────────────────────────────────────

_FM_OPEN_RE = re.compile(r"^﻿?---[ \t]*\r?\n", re.MULTILINE)
_FM_DELIM_RE = re.compile(r"\r?\n---[ \t]*\r?\n")
# Only the fm keys that aren't named add_entry parameters can slip through **kwargs.
# (Python already raises TypeError if a caller passes type=/source=/valid_until= etc.)
_RESERVED_FRONTMATTER_KEYS = frozenset({"id", "created_at", "last_used_at"})


def _normalize_iso_string(value) -> Optional[str]:
    """Coerce a value (str / datetime / date / None) to an ISO8601+TZ string.

    Returns None for None or unparseable input. Maps `Z` → `+00:00`.
    Naive datetimes are interpreted as UTC and emitted with `+00:00`.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    s = str(value).strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _split_frontmatter(content: str):
    """Return (frontmatter_yaml_str, body_str, had_frontmatter).

    Tolerant of:
      - Leading UTF-8 BOM
      - `---\r\n` (CRLF) line endings
      - `--- \n` trailing whitespace on the fence line
    """
    m_open = _FM_OPEN_RE.match(content)
    if not m_open:
        return "", content, False
    after_open = content[m_open.end():]
    m_close = _FM_DELIM_RE.search(after_open)
    if not m_close:
        return "", content, False
    fm_str = after_open[: m_close.start()]
    body = after_open[m_close.end():]
    return fm_str, body, True


def _atomic_write(path: Path, content: str) -> None:
    """Write `content` to `path` atomically (P6: tmp + os.replace)."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


# ─────────────────────────────────────────────────────────────────────────────
# Entry type
# ─────────────────────────────────────────────────────────────────────────────

EntryType = Literal[
    "preference", "fact", "procedure", "episode",
    "superseded", "trajectory", "unknown",
]


# ─────────────────────────────────────────────────────────────────────────────
# Canonical writer: add_entry
# ─────────────────────────────────────────────────────────────────────────────

def add_entry(
    type: EntryType,
    body: str,
    source: str,
    *,
    evidence: Optional[str] = None,
    valid_until: Optional[str] = None,
    supersedes: Optional[str] = None,
    model: Optional[str] = None,       # P11: model that produced this entry (None for human).
    memory_dir: Optional[str] = None,  # Test-only override; production callers omit.
    raw_dir: Optional[str] = None,     # Test-only override for raw layer.
    **kwargs,
) -> str:
    """Only sanctioned writer of typed memory entries (FR-1, FR-2, FR-3).

    NFR-16: Pre-scans body for secrets; raises ValueError on hit.
    FR-8: Unknown keyword args are preserved verbatim in frontmatter.
    P5: Reserved canonical keys (id, type, created_at, …) cannot be overridden
        via **kwargs — attempting to do so raises ValueError.
    FR-12: Pairs each typed entry with an immutable raw-layer JSONL append.
           If the raw write fails, the typed entry is rolled back.

    Returns the new entry's ULID.
    """
    # ── P5: refuse reserved keys ──
    shadowed = set(kwargs) & _RESERVED_FRONTMATTER_KEYS
    if shadowed:
        raise ValueError(
            f"add_entry: cannot override canonical frontmatter keys via **kwargs: "
            f"{sorted(shadowed)}"
        )

    # ── NFR-16: secret-scanner pre-check ──
    secret_kind = _scan_for_secrets(body)
    if secret_kind:
        raise ValueError(
            f"Memory write aborted: body contains suspected secret "
            f"({secret_kind}). Remove the credential and retry."
        )

    # ── Resolve directories ──
    memory_path = _resolve_memory_dir(memory_dir)
    memory_path.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    entry_id = _generate_ulid()

    # Normalize valid_until input
    valid_until_norm = _normalize_iso_string(valid_until) if valid_until is not None else None

    fm = {
        "id": entry_id,
        "type": type,
        "created_at": now.isoformat(),
        "last_used_at": now.isoformat(),
        "source": source,
        "valid_until": valid_until_norm,
        "supersedes": supersedes,
        "evidence": evidence,
    }
    fm.update(kwargs)  # FR-8 forward-compat; reserved keys already refused above

    content = _serialize_entry(fm, body)
    filepath = memory_path / f"{entry_id}.md"

    # ── FR-12 transactional pairing — raw first, typed second (DN1) ──
    # Rationale: raw is the source-of-truth log. An orphan raw line is
    # information; an orphan typed entry is corruption (Hard Invariant #6).
    # The rebuildability check tolerates raw-without-typed and reports any
    # typed-without-raw as broken.
    _append_raw_line(
        entry_id=entry_id,
        ts=now,
        kind=type,
        content=body,
        evidence=evidence,
        model=model,
        raw_dir_override=raw_dir,
    )

    # If typed write fails, the raw line stays — it's an audit record. No
    # rollback of raw (immutable by design). Caller can retry with a fresh
    # ULID; the orphan raw line is surfaced by the rebuildability check.
    try:
        _atomic_write(filepath, content)
    except BaseException:
        # P10: BaseException covers KeyboardInterrupt/SystemExit too, so a
        # Ctrl-C mid-write doesn't leave a typed orphan. We never delete the
        # raw line (immutable), but we re-raise so the caller knows the
        # typed write failed. P13: log the failure for forensics.
        logger.error(
            "add_entry: raw line %s appended but typed write failed; orphan raw line will surface in rebuildability check",
            entry_id,
        )
        raise

    logger.debug("add_entry: wrote %s (type=%s, source=%s)", filepath, type, source)

    # ── Story 11.2: async sidecar embedding for trajectories (NFR-29) ──
    if type == "trajectory":
        _queue_embedding_write(entry_id, body, filepath)

    return entry_id


# ─────────────────────────────────────────────────────────────────────────────
# Entry serialization
# ─────────────────────────────────────────────────────────────────────────────

def _serialize_entry(fm: dict, body: str) -> str:
    import yaml
    fm_yaml = yaml.dump(
        fm, default_flow_style=False, allow_unicode=True, sort_keys=False
    ).rstrip("\n")
    # P8: do NOT strip body. Preserve verbatim, but ensure single trailing newline.
    body_out = body if body.endswith("\n") else body + "\n"
    return f"---\n{fm_yaml}\n---\n{body_out}"


def _parse_entry_content(content: str) -> "tuple[dict, str, bool]":
    """Parse `content` into (frontmatter_dict, body, had_frontmatter).

    A non-dict YAML payload (list, scalar) yields fm = {}.
    """
    import yaml
    fm_str, body, had_fm = _split_frontmatter(content)
    if not had_fm:
        return {}, body, False
    try:
        loaded = yaml.safe_load(fm_str)
    except Exception:
        logger.warning("hermes_memory: invalid YAML in frontmatter; treating as legacy")
        return {}, content, False
    fm = loaded if isinstance(loaded, dict) else {}
    return fm, body, True


# ─────────────────────────────────────────────────────────────────────────────
# Sibling writers (FR-7): update_entry, supersede_entry, expire_entry
# ─────────────────────────────────────────────────────────────────────────────

def _read_entry_file(entry_id: str, memory_dir: Path) -> "tuple[dict, str]":
    """Read an entry. P10: tolerates blank/missing frontmatter.
    P13: legacy entries (no frontmatter) get a backfilled fm with
    type=unknown and created_at=mtime so siblings can mutate them.
    """
    filepath = memory_dir / f"{entry_id}.md"
    if not filepath.exists():
        raise FileNotFoundError(f"Entry {entry_id} not found at {filepath}")
    content = filepath.read_text(encoding="utf-8")
    fm, body, had_fm = _parse_entry_content(content)

    if not had_fm:
        # P13 / Story 1.5 AC #3: backfill on first touch
        mtime_iso = datetime.fromtimestamp(
            filepath.stat().st_mtime, tz=timezone.utc
        ).isoformat()
        fm = {
            "id": entry_id,
            "type": "unknown",
            "created_at": mtime_iso,
            "last_used_at": mtime_iso,
            "source": "legacy-backfill",
            "valid_until": None,
            "supersedes": None,
            "evidence": None,
        }
    return fm, body


def _write_entry_file(entry_id: str, fm: dict, body: str, memory_dir: Path) -> None:
    """Write entry file atomically (P6)."""
    content = _serialize_entry(fm, body)
    filepath = memory_dir / f"{entry_id}.md"
    _atomic_write(filepath, content)


def update_entry(
    entry_id: str,
    body: str,
    *,
    memory_dir: Optional[str] = None,
    raw_dir: Optional[str] = None,
) -> None:
    """Replace body of an existing entry. Preserves id/created_at/source.
    Bumps last_used_at. P9: secret-scans the new body before writing.
    P6: appends a `kind: update` event to the raw layer (Hard Invariant #6).
    """
    secret_kind = _scan_for_secrets(body)
    if secret_kind:
        raise ValueError(
            f"update_entry aborted: body contains suspected secret ({secret_kind})."
        )
    mem_path = _resolve_memory_dir(memory_dir)
    fm, _ = _read_entry_file(entry_id, mem_path)
    now = datetime.now(timezone.utc)
    fm["last_used_at"] = now.isoformat()
    # P6: raw mutation event first (audit precedes effect).
    _append_raw_line(
        entry_id=entry_id, ts=now, kind="update",
        content=body, evidence=None, raw_dir_override=raw_dir,
    )
    _write_entry_file(entry_id, fm, body, mem_path)
    logger.debug("update_entry: updated %s", entry_id)


def supersede_entry(
    old_id: str,
    new_id: str,
    *,
    memory_dir: Optional[str] = None,
    raw_dir: Optional[str] = None,
) -> None:
    """Mark old as type:superseded; link new.supersedes -> old. Neither deleted.
    P6: appends a `kind: supersede` event to the raw layer.
    """
    mem_path = _resolve_memory_dir(memory_dir)

    # P6: raw mutation event first
    _append_raw_line(
        entry_id=old_id, ts=datetime.now(timezone.utc),
        kind="supersede", content=new_id, evidence=None,
        raw_dir_override=raw_dir,
    )

    old_fm, old_body = _read_entry_file(old_id, mem_path)
    old_fm["type"] = "superseded"
    _write_entry_file(old_id, old_fm, old_body, mem_path)

    new_fm, new_body = _read_entry_file(new_id, mem_path)
    new_fm["supersedes"] = old_id
    _write_entry_file(new_id, new_fm, new_body, mem_path)

    logger.debug("supersede_entry: %s -> %s", old_id, new_id)


def expire_entry(
    entry_id: str,
    *,
    memory_dir: Optional[str] = None,
    raw_dir: Optional[str] = None,
) -> None:
    """Set valid_until=now. Entry stays on disk; reader filters it out (FR-4).
    P6: appends a `kind: expire` event to the raw layer.
    """
    mem_path = _resolve_memory_dir(memory_dir)
    fm, body = _read_entry_file(entry_id, mem_path)
    now = datetime.now(timezone.utc)
    fm["valid_until"] = now.isoformat()
    _append_raw_line(
        entry_id=entry_id, ts=now, kind="expire",
        content="", evidence=None, raw_dir_override=raw_dir,
    )
    _write_entry_file(entry_id, fm, body, mem_path)
    logger.debug("expire_entry: expired %s", entry_id)


# ─────────────────────────────────────────────────────────────────────────────
# Story 9.1: reinforce_entry — bump access_count + last_hit_at on verified hits
# ─────────────────────────────────────────────────────────────────────────────

def _has_prior_reinforce(entry_id: str, source: str, session_id: str = "", raw_dir: Optional[str] = None) -> bool:
    """Check if a reinforce event with matching (entry_id, compound_key) already exists.

    A3: Compound key = f"{source}:{session_id}" encoded in raw content field.
    P1: Uses equality, not startswith, to avoid prefix-collision.
    Scans today's and yesterday's JSONL files.
    """
    from datetime import timedelta
    raw_root = _resolve_raw_dir(raw_dir)
    project = os.environ.get("HERMES_PROJECT", "default")
    role = os.environ.get("HERMES_ROLE", "engineer")
    now = datetime.now(timezone.utc)
    dates = [now.strftime("%Y-%m-%d"), (now - timedelta(days=1)).strftime("%Y-%m-%d")]
    compound_key = f"{source}:{session_id}" if session_id else source

    for date_str in dates:
        raw_file = raw_root / project / role / f"{date_str}.jsonl"
        if not raw_file.exists():
            continue
        try:
            for line in raw_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (row.get("kind") == "reinforce"
                        and row.get("entry_id") == entry_id
                        and row.get("content", "") == compound_key):
                    return True
        except FileNotFoundError:
            continue
        except OSError as e:
            logger.warning("_has_prior_reinforce: OSError reading %s: %s", raw_file, e)
            raise  # P4: fail-closed on permission/corruption errors
    return False


def reinforce_entry(
    entry_id: str,
    source: str = "verify-cited-hit",
    *,
    session_id: str = "",
    memory_dir: Optional[str] = None,
    raw_dir: Optional[str] = None,
) -> None:
    """Story 9.1: Bump access_count + set last_hit_at on a verified-cited entry.

    A3: session_id parameter — idempotency keyed on (session_id, entry_id) via
    compound content field "{source}:{session_id}" in the raw layer. This matches
    AC3: "exactly once per (session, cited_id)."

    Atomic frontmatter rewrite — body bytes unchanged (content-hash stable,
    dream re-runs don't churn). Pairs with a raw-layer reinforce event
    (Epic 2 invariant / FR-12).

    P2: Frontmatter write happens BEFORE raw-layer append. If frontmatter
    fails, no raw event is written; retry is safe.
    P3: File-level lock prevents lost-update on concurrent verify hooks.
    """
    import fcntl

    compound_key = f"{source}:{session_id}" if session_id else source

    # Idempotency guard — check raw layer before doing any I/O
    if _has_prior_reinforce(entry_id, source, session_id, raw_dir):
        logger.debug("reinforce_entry: skip %s (already reinforced with key=%s)", entry_id, compound_key)
        return

    mem_path = _resolve_memory_dir(memory_dir)
    filepath = mem_path / f"{entry_id}.md"
    if not filepath.exists():
        raise FileNotFoundError(f"Entry {entry_id} not found at {filepath}")

    # P3: Per-entry file lock for read-modify-write atomicity
    lock_path = mem_path / f".{entry_id}.lock"
    lock_path.touch(exist_ok=True)
    lock_fd = open(lock_path, "r+")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        fm, body = _read_entry_file(entry_id, mem_path)
        now = datetime.now(timezone.utc)

        # Bump access_count (default 0 for pre-existing entries without the field)
        current_count = fm.get("access_count", 0)
        if not isinstance(current_count, int) or current_count < 0:
            current_count = 0
        fm["access_count"] = current_count + 1
        fm["last_hit_at"] = now.isoformat()

        # P2: Frontmatter write FIRST (atomic tmp+rename)
        _write_entry_file(entry_id, fm, body, mem_path)

        # Raw-layer pair AFTER frontmatter success
        _append_raw_line(
            entry_id=entry_id,
            ts=now,
            kind="reinforce",
            content=compound_key,  # A3: compound key for (session, entry) dedup
            evidence=None,
            raw_dir_override=raw_dir,
        )
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()

    logger.debug("reinforce_entry: %s access_count=%d key=%s", entry_id, fm["access_count"], compound_key)


# ─────────────────────────────────────────────────────────────────────────────
# Story 9.2: Manifest-based dedup for trajectory recorder
# ─────────────────────────────────────────────────────────────────────────────

_MANIFEST_MAX_ENTRIES = 50
_MANIFEST_SUMMARY_LEN = 80  # chars per entry summary


def build_manifest(
    memory_dir: Optional[str] = None,
    *,
    max_entries: int = _MANIFEST_MAX_ENTRIES,
) -> str:
    """Build a MANIFEST block listing up to `max_entries` trajectory entries.

    Used by the trajectory recorder to instruct the LLM: "do NOT duplicate
    these entries." Returns a formatted string suitable for injection into
    the classifier prompt.

    Entries are sorted by last_used_at (most recent first); capped at
    max_entries (default 50, ~4 KB prompt budget).
    """
    entries = read_entries(memory_dir=memory_dir, read_only=True)
    # Filter to trajectory-typed entries only
    trajectories = [e for e in entries if e.get("type") == "trajectory"]
    # Sort by last_used_at descending (most recently touched first)
    trajectories.sort(
        key=lambda e: e.get("last_used_at") or "",
        reverse=True,
    )
    trajectories = trajectories[:max_entries]

    if not trajectories:
        return "MANIFEST (existing trajectories for this project+role):\n(none)\n"

    lines = ["MANIFEST (existing trajectories for this project+role):"]
    for e in trajectories:
        eid = e.get("id")  # P13: guard legacy entries without id
        if not eid:
            continue
        body_summary = (e.get("body") or "")[:_MANIFEST_SUMMARY_LEN].replace("\n", " ").strip()
        lines.append(f"[{eid}] {body_summary}")
    return "\n".join(lines) + "\n"


# Pydantic schema for the dedup classifier response (Hard Invariant #11)
_MANIFEST_DEDUP_PROMPT = """\
Before extracting, check the MANIFEST. If the new pattern is
already present, return {action: 'reinforce', id: '<existing_id>'}
instead of a new entry.

Respond with JSON matching one of:
  {{"action": "reinforce", "id": "<existing_entry_id>"}}
  {{"action": "new", "type": "<entry_type>", "body": "<entry_body>"}}
"""


def classify_trajectory_with_manifest(
    failure_pattern: str,
    manifest: str,
    *,
    workload: str = "trajectory_dedup",
) -> dict:
    """Story 9.2: Classify a failure pattern against existing trajectories.

    Sends manifest + failure pattern to the LLM. Returns one of:
      {"action": "reinforce", "id": "<id>"}
      {"action": "new", "type": "...", "body": "..."}
      {"action": "error", "reason": "..."}  (fail-open)

    Routes through hermes_llm.llm_call (Hard Invariant #2).
    Uses Pydantic to gate the LLM output (Hard Invariant #11).
    """
    try:
        from pydantic import BaseModel, ConfigDict
        from typing import Literal, Union
    except ImportError:
        return {"action": "error", "reason": "pydantic not available"}

    EntryType = Literal["preference", "fact", "procedure", "episode", "trajectory", "unknown"]

    class ReinforceAction(BaseModel):
        model_config = ConfigDict(extra="forbid")  # P8
        action: Literal["reinforce"]
        id: str

    class NewEntryAction(BaseModel):
        model_config = ConfigDict(extra="forbid")  # P8
        action: Literal["new"]
        type: EntryType  # P7: constrained to valid entry types
        body: str

    class ClassifierResult(BaseModel):
        """P10: Discriminated union for LLMSpec response_model gate."""
        model_config = ConfigDict(extra="forbid")
        action: Literal["reinforce", "new"]
        id: str = ""
        type: EntryType = "trajectory"
        body: str = ""

    prompt = (
        f"{manifest}\n"
        f"NEW FAILURE PATTERN:\n{failure_pattern}\n\n"
        f"{_MANIFEST_DEDUP_PROMPT}"
    )

    try:
        from lib.hermes_llm import llm_call, LLMSpec
        spec = LLMSpec(
            workload=workload,
            messages=[{"role": "user", "content": prompt}],
            response_model=ClassifierResult,  # P10: Pydantic gate at LLM layer
        )
        result = llm_call(spec)
        content = result.get("content", "") if isinstance(result, dict) else str(result)
        if not content or not content.strip():
            return {"action": "error", "reason": "empty response"}

        # P9: Use json.JSONDecoder().raw_decode for robust extraction
        import json as _json
        decoder = _json.JSONDecoder()
        # Find first '{' and try raw_decode
        brace_start = content.find('{')
        if brace_start < 0:
            return {"action": "error", "reason": "no JSON found"}
        try:
            parsed, _ = decoder.raw_decode(content, brace_start)
        except _json.JSONDecodeError:
            return {"action": "error", "reason": "malformed JSON"}

        # Pydantic gate (Hard Invariant #11)
        action = parsed.get("action")
        if action == "reinforce":
            validated = ReinforceAction(**parsed)
            # P11: verify id appears in manifest before returning
            if f"[{validated.id}]" not in manifest and f"[{validated.id} " not in manifest:
                return {"action": "error", "reason": f"id_not_in_manifest: {validated.id}"}
            return {"action": "reinforce", "id": validated.id}
        elif action == "new":
            validated = NewEntryAction(**parsed)
            return {"action": "new", "type": validated.type, "body": validated.body}
        else:
            return {"action": "error", "reason": f"unknown action: {action}"}

    except Exception as e:
        logger.debug("classify_trajectory_with_manifest: failed: %s", e)
        return {"action": "error", "reason": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# Story 9.3: Skill-dream hit-rate report builder
# ─────────────────────────────────────────────────────────────────────────────

def build_hit_rate_report(
    preflight_log_dir: Optional[str] = None,
    *,
    min_fires: int = 20,
) -> list[dict]:
    """Story 9.3: Compute per-category hit-rate report for skill-dream consumption.

    Joins preflight telemetry rows (~/.hermes/preflight/log/<date>.jsonl) with
    verify_citation events for the same (session_id, intent_hash). Groups by
    category and computes: {category, n_fired, n_matched_hit, n_matched_miss, hit_rate}.

    Gated on ≥ min_fires (default 20) per category to avoid noise.
    Returns list of dicts sorted by hit_rate ascending (worst categories first).
    """
    from collections import defaultdict
    from pathlib import Path as _Path

    if preflight_log_dir is None:
        import os as _os
        home = _os.environ.get("HERMES_HOME") or str(_Path.home() / ".hermes")
        preflight_log_dir = str(_Path(home) / "preflight" / "log")

    log_dir = _Path(preflight_log_dir)
    if not log_dir.exists():
        return []

    # Collect all preflight + verify_citation rows
    preflight_rows = []  # (session_id, intent_hash, category)
    citation_rows = []   # (session_id, intent_hash, cited_ids)

    for jsonl_file in sorted(log_dir.glob("*.jsonl")):
        try:
            for line in jsonl_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue

                event = row.get("event")
                sid = row.get("session_id", "")
                ih = row.get("intent_hash", "")

                if event == "verify_citation":
                    citation_rows.append((sid, ih, row.get("cited_ids", [])))
                elif not event:
                    # Regular preflight telemetry row
                    # A4: prefer explicit category field, fall back to primary_domain, then domains[0]
                    cat = row.get("category") or row.get("primary_domain") or (
                        row.get("domains", ["unknown"])[0] if row.get("domains") else "unknown"
                    )
                    preflight_rows.append((sid, ih, cat))
        except OSError:
            continue

    # Build a set of (session_id, intent_hash) that had verify_citation hits
    citation_hits = set()
    for sid, ih, cited_ids in citation_rows:
        if cited_ids:
            citation_hits.add((sid, ih))

    # Group by category
    cat_stats = defaultdict(lambda: {"n_fired": 0, "n_matched_hit": 0, "n_matched_miss": 0})
    for sid, ih, cat in preflight_rows:
        stats = cat_stats[cat]
        stats["n_fired"] += 1
        if (sid, ih) in citation_hits:
            stats["n_matched_hit"] += 1
        else:
            stats["n_matched_miss"] += 1

    # Build report, filtering by min_fires
    report = []
    for cat, stats in sorted(cat_stats.items()):
        n = stats["n_fired"]
        if n < min_fires:
            continue
        hit_rate = stats["n_matched_hit"] / n if n > 0 else 0.0
        report.append({
            "category": cat,
            "n_fired": n,
            "n_matched_hit": stats["n_matched_hit"],
            "n_matched_miss": stats["n_matched_miss"],
            "hit_rate": round(hit_rate, 4),
        })

    # Sort by hit_rate ascending (worst categories first)
    report.sort(key=lambda r: r["hit_rate"])
    return report


# Story 9.3: Threshold constants (hard — don't drift without measuring)
_HIT_RATE_LOW_THRESHOLD = 0.15
_HIT_RATE_HIGH_THRESHOLD = 0.5
_HIT_RATE_BLIND_SPOT_THRESHOLD = 0.05
_UNRELATED_RATE_BLIND_SPOT = 0.6


def propose_category_weight_nudges(
    hit_rate_report: list[dict],
    *,
    low_threshold: float = _HIT_RATE_LOW_THRESHOLD,
    high_threshold: float = _HIT_RATE_HIGH_THRESHOLD,
    blind_spot_threshold: float = _HIT_RATE_BLIND_SPOT_THRESHOLD,
    unrelated_rate_threshold: float = _UNRELATED_RATE_BLIND_SPOT,
) -> dict:
    """Story 9.3: Propose category weight nudges from hit-rate data.

    Returns dict with keys:
      - low_hit_rate: list of categories to nudge down
      - high_hit_rate: list of categories to nudge up
      - blind_spots: list of categories flagged as domain blind spots

    All are PROPOSALS only — operator decides via Story 4.6's apply.
    """
    low = []
    high = []
    blind_spots = []

    for row in hit_rate_report:
        cat = row["category"]
        hr = row["hit_rate"]
        n = row["n_fired"]
        # P14: derive unrelated_rate from match:miss (cited but didn't help),
        # distinct from match:unrelated (no citation at all). Currently the
        # telemetry schema doesn't distinguish these, so we use n_matched_miss
        # as a proxy. When telemetry is extended, update this calculation.
        unrelated_rate = row["n_matched_miss"] / n if n > 0 else 0.0

        # P12: use <= / >= for boundary inclusion (flicker prevention)
        if hr <= blind_spot_threshold and unrelated_rate > unrelated_rate_threshold:
            blind_spots.append({
                "category": cat,
                "hit_rate": hr,
                "unrelated_rate": round(unrelated_rate, 4),
                "n_fired": n,
                "action": "add_vocab_candidate",
            })
        elif hr <= low_threshold:
            low.append({
                "category": cat,
                "hit_rate": hr,
                "n_fired": n,
                "action": "nudge_down",
            })
        elif hr >= high_threshold:
            high.append({
                "category": cat,
                "hit_rate": hr,
                "n_fired": n,
                "action": "nudge_up",
            })

    return {
        "low_hit_rate": low,
        "high_hit_rate": high,
        "blind_spots": blind_spots,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Reader: read_entries (FR-4 filter, FR-5 bump, FR-6 legacy support)
# ─────────────────────────────────────────────────────────────────────────────

_DEBOUNCE_SECONDS = 60


def read_entries(
    memory_dir: Optional[str] = None,
    *,
    read_only: bool = False,
) -> list:
    """Read all entries. Filters expired (FR-4). Bumps last_used_at (FR-5).

    DN3 / read_only=True: skip the `last_used_at` write-back entirely.
    Dream-create / preflight / dry-run callers MUST pass read_only=True.

    Returns list of dicts with keys: id, type, body, created_at, last_used_at,
    source, valid_until, supersedes, evidence.
    """
    mem_path = _resolve_memory_dir(memory_dir)
    if not mem_path.exists():
        return []

    now = datetime.now(timezone.utc)
    entries = []

    for filepath in sorted(mem_path.glob("*.md")):
        try:
            content = filepath.read_text(encoding="utf-8")
        except OSError:
            logger.warning("read_entries: cannot read %s, skipping", filepath)
            continue

        fm, body, had_fm = _parse_entry_content(content)
        stem = filepath.stem
        entry_id = fm.get("id") if had_fm and isinstance(fm.get("id"), str) else stem

        # ── FR-4: filter expired entries (P2: fail-closed on malformed) ──
        valid_until_raw = fm.get("valid_until") if had_fm else None
        if valid_until_raw not in (None, ""):
            normalized = _normalize_iso_string(valid_until_raw)
            if normalized is None:
                logger.warning(
                    "read_entries: filtered %s, reason: valid_until_malformed (%r)",
                    entry_id, valid_until_raw,
                )
                continue
            try:
                vu = datetime.fromisoformat(normalized)
                if vu < now:
                    logger.debug(
                        "read_entries: filtered %s, reason: valid_until_past",
                        entry_id,
                    )
                    continue
            except ValueError:
                logger.warning(
                    "read_entries: filtered %s, reason: valid_until_malformed (%r)",
                    entry_id, valid_until_raw,
                )
                continue

        # ── FR-5: bump last_used_at (P7: use on-disk last_used_at as truth) ──
        if had_fm and not read_only and _is_ulid(stem):
            last_used_str = fm.get("last_used_at")
            should_bump = True
            if last_used_str:
                last_normalized = _normalize_iso_string(last_used_str)
                if last_normalized:
                    try:
                        last_dt = datetime.fromisoformat(last_normalized)
                        if (now - last_dt).total_seconds() < _DEBOUNCE_SECONDS:
                            should_bump = False
                    except ValueError:
                        pass
            if should_bump:
                try:
                    fm["last_used_at"] = now.isoformat()
                    _write_entry_file(stem, fm, body, mem_path)
                except OSError:
                    # FR-5: don't block recall on metadata I/O failure
                    logger.debug("read_entries: failed to bump last_used_at for %s", entry_id)

        entries.append({
            "id": entry_id,
            "type": fm.get("type", "unknown") if had_fm else "unknown",
            "body": body,  # P8: NOT stripped — caller-visible round-trip preserved
            "created_at": fm.get("created_at"),
            "last_used_at": fm.get("last_used_at"),
            "source": fm.get("source"),
            "valid_until": fm.get("valid_until"),
            "supersedes": fm.get("supersedes"),
            "evidence": fm.get("evidence"),
            "access_count": fm.get("access_count", 0),  # Story 9.1
            "last_hit_at": fm.get("last_hit_at"),         # Story 9.1
        })

    return entries
