"""
hermes_trust — Trust Plane Integration (Epic 6)

FR-41: Attestation pre-flight before lock acquire (create_dream_artifact calls).
FR-42: Attestation rebaseline after apply (apply_dream calls).
FR-43: soul-guardian carve-out verification at every create start.
FR-44: Ed25519 sign-on-apply (real Ed25519 via `cryptography`).
NFR-15: Signing keys loaded from `HERMES_DREAM_SIGNING_KEY_HEX` env (or
        ~/.hermes/keys/dream.ed25519); never inlined.
NFR-19: Hash-chained audit log — delegates to hermes_dream.write_audit
        (one canonical format, no parallel chain).

DN4: This module's `~/.hermes/attestation/.attest-state.json` is the
     Hermes-private attestation state — separate from the JS skill's
     `~/.hermes/security/attestations/current.json`. Bridging the two is
     tracked as Epic 6 follow-up.
"""
from __future__ import annotations

import errno
import fnmatch
import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Data models
# ─────────────────────────────────────────────────────────────────────────────


_DriftSeverity = Literal["ok", "warning", "critical"]


class AttestationResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    passed: bool
    severity: _DriftSeverity = "ok"
    found_during: str = "dream-create"
    details: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class AuditRow(BaseModel):
    """In-memory shape returned by append_audit_row. The on-disk format is
    Epic 4's flat shape (delegated to hermes_dream.write_audit) — see P7."""
    model_config = ConfigDict(frozen=True)
    op: str
    dream_id: str
    ts: str
    hash: str
    prev_hash: str


# ─────────────────────────────────────────────────────────────────────────────
# Path resolution
# ─────────────────────────────────────────────────────────────────────────────


def _hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes"))


def _resolve_attestation_dir(override: Optional[str] = None) -> Path:
    if override:
        return Path(override)
    env = os.environ.get("HERMES_ATTESTATION_DIR")
    if env:
        return Path(env)
    return _hermes_home() / "attestation"


def _resolve_soul_guardian_dir(override: Optional[str] = None) -> Path:
    if override:
        return Path(override)
    env = os.environ.get("HERMES_SOUL_GUARDIAN_DIR")
    if env:
        return Path(env)
    return _hermes_home() / "soul-guardian"


def _resolve_observability_dir() -> Path:
    env = os.environ.get("HERMES_OBSERVABILITY_DIR")
    if env:
        return Path(env)
    return _hermes_home() / "observability"


# ─────────────────────────────────────────────────────────────────────────────
# Advisory emission (P10)
# ─────────────────────────────────────────────────────────────────────────────


def write_advisory(kind: str, severity: str, details: dict) -> None:
    """Append one advisory row to ~/.hermes/observability/advisory.jsonl.
    Used for attestation drift, soul-guardian misconfig, audit-chain break.
    """
    obs = _resolve_observability_dir()
    obs.mkdir(parents=True, mode=0o700, exist_ok=True)
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "kind": kind,
        "severity": severity,
        **details,
    }
    line = (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    fd = os.open(
        str(obs / "advisory.jsonl"),
        os.O_WRONLY | os.O_CREAT | os.O_APPEND,
        0o600,
    )
    try:
        os.write(fd, line)
    finally:
        os.close(fd)


# ─────────────────────────────────────────────────────────────────────────────
# Story 6.1: Attestation pre-flight (FR-41) — accumulates all drifts (P11)
# ─────────────────────────────────────────────────────────────────────────────


_MAX_PROTECTED_FILE_BYTES = 100 * 1024 * 1024  # P13: 100 MB cap
_VALID_MODES = ("restore", "alert", "ignore")


def _safe_resolve(filename: str, root: Path) -> Optional[Path]:
    """P12: reject `..` / absolute paths / symlinks that escape `root`."""
    if not filename or filename.startswith("/"):
        return None
    candidate = (root / filename).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def _hash_file_safely(path: Path) -> Optional[str]:
    """P13: refuse symlinks; cap size; sha256 the bytes."""
    try:
        if path.is_symlink():
            return None
        st = path.stat()
    except OSError:
        return None
    if st.st_size > _MAX_PROTECTED_FILE_BYTES:
        return None
    h = hashlib.sha256()
    try:
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def run_attestation_preflight(
    attestation_dir: Optional[str] = None,
    *,
    emit_advisory: bool = True,
) -> AttestationResult:
    """FR-41: Run attestation pre-flight before lock acquire.

    P11: accumulates ALL drifts (no early return on first).
    P10: emits advisory on critical drift.
    P12/P13: path-traversal + symlink + size-cap protection.
    P14/P15: validates mode value; rejects empty expected_hash.
    """
    adir = _resolve_attestation_dir(attestation_dir)
    state_path = adir / ".attest-state.json"

    if not state_path.exists():
        return AttestationResult(passed=True, severity="ok",
                                 details=["no attestation state found"])
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return AttestationResult(
            passed=False, severity="critical", found_during="dream-create",
            details=["cannot read attestation state"],
        )

    root = adir.parent  # files live relative to attestation dir's parent
    protected = state.get("protected") or {}

    warnings: list[str] = []
    critical_details: list[str] = []
    severity: _DriftSeverity = "ok"

    for filename, entry in protected.items():
        if not isinstance(entry, dict):
            critical_details.append(f"{filename}: malformed entry (not a dict)")
            severity = "critical"
            continue

        expected_hash = entry.get("sha256", "")
        # P15: reject empty hash (bypass primitive).
        if not isinstance(expected_hash, str) or not expected_hash:
            critical_details.append(f"{filename}: missing or empty sha256")
            severity = "critical"
            continue

        # P14: validate mode value.
        mode = entry.get("mode", "restore")
        if mode not in _VALID_MODES:
            critical_details.append(
                f"{filename}: invalid mode {mode!r} (allowed: {_VALID_MODES})"
            )
            severity = "critical"
            continue

        # P12: path-traversal protection.
        file_path = _safe_resolve(filename, root)
        if file_path is None:
            critical_details.append(f"{filename}: path traversal or absolute path rejected")
            severity = "critical"
            continue

        if not file_path.exists():
            warnings.append(f"{filename}: file missing")
            if severity == "ok":
                severity = "warning"
            continue

        actual_hash = _hash_file_safely(file_path)
        if actual_hash is None:
            warnings.append(f"{filename}: cannot hash (symlink/oversize/IO)")
            if severity == "ok":
                severity = "warning"
            continue

        if actual_hash != expected_hash:
            if mode == "restore":
                critical_details.append(f"{filename}: hash mismatch (mode=restore)")
                severity = "critical"
            elif mode == "alert":
                warnings.append(f"{filename}: hash mismatch (mode=alert)")
                if severity == "ok":
                    severity = "warning"
            # mode=ignore: no-op

    passed = severity != "critical"
    result = AttestationResult(
        passed=passed,
        severity=severity,
        found_during="dream-create",
        details=critical_details,
        warnings=warnings,
    )

    # P10: emit advisory on critical drift.
    if not passed and emit_advisory:
        try:
            write_advisory(
                "attestation-drift", "critical",
                {"found_during": "dream-create", "details": critical_details,
                 "warnings": warnings},
            )
        except OSError as e:
            logger.warning("write_advisory failed: %s", e)

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Story 6.2: soul-guardian carve-out (FR-43, Hard Invariant #5)
# ─────────────────────────────────────────────────────────────────────────────


_CARVE_OUT_GLOBS = (
    "dreams/",          # ~/.hermes/dreams/
    "dreams/**",
    "raw/",             # ~/.hermes/raw/
    "raw/**",
    "agents/*/memory/",  # per-role memory dirs
    "agents/*/memory/**",
    "memory/typed/",
    "memory/typed/**",
)


def _is_memory_or_dreams_path(path: str) -> bool:
    """P19: precise glob match (not substring). True iff `path` is a
    memory/dreams/raw subpath that must NOT be in the protected set."""
    p = path.lstrip("./").rstrip("/")
    for glob in _CARVE_OUT_GLOBS:
        if fnmatch.fnmatch(p, glob.rstrip("/")) or fnmatch.fnmatch(p + "/", glob):
            return True
    return False


def check_soul_guardian_carveout(
    policy_dir: Optional[str] = None,
    *,
    emit_advisory: bool = True,
) -> dict:
    """FR-43 / Hard Invariant #5: verify soul-guardian's protected set
    does NOT include memory/dreams/raw subpaths in `restore` mode.

    P20: `alert` mode allowed (monitor-only).
    Returns {"ok": bool, "reason": str, "offenders": [...]}.
    """
    import yaml as _yaml

    pdir = _resolve_soul_guardian_dir(policy_dir)
    policy_path = pdir / "policy.yaml"

    if not policy_path.exists():
        return {"ok": True, "reason": "no policy file — assuming clean", "offenders": []}

    try:
        policy = _yaml.safe_load(policy_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {"ok": False, "reason": "cannot parse soul-guardian policy", "offenders": []}

    protected = policy.get("protected") or {}
    if not isinstance(protected, dict):
        return {"ok": False, "reason": "protected: must be a mapping", "offenders": []}

    offenders: list[str] = []
    for path, config in protected.items():
        if not isinstance(config, dict):
            offenders.append(f"{path}: malformed entry (not a dict)")
            continue
        mode = config.get("mode", "")
        if not isinstance(mode, str):
            offenders.append(f"{path}: mode must be a string (got {type(mode).__name__})")
            continue
        # P19: precise match.
        if _is_memory_or_dreams_path(str(path)):
            # P20: restore-mode is forbidden; alert-mode is allowed.
            if mode == "restore":
                offenders.append(
                    f"{path}: memory/dreams path in mode=restore "
                    "(must be 'ignore' or 'alert')"
                )

    if offenders:
        result = {
            "ok": False,
            "reason": "soul-guardian carve-out violated",
            "offenders": offenders,
        }
        if emit_advisory:
            try:
                write_advisory("soul-guardian-misconfig", "critical",
                               {"offenders": offenders})
            except OSError as e:
                logger.warning("write_advisory failed: %s", e)
        return result

    return {"ok": True, "reason": "carve-out verified", "offenders": []}


# ─────────────────────────────────────────────────────────────────────────────
# Story 6.3: Ed25519 sign-on-apply (FR-44, NFR-15)
# ─────────────────────────────────────────────────────────────────────────────

_SIGN_ALG = "ed25519"


def _load_signing_key() -> Optional[bytes]:
    """NFR-15: load 32-byte Ed25519 seed from env or keyfile.
    Returns None if no key configured."""
    hex_key = os.environ.get("HERMES_DREAM_SIGNING_KEY_HEX")
    if hex_key:
        try:
            seed = bytes.fromhex(hex_key.strip())
        except ValueError:
            logger.error("HERMES_DREAM_SIGNING_KEY_HEX: invalid hex")
            return None
        if len(seed) != 32:
            logger.error("HERMES_DREAM_SIGNING_KEY_HEX: must be 32 bytes (got %d)", len(seed))
            return None
        return seed

    keyfile = _hermes_home() / "keys" / "dream.ed25519"
    if keyfile.exists():
        try:
            raw = keyfile.read_bytes().strip()
        except OSError:
            return None
        if len(raw) == 32:
            return raw
        if len(raw) == 64:  # hex-encoded
            try:
                return bytes.fromhex(raw.decode("ascii"))
            except ValueError:
                return None
    return None


def _ed25519_sign_real(seed: bytes, message: bytes) -> tuple[bytes, bytes]:
    """Real Ed25519 via cryptography library.
    Returns (signature_64_bytes, public_key_32_bytes)."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    priv = Ed25519PrivateKey.from_private_bytes(seed)
    sig = priv.sign(message)
    pub_bytes = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return sig, pub_bytes


def _ed25519_verify_real(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """Real Ed25519 verify via cryptography library."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.exceptions import InvalidSignature
    try:
        pub = Ed25519PublicKey.from_public_bytes(public_key)
        pub.verify(signature, message)
        return True
    except (InvalidSignature, ValueError):
        return False


def sign_patches(
    dream_dir: str,
    signing_key: Optional[bytes] = None,
) -> dict:
    """FR-44 + NFR-15: sign all *.patch files in the artifact with Ed25519.

    `signing_key` override is test-only; production loads from env/keyfile.
    Refuses to sign with a random throwaway key — raises ValueError if no
    key is configured.

    Writes sign.json with `alg`, `version`, `public_key_hex`, and signatures.
    """
    dream_path = Path(dream_dir)
    if signing_key is None:
        signing_key = _load_signing_key()
        if signing_key is None:
            raise ValueError(
                "sign_patches: no signing key configured. Set "
                "HERMES_DREAM_SIGNING_KEY_HEX (32-byte hex) or place a 32-byte "
                "(or 64-char hex) key at ~/.hermes/keys/dream.ed25519. "
                "Refusing to sign with a throwaway random key (NFR-15)."
            )

    private_dir = dream_path / ".hermes-private"
    private_dir.mkdir(mode=0o700, exist_ok=True)
    # P15 hygiene: tighten mode even if dir pre-existed.
    try:
        private_dir.chmod(0o700)
    except OSError:
        pass

    # P21: enumerate all *.patch files, including skills.proposed/*.patch.
    patch_files: list[Path] = []
    for p in sorted(dream_path.glob("*.patch")):
        if p.is_file() and not p.is_symlink():
            patch_files.append(p)
    skills_proposed = dream_path / "skills.proposed"
    if skills_proposed.is_dir():
        for p in sorted(skills_proposed.glob("*.patch")):
            if p.is_file() and not p.is_symlink():
                patch_files.append(p)

    sign_path = private_dir / "sign.json"
    # P23: refuse to overwrite an existing signature (avoids silent rotation).
    if sign_path.exists():
        raise FileExistsError(
            f"sign_patches: {sign_path} already exists. Refuse silent overwrite; "
            "discard and re-create the dream to re-sign."
        )

    # Derive public key once.
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    priv = Ed25519PrivateKey.from_private_bytes(signing_key)
    pub_hex = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()

    signed_at = datetime.now(timezone.utc).isoformat()
    signatures: list[dict] = []
    for patch_file in patch_files:
        patch_content = patch_file.read_bytes()
        sig = priv.sign(patch_content)
        signatures.append({
            "patch_filename": str(patch_file.relative_to(dream_path)),
            "signature": sig.hex(),
            "signed_at": signed_at,
        })

    sign_data = {
        "alg": _SIGN_ALG,
        "version": 1,
        "dream_id": dream_path.name,
        "signed_at": signed_at,
        "public_key_hex": pub_hex,
        "signatures": signatures,
    }

    # Atomic write.
    tmp = sign_path.with_suffix(sign_path.suffix + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, (json.dumps(sign_data, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    os.replace(tmp, sign_path)

    return sign_data


def verify_signature(
    dream_dir: str,
    signature_data: dict,
    public_key: Optional[bytes] = None,
) -> dict:
    """Verify signatures in sign.json.

    If `public_key` is None, uses the public_key_hex embedded in sign.json
    (FR-44 self-describing artifact). Returns {"verified", "total", "mismatches"}.
    """
    dream_path = Path(dream_dir).resolve()

    if public_key is None:
        pub_hex = signature_data.get("public_key_hex", "")
        if not pub_hex:
            return {"verified": 0, "total": 0,
                    "mismatches": ["missing public_key_hex in sign.json"]}
        try:
            public_key = bytes.fromhex(pub_hex)
        except ValueError:
            return {"verified": 0, "total": 0,
                    "mismatches": ["invalid public_key_hex"]}

    alg = signature_data.get("alg", "")
    if alg and alg != _SIGN_ALG:
        return {"verified": 0, "total": 0,
                "mismatches": [f"unsupported alg: {alg}"]}

    verified = 0
    total = 0
    mismatches: list[str] = []

    for sig_entry in signature_data.get("signatures", []):
        total += 1
        filename = sig_entry.get("patch_filename", "")
        if not filename:
            mismatches.append("missing patch_filename")
            continue
        # P12: path-traversal protection on filename.
        safe = _safe_resolve(filename, dream_path)
        if safe is None:
            mismatches.append(f"{filename}: path traversal rejected")
            continue
        if not safe.exists():
            mismatches.append(f"{filename}: file not found")
            continue
        try:
            sig_bytes = bytes.fromhex(sig_entry.get("signature", ""))
        except ValueError:
            mismatches.append(f"{filename}: malformed signature hex")
            continue
        patch_content = safe.read_bytes()
        if _ed25519_verify_real(public_key, patch_content, sig_bytes):
            verified += 1
        else:
            mismatches.append(f"{filename}: signature mismatch")

    return {"verified": verified, "total": total, "mismatches": mismatches}


# ─────────────────────────────────────────────────────────────────────────────
# Story 6.4: rebaseline (FR-42) — actually re-hashes protected files (P16)
# ─────────────────────────────────────────────────────────────────────────────


def rebaseline_attestation(
    dream_dir: str,
    attestation_dir: Optional[str] = None,
) -> dict:
    """FR-42: Rebaseline attestation after successful apply.

    P16: actually re-hashes all protected files (was a stub).
    P17: only stamps the manifest on success (no lying on partial failure).
    P18: atomic writes via tmp + os.replace.
    """
    import uuid
    adir = _resolve_attestation_dir(attestation_dir)
    dream_path = Path(dream_dir)
    rebaseline_id = uuid.uuid4().hex[:12]

    state_path = adir / ".attest-state.json"
    if not state_path.exists():
        return {"ok": False, "reason": "no attestation state to rebaseline"}

    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return {"ok": False, "reason": f"cannot read attestation state: {e}"}

    protected = state.get("protected") or {}
    root = adir.parent
    rehashed: list[str] = []
    skipped: list[str] = []

    for filename, entry in protected.items():
        if not isinstance(entry, dict):
            continue
        file_path = _safe_resolve(filename, root)
        if file_path is None or not file_path.exists():
            skipped.append(filename)
            continue
        new_hash = _hash_file_safely(file_path)
        if new_hash is None:
            skipped.append(filename)
            continue
        entry["sha256"] = new_hash
        rehashed.append(filename)

    state["last_rebaseline"] = {
        "id": rebaseline_id,
        "dream_id": dream_path.name,
        "ts": datetime.now(timezone.utc).isoformat(),
        "rehashed_count": len(rehashed),
    }

    # P18: atomic write of state.
    tmp = state_path.with_suffix(state_path.suffix + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, (json.dumps(state, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    os.replace(tmp, state_path)

    # P17: manifest stamped only on state-write success.
    manifest_path = dream_path / "manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["attestation_rebaselined"] = True
            manifest["rebaseline_id"] = rebaseline_id
            tmp_m = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
            fd = os.open(str(tmp_m), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.write(fd, (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
            finally:
                os.close(fd)
            os.replace(tmp_m, manifest_path)
        except (OSError, json.JSONDecodeError) as e:
            return {
                "ok": False,
                "reason": f"state rebaselined but manifest update failed: {e}",
                "rebaseline_id": rebaseline_id,
            }

    return {
        "ok": True,
        "rebaseline_id": rebaseline_id,
        "rehashed_count": len(rehashed),
        "skipped": skipped,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Story 6.4: hash-chained audit — DELEGATES to Epic 4 write_audit (P7/DN2)
# ─────────────────────────────────────────────────────────────────────────────


def append_audit_row(
    op: str,
    scope: str,
    *,
    audit_path: Optional[str] = None,
    extras: Optional[dict] = None,
) -> AuditRow:
    """Append a hash-chained audit row.

    DN2 / P7: delegates to hermes_dream.write_audit for the canonical format.
    Backwards-compatible wrapper for Epic 6 callers that used the old API.
    """
    from lib.hermes_dream import write_audit, _resolve_dreams_dir

    # The Epic 4 audit log is keyed under dreams_dir; respect any override.
    dreams_dir_arg: Optional[str] = None
    if audit_path:
        # If a custom audit path was given, derive the dreams_dir.
        ap = Path(audit_path)
        if ap.name == "audit.jsonl":
            dreams_dir_arg = str(ap.parent)
        else:
            dreams_dir_arg = str(ap.parent)

    digest = write_audit(
        op, dream_id=scope, actor=None,
        extra=extras or {},
        dreams_dir=dreams_dir_arg,
    )
    return AuditRow(
        op=op,
        dream_id=scope,
        ts=datetime.now(timezone.utc).isoformat(),
        hash=digest,
        prev_hash="",  # not exposed by write_audit's return; lookup if needed
    )


def verify_audit_chain(audit_path: str) -> dict:
    """DN2 / P28: delegate to hermes_dream.verify_audit_chain (the canonical
    implementation). Returns {"valid": bool, "rows_checked": int,
    "first_error": str | None}.
    """
    from lib.hermes_dream import verify_audit_chain as _verify
    p = Path(audit_path)
    if not p.exists():
        return {"valid": True, "rows_checked": 0, "first_error": None}

    # _verify takes a dreams_dir. Derive it.
    if p.name == "audit.jsonl":
        dreams_dir = str(p.parent)
    else:
        dreams_dir = str(p.parent)

    valid = _verify(dreams_dir)
    # Count rows for the return shape.
    rows = 0
    try:
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    rows += 1
    except OSError:
        rows = 0

    if not valid and rows > 0:
        # P10: advisory on chain break.
        try:
            write_advisory(
                "audit-chain-broken", "critical",
                {"audit_path": str(p), "rows_checked": rows},
            )
        except OSError:
            pass
    return {
        "valid": valid,
        "rows_checked": rows,
        "first_error": None if valid else "chain validation failed (see dream audit)",
    }
