"""Tests for the gitnexus-autorefresh plugin.

Covers the bundled plugin at ``plugins/gitnexus-autorefresh/``:

  * ``_GIT_MUTATION`` regex — match / no-match cases for the six git verbs.
  * ``_post_tool_call`` callback — tool filtering, args validation, cwd
    resolution, forward-compat kwarg swallowing, fire-and-forget threading.
  * ``_refresh_gitnexus`` worker — .gitnexus/ guard, Popen invocation
    shape (start_new_session, --skip-agents-md), npx-missing tolerance.
  * ``register()`` — hooks "post_tool_call" via ctx.register_hook.
  * Bundled-plugin discovery via ``PluginManager.discover_and_load``.
"""

import importlib.util
import subprocess
import sys
import threading
import time
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures + loaders
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_env(tmp_path, monkeypatch):
    """Per-test HERMES_HOME so the plugin's path resolutions are deterministic."""
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    yield hermes_home


def _load_plugin():
    """Import plugins/gitnexus-autorefresh/__init__.py with the
    hermes_plugins.<name> naming PluginManager uses so the module is
    independent of any prior import order in the test session."""
    repo_root = Path(__file__).resolve().parents[2]
    plugin_dir = repo_root / "plugins" / "gitnexus-autorefresh"
    spec = importlib.util.spec_from_file_location(
        "hermes_plugins.gitnexus_autorefresh_under_test",
        plugin_dir / "__init__.py",
        submodule_search_locations=[str(plugin_dir)],
    )
    if "hermes_plugins" not in sys.modules:
        ns = types.ModuleType("hermes_plugins")
        ns.__path__ = []
        sys.modules["hermes_plugins"] = ns
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "hermes_plugins.gitnexus_autorefresh_under_test"
    mod.__path__ = [str(plugin_dir)]
    sys.modules["hermes_plugins.gitnexus_autorefresh_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def _wait_for_thread() -> None:
    """Block briefly so daemon thread spawned by _post_tool_call can run.

    The thread only calls _refresh_gitnexus which we mock in callback tests,
    so the wait is bounded by the mock execution time (microseconds).
    50 ms is generous; pytest-xdist worker scheduling can be jittery.
    """
    time.sleep(0.05)


# ---------------------------------------------------------------------------
# Regex
# ---------------------------------------------------------------------------


class TestGitMutationRegex:
    @pytest.fixture(scope="class")
    def regex(self):
        return _load_plugin()._GIT_MUTATION

    @pytest.mark.parametrize("command", [
        "git commit -m foo",
        "git commit --allow-empty",
        "git merge --no-ff dev",
        "git pull origin main",
        "git checkout -b feat/x",
        "git rebase main",
        "git reset --hard HEAD",
        "  git pull  ",                     # leading + internal whitespace
        "cd /tmp && git commit -m bar",     # embedded in compound command
    ])
    def test_matches_mutations(self, regex, command):
        assert regex.search(command) is not None

    @pytest.mark.parametrize("command", [
        "git status",
        "git log --oneline",
        "git branch -a",
        "git diff HEAD~",
        "gitlab clone",                     # false-friend
        "gitignore add *.tmp",              # false-friend
        "ls -la",                           # not a git command at all
        "",                                 # empty
    ])
    def test_rejects_non_mutations(self, regex, command):
        assert regex.search(command) is None

    def test_false_positive_in_echo_is_acceptable(self, regex):
        """Documented design tradeoff: `echo "git commit ..."` matches but
        triggers a no-op analyze on unchanged HEAD. Cheap; acceptable."""
        assert regex.search('echo "git commit foo"') is not None


# ---------------------------------------------------------------------------
# _post_tool_call callback
# ---------------------------------------------------------------------------


class TestPostToolCallCallback:
    def _common_kwargs(self, **override):
        base = dict(
            task_id="t1",
            session_id="s1",
            tool_call_id="c1",
            duration_ms=42,
        )
        base.update(override)
        return base

    def test_skips_non_terminal_tool(self, monkeypatch):
        mod = _load_plugin()
        called = []
        monkeypatch.setattr(mod, "_refresh_gitnexus", lambda cwd: called.append(cwd))
        mod._post_tool_call(
            tool_name="read_file",
            args={"path": "/tmp/x"},
            result="ok",
            **self._common_kwargs(),
        )
        _wait_for_thread()
        assert called == []

    def test_skips_when_args_is_none(self, monkeypatch):
        mod = _load_plugin()
        called = []
        monkeypatch.setattr(mod, "_refresh_gitnexus", lambda cwd: called.append(cwd))
        mod._post_tool_call(
            tool_name="terminal", args=None, result="ok",
            **self._common_kwargs(),
        )
        _wait_for_thread()
        assert called == []

    def test_skips_when_args_not_dict(self, monkeypatch):
        mod = _load_plugin()
        called = []
        monkeypatch.setattr(mod, "_refresh_gitnexus", lambda cwd: called.append(cwd))
        mod._post_tool_call(
            tool_name="terminal", args="not-a-dict", result="ok",
            **self._common_kwargs(),
        )
        _wait_for_thread()
        assert called == []

    def test_skips_when_command_missing(self, monkeypatch):
        mod = _load_plugin()
        called = []
        monkeypatch.setattr(mod, "_refresh_gitnexus", lambda cwd: called.append(cwd))
        mod._post_tool_call(
            tool_name="terminal", args={"cwd": "/tmp"}, result="ok",
            **self._common_kwargs(),
        )
        _wait_for_thread()
        assert called == []

    def test_skips_when_command_not_string(self, monkeypatch):
        mod = _load_plugin()
        called = []
        monkeypatch.setattr(mod, "_refresh_gitnexus", lambda cwd: called.append(cwd))
        mod._post_tool_call(
            tool_name="terminal", args={"command": ["git", "commit"]}, result="ok",
            **self._common_kwargs(),
        )
        _wait_for_thread()
        assert called == []

    def test_skips_non_mutation_command(self, monkeypatch):
        mod = _load_plugin()
        called = []
        monkeypatch.setattr(mod, "_refresh_gitnexus", lambda cwd: called.append(cwd))
        mod._post_tool_call(
            tool_name="terminal", args={"command": "git status"}, result="ok",
            **self._common_kwargs(),
        )
        _wait_for_thread()
        assert called == []

    def test_fires_on_git_commit(self, monkeypatch):
        mod = _load_plugin()
        called = []
        monkeypatch.setattr(mod, "_refresh_gitnexus", lambda cwd: called.append(cwd))
        mod._post_tool_call(
            tool_name="terminal",
            args={"command": "git commit -m smoke", "cwd": "/repo"},
            result="ok",
            **self._common_kwargs(),
        )
        _wait_for_thread()
        assert called == ["/repo"]

    def test_uses_explicit_cwd_from_args(self, monkeypatch):
        mod = _load_plugin()
        called = []
        monkeypatch.setattr(mod, "_refresh_gitnexus", lambda cwd: called.append(cwd))
        mod._post_tool_call(
            tool_name="terminal",
            args={"command": "git pull", "cwd": "/some/repo"},
            result="ok",
            **self._common_kwargs(),
        )
        _wait_for_thread()
        assert called == ["/some/repo"]

    def test_falls_back_to_process_cwd(self, monkeypatch):
        mod = _load_plugin()
        called = []
        monkeypatch.setattr(mod, "_refresh_gitnexus", lambda cwd: called.append(cwd))
        monkeypatch.setattr(mod.os, "getcwd", lambda: "/process/cwd")
        mod._post_tool_call(
            tool_name="terminal",
            args={"command": "git rebase main"},  # no cwd key
            result="ok",
            **self._common_kwargs(),
        )
        _wait_for_thread()
        assert called == ["/process/cwd"]

    def test_swallows_forward_compat_kwargs(self, monkeypatch):
        """Hook contract may grow new kwargs (new fields, audit metadata, etc).
        The plugin must accept them via **kwargs without raising."""
        mod = _load_plugin()
        called = []
        monkeypatch.setattr(mod, "_refresh_gitnexus", lambda cwd: called.append(cwd))
        mod._post_tool_call(
            tool_name="terminal",
            args={"command": "git commit -m x"},
            result="ok",
            future_field_xyz="ignored",
            another_new_field=123,
            **self._common_kwargs(),
        )
        _wait_for_thread()
        # Should still fire on the match, not raise on unknown kwargs.
        assert len(called) == 1


# ---------------------------------------------------------------------------
# _refresh_gitnexus worker
# ---------------------------------------------------------------------------


class TestRefreshGitnexus:
    def test_no_op_when_no_gitnexus_dir(self, tmp_path):
        mod = _load_plugin()
        # tmp_path has no .gitnexus/ subdir.
        with patch.object(subprocess, "Popen") as mock_popen:
            mod._refresh_gitnexus(str(tmp_path))
            mock_popen.assert_not_called()

    def test_launches_popen_when_gitnexus_exists(self, tmp_path):
        mod = _load_plugin()
        (tmp_path / ".gitnexus").mkdir()
        with patch.object(subprocess, "Popen") as mock_popen:
            mod._refresh_gitnexus(str(tmp_path))
            mock_popen.assert_called_once()

    def test_popen_args_include_skip_agents_md(self, tmp_path):
        mod = _load_plugin()
        (tmp_path / ".gitnexus").mkdir()
        with patch.object(subprocess, "Popen") as mock_popen:
            mod._refresh_gitnexus(str(tmp_path))
            call_args = mock_popen.call_args
            cmd = call_args.args[0]
            assert "npx" in cmd[0]
            assert "gitnexus" in cmd
            assert "analyze" in cmd
            assert "--skip-agents-md" in cmd
            # operator-curated docs should not churn

    def test_popen_uses_start_new_session(self, tmp_path):
        """start_new_session=True detaches the subprocess so SIGINT/SIGTERM
        of Hermes doesn't kill an in-flight analyze."""
        mod = _load_plugin()
        (tmp_path / ".gitnexus").mkdir()
        with patch.object(subprocess, "Popen") as mock_popen:
            mod._refresh_gitnexus(str(tmp_path))
            kwargs = mock_popen.call_args.kwargs
            assert kwargs.get("start_new_session") is True
            assert kwargs.get("stdout") is subprocess.DEVNULL
            assert kwargs.get("stderr") is subprocess.DEVNULL
            assert kwargs.get("cwd") == str(tmp_path)

    def test_silent_on_missing_npx(self, tmp_path):
        """FileNotFoundError on Popen → silent skip, not crash."""
        mod = _load_plugin()
        (tmp_path / ".gitnexus").mkdir()
        with patch.object(subprocess, "Popen", side_effect=FileNotFoundError("npx")):
            # No raise.
            mod._refresh_gitnexus(str(tmp_path))

    def test_silent_on_os_error(self, tmp_path):
        """OSError on Popen → silent skip. Transient fork() failures shouldn't
        spam errors per turn."""
        mod = _load_plugin()
        (tmp_path / ".gitnexus").mkdir()
        with patch.object(subprocess, "Popen", side_effect=OSError("EAGAIN")):
            mod._refresh_gitnexus(str(tmp_path))


# ---------------------------------------------------------------------------
# register()
# ---------------------------------------------------------------------------


class TestRegister:
    def test_registers_post_tool_call_hook(self):
        mod = _load_plugin()
        ctx = MagicMock()
        mod.register(ctx)
        ctx.register_hook.assert_called_once_with("post_tool_call", mod._post_tool_call)


# ---------------------------------------------------------------------------
# Threading isolation — sanity check that we don't share state across calls
# ---------------------------------------------------------------------------


class TestThreadingIsolation:
    def test_consecutive_calls_dont_block(self, monkeypatch):
        """Each _post_tool_call should return in sub-millisecond time
        regardless of how slow the worker is — the worker runs on a
        daemon thread."""
        mod = _load_plugin()
        # Simulate slow worker.
        monkeypatch.setattr(mod, "_refresh_gitnexus", lambda cwd: time.sleep(0.3))
        t0 = time.perf_counter()
        for _ in range(5):
            mod._post_tool_call(
                tool_name="terminal",
                args={"command": "git commit -m x", "cwd": "/r"},
                result="ok",
                task_id="t", session_id="s", tool_call_id="c", duration_ms=1,
            )
        elapsed_ms = (time.perf_counter() - t0) * 1000
        # Should return ~immediately; threading.Thread.start() is fast even
        # under load. Give 200 ms slack for slow CI hosts.
        assert elapsed_ms < 200, (
            f"_post_tool_call appears synchronous; 5 calls took {elapsed_ms:.0f} ms"
        )


# ---------------------------------------------------------------------------
# Bundled-plugin discovery via PluginManager
# ---------------------------------------------------------------------------


class TestPluginDiscovery:
    def test_discoverable_as_bundled_plugin(self, monkeypatch, tmp_path):
        """The plugin lives in <repo>/plugins/gitnexus-autorefresh/ so
        PluginManager.discover_and_load should find it as a bundled plugin
        regardless of whether the runtime ~/.hermes/plugins/ has it."""
        from hermes_cli.plugins import PluginManager

        # Enable the plugin via a minimal config in the isolated HERMES_HOME.
        cfg = tmp_path / ".hermes" / "config.yaml"
        cfg.write_text(
            "plugins:\n"
            "  enabled:\n"
            "    - gitnexus-autorefresh\n"
        )
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))

        mgr = PluginManager()
        mgr.discover_and_load(force=True)

        # Plugin should be discovered.
        assert "gitnexus-autorefresh" in mgr._plugins
        plugin = mgr._plugins["gitnexus-autorefresh"]
        assert plugin.enabled is True

        # Hook should be registered.
        callbacks = mgr._hooks.get("post_tool_call", [])
        names = [getattr(cb, "__name__", "") for cb in callbacks]
        assert "_post_tool_call" in names
