"""Integration tests for hermes-preflight CLI binary.

Invokes the actual CLI binary as a subprocess and asserts on exit codes,
stdout content, and stderr behavior.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

# HERMES_ROOT is the deployed-runtime root (~/.hermes/). The preflight CLI
# binary + its venv only exist there, not in the dev tree. These tests
# integration-test the deployed runtime; pre-deploy they will skip via the
# CLI_BIN.exists() guards in individual tests.
HERMES_ROOT = Path.home() / ".hermes"
CLI_BIN = HERMES_ROOT / "bin" / "hermes-preflight"
VENV_PYTHON = HERMES_ROOT / "hermes-agent" / "venv" / "bin" / "python"


def _run(*args, env=None, timeout=30):
    """Run CLI binary via venv python, return (exit_code, stdout, stderr)."""
    cmd = [str(VENV_PYTHON), str(CLI_BIN)] + list(args)
    env = env or os.environ.copy()
    env.setdefault("HERMES_HOME", str(HERMES_ROOT))
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    return r.returncode, r.stdout, r.stderr


class TestCLIVersion:
    def test_version_flag(self):
        code, out, err = _run("--version")
        assert code == 0
        assert "hermes-preflight" in out
        assert err == ""

    def test_version_json(self):
        code, out, err = _run("--version", "--json")
        assert code == 0
        data = json.loads(out)
        assert data["version"] == "1.1.0"
        assert "commit" in data
        assert err == ""


class TestCLIHelp:
    def test_help_flag(self):
        code, out, err = _run("--help")
        assert code == 0
        assert "usage:" in out
        assert "check" in out
        assert "force" in out
        assert err == ""

    def test_subcommand_help(self):
        code, out, err = _run("check", "--help")
        assert code == 0
        assert "--json" in out
        assert "--format" in out
        assert "--timeout" in out
        assert "--dry-run" in out


class TestCLICheck:
    def test_check_json_output(self):
        code, out, err = _run("check", "--json", "test message")
        assert code in (0, 1), f"unexpected exit {code}"
        data = json.loads(out)
        assert "mode" in data
        assert "skip_reason" in data
        assert "heads_up" in data
        # stderr clean on success
        assert err == "" or "hermes-preflight" not in err

    def test_check_text_format(self):
        code, out, err = _run("check", "--format=text", "test message")
        assert code in (0, 1)
        assert "Mode:" in out
        assert "Skip reason:" in out

    def test_check_dry_run(self):
        code, out, err = _run("check", "--dry-run", "test message")
        assert code == 0
        assert "DRY-RUN" in out

    def test_check_dry_run_json(self):
        code, out, err = _run("check", "--dry-run", "--json", "test message")
        assert code == 0
        data = json.loads(out)
        assert data["command"] == "check"
        assert data["flags"]["dry_run"] is True


class TestCLIForce:
    def test_force_json_output(self):
        code, out, err = _run("force", "--json", "debug hermes preflight")
        # Force with shadow mode → pipeline fires, heads-up suppressed → code 0
        assert code == 0, f"unexpected exit {code}: stderr={err}"
        data = json.loads(out)
        assert data.get("skip_reason") in (None, "shadow-mode")
        assert err == "" or "hermes-preflight" not in err

    def test_force_text_format(self):
        code, out, err = _run("force", "--format=text", "debug hermes")
        assert code == 0
        assert "Mode:" in out

    def test_force_dry_run(self):
        code, out, err = _run("force", "--dry-run", "test message")
        assert code == 0
        assert "DRY-RUN" in out
        assert "force=yes" in out

    def test_force_verbose(self):
        code, out, err = _run("force", "--verbose", "--json", "debug hermes")
        assert code == 0
        assert "hermes-preflight" in err  # verbose logs to stderr
        data = json.loads(out)
        assert "mode" in data


class TestCLIMode:
    def test_mode_read(self):
        code, out, err = _run("mode")
        assert code == 0
        assert out.strip() in ("shadow", "live")

    def test_mode_read_json(self):
        code, out, err = _run("mode", "--json")
        assert code == 0
        data = json.loads(out)
        assert data["mode"] in ("shadow", "live")

    def test_mode_invalid(self):
        code, out, err = _run("mode", "invalid")
        assert code == 2


class TestCLITail:
    def test_tail_default(self):
        code, out, err = _run("tail", "--n", "2")
        assert code == 0
        # May have entries or "(no telemetry yet today)"
        assert len(out) > 0

    def test_tail_json(self):
        code, out, err = _run("tail", "--n", "1", "--json")
        assert code == 0
        data = json.loads(out)
        assert "entries" in data
        assert "count" in data


class TestCLIExitCodes:
    def test_check_skip_returns_1(self, isolated_preflight_env):
        """Short message with no domains → skip → exit 1."""
        code, out, err = _run("check", "hi")
        assert code == 1

    def test_force_returns_0(self):
        """Force always fires → exit 0."""
        code, out, err = _run("force", "--json", "debug hermes preflight pipeline")
        assert code == 0

    def test_usage_error_no_message(self):
        """Missing required message arg → exit 2."""
        r = subprocess.run(
            [str(VENV_PYTHON), str(CLI_BIN), "check"],
            capture_output=True, text=True, timeout=10,
        )
        assert r.returncode == 2

    def test_stderr_empty_on_success(self):
        """Successful runs write nothing to stderr (except --verbose)."""
        code, out, err = _run("force", "--json", "debug hermes")
        assert err == ""


class TestCLITimeout:
    def test_timeout_not_exceeded(self):
        """Normal operation within timeout returns 0 or 1."""
        code, out, err = _run("force", "--json", "--timeout", "5", "debug hermes")
        assert code in (0, 1)

    def test_timeout_flag_accepted(self):
        """--timeout flag is parsed correctly."""
        code, out, err = _run("force", "--dry-run", "--timeout", "10", "msg")
        assert code == 0


# ─────────────────────────────────────────────────────────────────────────────
# Fixture: isolated preflight environment for exit-code tests
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def isolated_preflight_env(tmp_path, monkeypatch):
    """Isolate preflight to a temp dir so warm-up turn_count starts fresh."""
    from autodream.preflight import _gates
    _gates.clear()
    cfg_dir = tmp_path / "preflight"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config.yaml").write_text(yaml.dump({
        "mode": "shadow", "enabled": True, "warmup_turns": 3,
    }))
    (cfg_dir / "domain-vocab.txt").write_text("kubernetes\ndocker\n")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path
