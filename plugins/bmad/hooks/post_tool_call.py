"""post_tool_call hook — BMAD auto-status tracking + runtime mirror.

Per architecture A-9 (FR-10): after every Write/Edit tool call that
matches a known BMAD artifact path pattern, automatically update
workflow-status.yaml to mark the corresponding slot as complete.

Story 6.8 (FR-25): When a write lands inside a worktree that declares
``runtime_mirror:``, single-file copy to the corresponding runtime path
+ remove matching ``__pycache__/*.pyc``.

Hard invariant: WI-5 — runtime_mirror is opt-in per worktree, single-file
cp only (never recursive).

Wrapped by _catch_all in __init__.py — never raises.
"""

from __future__ import annotations

import glob
import hashlib
import logging
import os
import re
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

# Ordered: most specific first
PATH_RULES: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"planning-artifacts/solutioning-gate-check.*\.md$"), "solutioning", "solutioning-gate-check"),
    (re.compile(r"planning-artifacts/(epics-stories|epics)[-_/].*"), "solutioning", "epics-stories"),
    (re.compile(r"planning-artifacts/(architecture|tech-spec)[-_].*\.md$"), "solutioning", "architecture"),
    (re.compile(r"planning-artifacts/prd[-_].*\.md$"), "planning", "prd"),
    (re.compile(r"planning-artifacts/product-brief.*\.md$"), "analysis", "product-brief"),
    (re.compile(r"planning-artifacts/research/.*"), "analysis", "research"),
    (re.compile(r"implementation-artifacts/stories/.*\.md$"), "implementation", "story"),
]


def post_tool_call(ctx, tool_name: str, args: dict, result: dict | None = None, **kwargs) -> None:
    """Auto-update workflow-status.yaml + runtime mirror after Write/Edit.

    If the written file matches a known artifact pattern and the slot
    isn't already marked as complete, atomically update the status.

    If in workspace mode and the write targets a worktree with runtime_mirror,
    sync the file to the runtime location (Story 6.8).
    """
    if tool_name not in ("Write", "Edit", "write_file", "edit_file"):
        return

    project_dir = _resolve_project_dir(ctx)
    if project_dir is None:
        return

    config_path = project_dir / "bmad" / "config.yaml"
    if not config_path.exists():
        return  # Not a BMAD project

    file_path = args.get("file_path", args.get("path", ""))
    if not file_path:
        return

    # ── Story 6.8: Runtime mirror check ─────────────────────────────
    _try_runtime_mirror(file_path, project_dir, config_path)

    # ── Existing status update logic ────────────────────────────────
    rel = _relative_to_project(file_path, project_dir)
    if rel is None:
        return  # Write outside project

    phase_slot = _match_path(rel)
    if phase_slot is None:
        return  # Not a known artifact

    phase, slot = phase_slot

    from plugins.bmad.lib import status as s

    try:
        state = s.load(project_dir)
        current = state.get("phases", {}).get(phase, {}).get(slot)
        if current != rel:  # idempotency check
            logger.info("[bmad:post_tool_call] %s → %s/%s complete", rel, phase, slot)
            s.mark_complete(project_dir, phase, slot, rel)
    except Exception:
        logger.exception("[bmad:post_tool_call] Status update failed — allowing through")


def _try_runtime_mirror(
    file_path: str,
    project_dir: Path,
    config_path: Path,
) -> None:
    """Attempt runtime mirror for a write (Story 6.8).

    Checks if the file belongs to a worktree with runtime_mirror configured.
    If so, copies the file to the runtime location and cleans stale .pyc files.
    """
    try:
        import yaml
        raw_config = yaml.safe_load(config_path.read_text()) or {}
        if not raw_config.get("workspace_mode"):
            return

        worktrees = raw_config.get("worktrees", [])
        if not worktrees:
            return

        resolved = Path(file_path).resolve()
        root = project_dir.resolve()

        for wt in worktrees:
            runtime_mirror = wt.get("runtime_mirror")
            if not runtime_mirror:
                continue  # WI-5: skip when no mirror declared (AC-6.8.3)

            wt_path = root / wt["path"]
            try:
                rel = resolved.relative_to(wt_path.resolve())
            except ValueError:
                continue

            # File belongs to this worktree
            mirror_root = Path(os.path.expanduser(runtime_mirror))
            if not mirror_root.exists():
                logger.warning(
                    "[bmad:runtime_mirror] Mirror dir missing: %s — skipping",
                    mirror_root,
                )
                return  # AC-6.8.5: warn, don't block

            dest = mirror_root / rel

            # AC-6.8.4: Skip if content unchanged
            if dest.exists():
                try:
                    src_hash = hashlib.sha256(resolved.read_bytes()).hexdigest()
                    dest_hash = hashlib.sha256(dest.read_bytes()).hexdigest()
                    if src_hash == dest_hash:
                        return  # Idempotent — no copy needed
                except OSError:
                    pass

            # AC-6.8.2: Single-file copy only
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(resolved), str(dest))

            # Clean matching __pycache__/*.pyc (WI-5)
            _clean_pycache(dest)

            # AC-6.8.7: Telemetry
            logger.info(
                "[bmad:runtime_mirror] %s → %s",
                resolved, dest,
            )
            return  # Only mirror to first matching worktree

    except Exception:
        logger.exception("[bmad:runtime_mirror] Mirror failed — not blocking")


def _clean_pycache(file_path: Path) -> None:
    """Remove matching __pycache__/*.pyc for a Python file (WI-5)."""
    if file_path.suffix != ".py":
        return

    pycache_dir = file_path.parent / "__pycache__"
    if not pycache_dir.exists():
        return

    # Find matching .pyc files (e.g. foo.cpython-311.pyc)
    stem = file_path.stem
    pattern = str(pycache_dir / f"{stem}.cpython-*.pyc")
    for pyc in glob.glob(pattern):
        try:
            os.unlink(pyc)
        except OSError:
            pass


def _relative_to_project(file_path: str, project_dir: Path) -> str | None:
    path = Path(file_path).resolve()
    try:
        return str(path.relative_to(project_dir.resolve()))
    except ValueError:
        return None


def _match_path(rel_path: str) -> tuple[str, str] | None:
    for pattern, phase, slot in PATH_RULES:
        if pattern.search(rel_path):
            return (phase, slot)
    return None


def _resolve_project_dir(ctx) -> Path | None:
    if hasattr(ctx, "project_dir") and ctx.project_dir:
        return Path(ctx.project_dir)
    if hasattr(ctx, "working_directory") and ctx.working_directory:
        return Path(ctx.working_directory)
    return None
