"""
hermes_memory — Canonical typed memory writer for Hermes Agent.

The ONLY sanctioned path for writing memory entries. All writes go through
add_entry() and its siblings (update_entry, supersede_entry, expire_entry).

Each entry is a standalone .md file with YAML frontmatter following the
upstream consensus spec (NousResearch/hermes-agent#10771, @alexzhu0).

Frontmatter fields:
    id: <ULID>           # 26-char unique identifier
    type: preference     # preference | fact | procedure | episode | superseded | trajectory | unknown
    created_at: <ISO8601+TZ>
    last_used_at: <ISO8601+TZ>
    source: user-correction  # user-correction | self-derived | dogfood-incident | session:<id> | trajectory | import:<origin>
    valid_until: null    # ISO8601 or null
    supersedes: null     # ULID of superseded entry or null
    evidence: null       # free-form evidence pointer or null

FR-2: Frontmatter emitted unconditionally.
FR-8: Unknown frontmatter keys preserved verbatim (forward-compat).
NFR-16: Secret-scanner pre-check aborts writes with secrets.
"""
import os
import re
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ULID generation (stdlib only — no external deps)
# ---------------------------------------------------------------------------

import time as _time
import os as _os

# Crockford base32 alphabet for ULID
_ULID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

def _generate_ulid() -> str:
    """Generate a 26-character ULID (Crockford base32, monotonic within ms)."""
    timestamp_ms = int(_time.time() * 1000)
    randomness = _os.urandom(10)

    # 48-bit timestamp → 10 base32 chars (MSB first)
    ts_part = []
    for i in range(9, -1, -1):
        ts_part.append(_ULID_ALPHABET[(timestamp_ms >> (5 * i)) & 0x1F])

    # 80-bit random → 16 base32 chars
    rand_int = int.from_bytes(randomness, 'big')
    rand_part = []
    for i in range(15, -1, -1):
        rand_part.append(_ULID_ALPHABET[(rand_int >> (5 * i)) & 0x1F])

    return "".join(ts_part + rand_part)


# ---------------------------------------------------------------------------
# Secret scanner (NFR-16)
# ---------------------------------------------------------------------------

_SECRET_PATTERNS = [
    # OpenAI / common API keys
    (re.compile(r'sk-[A-Za-z0-9]{20,}', re.IGNORECASE), "OpenAI-style API key"),
    (re.compile(r'AIza[0-9A-Za-z\-_]{35}', re.IGNORECASE), "Google API key"),
    # AWS access keys
    (re.compile(r'AKIA[0-9A-Z]{16}', re.IGNORECASE), "AWS access key"),
    # Generic key patterns
    (re.compile(r'(?:api[_-]?key|apikey|api[_-]?secret)\s*[:=]\s*[\'"]?[A-Za-z0-9+/=_-]{20,}', re.IGNORECASE), "API key assignment"),
    # Private key headers
    (re.compile(r'-----BEGIN\s+(?:RSA|EC|DSA|OPENSSH)\s+PRIVATE\s+KEY-----', re.IGNORECASE), "Private key header"),
    # JWT tokens
    (re.compile(r'eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}', re.IGNORECASE), "JWT token"),
    # GitHub tokens
    (re.compile(r'gh[pousr]_[A-Za-z0-9_]{20,}', re.IGNORECASE), "GitHub token"),
    # Generic tokens in env-var style
    (re.compile(r'(?:TOKEN|SECRET|PASSWORD|CREDENTIAL)\s*[:=]\s*[\'"]?\S{8,}', re.IGNORECASE), "Credential assignment"),
]


def _scan_for_secrets(body: str) -> Optional[str]:
    """Scan body for secret patterns. Returns the secret kind if found, None if clean."""
    for pattern, kind in _SECRET_PATTERNS:
        if pattern.search(body):
            return kind
    return None


# ---------------------------------------------------------------------------
# Entry type
# ---------------------------------------------------------------------------

EntryType = Literal[
    "preference", "fact", "procedure", "episode",
    "superseded", "trajectory", "unknown",
]


# ---------------------------------------------------------------------------
# Canonical writer: add_entry
# ---------------------------------------------------------------------------

def add_entry(
    type: EntryType,
    body: str,
    source: str,
    *,
    memory_dir: Optional[str] = None,
    evidence: Optional[str] = None,
    valid_until: Optional[str] = None,  # ISO8601 string
    supersedes: Optional[str] = None,
    **kwargs,  # forward-compat: unknown fields preserved
) -> str:
    """
    Only sanctioned writer of typed memory entries (FR-1, FR-2, FR-3).

    Emits frontmatter unconditionally with id (ULID), created_at, last_used_at,
    source, plus valid_until: null, supersedes: null, evidence: null.

    Runs secret-scanner pre-check (NFR-16); aborts on hit.
    Unknown keyword arguments are preserved in frontmatter (FR-8 forward-compat).

    Returns the new entry's ULID.

    Raises ValueError if body contains a detected secret.
    """
    # ── Secret scanner pre-check (NFR-16) ──
    secret_kind = _scan_for_secrets(body)
    if secret_kind:
        raise ValueError(
            f"Memory write aborted: body contains suspected secret "
            f"({secret_kind}). Remove the credential and retry."
        )

    # ── Resolve memory directory ──
    if memory_dir is None:
        raise ValueError("memory_dir is required")

    memory_path = Path(memory_dir)
    memory_path.mkdir(parents=True, exist_ok=True)

    # ── Generate entry ──
    now = datetime.now(timezone.utc).isoformat()
    entry_id = _generate_ulid()

    # Build frontmatter (required fields first, then optionals, then **kwargs)
    fm = {
        "id": entry_id,
        "type": type,
        "created_at": now,
        "last_used_at": now,
        "source": source,
        "valid_until": valid_until,
        "supersedes": supersedes,
        "evidence": evidence,
    }
    # Preserve unknown fields (FR-8)
    fm.update(kwargs)

    # Serialize to YAML frontmatter + body
    import yaml
    frontmatter_yaml = yaml.dump(fm, default_flow_style=False, allow_unicode=True, sort_keys=False).strip()

    content = f"---\n{frontmatter_yaml}\n---\n{body}"
    if not content.endswith("\n"):
        content += "\n"

    # ── Write to file ──
    filepath = memory_path / f"{entry_id}.md"
    filepath.write_text(content)

    logger.debug(f"add_entry: wrote {filepath} (type={type}, source={source})")
    return entry_id


# ---------------------------------------------------------------------------
# Sibling writers: update_entry, supersede_entry, expire_entry (FR-7)
# ---------------------------------------------------------------------------

def _read_entry_file(entry_id: str, memory_dir: Path) -> tuple:
    """Read an entry file, returning (frontmatter_dict, body_string)."""
    filepath = memory_dir / f"{entry_id}.md"
    if not filepath.exists():
        raise FileNotFoundError(f"Entry {entry_id} not found at {filepath}")
    content = filepath.read_text()
    parts = content.split("---\n", 2)
    if len(parts) < 3:
        raise ValueError(f"Entry {entry_id} has invalid format (no YAML frontmatter)")
    import yaml
    fm = yaml.safe_load(parts[1])
    body = parts[2]
    return fm, body


def _write_entry_file(entry_id: str, fm: dict, body: str, memory_dir: Path) -> None:
    """Write an entry file with frontmatter and body."""
    import yaml
    frontmatter_yaml = yaml.dump(fm, default_flow_style=False, allow_unicode=True, sort_keys=False).strip()
    content = f"---\n{frontmatter_yaml}\n---\n{body}"
    if not content.endswith("\n"):
        content += "\n"
    filepath = memory_dir / f"{entry_id}.md"
    filepath.write_text(content)


def update_entry(entry_id: str, body: str, *, memory_dir: str) -> None:
    """
    Replace the body of an existing entry. Preserves id, created_at, source.
    Bumps last_used_at to now (FR-7).
    """
    mem_path = Path(memory_dir)
    fm, _ = _read_entry_file(entry_id, mem_path)
    now = datetime.now(timezone.utc).isoformat()
    fm["last_used_at"] = now
    _write_entry_file(entry_id, fm, body, mem_path)
    logger.debug(f"update_entry: updated {entry_id}")


def supersede_entry(old_id: str, new_id: str, *, memory_dir: str) -> None:
    """
    Mark old entry as type:superseded and set new entry's supersedes link.
    Neither entry is deleted (FR-4, FR-7).
    """
    mem_path = Path(memory_dir)

    # Update old entry: mark as superseded
    old_fm, old_body = _read_entry_file(old_id, mem_path)
    old_fm["type"] = "superseded"
    _write_entry_file(old_id, old_fm, old_body, mem_path)

    # Update new entry: link back
    new_fm, new_body = _read_entry_file(new_id, mem_path)
    new_fm["supersedes"] = old_id
    _write_entry_file(new_id, new_fm, new_body, mem_path)

    logger.debug(f"supersede_entry: {old_id} -> {new_id}")


def expire_entry(entry_id: str, *, memory_dir: str) -> None:
    """
    Set valid_until to now. Entry remains on disk; reader filters it out (FR-4).
    """
    mem_path = Path(memory_dir)
    fm, body = _read_entry_file(entry_id, mem_path)
    fm["valid_until"] = datetime.now(timezone.utc).isoformat()
    _write_entry_file(entry_id, fm, body, mem_path)
    logger.debug(f"expire_entry: expired {entry_id}")


# ---------------------------------------------------------------------------
# Reader: read_entries with valid_until filtering (FR-4) + last_used_at bump (FR-5)
# ---------------------------------------------------------------------------

# Debounce tracking: entry_id → last bump timestamp (in-process only)
_last_used_bump_tracker: dict = {}


def read_entries(memory_dir: str) -> list[dict]:
    """
    Read all memory entries, filtering out expired ones.

    FR-4: Entries with valid_until in the past are excluded from results
          but NEVER deleted from disk.
    FR-5: Bumps last_used_at on read (debounced: ≤1x per entry per minute).
    FR-6: Legacy entries without frontmatter are treated as type: unknown.

    Returns list of dicts with keys: id, type, body, created_at, last_used_at,
    source, valid_until, supersedes, evidence.
    """
    import yaml

    mem_path = Path(memory_dir)
    if not mem_path.exists():
        return []

    now = datetime.now(timezone.utc)
    entries = []
    global _last_used_bump_tracker

    for filepath in sorted(mem_path.glob("*.md")):
        try:
            content = filepath.read_text()
        except Exception:
            logger.warning(f"read_entries: cannot read {filepath}, skipping")
            continue

        # Parse frontmatter (or treat as legacy)
        fm = {}
        body = content
        entry_id = filepath.stem

        if content.startswith("---\n"):
            parts = content.split("---\n", 2)
            if len(parts) >= 3:
                try:
                    fm = yaml.safe_load(parts[1]) or {}
                except Exception:
                    logger.warning(f"read_entries: invalid YAML in {filepath}")
                body = parts[2]

        # Defaults for missing fields
        entry_type = fm.get("type", "unknown")
        valid_until_str = fm.get("valid_until")

        # ── FR-4: Filter expired entries ──
        if valid_until_str is not None:
            try:
                valid_until = datetime.fromisoformat(str(valid_until_str))
                if valid_until < now:
                    logger.debug(f"read_entries: filtered {entry_id}, reason: valid_until_past")
                    continue  # skip this entry
            except (ValueError, TypeError):
                pass  # malformed valid_until → include it

        # ── FR-5: Bump last_used_at (debounced) ──
        should_bump = True
        if entry_id in _last_used_bump_tracker:
            last_bump = _last_used_bump_tracker[entry_id]
            if (now - last_bump).total_seconds() < 60:
                should_bump = False

        if should_bump and fm:
            try:
                fm["last_used_at"] = now.isoformat()
                _write_entry_file(entry_id, fm, body, mem_path)
                _last_used_bump_tracker[entry_id] = now
            except Exception:
                # FR-5: Do not block recall on metadata I/O failure
                logger.debug(f"read_entries: failed to bump last_used_at for {entry_id}")

        entries.append({
            "id": entry_id,
            "type": entry_type,
            "body": body.strip(),
            "created_at": fm.get("created_at"),
            "last_used_at": fm.get("last_used_at"),
            "source": fm.get("source"),
            "valid_until": fm.get("valid_until"),
            "supersedes": fm.get("supersedes"),
            "evidence": fm.get("evidence"),
        })

    return entries
