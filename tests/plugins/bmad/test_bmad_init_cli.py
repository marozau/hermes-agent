"""Tests for bmad-init CLI — workspace/worktree flags."""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


class TestBmadInitWorkspaceFlags:
    """Test --workspace and --worktree CLI flags for hermes bmad-init."""

    def test_workspace_without_worktree_errors(self, capsys):
        """--workspace without --worktree should error."""
        # Import the registration function to get the handler
        from plugins.bmad import register

        # Create a mock context
        ctx = MagicMock()
        registered = {}
        def mock_register_cli_command(name, handler_fn, **kwargs):
            registered[name] = handler_fn
        ctx.register_cli_command = mock_register_cli_command

        # Register the command
        register(ctx)

        # Get the handler
        handler = registered.get("bmad-init")
        assert handler is not None

        # Create args namespace with --workspace but no --worktree
        args = MagicMock()
        args.workspace = True
        args.worktree = []
        args.force = False
        args.non_interactive = True
        args.project_name = "test-project"
        args.project_type = "other"
        args.project_level = 1
        args.user_name = ""

        with pytest.raises(SystemExit) as exc_info:
            handler(args)
        assert exc_info.value.code == 3

        captured = capsys.readouterr()
        assert "--workspace requires at least one --worktree" in captured.err

    def test_workspace_with_invalid_worktree_format_errors(self, capsys):
        """--worktree with wrong format should error."""
        from plugins.bmad import register

        ctx = MagicMock()
        registered = {}
        def mock_register_cli_command(name, handler_fn, **kwargs):
            registered[name] = handler_fn
        ctx.register_cli_command = mock_register_cli_command

        register(ctx)
        handler = registered.get("bmad-init")

        args = MagicMock()
        args.workspace = True
        args.worktree = ["bad-format"]
        args.force = False
        args.non_interactive = True
        args.project_name = "test-project"
        args.project_type = "other"
        args.project_level = 1
        args.user_name = ""

        with pytest.raises(SystemExit) as exc_info:
            handler(args)
        assert exc_info.value.code == 3

        captured = capsys.readouterr()
        assert "invalid --worktree format" in captured.err

    def test_workspace_with_valid_worktree_calls_bootstrap_workspace(self, tmp_path):
        """--workspace with valid --worktree should call bootstrap_workspace."""
        from plugins.bmad import register

        ctx = MagicMock()
        registered = {}
        def mock_register_cli_command(name, handler_fn, **kwargs):
            registered[name] = handler_fn
        ctx.register_cli_command = mock_register_cli_command

        register(ctx)
        handler = registered.get("bmad-init")

        args = MagicMock()
        args.workspace = True
        args.worktree = ["hermes-agent:/Users/im/usr-local/hermes:main"]
        args.force = True
        args.non_interactive = True
        args.project_name = "test-workspace"
        args.project_type = "other"
        args.project_level = 1
        args.user_name = ""

        with patch("plugins.bmad.scripts.bmad_init.bootstrap_workspace") as mock_bw:
            mock_bw.return_value = {"project_name": "test-workspace"}
            with patch("pathlib.Path.cwd", return_value=tmp_path):
                handler(args)

        mock_bw.assert_called_once()
        call_kwargs = mock_bw.call_args
        assert call_kwargs[1]["project_name"] == "test-workspace"
        assert len(call_kwargs[1]["worktrees"]) == 1
        assert call_kwargs[1]["worktrees"][0]["name"] == "hermes-agent"
        assert call_kwargs[1]["worktrees"][0]["upstream"] == "/Users/im/usr-local/hermes"
        assert call_kwargs[1]["worktrees"][0]["branch"] == "main"

    def test_workspace_defaults_project_name_from_cwd(self, tmp_path):
        """--workspace without --project-name should use cwd name."""
        from plugins.bmad import register

        ctx = MagicMock()
        registered = {}
        def mock_register_cli_command(name, handler_fn, **kwargs):
            registered[name] = handler_fn
        ctx.register_cli_command = mock_register_cli_command

        register(ctx)
        handler = registered.get("bmad-init")

        args = MagicMock()
        args.workspace = True
        args.worktree = ["repo:/path:main"]
        args.force = True
        args.non_interactive = False
        args.project_name = None  # Not specified
        args.project_type = "other"
        args.project_level = 1
        args.user_name = ""

        with patch("plugins.bmad.scripts.bmad_init.bootstrap_workspace") as mock_bw:
            mock_bw.return_value = {"project_name": tmp_path.name}
            with patch("pathlib.Path.cwd", return_value=tmp_path):
                handler(args)

        call_kwargs = mock_bw.call_args
        assert call_kwargs[1]["project_name"] == tmp_path.name
