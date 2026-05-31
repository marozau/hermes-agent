"""BMAD project bootstrap — scaffold a new project with config.yaml and workflow-status.yaml.

Architecture A-12 (bootstrap script), D-3 (config generation).

Supports two modes:
1. Standard single-repo: ``bmad-init --project-name ...``
2. Workspace mode: ``bmad-init --workspace --worktree NAME:UPSTREAM:BRANCH ...``

Exports:
    bootstrap(project_dir, ...) -> dict
    bootstrap_workspace(project_dir, *, worktrees, ...) -> dict
    cli_main()  — CLI entry point with argparse
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

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
    """Scaffold a new BMAD project under *project_dir* (non-workspace mode).

    Creates::

        bmad/config.yaml
        planning-artifacts/workflow-status.yaml
        planning-artifacts/research/
        implementation-artifacts/stories/
    """
    project_dir = Path(project_dir)

    # ── Guard: existing config ──────────────────────────────────────
    config_path = project_dir / "bmad" / "config.yaml"
    if config_path.exists():
        if force:
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
    is_prd_required = level >= 2
    is_architecture_required = level >= 2
    is_gate_check_required = level >= 2
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


def bootstrap_workspace(
    project_dir: str | Path,
    *,
    project_name: str,
    worktrees: list[dict[str, str]],
    project_type: str = "other",
    project_level: int = 1,
    user_name: str = "",
    envrc: bool = False,
) -> dict:
    """Scaffold a new BMAD workspace under *project_dir*.

    Creates::

        bmad/config.yaml          (workspace_mode: true + worktrees)
        planning-artifacts/       (empty)
        worktree/<name>/          (git worktree add for each)
        AGENTS.md                 (rendered from template)
        CLAUDE.md                 (symlink to AGENTS.md on Unix)
        WORKTREES.md              (rendered from template)

    Parameters
    ----------
    project_dir:
        Root directory for the new workspace.
    project_name:
        Human-readable workspace name.
    worktrees:
        List of dicts with keys: name, upstream, branch.
    envrc:
        If True, write .envrc from template.

    Returns
    -------
    dict
        The final config dict as written to ``bmad/config.yaml``.

    Raises
    ------
    RuntimeError
        If the workspace is already initialized.
    ValueError
        If a git worktree add fails.
    """
    project_dir = Path(project_dir).resolve()

    # ── Guard: already initialized ──────────────────────────────────
    config_path = project_dir / "bmad" / "config.yaml"
    if config_path.exists():
        raw = yaml.safe_load(config_path.read_text()) or {}
        if raw.get("workspace_mode"):
            raise RuntimeError(
                f"workspace already initialized at {project_dir}"
            )

    # ── Validate worktrees ──────────────────────────────────────────
    seen_names: set[str] = set()
    for wt in worktrees:
        if not wt.get("name") or not wt.get("upstream") or not wt.get("branch"):
            raise ValueError(
                f"Each worktree must have name, upstream, and branch: {wt}"
            )
        # R3-m6: Case-insensitive duplicate detection
        name_lower = wt["name"].lower()
        if name_lower in seen_names:
            raise ValueError(
                f"Duplicate worktree name (case-insensitive): {wt['name']!r}"
            )
        seen_names.add(name_lower)
        upstream = Path(wt["upstream"]).expanduser()
        if not upstream.exists():
            raise ValueError(f"Upstream path does not exist: {upstream}")

    # ── Create base structure ───────────────────────────────────────
    today = _today_iso()
    bmad_dir = project_dir / "bmad"
    planning_dir = project_dir / "planning-artifacts"
    research_dir = planning_dir / "research"
    stories_dir = project_dir / "implementation-artifacts" / "stories"
    worktree_base = project_dir / "worktree"

    bmad_dir.mkdir(parents=True, exist_ok=True)
    planning_dir.mkdir(parents=True, exist_ok=True)
    research_dir.mkdir(parents=True, exist_ok=True)
    stories_dir.mkdir(parents=True, exist_ok=True)
    worktree_base.mkdir(parents=True, exist_ok=True)

    # ── Build config with workspace_mode ────────────────────────────
    worktree_specs = []
    for wt in worktrees:
        spec: dict[str, Any] = {
            "name": wt["name"],
            "upstream": str(Path(wt["upstream"]).expanduser()),
            "branch": wt["branch"],
            "path": f"worktree/{wt['name']}",
        }
        if wt.get("runtime_mirror"):
            spec["runtime_mirror"] = wt["runtime_mirror"]
        worktree_specs.append(spec)

    config: dict[str, Any] = {
        "project_name": project_name,
        "project_type": project_type,
        "project_level": project_level,
        "user_name": user_name,
        "planning_artifacts": "planning-artifacts",
        "implementation_artifacts": "implementation-artifacts",
        "created": today,
        "workspace_mode": True,
        "worktrees": worktree_specs,
    }

    # ── Atomic writes ───────────────────────────────────────────────
    _atomic_write_yaml(config_path, config)

    # ── Git worktree add ────────────────────────────────────────────
    created_worktrees: list[str] = []
    try:
        for wt in worktrees:
            name = wt["name"]
            upstream = str(Path(wt["upstream"]).expanduser())
            branch = wt["branch"]
            wt_path = project_dir / "worktree" / name

            if wt_path.exists():
                raise RuntimeError(
                    f"worktree directory already exists: {wt_path}"
                )

            cmd = [
                "git", "-C", upstream,
                "worktree", "add",
                str(wt_path), branch,
            ]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                # Clean up partial scaffold
                _cleanup_partial(project_dir, created_worktrees)
                raise ValueError(
                    f"git worktree add failed for {name}: {result.stderr.strip()}"
                )
            created_worktrees.append(name)
    except Exception:
        # Clean up on any failure
        _cleanup_partial(project_dir, created_worktrees)
        raise

    # ── Render AGENTS.md from template ──────────────────────────────
    agents_content = _render_agents_template(project_name, config, project_dir)
    agents_path = project_dir / "AGENTS.md"
    agents_path.write_text(agents_content)

    # ── CLAUDE.md symlink (Unix) or copy (Windows) ──────────────────
    claude_path = project_dir / "CLAUDE.md"
    if os.name == "nt":
        claude_path.write_text(agents_content)
    else:
        if claude_path.exists() or claude_path.is_symlink():
            claude_path.unlink()
        claude_path.symlink_to("AGENTS.md")

    # ── Render WORKTREES.md ─────────────────────────────────────────
    worktrees_content = _render_worktrees_template(project_name, worktrees)
    (project_dir / "WORKTREES.md").write_text(worktrees_content)

    # ── Optional .envrc ─────────────────────────────────────────────
    if envrc:
        envrc_content = (
            f"# .envrc — auto-loaded by direnv\n"
            f"# Run: direnv allow\n\n"
            f'export BMAD_WORKSPACE_ROOT="$(pwd)"\n'
            f'export HERMES_PROFILE="${{HERMES_PROFILE:-engineer}}"\n'
        )
        (project_dir / ".envrc").write_text(envrc_content)
        print("  ℹ️  Run `direnv allow` to activate the .envrc", file=sys.stderr)

    return config


# ── Template rendering ──────────────────────────────────────────────────────


def _render_agents_template(
    project_name: str,
    config: dict,
    project_dir: Path,
) -> str:
    """Render AGENTS.md from the Jinja2 template."""
    template_dir = Path(__file__).parent.parent / "templates"
    template_path = template_dir / "AGENTS.md.j2"

    if not template_path.exists():
        # Fallback: generate a minimal AGENTS.md
        return _generate_minimal_agents(project_name, config, project_dir)

    from jinja2 import BaseLoader, Environment, StrictUndefined

    env = Environment(
        loader=BaseLoader(),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        autoescape=False,
    )

    template_text = template_path.read_text()

    # Check for Jinja control flow (allowed in workspace templates)
    has_control_flow = "{%" in template_text

    worktrees_data = config.get("worktrees", [])
    has_runtime_mirror = any(
        wt.get("runtime_mirror") for wt in worktrees_data
    )

    template_vars = {
        "project_name": project_name,
        "project_root": str(project_dir),
        "date": _today_iso(),
        "worktrees": worktrees_data,
        "has_runtime_mirror": has_runtime_mirror,
        "mission": "<!-- FILL IN: Describe the mission of this workspace. -->",
        "feature_description": "<!-- FILL IN: Describe the feature you are extending. -->",
        "hard_invariants": "<!-- FILL IN: List numbered hard invariants. -->",
        "canonical_helpers": "<!-- FILL IN: List canonical helpers. -->",
        "provider_routing": "<!-- FILL IN (optional): Provider/profile routing. -->",
        "anti_patterns": "<!-- FILL IN: List anti-patterns. -->",
    }

    try:
        template = env.from_string(template_text)
        return template.render(**template_vars)
    except Exception as e:
        # Fallback on render failure
        print(f"Warning: template render failed ({e}), using minimal AGENTS.md", file=sys.stderr)
        return _generate_minimal_agents(project_name, config, project_dir)


def _generate_minimal_agents(
    project_name: str,
    config: dict,
    project_dir: Path,
) -> str:
    """Generate a minimal AGENTS.md when template is unavailable."""
    worktrees = config.get("worktrees", [])
    lines = [
        f"# AGENTS.md — {project_name}",
        "",
        "> ## ⮕ Where to do the work",
        ">",
        f"> **Development happens in `./worktree/{worktrees[0]['name']}/`**",
        f"> on branch `{worktrees[0]['branch']}`.",
        "",
        f"**Date:** {_today_iso()}",
        f"**Workspace root:** {project_dir}",
        "",
        "---",
        "",
        "## Layout",
        "",
        "```",
        f"{project_name}/",
        "├── AGENTS.md",
        "├── CLAUDE.md → AGENTS.md",
        "├── WORKTREES.md",
        "├── bmad/config.yaml",
        "├── planning-artifacts/",
        "└── worktree/",
    ]
    for wt in worktrees:
        lines.append(f"    ├── {wt['name']}/")
    lines.extend([
        "```",
        "",
        "## Hard invariants",
        "",
        "<!-- MUST be numbered -->",
        "",
        "## If you get stuck",
        "",
        "1. Re-read planning artifacts.",
        "2. Do not invent.",
        "",
    ])
    return "\n".join(lines) + "\n"


def _render_worktrees_template(
    project_name: str,
    worktrees: list[dict],
) -> str:
    """Render WORKTREES.md from the Jinja2 template."""
    template_dir = Path(__file__).parent.parent / "templates"
    template_path = template_dir / "WORKTREES.md.j2"

    if not template_path.exists():
        return _generate_minimal_worktrees(project_name, worktrees)

    from jinja2 import BaseLoader, Environment, StrictUndefined

    env = Environment(
        loader=BaseLoader(),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        autoescape=False,
    )

    template_text = template_path.read_text()
    template = env.from_string(template_text)

    try:
        return template.render(
            project_name=project_name,
            worktrees=worktrees,
        )
    except Exception:
        return _generate_minimal_worktrees(project_name, worktrees)


def _generate_minimal_worktrees(
    project_name: str,
    worktrees: list[dict],
) -> str:
    """Generate a minimal WORKTREES.md when template is unavailable."""
    lines = [
        f"# WORKTREES.md — {project_name}",
        "",
        "| Worktree | Branch | Agent | Task | Status | Last commit |",
        "|---|---|---|---|---|---|",
    ]
    for wt in worktrees:
        lines.append(
            f"| `worktree/{wt['name']}` | `{wt['branch']}` | - | - | idle | - |"
        )
    return "\n".join(lines) + "\n"


# ── CLI Entry Point ─────────────────────────────────────────────────────────


def cli_main(argv: list[str] | None = None) -> None:
    """CLI entry point for ``bmad-init``.

    Exit codes:
        0 — success
        1 — user-correctable error
        2 — invalid arguments
        3 — existing config without --force
    """
    parser = argparse.ArgumentParser(
        prog="bmad-init",
        description="Scaffold a new BMAD project or workspace.",
    )
    parser.add_argument(
        "--project-name", type=str, default=None,
        help="Human-readable project name (required).",
    )
    parser.add_argument(
        "--project-type", type=str, default="other",
        choices=["web-app", "mobile-app", "api", "library", "game", "other"],
    )
    parser.add_argument(
        "--project-level", type=int, default=1,
        choices=[0, 1, 2, 3, 4],
    )
    parser.add_argument("--user-name", type=str, default="")
    parser.add_argument("--force", action="store_true", default=False)
    parser.add_argument("--non-interactive", action="store_true", default=False)

    # Workspace mode arguments
    parser.add_argument(
        "--workspace", action="store_true", default=False,
        help="Initialize a workspace with git worktrees instead of a single project.",
    )
    parser.add_argument(
        "--worktree", action="append", default=[],
        metavar="NAME:UPSTREAM:BRANCH",
        help="Add a worktree (repeatable). Format: name:upstream_path:branch",
    )
    parser.add_argument(
        "--envrc", action="store_true", default=False,
        help="Write a .envrc file for direnv (workspace mode only).",
    )

    parsed = parser.parse_args(argv)

    # ── Workspace mode ──────────────────────────────────────────────
    if parsed.workspace:
        if not parsed.worktree:
            print("Error: --workspace requires at least one --worktree.", file=sys.stderr)
            sys.exit(2)

        worktrees = []
        for spec in parsed.worktree:
            parts = spec.split(":")
            if len(parts) < 3:
                print(
                    f"Error: --worktree format is NAME:UPSTREAM:BRANCH, got: {spec}",
                    file=sys.stderr,
                )
                sys.exit(2)
            worktrees.append({
                "name": parts[0],
                "upstream": parts[1],
                "branch": ":".join(parts[2:]),  # branch may contain ':'
            })

        project_name = parsed.project_name
        if not project_name:
            project_name = Path.cwd().name

        project_dir = Path.cwd()

        try:
            config = bootstrap_workspace(
                project_dir,
                project_name=project_name,
                worktrees=worktrees,
                project_type=parsed.project_type,
                project_level=parsed.project_level,
                user_name=parsed.user_name or "",
                envrc=parsed.envrc,
            )
        except RuntimeError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(3)
        except (ValueError, subprocess.SubprocessError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)

        print(f"✅ BMAD workspace '{config['project_name']}' initialized at {project_dir}")
        print(f"   workspace_mode: true")
        print(f"   worktrees:")
        for wt in config["worktrees"]:
            mirror = f" (mirror: {wt['runtime_mirror']})" if wt.get("runtime_mirror") else ""
            print(f"     - {wt['name']} → {wt['upstream']} [{wt['branch']}]{mirror}")
        print(f"   planning-artifacts/  — created")
        print(f"   AGENTS.md            — rendered")
        print(f"   CLAUDE.md            — symlink" if os.name != "nt" else "   CLAUDE.md            — copy")
        print(f"   WORKTREES.md         — rendered")
        sys.exit(0)

    # ── Standard (non-workspace) mode ───────────────────────────────
    if parsed.non_interactive and not parsed.project_name:
        print("Error: --project-name is required in non-interactive mode.", file=sys.stderr)
        sys.exit(2)

    if parsed.non_interactive and not parsed.user_name:
        print("Error: --user-name is required in non-interactive mode.", file=sys.stderr)
        sys.exit(2)

    project_name: str | None = parsed.project_name
    user_name: str | None = parsed.user_name

    if not parsed.non_interactive:
        if not project_name:
            project_name = input("Project name: ").strip()
            if not project_name:
                print("Error: project name is required.", file=sys.stderr)
                sys.exit(1)
        if not user_name:
            user_name = input("User name: ").strip()

    if not project_name:
        print("Error: --project-name is required.", file=sys.stderr)
        sys.exit(1)

    project_dir = Path.cwd()

    try:
        config = bootstrap(
            project_dir,
            project_name=project_name,
            project_type=parsed.project_type,
            project_level=parsed.project_level,
            user_name=user_name or "",
            force=parsed.force,
            interactive=not parsed.non_interactive,
        )
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(3)

    print(f"✅ BMAD project '{config['project_name']}' initialized at {project_dir}")
    print(f"   Level: {config['project_level']}  |  Type: {config['project_type']}")
    print(f"   planning-artifacts/research/    — created")
    print(f"   implementation-artifacts/stories/  — created")
    print(f"   bmad/config.yaml               — written")
    print(f"   planning-artifacts/workflow-status.yaml  — written")
    sys.exit(0)


# ── Internal helpers ─────────────────────────────────────────────────────────


def _cleanup_partial(project_dir: Path, created_worktrees: list[str]) -> None:
    """Clean up partially scaffolded workspace on failure."""
    import shutil

    for name in created_worktrees:
        wt_path = project_dir / "worktree" / name
        if wt_path.exists():
            try:
                # Try git worktree remove first
                subprocess.run(
                    ["git", "worktree", "remove", "--force", str(wt_path)],
                    capture_output=True, timeout=10,
                )
            except Exception:
                shutil.rmtree(wt_path, ignore_errors=True)

    # Remove scaffolded files
    for name in ["AGENTS.md", "CLAUDE.md", "WORKTREES.md"]:
        p = project_dir / name
        if p.exists() or p.is_symlink():
            p.unlink(missing_ok=True)


def _atomic_write_yaml(path: Path, data: dict) -> None:
    """Write *data* as YAML to *path* using POSIX-atomic rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from plugins.bmad.lib.status import _atomic_write
        _atomic_write(path, data)
        return
    except ImportError:
        pass
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
