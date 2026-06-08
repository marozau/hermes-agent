"""Shared HERMES_HOME resolver for lib/* helpers (Bug 1 fix).

The agent runtime scopes HERMES_HOME per-task via a ContextVar in
``hermes_constants`` (see ``set_hermes_home_override``) deliberately *without*
mutating ``os.environ`` — the env is shared across threads, which would race.

Until this module existed, lib/* helpers only consulted ``os.environ`` and
were blind to that override, so every preflight / memory / dream / recall
write made under a non-default profile silently landed in the default
``~/.hermes`` scope. (Observed empirically: 603 distinct session_ids in
``~/.hermes/preflight/log/2026-05-29.jsonl`` from profile-bound traffic.)

``resolve_hermes_home`` consults env first, then the runtime's ContextVar
(via lazy import — keeps lib/* importable from standalone scripts and tests),
then falls back to ``~/.hermes``.
"""
from __future__ import annotations

import os
from pathlib import Path


def resolve_hermes_home() -> str:
    """Return the active HERMES_HOME as a string path.

    Resolution order:
      1. ``$HERMES_HOME`` env var (set explicitly by subprocess spawners)
      2. ``hermes_constants.get_hermes_home_override()`` ContextVar
         (set by profile activation in cron/scheduler.py and friends)
      3. ``~/.hermes``
    """
    env = os.environ.get("HERMES_HOME", "").strip()
    if env:
        return env
    try:
        from hermes_constants import get_hermes_home_override
        override = get_hermes_home_override()
        if override:
            return str(override)
    except ImportError:
        pass
    return str(Path.home() / ".hermes")
