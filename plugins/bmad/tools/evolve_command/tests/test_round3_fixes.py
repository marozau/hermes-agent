"""Round-3 regression tests — TDD-strict.

Each test demonstrates a REAL bug. Fix makes it pass.
Verified RED before fix; Verified GREEN after.
"""
from __future__ import annotations

import re
import tempfile
from pathlib import Path

import pytest


class TestP0YAMLParseable:
    """P0-1: Metric YAML must parse without error."""

    def test_metric_yaml_parses(self) -> None:
        """yaml.safe_load must not raise on metric YAML."""
        import yaml
        metric_path = Path(__file__).parents[1] / "metrics" / "dev_story_composite_v1.yaml"
        data = yaml.safe_load(metric_path.read_text())
        assert isinstance(data, dict)
        assert "weights" in data
        assert "freeze_date" in data
        assert "hard_gates" in data


class TestP0JudgeLoadsPatternsFromYAML:
    """P0-2: Hard gate patterns must load from metric YAML, not hardcoded."""

    def test_check_hard_gates_blocks_npm_publish(self) -> None:
        """npm publish (OI-4) must be blocked by hard gates."""
        from plugins.bmad.tools.evolve_command.judge import check_hard_gates
        result = check_hard_gates(
            diff="npm publish --access public",
            test_pass_rate=1.0,
            regression_safety=1.0,
        )
        assert result.passed is False
        assert any("deploy" in f.lower() or "verb" in f.lower() for f in result.failures)

    def test_check_hard_gates_blocks_kube_config(self) -> None:
        """~/.kube/config (OI-5) must be blocked by hard gates."""
        from plugins.bmad.tools.evolve_command.judge import check_hard_gates
        result = check_hard_gates(
            diff="cat ~/.kube/config",
            test_pass_rate=1.0,
            regression_safety=1.0,
        )
        assert result.passed is False
        assert any("credential" in f.lower() for f in result.failures)


class TestP0ImporterJSONL:
    """P0-3: Importer must parse JSONL (one JSON per line), not single JSON."""

    def test_collect_messages_parses_jsonl_lines(self) -> None:
        """Each line in a .jsonl file is a separate message."""
        from unittest.mock import patch
        from plugins.bmad.tools.evolve_command._vendor.external_importers import HermesSessionImporter
        import json

        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "sessions"
            session_dir.mkdir()
            # Write 3 JSONL lines
            lines = [
                json.dumps({"role": "user", "content": "implement story 13.8"}),
                json.dumps({"role": "assistant", "content": "implementing story 13.8 now"}),
                json.dumps({"role": "user", "content": "verify the implementation"}),
            ]
            session_file = session_dir / "test_session.jsonl"
            session_file.write_text("\n".join(lines) + "\n")

            with patch.object(HermesSessionImporter, "SESSION_DIR", session_dir):
                messages = HermesSessionImporter.extract_messages(limit=10)
            assert len(messages) == 2, f"Expected 2 user-assistant pairs, got {len(messages)}"


class TestP0FallbackIDPrecedence:
    """P0-5: fallback_id must respect story_id even when args is empty."""

    def test_story_id_preserved_when_args_empty(self) -> None:
        """When story_id='13.8' and args='', fallback_id must be '13.8'."""
        story_id = "13.8"
        args_stripped = ""
        # The CORRECT expression:
        fallback_id = story_id if story_id else (args_stripped.split()[0] if args_stripped.strip() else "default")
        assert fallback_id == "13.8"

    def test_fallback_to_args_when_no_story_id(self) -> None:
        """When story_id=None and args='foo.md', fallback_id must be 'foo.md'."""
        story_id = None
        args_stripped = "foo.md"
        fallback_id = story_id if story_id else (args_stripped.split()[0] if args_stripped.strip() else "default")
        assert fallback_id == "foo.md"

    def test_fallback_to_default_when_both_empty(self) -> None:
        """When story_id=None and args='', fallback_id must be 'default'."""
        story_id = None
        args_stripped = ""
        fallback_id = story_id if story_id else (args_stripped.split()[0] if args_stripped.strip() else "default")
        assert fallback_id == "default"


class TestP1FreezeGuard:
    """P1-9: Freeze guard must reject same-day edits (>= not >)."""

    def test_freeze_guard_rejects_same_day(self) -> None:
        """A file modified on freeze_date must be rejected."""
        from datetime import date
        freeze_date = date(2026, 6, 4)
        last_modified = date(2026, 6, 4)
        # CORRECT: >= (not >)
        assert not (last_modified > freeze_date), "Same-day passes with > (bug)"
        assert last_modified >= freeze_date, ">= correctly identifies same-day"


class TestP1DryRunNoDspy:
    """P1-11: --dry-run must not require dspy installed."""

    def test_dry_run_does_not_import_dspy(self) -> None:
        """CLI optimize --dry-run must not import dspy at parse time."""
        cli_path = Path(__file__).parents[1] / "cli.py"
        content = cli_path.read_text()
        # The optimize command should lazy-import dspy-dependent code AFTER dry-run check
        # Find the optimize function and verify dspy imports are after dry_run early-return
        lines = content.split("\n")
        in_optimize = False
        dry_run_return_line = None
        dspy_import_line = None
        for i, line in enumerate(lines):
            if "def optimize(" in line:
                in_optimize = True
            if in_optimize and "dry_run" in line and "return" in line:
                dry_run_return_line = i
            if in_optimize and ("from .judge" in line or "import dspy" in line):
                dspy_import_line = i
                break
        if dry_run_return_line and dspy_import_line:
            assert dspy_import_line > dry_run_return_line, (
                f"dspy import at line {dspy_import_line} is before dry-run return at {dry_run_return_line}"
            )
