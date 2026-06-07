"""Tests for the `hermes dream` CLI subcommand (Story 4.8).

Covers `hermes_cli.dream`:
  * register_cli() wires create/status/diff/apply/discard subcommands
  * Each subcommand parses args correctly via the top-level parser
  * Verb handlers translate args → autodream.dream.* calls correctly
  * Error paths: dream-not-found → exit 2, apply-refused → exit 3,
    regression-blocked → exit 4
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Argparse routing
# ---------------------------------------------------------------------------


def _parse(argv):
    """Build the top-level parser and parse argv. Mirrors the pattern from
    other tests in this dir (test_send_cmd.py, etc.)."""
    from hermes_cli._parser import build_top_level_parser
    from hermes_cli.dream import register_cli

    parser, subparsers, _chat = build_top_level_parser()
    dream_parser = subparsers.add_parser("dream")
    register_cli(dream_parser)
    return parser.parse_args(argv)


class TestParseCreate:
    def test_create_minimal(self):
        args = _parse(["dream", "create"])
        assert args.command == "dream"
        assert args.dream_command == "create"
        assert args.scope == "default"
        assert args.dry_run is False
        assert args.json is False

    def test_create_with_scope(self):
        args = _parse(["dream", "create", "--scope", "memory"])
        assert args.scope == "memory"

    def test_create_invalid_scope_rejected(self):
        with pytest.raises(SystemExit):
            _parse(["dream", "create", "--scope", "garbage"])

    def test_create_with_dry_run_and_json(self):
        args = _parse(["dream", "create", "--dry-run", "--json"])
        assert args.dry_run is True
        assert args.json is True

    def test_create_with_dirs(self):
        args = _parse([
            "dream", "create",
            "--memory-dir", "/m", "--dreams-dir", "/d",
        ])
        assert args.memory_dir == "/m"
        assert args.dreams_dir == "/d"


class TestParseStatus:
    def test_status_minimal(self):
        args = _parse(["dream", "status"])
        assert args.dream_command == "status"
        assert args.dreams_dir is None
        assert args.json is False

    def test_status_with_dreams_dir(self):
        args = _parse(["dream", "status", "--dreams-dir", "/d"])
        assert args.dreams_dir == "/d"


class TestParseDiff:
    def test_diff_requires_id(self):
        with pytest.raises(SystemExit):
            _parse(["dream", "diff"])

    def test_diff_with_id(self):
        args = _parse(["dream", "diff", "01ABCD"])
        assert args.dream_id == "01ABCD"


class TestParseApply:
    def test_apply_requires_id(self):
        with pytest.raises(SystemExit):
            _parse(["dream", "apply"])

    def test_apply_default_no_accept(self):
        args = _parse(["dream", "apply", "01XYZ"])
        assert args.dream_id == "01XYZ"
        assert args.accept is False  # explicit ack required per Hard Invariant #4
        assert args.force_recall is False
        assert args.force_reason == ""

    def test_apply_with_accept_and_only(self):
        args = _parse(["dream", "apply", "01XYZ", "--accept", "--only", "*pref*"])
        assert args.accept is True
        assert args.only == "*pref*"

    def test_apply_force_recall_args(self):
        args = _parse([
            "dream", "apply", "01XYZ", "--accept",
            "--force-recall", "--force-reason", "operator override, see audit",
        ])
        assert args.force_recall is True
        assert args.force_reason == "operator override, see audit"


class TestParseDiscard:
    def test_discard_requires_id(self):
        with pytest.raises(SystemExit):
            _parse(["dream", "discard"])

    def test_discard_with_id(self):
        args = _parse(["dream", "discard", "01XYZ"])
        assert args.dream_id == "01XYZ"


# ---------------------------------------------------------------------------
# Handler dispatch (with library calls mocked)
# ---------------------------------------------------------------------------


class TestCreateHandler:
    def test_calls_library_with_args(self, tmp_path, capsys):
        from hermes_cli import dream as dream_mod

        args = MagicMock()
        args.scope = "memory"
        args.memory_dir = "/m"
        args.dreams_dir = str(tmp_path)
        args.dry_run = True
        args.json = False

        with patch.object(dream_mod, "_cmd_create") as _:
            pass  # ensure we test the real handler below
        with patch("autodream.dream.create_dream_artifact",
                   return_value="01TEST") as mock_create:
            dream_mod._cmd_create(args)

        mock_create.assert_called_once_with(
            scope="memory",
            memory_dir="/m",
            dreams_dir=str(tmp_path),
            dry_run=True,
        )
        out = capsys.readouterr().out
        assert "01TEST" in out
        assert "hermes dream diff 01TEST" in out

    def test_json_output(self, tmp_path, capsys):
        from hermes_cli import dream as dream_mod

        args = MagicMock()
        args.scope = "default"
        args.memory_dir = None
        args.dreams_dir = str(tmp_path)
        args.dry_run = True
        args.json = True

        with patch("autodream.dream.create_dream_artifact",
                   return_value="01ABC"):
            dream_mod._cmd_create(args)

        out = capsys.readouterr().out
        payload = json.loads(out)
        assert payload["dream_id"] == "01ABC"
        assert "artifact_dir" in payload
        assert "report" in payload

    def test_runtime_error_exits_2(self, capsys):
        """Attestation pre-flight / soul-guardian misconfig surfaces as exit 2."""
        from hermes_cli import dream as dream_mod

        args = MagicMock()
        args.scope = "default"
        args.memory_dir = None
        args.dreams_dir = "/d"
        args.dry_run = True
        args.json = False

        with patch("autodream.dream.create_dream_artifact",
                   side_effect=RuntimeError("attestation pre-flight failed")):
            with pytest.raises(SystemExit) as exc:
                dream_mod._cmd_create(args)

        assert exc.value.code == 2
        err = capsys.readouterr().err
        assert "attestation pre-flight failed" in err


class TestStatusHandler:
    def test_no_dreams(self, capsys):
        from hermes_cli import dream as dream_mod

        args = MagicMock()
        args.dreams_dir = "/d"
        args.json = False

        with patch("autodream.dream.list_dreams", return_value=[]):
            dream_mod._cmd_status(args)

        out = capsys.readouterr().out
        assert "No staged dreams" in out

    def test_table_format(self, capsys):
        from hermes_cli import dream as dream_mod

        args = MagicMock()
        args.dreams_dir = None
        args.json = False

        fake = [{
            "dream_id": "01ABC",
            "scope": "memory",
            "created": "2026-05-29T11:22:43+00:00",
            "regression": "pass",
            "applied": False,
        }]
        with patch("autodream.dream.list_dreams", return_value=fake):
            dream_mod._cmd_status(args)

        out = capsys.readouterr().out
        assert "01ABC" in out
        assert "memory" in out
        assert "pass" in out
        assert "DREAM_ID" in out  # header

    def test_json_format(self, capsys):
        from hermes_cli import dream as dream_mod

        args = MagicMock()
        args.dreams_dir = None
        args.json = True

        fake = [{"dream_id": "01X", "scope": "default"}]
        with patch("autodream.dream.list_dreams", return_value=fake):
            dream_mod._cmd_status(args)

        out = capsys.readouterr().out
        assert json.loads(out) == fake


class TestDiffHandler:
    def test_diff_passes_through(self, capsys):
        from hermes_cli import dream as dream_mod

        args = MagicMock()
        args.dream_id = "01XYZ"
        args.dreams_dir = None

        with patch("autodream.dream.dream_diff",
                   return_value="# Dream Report\n...") as mock_diff:
            dream_mod._cmd_diff(args)

        mock_diff.assert_called_once_with("01XYZ", dreams_dir=None)
        out = capsys.readouterr().out
        assert "Dream Report" in out

    def test_diff_not_found_exits_2(self, capsys):
        from hermes_cli import dream as dream_mod

        args = MagicMock()
        args.dream_id = "missing"
        args.dreams_dir = None

        with patch("autodream.dream.dream_diff",
                   side_effect=FileNotFoundError()):
            with pytest.raises(SystemExit) as exc:
                dream_mod._cmd_diff(args)

        assert exc.value.code == 2
        err = capsys.readouterr().err
        assert "missing" in err


class TestApplyHandler:
    def _args(self, **kw):
        a = MagicMock()
        a.dream_id = kw.get("dream_id", "01XYZ")
        a.dreams_dir = kw.get("dreams_dir")
        a.memory_dir = kw.get("memory_dir")
        a.only = kw.get("only")
        a.accept = kw.get("accept", False)
        a.force_recall = kw.get("force_recall", False)
        a.force_reason = kw.get("force_reason", "")
        a.actor = kw.get("actor", "test-user")
        return a

    def test_refused_exits_3(self, capsys):
        from hermes_cli import dream as dream_mod

        with patch("autodream.dream.apply_dream",
                   return_value={"status": "refused", "operations": 0,
                                 "reason": "needs --accept"}):
            with pytest.raises(SystemExit) as exc:
                dream_mod._cmd_apply(self._args())

        assert exc.value.code == 3
        out = capsys.readouterr().out
        assert "refused" in out

    def test_regression_blocked_exits_4(self, capsys):
        from hermes_cli import dream as dream_mod

        with patch("autodream.dream.apply_dream",
                   return_value={"status": "regression_blocked",
                                 "operations": 0, "reason": "recall regressed"}):
            with pytest.raises(SystemExit) as exc:
                dream_mod._cmd_apply(self._args(accept=True))

        assert exc.value.code == 4
        out = capsys.readouterr().out
        assert "regression_blocked" in out

    def test_applied_exit_0(self, capsys):
        from hermes_cli import dream as dream_mod

        with patch("autodream.dream.apply_dream",
                   return_value={"status": "applied", "operations": 5}):
            dream_mod._cmd_apply(self._args(accept=True))

        out = capsys.readouterr().out
        assert "applied" in out
        assert "5" in out

    def test_not_found_exits_2(self):
        from hermes_cli import dream as dream_mod

        with patch("autodream.dream.apply_dream",
                   side_effect=FileNotFoundError()):
            with pytest.raises(SystemExit) as exc:
                dream_mod._cmd_apply(self._args(accept=True))

        assert exc.value.code == 2

    def test_passes_force_recall_with_reason(self):
        from hermes_cli import dream as dream_mod

        with patch("autodream.dream.apply_dream",
                   return_value={"status": "applied", "operations": 1}) as mock:
            dream_mod._cmd_apply(self._args(
                accept=True,
                force_recall=True,
                force_reason="operator decision, audit row preserves intent",
            ))

        kw = mock.call_args.kwargs
        assert kw["force_apply"] is True
        assert kw["force_recall"] is True
        assert kw["force_reason"] == "operator decision, audit row preserves intent"


class TestDiscardHandler:
    def _args(self, **kw):
        a = MagicMock()
        a.dream_id = kw.get("dream_id", "01XYZ")
        a.dreams_dir = kw.get("dreams_dir")
        a.actor = kw.get("actor", "test-user")
        return a

    def test_discarded_ok(self, capsys):
        from hermes_cli import dream as dream_mod

        with patch("autodream.dream.discard_dream",
                   return_value={"status": "discarded"}):
            dream_mod._cmd_discard(self._args())

        out = capsys.readouterr().out
        assert "discarded" in out

    def test_not_found_exit_0(self):
        """Idempotent: discarding a missing dream is success, per FR-21."""
        from hermes_cli import dream as dream_mod

        with patch("autodream.dream.discard_dream",
                   return_value={"status": "not_found"}):
            # No raise; exit code 0.
            dream_mod._cmd_discard(self._args())

    def test_runtime_error_exits_2(self, capsys):
        """Symlink refusal etc. surface as exit 2."""
        from hermes_cli import dream as dream_mod

        with patch("autodream.dream.discard_dream",
                   side_effect=RuntimeError("symlink refused")):
            with pytest.raises(SystemExit) as exc:
                dream_mod._cmd_discard(self._args())

        assert exc.value.code == 2


# ---------------------------------------------------------------------------
# Top-level dispatch
# ---------------------------------------------------------------------------


class TestCmdDream:
    def test_bare_dream_returns_silently(self):
        """When no subcommand is given, argparse's default-help has already
        run and args.dream_command is None — cmd_dream should noop."""
        from hermes_cli import dream as dream_mod

        args = MagicMock()
        args.dream_command = None
        # Should not raise; should not call any handler.
        dream_mod.cmd_dream(args)

    def test_delegates_to_func(self):
        from hermes_cli import dream as dream_mod

        args = MagicMock()
        args.dream_command = "create"
        args.func = MagicMock()

        dream_mod.cmd_dream(args)
        args.func.assert_called_once_with(args)
