"""Hermes plugin: auto-refresh GitNexus index after git-mutation tool calls.

Triggers `npx gitnexus analyze --skip-agents-md` (backgrounded) after any
terminal tool call whose command contains a git mutation (commit, merge,
pull, checkout, rebase, reset).

Fire-and-forget: the analyze runs in a detached subprocess so the next
agent turn isn't blocked. GitNexus's own `.gitnexus/.lock` serializes
concurrent invocations, so two near-simultaneous git commits don't race.

Why a thread wrapping Popen rather than just Popen directly: the
post_tool_call hook is sync and we want to return to the hook chain in
sub-millisecond time. Popen itself is non-blocking but on slow disks the
fork/exec can take tens of ms; the thread isolates that cost.

References:
- hermes_cli/plugins.py:669 — register_hook(hook_name, callback)
- hermes_cli/plugins.py:1284 — invoke_hook (exception-isolated)
- model_tools.py:825-838 — post_tool_call dispatch site
- /Users/im/usr-local/hermes-vscode/planning-artifacts/research/technical-gitnexus-index-freshness-hermes-2026-05-27.md
"""

import logging
import os
import re
import subprocess
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

# Word-boundary regex catches `git commit ...`, `  git pull  `, etc.
# Excludes false-friend tokens like `gitignore` or `gitlab`.
_GIT_MUTATION = re.compile(r"\bgit\s+(commit|merge|pull|checkout|rebase|reset)\b")


def _refresh_gitnexus(cwd: str) -> None:
    """Run `npx gitnexus analyze` in `cwd`, fully detached.

    Idempotent guard: if the cwd has no .gitnexus/ folder, this isn't a
    GitNexus-indexed repo — exit immediately without launching a subprocess.

    stdout/stderr go to /dev/null because:
    - the agent's turn output should stay clean of background-process chatter
    - GitNexus writes its own log to `.gitnexus/log.txt` for debugging
    """
    gitnexus_dir = Path(cwd) / ".gitnexus"
    if not gitnexus_dir.exists():
        return

    try:
        subprocess.Popen(
            ["npx", "--no-install", "gitnexus", "analyze", "--skip-agents-md"],
            cwd=cwd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,  # detach from Hermes' process group so SIGINT/SIGTERM
                                     # of the parent doesn't kill an in-flight analyze
        )
    except (FileNotFoundError, OSError) as exc:
        # npx not on PATH, or fork() failure. Don't spam errors per turn —
        # the user has either uninstalled Node or hit a transient OS issue.
        logger.debug("gitnexus-autorefresh: subprocess launch failed: %s", exc)


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

    # cwd resolution priority: explicit tool arg → process cwd. The tool
    # invocation may set cwd= to the repo it's operating in even when
    # Hermes' own cwd is elsewhere; we want to refresh THAT repo's index.
    cwd = args.get("cwd") or os.getcwd()

    # Fire-and-forget. Daemon thread so it doesn't keep Hermes alive at exit.
    threading.Thread(
        target=_refresh_gitnexus,
        args=(cwd,),
        daemon=True,
        name="gitnexus-refresh",
    ).start()


def register(ctx):
    """Plugin entry point. Called by the Hermes plugin loader exactly once."""
    ctx.register_hook("post_tool_call", _post_tool_call)
    logger.debug("gitnexus-autorefresh: post_tool_call hook registered")
