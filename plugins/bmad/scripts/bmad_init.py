"""BMAD project bootstrap — scaffold a new project with config.yaml and workflow-status.yaml.

Architecture A-12 (bootstrap script), D-3 (config generation).

Exports:
    bootstrap(project_dir, *, project_name, project_type, project_level,
              user_name, force=False, interactive=True) -> dict
    cli_main()  — CLI entry point with argparse
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

import yaml

from plugins.bmad.lib._datetime import _today_iso


# ── Public API ──────────────────────────────────────────────────────────────


def bootstrap(
    project_dir: str | Path,
    *,
    project_name: str,
    project_type: str = "other",
    project_level: int = 1,
    user_name: str = "",
    force: bool = False,
    interactive: bool = True,
) -> dict:
    """Scaffold a new BMAD project under *project_dir*.

    Creates::

        bmad/config.yaml
        planning-artifacts/workflow-status.yaml
        planning-artifacts/research/
        implementation-artifacts/stories/

    Parameters
    ----------
    project_dir:
        Root directory for the new project.  Created if it does not exist.
    project_name:
        Human-readable project name (required).
    project_type:
        One of ``web-app``, ``mobile-app``, ``api``, ``library``, ``game``, ``other``.
    project_level:
        BMAD rigor level (0–4).  Controls which workflow slots are required.
    user_name:
        Name of the user/owner (optional in CLI, required in interactive? — we store
        empty string if not provided).
    force:
        If ``False`` (default), raise :exc:`RuntimeError` when
        ``bmad/config.yaml`` already exists.
    interactive:
        If ``True`` (default), prompt before overwriting when *force* is
        ``False`` and config exists.  If ``False`` and config exists and
        *force* is ``False``, raise immediately.

    Returns
    -------
    dict
        The final config dict as written to ``bmad/config.yaml``.

    Raises
    ------
    RuntimeError
        If ``bmad/config.yaml`` already exists and *force* is ``False``.
    """
    project_dir = Path(project_dir)

    # ── Guard: existing config ──────────────────────────────────────
    config_path = project_dir / "bmad" / "config.yaml"
    if config_path.exists():
        if force:
            # Overwrite — no questions asked
            pass
        elif interactive:
            answer = input(
                f"bmad/config.yaml already exists at {project_dir}. "
                f"Overwrite? [y/N] "
            )
            if answer.strip().lower() not in ("y", "yes"):
                print("Aborted by user.")
                sys.exit(1)
        else:
            raise RuntimeError(
                f"bmad/config.yaml already exists at {project_dir}. "
                f"Use force=True or --force to overwrite."
            )

    # ── Build config dict ───────────────────────────────────────────
    today = _today_iso()
    config: dict = {
        "project_name": project_name,
        "project_type": project_type,
        "project_level": project_level,
        "user_name": user_name,
        "planning_artifacts": "planning-artifacts",
        "implementation_artifacts": "implementation-artifacts",
        "created": today,
    }

    # ── Build workflow-status ───────────────────────────────────────
    level = int(project_level)
    # Determine which slots are "required" vs "optional"/"not-started"
    is_prd_required = level >= 2
    is_architecture_required = level >= 2
    is_gate_check_required = level >= 2

    # For level <= 1, tech-spec replaces architecture
    use_tech_spec = level <= 1

    phases: dict = {
        "analysis": {
            "product-brief": "not-started",
            "research": "optional",
            "brainstorm": "optional",
            "document-project": "optional",
            "quick-spec": "optional",
        },
        "planning": {
            "prd": "required" if is_prd_required else "optional",
            "ux-design": "optional",
        },
        "solutioning": {
            "epics-stories": "optional",
        },
        "implementation": {
            "sprint-planning": "optional",
            "story": "optional",
            "dev-story": "optional",
            "code-review": "optional",
            "correct-course": "optional",
            "quick-dev": "optional",
        },
    }

    # Add ux-design to solutioning AND architecture / tech-spec
    if use_tech_spec:
        phases["solutioning"]["tech-spec"] = "optional"
    else:
        phases["solutioning"]["architecture"] = "optional"
        if is_architecture_required:
            phases["solutioning"]["architecture"] = "required"
    phases["solutioning"]["ux-design"] = "optional"
    phases["solutioning"]["solutioning-gate-check"] = (
        "required" if is_gate_check_required else "optional"
    )

    workflow_status: dict = {
        "project": project_name,
        "level": level,
        "created": today,
        "last_updated": today,
        "phases": phases,
    }

    # ── Create directories ──────────────────────────────────────────
    bmad_dir = project_dir / "bmad"
    planning_dir = project_dir / "planning-artifacts"
    research_dir = planning_dir / "research"
    stories_dir = project_dir / "implementation-artifacts" / "stories"

    bmad_dir.mkdir(parents=True, exist_ok=True)
    planning_dir.mkdir(parents=True, exist_ok=True)
    research_dir.mkdir(parents=True, exist_ok=True)
    stories_dir.mkdir(parents=True, exist_ok=True)

    # ── Atomic writes ───────────────────────────────────────────────
    _atomic_write_yaml(config_path, config)
    _atomic_write_yaml(planning_dir / "workflow-status.yaml", workflow_status)

    return config


# ── CLI Entry Point ─────────────────────────────────────────────────────────


def cli_main(argv: list[str] | None = None) -> None:
    """CLI entry point for ``bmad-init``.

    Exit codes:
        0 — success
        1 — user-correctable error (e.g. missing required field in non-interactive mode)
        2 — invalid arguments
        3 — existing config without --force
    """
    parser = argparse.ArgumentParser(
        prog="bmad-init",
        description="Scaffold a new BMAD project in the current directory.",
    )
    parser.add_argument(
        "--project-name",
        type=str,
        default=None,
        help="Human-readable project name (required).",
    )
    parser.add_argument(
        "--project-type",
        type=str,
        default="other",
        choices=["web-app", "mobile-app", "api", "library", "game", "other"],
        help="Type of project (default: other).",
    )
    parser.add_argument(
        "--project-level",
        type=int,
        default=1,
        choices=[0, 1, 2, 3, 4],
        help="BMAD rigor level 0–4 (default: 1).",
    )
    parser.add_argument(
        "--user-name",
        type=str,
        default="",
        help="Name of the project owner/user.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Overwrite existing bmad/config.yaml without prompting.",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        default=False,
        help="Fail immediately if config exists or required fields are missing.",
    )

    parsed = parser.parse_args(argv)

    # ── Validate required fields in non-interactive mode ────────────
    if parsed.non_interactive and not parsed.project_name:
        print("Error: --project-name is required in non-interactive mode.", file=sys.stderr)
        sys.exit(2)

    if parsed.non_interactive and not parsed.user_name:
        print("Error: --user-name is required in non-interactive mode.", file=sys.stderr)
        sys.exit(2)

    # ── Interactive prompting ───────────────────────────────────────
    project_name: str | None = parsed.project_name
    user_name: str | None = parsed.user_name
    project_type: str = parsed.project_type
    project_level: int = parsed.project_level
    force: bool = parsed.force
    non_interactive: bool = parsed.non_interactive

    if not non_interactive:
        if not project_name:
            project_name = input("Project name: ").strip()
            if not project_name:
                print("Error: project name is required.", file=sys.stderr)
                sys.exit(1)
        if not user_name:
            user_name = input("User name: ").strip()
            # Not required in interactive — can be empty
        # Could prompt for type/level too, but for now we accept defaults

    if not project_name:
        print("Error: --project-name is required.", file=sys.stderr)
        sys.exit(1)

    # ── Determine project dir (cwd) ─────────────────────────────────
    project_dir = Path.cwd()

    # ── Bootstrap ───────────────────────────────────────────────────
    try:
        config = bootstrap(
            project_dir,
            project_name=project_name,
            project_type=project_type,
            project_level=project_level,
            user_name=user_name or "",
            force=force,
            interactive=not non_interactive,
        )
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(3)

    # ── Report ──────────────────────────────────────────────────────
    print(f"✅ BMAD project '{config['project_name']}' initialized at {project_dir}")
    print(f"   Level: {config['project_level']}  |  Type: {config['project_type']}")
    print(f"   planning-artifacts/research/    — created")
    print(f"   implementation-artifacts/stories/  — created")
    print(f"   bmad/config.yaml               — written")
    print(f"   planning-artifacts/workflow-status.yaml  — written")
    sys.exit(0)


# ── Internal helpers ─────────────────────────────────────────────────────────


def _atomic_write_yaml(path: Path, data: dict) -> None:
    """Write *data* as YAML to *path* using POSIX-atomic rename.

    Delegates to ``lib.status._atomic_write`` to keep a single source
    of truth for the atomic-write logic (architecture A-7 / MED-2 fix).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # Lazy import to avoid making the CLI depend on Hermes runtime layout
    # before bootstrap; falls back to a local inline write on import error.
    try:
        from plugins.bmad.lib.status import _atomic_write
        _atomic_write(path, data)
        return
    except ImportError:
        pass
    # Standalone fallback (kept in sync with lib/status._atomic_write):
    tmp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", dir=path.parent, prefix=".", suffix=".tmp", delete=False,
        ) as f:
            tmp_name = f.name
            yaml.safe_dump(data, f, sort_keys=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
        tmp_name = None
    finally:
        if tmp_name is not None and os.path.exists(tmp_name):
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


if __name__ == "__main__":
    cli_main()
