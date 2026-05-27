"""Hermes plugin: auto-refresh GitNexus index after git-mutation tool calls.

Triggers `npx gitnexus analyze --skip-agents-md` (backgrounded) after any
terminal tool call whose command contains a git mutation (commit, merge,
pull, checkout, rebase, reset, cherry-pick, revert, am, stash pop), and
also recognises the `-C <path>` / `--git-dir=…` / `--work-tree=…` forms.

Threading model: `_post_tool_call` returns in sub-millisecond time. The
actual Popen + child reaping happen on a daemon worker thread so the
fork/exec cost (tens of ms on slow disks) plus the `wait()` that prevents
zombie children don't block the hook chain.

Concurrency model: a per-cwd debounce window (5 s) coalesces burst events
(rebase / dream apply / squash). Without it, a tight burst spawns N
concurrent npx processes that all queue on GitNexus's `.gitnexus/.lock`,
wasting node startup cost.

References:
- hermes_cli/plugins.py:669 — register_hook(hook_name, callback)
- hermes_cli/plugins.py:1284 — invoke_hook (exception-isolated)
- model_tools.py:825-838 — post_tool_call dispatch site
- tools/terminal_tool.py:2329 — schema declares `workdir` (NOT cwd)
- /Users/im/usr-local/hermes-vscode/planning-artifacts/research/technical-gitnexus-index-freshness-hermes-2026-05-27.md
"""

import logging
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Match `git VERB ...`, `git -C <path> VERB ...`, `git --git-dir=… VERB ...`,
# `git --work-tree=… VERB ...`, and combinations. Verbs cover every form
# that mutates HEAD / refs / working tree. False-friend tokens like
# `gitignore` / `gitlab` are rejected by the leading `\bgit` word boundary.
_GIT_MUTATION = re.compile(
    r"\bgit"
    r"(?:\s+-C\s+\S+|\s+--git-dir=\S+|\s+--work-tree=\S+)*"
    r"\s+(commit|merge|pull|checkout|rebase|reset|cherry-pick|revert|am|stash\s+pop)\b"
)

# Per-cwd debounce. Bounded growth: ~N unique repos per session, each
# entry is a (str, float) pair → kilobytes for any realistic workload.
# No explicit eviction; old entries are harmless.
_DEBOUNCE_SECONDS = 5.0
_LAST_REFRESH: dict[str, float] = {}
_REFRESH_LOCK = threading.Lock()


def _should_refresh(cwd: str) -> bool:
    """True iff we haven't fired for this cwd within _DEBOUNCE_SECONDS.

    Locking the dict access keeps concurrent _post_tool_call invocations
    from both observing "no recent fire" simultaneously and racing two
    npx processes to the GitNexus lock. The window is intentionally
    short — long enough to absorb a typical commit/push burst, short
    enough that a deliberate second commit ~10s later still refreshes.
    """
    now = time.monotonic()
    with _REFRESH_LOCK:
        last = _LAST_REFRESH.get(cwd, 0.0)
        if now - last < _DEBOUNCE_SECONDS:
            return False
        _LAST_REFRESH[cwd] = now
    return True


def _refresh_gitnexus(cwd: str) -> None:
    """Run `npx gitnexus analyze` in `cwd`, then reap the child.

    Daemon-thread context. The catch-all at the bottom prevents the
    threading default excepthook from printing to stderr (which would
    pollute the agent's turn output — the exact thing DEVNULL was
    meant to prevent for the npx subprocess).
    """
    try:
        gitnexus_dir = Path(cwd) / ".gitnexus"
        # is_dir() (not exists()) so a stray FILE named .gitnexus doesn't
        # trick npx into initializing a fresh index in the user's repo.
        # Also rejects dangling symlinks.
        if not gitnexus_dir.is_dir():
            return

        try:
            proc = subprocess.Popen(
                ["npx", "--no-install", "gitnexus", "analyze", "--skip-agents-md"],
                cwd=cwd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                # Detach from Hermes' process group so SIGINT/SIGTERM of the
                # parent doesn't kill an in-flight analyze. Still child of
                # this process — we MUST wait() below or it zombies until
                # Hermes exits.
                start_new_session=True,
            )
        except (FileNotFoundError, OSError) as exc:
            # npx not on PATH, fork() failure, or `--no-install` with no
            # locally-installed gitnexus. Surface at WARN — the operator
            # needs to know the plugin is silently no-op'ing.
            logger.warning(
                "gitnexus-autorefresh: subprocess launch failed (cwd=%s): %s",
                cwd, exc,
            )
            return

        # Block here so the OS reaps the child. We're on a daemon thread
        # spawned per-fire; nothing depends on us returning fast.
        # 300s timeout because gitnexus can take 30-60s on a 200k-symbol
        # repo and we don't want to false-alarm.
        try:
            proc.wait(timeout=300)
        except subprocess.TimeoutExpired:
            # Don't kill it. Either gitnexus is genuinely slow on a huge
            # repo or it's wedged; both are operator concerns, not ours.
            logger.warning(
                "gitnexus-autorefresh: analyze still running after 300s (cwd=%s); leaving it",
                cwd,
            )
    except Exception as exc:  # noqa: BLE001 — daemon thread top-level
        # Without this, threading.excepthook prints to stderr and the agent
        # sees "Exception in thread gitnexus-refresh ..." in its turn output.
        logger.warning("gitnexus-autorefresh: worker raised: %s", exc)


def _resolve_cwd(args: dict[str, Any]) -> str | None:
    """Pick the working dir to analyze. Returns None if unresolvable.

    Priority: `workdir` (terminal tool's actual schema field, see
    tools/terminal_tool.py:2329) → `cwd` (legacy/spec compat) →
    `os.getcwd()`. The last can raise FileNotFoundError after the user
    does `rm -rf $PWD`, so we wrap it.
    """
    cwd = args.get("workdir") or args.get("cwd")
    if cwd:
        return cwd
    try:
        return os.getcwd()
    except OSError:
        return None


def _post_tool_call(
    tool_name: str,
    args: dict,
    result: object,
    task_id: str = "",
    session_id: str = "",
    tool_call_id: str = "",
    duration_ms: int = 0,
    **kwargs,
) -> None:
    """post_tool_call hook — observational; return value ignored.

    Matches model_tools.py:827-836 callsite signature. Extra fields are
    swallowed by **kwargs so forward-additions to the hook contract don't
    break the plugin.
    """
    if tool_name != "terminal":
        return
    if not isinstance(args, dict):
        return

    command = args.get("command", "")
    if not isinstance(command, str) or not _GIT_MUTATION.search(command):
        return

    cwd = _resolve_cwd(args)
    if not cwd:
        return

    if not _should_refresh(cwd):
        return

    # Fire-and-forget. Daemon thread so it doesn't keep Hermes alive at exit.
    threading.Thread(
        target=_refresh_gitnexus,
        args=(cwd,),
        daemon=True,
        name=f"gitnexus-refresh-{int(time.monotonic() * 1000) & 0xFFFFFF:06x}",
    ).start()


def register(ctx):
    """Plugin entry point. Called by the Hermes plugin loader exactly once."""
    ctx.register_hook("post_tool_call", _post_tool_call)
    logger.debug("gitnexus-autorefresh: post_tool_call hook registered")
