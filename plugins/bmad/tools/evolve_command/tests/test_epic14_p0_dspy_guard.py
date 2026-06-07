"""Epic 14.1 P0: verify judge.py try/except guard works when dspy is synthetically absent.

D-41: proves the Epic 13 R4 fix is active — judge.py loads without dspy.
"""
from __future__ import annotations

import importlib
import sys
from unittest.mock import MagicMock

import pytest


def test_check_hard_gates_works_without_dspy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Synthetically set dspy=None in judge module → check_hard_gates still works."""
    import plugins.bmad.tools.evolve_command.judge as judge_mod

    # Save original dspy value
    original_dspy = judge_mod.dspy

    # Synthetically remove dspy (simulates what happens when dspy is not installed)
    monkeypatch.setattr(judge_mod, "dspy", None)

    # Verify check_hard_gates still works without dspy
    result = judge_mod.check_hard_gates(
        diff="+ npm publish --access public",
        test_pass_rate=1.0,
        regression_safety=1.0,
    )
    assert result.passed is False
    assert any("deploy" in f.lower() for f in result.failures)


def test_judge_module_has_try_except_guard() -> None:
    """Verify judge.py has try/except ImportError guard for dspy."""
    import inspect
    import plugins.bmad.tools.evolve_command.judge as judge_mod

    source = inspect.getsource(judge_mod)
    # Must have try/except ImportError pattern
    assert "try:" in source, "judge.py missing try block"
    assert "import dspy" in source, "judge.py missing import dspy"
    assert "except ImportError" in source, "judge.py missing except ImportError guard"
    assert "dspy = None" in source, "judge.py missing dspy = None fallback"


def test_code_output_judge_raises_without_dspy() -> None:
    """Verify CodeOutputJudge raises RuntimeError when dspy is None."""
    import plugins.bmad.tools.evolve_command.judge as judge_mod

    original_dspy = judge_mod.dspy
    try:
        judge_mod.dspy = None
        with pytest.raises(RuntimeError, match="dspy is required"):
            judge_mod.CodeOutputJudge()
    finally:
        judge_mod.dspy = original_dspy
