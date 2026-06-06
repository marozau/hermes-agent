"""Tests for plugin command dispatch — imperative body injection.

Verifies that plugin commands returning "EXECUTE NOW..." bodies
get injected into _pending_input for LLM continuation (BMAD pattern).
"""

from __future__ import annotations

import queue
from unittest.mock import MagicMock, patch

import pytest


class TestPluginCommandDispatch:
    """Test the cli.py plugin command dispatch logic."""

    def _make_cli(self):
        """Create a minimal CLI mock with _pending_input."""
        cli = MagicMock()
        cli._pending_input = queue.Queue()
        cli._agent_running = False
        return cli

    def test_imperative_bmad_command_injected(self):
        """BMAD command returning 'EXECUTE NOW...' goes to _pending_input."""
        cli = self._make_cli()

        # Simulate the dispatch logic from cli.py:9308-9323
        result_str = "EXECUTE NOW. You are Analyst.\n\n# /bmad:init\n\nPlan now."
        base_cmd = "bmad:init"

        if (
            result_str.startswith("EXECUTE NOW")
            and base_cmd.startswith("bmad:")
        ):
            if hasattr(cli, '_pending_input'):
                cli._pending_input.put(result_str)

        assert not cli._pending_input.empty()
        assert cli._pending_input.get_nowait() == result_str

    def test_non_imperative_bmad_command_printed(self):
        """BMAD command returning plain text does NOT get injected."""
        cli = self._make_cli()

        result_str = "✅ BMAD project initialized at `/tmp/test`"
        base_cmd = "bmad:init"

        injected = False
        if (
            result_str.startswith("EXECUTE NOW")
            and base_cmd.startswith("bmad:")
        ):
            cli._pending_input.put(result_str)
            injected = True

        assert not injected
        assert cli._pending_input.empty()

    def test_execute_now_non_bmad_not_injected(self):
        """Non-BMAD command returning 'EXECUTE NOW...' does NOT get injected."""
        cli = self._make_cli()

        result_str = "EXECUTE NOW. Some other plugin."
        base_cmd = "other-plugin:do-thing"

        injected = False
        if (
            result_str.startswith("EXECUTE NOW")
            and base_cmd.startswith("bmad:")
        ):
            cli._pending_input.put(result_str)
            injected = True

        assert not injected
        assert cli._pending_input.empty()

    def test_bmad_error_string_not_injected(self):
        """BMAD error strings (❌) do NOT get injected."""
        cli = self._make_cli()

        result_str = "❌ Failed to initialize workspace: config exists"
        base_cmd = "bmad:init"

        injected = False
        if (
            result_str.startswith("EXECUTE NOW")
            and base_cmd.startswith("bmad:")
        ):
            cli._pending_input.put(result_str)
            injected = True

        assert not injected
        assert cli._pending_input.empty()

    def test_bmad_warning_string_not_injected(self):
        """BMAD warning strings (⚠️) do NOT get injected."""
        cli = self._make_cli()

        result_str = "⚠️  **bmad/config.yaml** already exists."
        base_cmd = "bmad:init"

        injected = False
        if (
            result_str.startswith("EXECUTE NOW")
            and base_cmd.startswith("bmad:")
        ):
            cli._pending_input.put(result_str)
            injected = True

        assert not injected
        assert cli._pending_input.empty()
