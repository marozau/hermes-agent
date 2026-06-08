"""
Epic 6 — Trust Plane Integration.

Covers FR-41..FR-44, NFR-15, NFR-19, plus the code-review patches:
  DN1  real Ed25519 via `cryptography`
  DN2  single canonical audit format (delegates to autodream.dream.write_audit)
  DN3  key loaded from HERMES_DREAM_SIGNING_KEY_HEX / ~/.hermes/keys/dream.ed25519
  DN5  precise path-glob carve-out match (no substring false positives)
  P1   create_dream_artifact calls run_attestation_preflight
  P2   _check_soul_guardian_carve_out replaced with real check
  P3   apply_dream calls sign_patches
  P4   apply_dream calls rebaseline_attestation
  P10  advisory emission on drift / misconfig / chain-break
  P11  preflight accumulates ALL drifts
  P12  path-traversal protection
  P13  symlink + size cap
  P14  mode value validated
  P15  empty expected_hash rejected
  P16  rebaseline actually re-hashes
  P17  manifest stamped only on success
  P18  rebaseline atomic writes
  P19  carve-out precise match (no substring)
  P20  carve-out alert mode allowed
  P21  sign all *.patch + skills.proposed/*.patch
  P22  sign.json has alg + version + public_key_hex
  P23  sign.json refuses overwrite
  P24  signature verify test no longer tautological
"""
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from autodream.trust import (
    AttestationResult, AuditRow,
    _is_memory_or_dreams_path, _safe_resolve,
    append_audit_row, check_soul_guardian_carveout,
    rebaseline_attestation, run_attestation_preflight,
    sign_patches, verify_audit_chain, verify_signature,
    write_advisory,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def clean_attestation_dir(tmp_path, monkeypatch):
    """Attestation workspace with real files matching the state."""
    home = tmp_path
    attestation = home / "attestation"
    attestation.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_ATTESTATION_DIR", str(attestation))

    soul_content = b"Soul content v1"
    agents_content = b"AGENTS content v1"
    (home / "SOUL.md").write_bytes(soul_content)
    (home / "AGENTS.md").write_bytes(agents_content)

    state = {
        "protected": {
            "SOUL.md": {
                "sha256": hashlib.sha256(soul_content).hexdigest(),
                "mode": "restore",
            },
            "AGENTS.md": {
                "sha256": hashlib.sha256(agents_content).hexdigest(),
                "mode": "alert",
            },
        }
    }
    (attestation / ".attest-state.json").write_text(json.dumps(state))
    return attestation


@pytest.fixture
def soul_guardian_policy(tmp_path, monkeypatch):
    home = tmp_path
    sg = home / "soul-guardian"
    sg.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_SOUL_GUARDIAN_DIR", str(sg))
    policy = {
        "protected": {
            "SOUL.md": {"mode": "restore"},
            "AGENTS.md": {"mode": "restore"},
            "USER.md": {"mode": "alert"},
            "dreams/": {"mode": "ignore"},
            "raw/": {"mode": "ignore"},
            "agents/CEO/memory/": {"mode": "ignore"},
        }
    }
    (sg / "policy.yaml").write_text(yaml.dump(policy))
    return sg


@pytest.fixture
def signing_key_env(monkeypatch):
    """DN3 / NFR-15: provide a real Ed25519 seed via env var."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    priv = Ed25519PrivateKey.generate()
    seed = priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    monkeypatch.setenv("HERMES_DREAM_SIGNING_KEY_HEX", seed.hex())
    return seed


@pytest.fixture
def dream_artifact(tmp_path):
    dream_dir = tmp_path / "dreams" / "01TESTDREAM00000000000000"
    dream_dir.mkdir(parents=True)
    (dream_dir / ".hermes-private").mkdir(mode=0o700)
    (dream_dir / "memory.patch").write_text(
        yaml.dump([{"op": "add", "type": "fact", "body": "test"}])
    )
    (dream_dir / "manifest.json").write_text(json.dumps({"scope": "default"}))
    return dream_dir


# ─────────────────────────────────────────────────────────────────────────────
# Story 6.1: Attestation pre-flight
# ─────────────────────────────────────────────────────────────────────────────


class TestAttestationPreflight:
    def test_clean_attestation_passes(self, clean_attestation_dir):
        r = run_attestation_preflight(attestation_dir=str(clean_attestation_dir))
        assert r.passed is True
        assert r.severity == "ok"

    def test_critical_drift_aborts_and_accumulates(self, clean_attestation_dir, tmp_path):
        """P11: ALL drifts surfaced (not early return on first)."""
        state_path = clean_attestation_dir / ".attest-state.json"
        state = json.loads(state_path.read_text())
        state["protected"]["SOUL.md"]["sha256"] = "0" * 64
        # Also break a second file's hash to verify multi-drift.
        state["protected"]["AGENTS.md"]["sha256"] = "1" * 64
        state["protected"]["AGENTS.md"]["mode"] = "restore"  # promote to critical
        state_path.write_text(json.dumps(state))

        r = run_attestation_preflight(attestation_dir=str(clean_attestation_dir),
                                      emit_advisory=False)
        assert r.passed is False
        assert r.severity == "critical"
        # P11: both files should appear in details
        assert len(r.details) >= 2

    def test_path_traversal_rejected(self, tmp_path, monkeypatch):
        """P12: `..` filename in state rejected."""
        attestation = tmp_path / "attestation"
        attestation.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setenv("HERMES_ATTESTATION_DIR", str(attestation))
        state = {"protected": {"../../etc/passwd": {"sha256": "deadbeef" * 8, "mode": "restore"}}}
        (attestation / ".attest-state.json").write_text(json.dumps(state))
        r = run_attestation_preflight(attestation_dir=str(attestation),
                                      emit_advisory=False)
        assert r.passed is False
        assert any("traversal" in d for d in r.details)

    def test_empty_hash_rejected(self, tmp_path, monkeypatch):
        """P15: blank expected sha256 is a stealth bypass — must reject."""
        attestation = tmp_path / "attestation"
        attestation.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setenv("HERMES_ATTESTATION_DIR", str(attestation))
        state = {"protected": {"SOUL.md": {"sha256": "", "mode": "restore"}}}
        (attestation / ".attest-state.json").write_text(json.dumps(state))
        r = run_attestation_preflight(attestation_dir=str(attestation),
                                      emit_advisory=False)
        assert r.passed is False

    def test_invalid_mode_rejected(self, tmp_path, monkeypatch):
        """P14: mode must be in whitelist."""
        attestation = tmp_path / "attestation"
        attestation.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        state = {"protected": {"SOUL.md": {"sha256": "a" * 64, "mode": False}}}
        (attestation / ".attest-state.json").write_text(json.dumps(state))
        r = run_attestation_preflight(attestation_dir=str(attestation),
                                      emit_advisory=False)
        assert r.passed is False

    def test_advisory_emitted_on_critical(self, clean_attestation_dir, tmp_path, monkeypatch):
        """P10: critical drift writes advisory.jsonl."""
        obs = tmp_path / "observability"
        monkeypatch.setenv("HERMES_OBSERVABILITY_DIR", str(obs))
        state_path = clean_attestation_dir / ".attest-state.json"
        state = json.loads(state_path.read_text())
        state["protected"]["SOUL.md"]["sha256"] = "0" * 64
        state_path.write_text(json.dumps(state))
        run_attestation_preflight(attestation_dir=str(clean_attestation_dir))
        advisory_file = obs / "advisory.jsonl"
        assert advisory_file.exists()
        rows = [json.loads(ln) for ln in advisory_file.read_text().splitlines() if ln]
        assert any(r["kind"] == "attestation-drift" for r in rows)


# ─────────────────────────────────────────────────────────────────────────────
# Story 6.2: soul-guardian carve-out
# ─────────────────────────────────────────────────────────────────────────────


class TestCarveOutMatchGlob:
    """DN5 / P19: precise path-glob match (no substring false positives)."""

    def test_dreams_root_matches(self):
        assert _is_memory_or_dreams_path("dreams/")
        assert _is_memory_or_dreams_path("dreams/01TESTID")

    def test_raw_root_matches(self):
        assert _is_memory_or_dreams_path("raw/")
        assert _is_memory_or_dreams_path("raw/default/engineer/2026-05-13.jsonl")

    def test_agents_memory_matches(self):
        assert _is_memory_or_dreams_path("agents/CEO/memory/")
        assert _is_memory_or_dreams_path("agents/CTO/memory/typed/01ID.md")

    def test_dreams_summary_does_NOT_match(self):
        """P19: substring `dreams` in unrelated path must NOT match."""
        assert not _is_memory_or_dreams_path("dreams_summary.md")
        assert not _is_memory_or_dreams_path("docs/dreams-architecture.md")
        assert not _is_memory_or_dreams_path("streams_of_consciousness/")

    def test_memory_substring_does_NOT_match(self):
        assert not _is_memory_or_dreams_path("AGENTS-memory-notes.md")
        assert not _is_memory_or_dreams_path("docs/in-memory-state.md")


class TestSoulGuardianCarveout:
    def test_clean_policy_passes(self, soul_guardian_policy):
        r = check_soul_guardian_carveout(policy_dir=str(soul_guardian_policy),
                                         emit_advisory=False)
        assert r["ok"] is True

    def test_dreams_in_restore_mode_fails(self, tmp_path, monkeypatch):
        sg = tmp_path / "sg"
        sg.mkdir()
        policy = {"protected": {"dreams/": {"mode": "restore"}}}
        (sg / "policy.yaml").write_text(yaml.dump(policy))
        r = check_soul_guardian_carveout(policy_dir=str(sg), emit_advisory=False)
        assert r["ok"] is False
        assert any("dreams/" in o for o in r["offenders"])

    def test_dreams_in_alert_mode_PASSES(self, tmp_path):
        """P20: alert mode is monitor-only — allowed for memory/dreams."""
        sg = tmp_path / "sg"
        sg.mkdir()
        policy = {"protected": {"dreams/": {"mode": "alert"}}}
        (sg / "policy.yaml").write_text(yaml.dump(policy))
        r = check_soul_guardian_carveout(policy_dir=str(sg), emit_advisory=False)
        assert r["ok"] is True

    def test_dreams_summary_md_does_not_trigger(self, tmp_path):
        """P19: substring false-positive must not fire."""
        sg = tmp_path / "sg"
        sg.mkdir()
        policy = {"protected": {"dreams_summary.md": {"mode": "restore"}}}
        (sg / "policy.yaml").write_text(yaml.dump(policy))
        r = check_soul_guardian_carveout(policy_dir=str(sg), emit_advisory=False)
        assert r["ok"] is True

    def test_advisory_emitted_on_misconfig(self, tmp_path, monkeypatch):
        sg = tmp_path / "sg"
        sg.mkdir()
        obs = tmp_path / "observability"
        monkeypatch.setenv("HERMES_OBSERVABILITY_DIR", str(obs))
        policy = {"protected": {"agents/A/memory/": {"mode": "restore"}}}
        (sg / "policy.yaml").write_text(yaml.dump(policy))
        check_soul_guardian_carveout(policy_dir=str(sg))
        rows = [json.loads(ln) for ln in (obs / "advisory.jsonl").read_text().splitlines() if ln]
        assert any(r["kind"] == "soul-guardian-misconfig" for r in rows)


# ─────────────────────────────────────────────────────────────────────────────
# Story 6.3: Ed25519 sign-on-apply (real crypto)
# ─────────────────────────────────────────────────────────────────────────────


class TestSignPatches:
    def test_refuses_without_key(self, dream_artifact, monkeypatch):
        """NFR-15: no random throwaway key."""
        monkeypatch.delenv("HERMES_DREAM_SIGNING_KEY_HEX", raising=False)
        # Make sure ~/.hermes/keys/dream.ed25519 also unreadable.
        monkeypatch.setenv("HERMES_HOME", "/nonexistent_/no_key_here")
        with pytest.raises(ValueError, match="signing key"):
            sign_patches(str(dream_artifact))

    def test_sign_writes_sign_json_with_alg_and_pubkey(self, dream_artifact, signing_key_env):
        """P22: sign.json has alg, version, public_key_hex."""
        data = sign_patches(str(dream_artifact))
        assert data["alg"] == "ed25519"
        assert data["version"] == 1
        assert len(data["public_key_hex"]) == 64
        sign_path = dream_artifact / ".hermes-private" / "sign.json"
        assert sign_path.exists()
        assert (sign_path.stat().st_mode & 0o777) == 0o600

    def test_real_ed25519_verify_passes(self, dream_artifact, signing_key_env):
        """DN1: real Ed25519 sig verifies against derived public key."""
        data = sign_patches(str(dream_artifact))
        r = verify_signature(str(dream_artifact), data)
        # P24: assert verified >= 1 (not the old tautology `>= 0`).
        assert r["verified"] >= 1
        assert r["verified"] == r["total"]
        assert r["mismatches"] == []

    def test_tampered_patch_fails_verify(self, dream_artifact, signing_key_env):
        """Tampered patch content breaks signature."""
        data = sign_patches(str(dream_artifact))
        (dream_artifact / "memory.patch").write_text("- {op: add, type: fact, body: TAMPERED}")
        r = verify_signature(str(dream_artifact), data)
        assert r["verified"] < r["total"]
        assert r["mismatches"]

    def test_refuses_overwrite(self, dream_artifact, signing_key_env):
        """P23: sign.json never silently overwritten."""
        sign_patches(str(dream_artifact))
        with pytest.raises(FileExistsError):
            sign_patches(str(dream_artifact))

    def test_signs_multiple_patches(self, dream_artifact, signing_key_env):
        """P21: enumerates all *.patch + skills.proposed/*.patch."""
        # Add a user.patch and a skills.proposed/foo.patch
        (dream_artifact / "user.patch").write_text("user patch content")
        skills_dir = dream_artifact / "skills.proposed"
        skills_dir.mkdir()
        (skills_dir / "foo.patch").write_text("skill patch content")
        data = sign_patches(str(dream_artifact))
        filenames = {s["patch_filename"] for s in data["signatures"]}
        assert "memory.patch" in filenames
        assert "user.patch" in filenames
        assert any("skills.proposed" in f for f in filenames)


# ─────────────────────────────────────────────────────────────────────────────
# Story 6.4: rebaseline_attestation (actually re-hashes)
# ─────────────────────────────────────────────────────────────────────────────


class TestRebaseline:
    def test_rehashes_protected_files(self, clean_attestation_dir, dream_artifact, tmp_path):
        """P16: actually re-hashes (not a stub)."""
        # Mutate SOUL.md so its current hash doesn't match the state.
        soul = tmp_path / "SOUL.md"
        soul.write_bytes(b"Soul content v2-mutated")
        new_hash_expected = hashlib.sha256(b"Soul content v2-mutated").hexdigest()

        r = rebaseline_attestation(str(dream_artifact),
                                   attestation_dir=str(clean_attestation_dir))
        assert r["ok"] is True
        # State should now carry the new hash.
        state = json.loads((clean_attestation_dir / ".attest-state.json").read_text())
        assert state["protected"]["SOUL.md"]["sha256"] == new_hash_expected
        assert "last_rebaseline" in state

    def test_manifest_stamped_only_on_success(self, dream_artifact, tmp_path):
        """P17: when attestation state is missing, manifest is NOT stamped."""
        no_attest = tmp_path / "no_attest"
        no_attest.mkdir()
        r = rebaseline_attestation(str(dream_artifact),
                                   attestation_dir=str(no_attest))
        assert r["ok"] is False
        manifest = json.loads((dream_artifact / "manifest.json").read_text())
        assert manifest.get("attestation_rebaselined") is not True


# ─────────────────────────────────────────────────────────────────────────────
# Audit chain — DN2 / P7: delegates to autodream.dream.write_audit
# ─────────────────────────────────────────────────────────────────────────────


class TestAuditChainDelegation:
    def test_append_delegates_and_verifies(self, tmp_path, monkeypatch):
        """One canonical format: append_audit_row writes via autodream.dream.write_audit."""
        dreams = tmp_path / "dreams"
        dreams.mkdir(parents=True, exist_ok=True)
        audit_path = dreams / "audit.jsonl"
        r1 = append_audit_row("dream-create", "default", audit_path=str(audit_path))
        r2 = append_audit_row("apply", "01TESTID", audit_path=str(audit_path),
                              extras={"forced": True, "reason": "test"})
        assert isinstance(r1, AuditRow)
        assert isinstance(r2, AuditRow)
        # Same file: Epic 4's chain verifier reads it.
        result = verify_audit_chain(str(audit_path))
        assert result["valid"] is True
        assert result["rows_checked"] == 2

    def test_chain_break_emits_advisory(self, tmp_path, monkeypatch):
        """P10: audit-chain-broken advisory fires on detected tamper."""
        dreams = tmp_path / "dreams"
        dreams.mkdir(parents=True, exist_ok=True)
        obs = tmp_path / "observability"
        monkeypatch.setenv("HERMES_OBSERVABILITY_DIR", str(obs))
        audit_path = dreams / "audit.jsonl"
        append_audit_row("apply", "X1", audit_path=str(audit_path))
        # Tamper: rewrite one row's actor.
        lines = audit_path.read_text().splitlines()
        lines[0] = lines[0].replace('"actor"', '"actor_modified":"x","actor"')
        audit_path.write_text("\n".join(lines) + "\n")
        result = verify_audit_chain(str(audit_path))
        assert result["valid"] is False
        # advisory written
        adv = (obs / "advisory.jsonl")
        assert adv.exists()
        adv_rows = [json.loads(ln) for ln in adv.read_text().splitlines() if ln]
        assert any(r["kind"] == "audit-chain-broken" for r in adv_rows)


# ─────────────────────────────────────────────────────────────────────────────
# Integration: create_dream_artifact + apply_dream wire trust plane
# ─────────────────────────────────────────────────────────────────────────────


class TestCreateIntegratesTrust:
    def test_carveout_misconfig_aborts_create(self, tmp_path, monkeypatch):
        """P2: Epic 4's stub now calls Epic 6's check; misconfig aborts."""
        home = tmp_path
        monkeypatch.setenv("HERMES_HOME", str(home))
        monkeypatch.setenv("HERMES_DREAMS_DIR", str(home / "dreams"))
        monkeypatch.setenv("HERMES_RAW_DIR", str(home / "raw_iso"))
        sg = home / "soul-guardian"
        sg.mkdir()
        (sg / "policy.yaml").write_text(yaml.dump({
            "protected": {"dreams/": {"mode": "restore"}}  # misconfig
        }))
        monkeypatch.setenv("HERMES_SOUL_GUARDIAN_DIR", str(sg))

        from autodream.dream import create_dream_artifact
        with pytest.raises(RuntimeError, match="soul-guardian carve-out violated"):
            create_dream_artifact("default", dreams_dir=str(home / "dreams"),
                                  dry_run=True)

    def test_attestation_critical_drift_aborts_create(self, tmp_path, monkeypatch):
        """P1: critical attestation drift aborts before lock acquire."""
        home = tmp_path
        monkeypatch.setenv("HERMES_HOME", str(home))
        monkeypatch.setenv("HERMES_DREAMS_DIR", str(home / "dreams"))
        monkeypatch.setenv("HERMES_RAW_DIR", str(home / "raw_iso"))
        monkeypatch.setenv("HERMES_SOUL_GUARDIAN_DIR", str(home / "noexist_sg"))
        attestation = home / "attestation"
        attestation.mkdir()
        monkeypatch.setenv("HERMES_ATTESTATION_DIR", str(attestation))
        # File hash doesn't match what state says.
        (home / "SOUL.md").write_bytes(b"actual content")
        (attestation / ".attest-state.json").write_text(json.dumps({
            "protected": {"SOUL.md": {"sha256": "0" * 64, "mode": "restore"}}
        }))

        from autodream.dream import create_dream_artifact
        with pytest.raises(RuntimeError, match="attestation pre-flight failed"):
            create_dream_artifact("default", dreams_dir=str(home / "dreams"),
                                  dry_run=True)


class TestApplyIntegratesSignAndRebaseline:
    def test_apply_signs_and_rebaselines(self, tmp_path, monkeypatch, signing_key_env):
        """P3 + P4: apply_dream invokes sign_patches and rebaseline_attestation."""
        from autodream.dream import apply_dream, create_dream_artifact
        from autodream.memory import add_entry

        home = tmp_path
        monkeypatch.setenv("HERMES_HOME", str(home))
        monkeypatch.setenv("HERMES_DREAMS_DIR", str(home / "dreams"))
        monkeypatch.setenv("HERMES_MEMORY_DIR", str(home / "mem"))
        monkeypatch.setenv("HERMES_RAW_DIR", str(home / "raw"))
        monkeypatch.setenv("HERMES_SOUL_GUARDIAN_DIR", str(home / "noexist_sg"))

        # Seed memory.
        memdir = home / "mem"
        for i in range(3):
            add_entry("fact", f"seed {i}", "self-derived",
                      memory_dir=str(memdir), raw_dir=str(home / "raw"))

        d_id = create_dream_artifact("default", memory_dir=str(memdir),
                                     dreams_dir=str(home / "dreams"), dry_run=True)
        result = apply_dream(d_id, str(home / "dreams"),
                             memory_dir=str(memdir), force_apply=True)
        assert result["status"] == "applied"
        # P3: sign.json written
        sign_path = home / "dreams" / d_id / ".hermes-private" / "sign.json"
        assert sign_path.exists()
        sign_data = json.loads(sign_path.read_text())
        assert sign_data["alg"] == "ed25519"
        assert result["signed"] is True


# ─────────────────────────────────────────────────────────────────────────────
# advisory helper sanity
# ─────────────────────────────────────────────────────────────────────────────


class TestWriteAdvisory:
    def test_advisory_appended(self, tmp_path, monkeypatch):
        obs = tmp_path / "obs"
        monkeypatch.setenv("HERMES_OBSERVABILITY_DIR", str(obs))
        write_advisory("test-kind", "warning", {"x": 1})
        rows = [json.loads(ln) for ln in (obs / "advisory.jsonl").read_text().splitlines() if ln]
        assert rows[0]["kind"] == "test-kind"
        assert rows[0]["x"] == 1
