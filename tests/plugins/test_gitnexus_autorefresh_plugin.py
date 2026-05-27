"""Tests for the gitnexus-autorefresh plugin.

Covers the bundled plugin at ``plugins/gitnexus-autorefresh/``:

  * ``_GIT_MUTATION`` regex — match / no-match cases for the 10 git verbs
    plus the `-C <path>` / `--git-dir=…` / `--work-tree=…` forms.
  * ``_post_tool_call`` callback — tool filtering, args validation,
    workdir (NOT cwd — see tools/terminal_tool.py:2329) resolution,
    debounce coalescing, forward-compat kwarg swallowing,
    fire-and-forget threading.
  * ``_refresh_gitnexus`` worker — .gitnexus/ guard (is_dir not exists),
    Popen invocation shape (exact argv, start_new_session, DEVNULL),
    child reaping via wait(), timeout tolerance, catch-all on worker
    exceptions so the threading default excepthook doesn't leak.
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


@pytest.fixture(autouse=True)
def _reset_debounce():
    """Clear the module-level debounce dict between tests so a fire in one
    test doesn't silently mask the next test's fire."""
    mod = _load_plugin()
    with mod._REFRESH_LOCK:
        mod._LAST_REFRESH.clear()
    yield


def _load_plugin():
    """Import plugins/gitnexus-autorefresh/__init__.py with the
    hermes_plugins.<name> naming PluginManager uses so the module is
    independent of any prior import order in the test session."""
    repo_root = Path(__file__).resolve().parents[2]
    plugin_dir = repo_root / "plugins" / "gitnexus-autorefresh"
    # Reuse the loaded module across calls within a session — module-level
    # state (_LAST_REFRESH) must be stable so _reset_debounce can clear it.
    cached = sys.modules.get("hermes_plugins.gitnexus_autorefresh_under_test")
    if cached is not None:
        return cached
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


def _patch_worker_with_event(monkeypatch):
    """Replace `_refresh_gitnexus` with a recorder that signals an Event.

    Returns (called: list[str], done: Event). The Event allows tests to
    deterministically wait for the daemon thread to invoke the worker —
    no more `time.sleep(0.05)` polling that flakes under CPU load.
    """
    mod = _load_plugin()
    called: list[str] = []
    done = threading.Event()

    def _fake_refresh(cwd: str) -> None:
        called.append(cwd)
        done.set()

    monkeypatch.setattr(mod, "_refresh_gitnexus", _fake_refresh)
    return called, done


# ---------------------------------------------------------------------------
# Regex
# ---------------------------------------------------------------------------


class TestGitMutationRegex:
    @pytest.fixture(scope="class")
    def regex(self):
        return _load_plugin()._GIT_MUTATION

    @pytest.mark.parametrize("command", [
        # bare verbs
        "git commit -m foo",
        "git commit --allow-empty",
        "git merge --no-ff dev",
        "git pull origin main",
        "git checkout -b feat/x",
        "git rebase main",
        "git reset --hard HEAD",
        # new verbs added in the correctness pass
        "git cherry-pick abc123",
        "git revert HEAD~1",
        "git am < /tmp/patch.mbox",
        "git stash pop",
        "git stash  pop",  # double space tolerated by \s+
        # whitespace shapes
        "  git pull  ",
        "cd /tmp && git commit -m bar",
        # -C / --git-dir / --work-tree forms (the load-bearing miss the
        # original regex had — `git -C /repo commit` is the standard way
        # to run git in another directory without `cd`).
        "git -C /repo commit -m foo",
        "git -C /some/path pull",
        "git --git-dir=/x/.git commit",
        "git --work-tree=/x commit",
        "git -C /a --work-tree=/b commit -m foo",
        # env-prefixed
        "GIT_AUTHOR_NAME=foo git commit -m x",
        # sudo prefix (word-boundary on `git` handles this)
        "sudo git commit -m x",
    ])
    def test_matches_mutations(self, regex, command):
        assert regex.search(command) is not None, f"missed: {command!r}"

    @pytest.mark.parametrize("command", [
        "git status",
        "git log --oneline",
        "git branch -a",
        "git diff HEAD~",
        "git push origin main",   # push is intentionally not in the list
                                   # (HEAD doesn't change locally; index is
                                   # already up-to-date when push runs)
        "git clone https://example.com/x.git",  # creates a NEW repo elsewhere
        "gitlab clone",
        "gitignore add *.tmp",
        "ls -la",
        "",
    ])
    def test_rejects_non_mutations(self, regex, command):
        assert regex.search(command) is None, f"false-positive: {command!r}"

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
        called, done = _patch_worker_with_event(monkeypatch)
        mod._post_tool_call(
            tool_name="read_file",
            args={"path": "/tmp/x"},
            result="ok",
            **self._common_kwargs(),
        )
        # Worker should NOT fire — wait briefly and verify event not set.
        assert not done.wait(timeout=0.1)
        assert called == []

    def test_skips_when_args_is_none(self, monkeypatch):
        mod = _load_plugin()
        called, done = _patch_worker_with_event(monkeypatch)
        mod._post_tool_call(
            tool_name="terminal", args=None, result="ok",
            **self._common_kwargs(),
        )
        assert not done.wait(timeout=0.1)
        assert called == []

    def test_skips_when_args_not_dict(self, monkeypatch):
        mod = _load_plugin()
        called, done = _patch_worker_with_event(monkeypatch)
        mod._post_tool_call(
            tool_name="terminal", args="not-a-dict", result="ok",
            **self._common_kwargs(),
        )
        assert not done.wait(timeout=0.1)
        assert called == []

    def test_skips_when_command_missing(self, monkeypatch):
        mod = _load_plugin()
        called, done = _patch_worker_with_event(monkeypatch)
        mod._post_tool_call(
            tool_name="terminal", args={"workdir": "/tmp"}, result="ok",
            **self._common_kwargs(),
        )
        assert not done.wait(timeout=0.1)
        assert called == []

    def test_skips_when_command_not_string(self, monkeypatch):
        mod = _load_plugin()
        called, done = _patch_worker_with_event(monkeypatch)
        mod._post_tool_call(
            tool_name="terminal", args={"command": ["git", "commit"]}, result="ok",
            **self._common_kwargs(),
        )
        assert not done.wait(timeout=0.1)
        assert called == []

    def test_skips_non_mutation_command(self, monkeypatch):
        mod = _load_plugin()
        called, done = _patch_worker_with_event(monkeypatch)
        mod._post_tool_call(
            tool_name="terminal", args={"command": "git status"}, result="ok",
            **self._common_kwargs(),
        )
        assert not done.wait(timeout=0.1)
        assert called == []

    def test_fires_on_git_commit(self, monkeypatch):
        """Uses `workdir` because that's the terminal tool's actual schema
        field (tools/terminal_tool.py:2329) — NOT `cwd`. The original test
        used `cwd` and would have shipped a broken plugin."""
        mod = _load_plugin()
        called, done = _patch_worker_with_event(monkeypatch)
        mod._post_tool_call(
            tool_name="terminal",
            args={"command": "git commit -m smoke", "workdir": "/repo"},
            result="ok",
            **self._common_kwargs(),
        )
        assert done.wait(timeout=1.0), "worker thread did not fire"
        assert called == ["/repo"]

    def test_uses_explicit_workdir_from_args(self, monkeypatch):
        """workdir is the canonical key (terminal_tool schema field)."""
        mod = _load_plugin()
        called, done = _patch_worker_with_event(monkeypatch)
        mod._post_tool_call(
            tool_name="terminal",
            args={"command": "git pull", "workdir": "/some/repo"},
            result="ok",
            **self._common_kwargs(),
        )
        assert done.wait(timeout=1.0)
        assert called == ["/some/repo"]

    def test_cwd_legacy_key_still_works(self, monkeypatch):
        """Backward-compat: if some caller passes `cwd` (spec's original
        name), prefer workdir but fall back to cwd before getcwd()."""
        mod = _load_plugin()
        called, done = _patch_worker_with_event(monkeypatch)
        mod._post_tool_call(
            tool_name="terminal",
            args={"command": "git rebase main", "cwd": "/legacy/repo"},
            result="ok",
            **self._common_kwargs(),
        )
        assert done.wait(timeout=1.0)
        assert called == ["/legacy/repo"]

    def test_workdir_takes_priority_over_cwd(self, monkeypatch):
        """When both keys present, workdir wins (it's the real schema name)."""
        mod = _load_plugin()
        called, done = _patch_worker_with_event(monkeypatch)
        mod._post_tool_call(
            tool_name="terminal",
            args={
                "command": "git commit -m x",
                "workdir": "/real",
                "cwd": "/legacy",
            },
            result="ok",
            **self._common_kwargs(),
        )
        assert done.wait(timeout=1.0)
        assert called == ["/real"]

    def test_falls_back_to_process_cwd(self, monkeypatch):
        mod = _load_plugin()
        called, done = _patch_worker_with_event(monkeypatch)
        monkeypatch.setattr(mod.os, "getcwd", lambda: "/process/cwd")
        mod._post_tool_call(
            tool_name="terminal",
            args={"command": "git rebase main"},  # no workdir or cwd
            result="ok",
            **self._common_kwargs(),
        )
        assert done.wait(timeout=1.0)
        assert called == ["/process/cwd"]

    def test_handles_os_getcwd_oserror(self, monkeypatch):
        """After `rm -rf $PWD`, os.getcwd() raises FileNotFoundError. The
        plugin should silently skip rather than propagate."""
        mod = _load_plugin()
        called, done = _patch_worker_with_event(monkeypatch)

        def _raising_getcwd():
            raise FileNotFoundError("no such directory")

        monkeypatch.setattr(mod.os, "getcwd", _raising_getcwd)
        # No raise; no fire.
        mod._post_tool_call(
            tool_name="terminal",
            args={"command": "git commit -m x"},  # no workdir/cwd
            result="ok",
            **self._common_kwargs(),
        )
        assert not done.wait(timeout=0.1)
        assert called == []

    def test_swallows_forward_compat_kwargs(self, monkeypatch):
        mod = _load_plugin()
        called, done = _patch_worker_with_event(monkeypatch)
        mod._post_tool_call(
            tool_name="terminal",
            args={"command": "git commit -m x", "workdir": "/r"},
            result="ok",
            future_field_xyz="ignored",
            another_new_field=123,
            **self._common_kwargs(),
        )
        assert done.wait(timeout=1.0)
        assert len(called) == 1


class TestDebounce:
    """Per-cwd debounce coalesces burst events so we don't fork N concurrent
    npx processes when rebase / dream apply produce a tight commit stream."""

    def test_second_call_within_window_is_coalesced(self, monkeypatch):
        mod = _load_plugin()
        called: list[str] = []
        done = threading.Event()

        def _record(cwd):
            called.append(cwd)
            done.set()

        monkeypatch.setattr(mod, "_refresh_gitnexus", _record)
        kw = dict(
            tool_name="terminal",
            args={"command": "git commit -m x", "workdir": "/r"},
            result="ok",
            task_id="", session_id="", tool_call_id="", duration_ms=1,
        )
        mod._post_tool_call(**kw)
        assert done.wait(timeout=1.0)
        # Second fire within the 5s window: should be coalesced.
        done.clear()
        mod._post_tool_call(**kw)
        assert not done.wait(timeout=0.2)  # never fires
        assert called == ["/r"]

    def test_different_cwds_dont_coalesce(self, monkeypatch):
        mod = _load_plugin()
        called: list[str] = []
        done1 = threading.Event()
        done2 = threading.Event()

        def _record(cwd):
            called.append(cwd)
            if cwd == "/repo-A":
                done1.set()
            elif cwd == "/repo-B":
                done2.set()

        monkeypatch.setattr(mod, "_refresh_gitnexus", _record)
        mod._post_tool_call(
            tool_name="terminal",
            args={"command": "git commit -m a", "workdir": "/repo-A"},
            result="ok",
            task_id="", session_id="", tool_call_id="", duration_ms=1,
        )
        mod._post_tool_call(
            tool_name="terminal",
            args={"command": "git commit -m b", "workdir": "/repo-B"},
            result="ok",
            task_id="", session_id="", tool_call_id="", duration_ms=1,
        )
        assert done1.wait(timeout=1.0)
        assert done2.wait(timeout=1.0)
        assert sorted(called) == ["/repo-A", "/repo-B"]

    def test_window_releases_after_debounce_seconds(self, monkeypatch):
        """After _DEBOUNCE_SECONDS elapses, a second call MUST fire."""
        mod = _load_plugin()
        called: list[str] = []

        def _record(cwd):
            called.append(cwd)

        monkeypatch.setattr(mod, "_refresh_gitnexus", _record)
        # First fire.
        mod._post_tool_call(
            tool_name="terminal",
            args={"command": "git commit -m x", "workdir": "/r"},
            result="ok",
            task_id="", session_id="", tool_call_id="", duration_ms=1,
        )
        # Simulate time passing by overriding monotonic; faster than sleep(5).
        future = time.monotonic() + 100.0
        monkeypatch.setattr(mod.time, "monotonic", lambda: future)
        mod._post_tool_call(
            tool_name="terminal",
            args={"command": "git commit -m y", "workdir": "/r"},
            result="ok",
            task_id="", session_id="", tool_call_id="", duration_ms=1,
        )
        # Both fire — give threads a beat.
        for _ in range(20):
            if len(called) == 2:
                break
            time.sleep(0.02)
        assert called.count("/r") == 2


# ---------------------------------------------------------------------------
# _refresh_gitnexus worker
# ---------------------------------------------------------------------------


class TestRefreshGitnexus:
    def test_no_op_when_no_gitnexus_dir(self, tmp_path):
        mod = _load_plugin()
        with patch.object(subprocess, "Popen") as mock_popen:
            mod._refresh_gitnexus(str(tmp_path))
            mock_popen.assert_not_called()

    def test_no_op_when_gitnexus_is_file_not_dir(self, tmp_path):
        """is_dir() guard prevents npx from initializing a fresh index into
        the user's repo when a stray FILE called .gitnexus exists."""
        mod = _load_plugin()
        (tmp_path / ".gitnexus").write_text("not a directory")
        with patch.object(subprocess, "Popen") as mock_popen:
            mod._refresh_gitnexus(str(tmp_path))
            mock_popen.assert_not_called()

    def test_launches_popen_when_gitnexus_dir_exists(self, tmp_path):
        mod = _load_plugin()
        (tmp_path / ".gitnexus").mkdir()
        with patch.object(subprocess, "Popen") as mock_popen:
            proc = mock_popen.return_value
            proc.wait.return_value = 0
            mod._refresh_gitnexus(str(tmp_path))
            mock_popen.assert_called_once()
            proc.wait.assert_called_once()  # reaping

    def test_popen_argv_exact(self, tmp_path):
        """Tight argv assertion — catches a regression that drops
        `--no-install` or `--skip-agents-md` (both load-bearing)."""
        mod = _load_plugin()
        (tmp_path / ".gitnexus").mkdir()
        with patch.object(subprocess, "Popen") as mock_popen:
            mock_popen.return_value.wait.return_value = 0
            mod._refresh_gitnexus(str(tmp_path))
            argv = mock_popen.call_args.args[0]
            assert argv == [
                "npx", "--no-install", "gitnexus", "analyze", "--skip-agents-md"
            ]

    def test_popen_uses_start_new_session_and_devnull(self, tmp_path):
        """start_new_session=True detaches the subprocess so SIGINT/SIGTERM
        of Hermes doesn't kill an in-flight analyze. DEVNULL keeps npx's
        progress output out of the agent's turn output."""
        mod = _load_plugin()
        (tmp_path / ".gitnexus").mkdir()
        with patch.object(subprocess, "Popen") as mock_popen:
            mock_popen.return_value.wait.return_value = 0
            mod._refresh_gitnexus(str(tmp_path))
            kwargs = mock_popen.call_args.kwargs
            assert kwargs.get("start_new_session") is True
            assert kwargs.get("stdout") is subprocess.DEVNULL
            assert kwargs.get("stderr") is subprocess.DEVNULL
            assert kwargs.get("cwd") == str(tmp_path)

    def test_reaps_child_via_wait(self, tmp_path):
        """Without wait(), the child becomes a zombie until Hermes exits.
        Multiplied across a long session this leaks PIDs."""
        mod = _load_plugin()
        (tmp_path / ".gitnexus").mkdir()
        with patch.object(subprocess, "Popen") as mock_popen:
            mock_popen.return_value.wait.return_value = 0
            mod._refresh_gitnexus(str(tmp_path))
            mock_popen.return_value.wait.assert_called_once()

    def test_handles_wait_timeout(self, tmp_path):
        """If gitnexus runs >300s, log and leave it — don't kill."""
        mod = _load_plugin()
        (tmp_path / ".gitnexus").mkdir()
        with patch.object(subprocess, "Popen") as mock_popen:
            mock_popen.return_value.wait.side_effect = subprocess.TimeoutExpired(
                cmd="npx gitnexus", timeout=300
            )
            # Should not raise.
            mod._refresh_gitnexus(str(tmp_path))

    def test_silent_on_missing_npx(self, tmp_path, caplog):
        """FileNotFoundError on Popen → WARN log + silent skip, no crash."""
        mod = _load_plugin()
        (tmp_path / ".gitnexus").mkdir()
        with patch.object(subprocess, "Popen", side_effect=FileNotFoundError("npx")):
            # No raise.
            mod._refresh_gitnexus(str(tmp_path))

    def test_silent_on_os_error(self, tmp_path):
        mod = _load_plugin()
        (tmp_path / ".gitnexus").mkdir()
        with patch.object(subprocess, "Popen", side_effect=OSError("EAGAIN")):
            mod._refresh_gitnexus(str(tmp_path))

    def test_swallows_unexpected_exception(self, tmp_path):
        """Daemon-thread context: an uncaught exception would hit
        threading.excepthook and print to stderr, polluting the agent's
        turn output. The catch-all at the bottom of _refresh_gitnexus
        prevents that."""
        mod = _load_plugin()
        (tmp_path / ".gitnexus").mkdir()
        # ValueError is NOT in the (FileNotFoundError, OSError) clause but
        # IS in the outer catch-all. Test that exhausts both layers.
        with patch.object(subprocess, "Popen", side_effect=ValueError("synthetic")):
            mod._refresh_gitnexus(str(tmp_path))  # no raise


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
# Threading non-blocking property
# ---------------------------------------------------------------------------


class TestThreadingNonBlocking:
    def test_callback_returns_before_worker_runs(self, monkeypatch):
        """Stronger version of the previous timing-based test: assert
        that the worker has NOT yet run when _post_tool_call returns.

        Without the daemon-thread hand-off, the worker would run inline
        and the event would already be set when we return. The Event-based
        check is unaffected by CPU load."""
        mod = _load_plugin()
        worker_started = threading.Event()
        worker_can_proceed = threading.Event()

        def _slow_worker(cwd):
            worker_started.set()
            worker_can_proceed.wait(timeout=1.0)

        monkeypatch.setattr(mod, "_refresh_gitnexus", _slow_worker)

        mod._post_tool_call(
            tool_name="terminal",
            args={"command": "git commit -m x", "workdir": "/r"},
            result="ok",
            task_id="", session_id="", tool_call_id="", duration_ms=1,
        )
        # The worker may or may not have started yet (it's a separate
        # thread); regardless, _post_tool_call ran in microseconds and
        # didn't block on worker_can_proceed.
        worker_started.wait(timeout=1.0)
        assert worker_started.is_set(), "worker thread never started"
        # If _post_tool_call had been synchronous it would have blocked
        # on worker_can_proceed forever; we'd never have reached here.
        worker_can_proceed.set()


# ---------------------------------------------------------------------------
# Bundled-plugin discovery via PluginManager
# ---------------------------------------------------------------------------


class TestPluginDiscovery:
    def test_discoverable_as_bundled_plugin(self, monkeypatch, tmp_path):
        """The plugin lives in <repo>/plugins/gitnexus-autorefresh/ so
        PluginManager.discover_and_load should find it as a bundled plugin
        regardless of whether the runtime ~/.hermes/plugins/ has it."""
        from hermes_cli.plugins import PluginManager

        cfg = tmp_path / ".hermes" / "config.yaml"
        cfg.write_text(
            "plugins:\n"
            "  enabled:\n"
            "    - gitnexus-autorefresh\n"
        )
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))

        mgr = PluginManager()
        mgr.discover_and_load(force=True)

        assert "gitnexus-autorefresh" in mgr._plugins
        plugin = mgr._plugins["gitnexus-autorefresh"]
        assert plugin.enabled is True

        callbacks = mgr._hooks.get("post_tool_call", [])
        names = [getattr(cb, "__name__", "") for cb in callbacks]
        assert "_post_tool_call" in names
