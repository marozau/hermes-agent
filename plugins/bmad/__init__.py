"""BMAD plugin — Hermes integration for BMAD Method v6.6.0 workflows.

Registers hooks, slash commands, and CLI commands for the BMAD
structured product-development methodology.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _catch_all(handler_name: str) -> callable:
    """Decorator factory: wrap hook callbacks so they NEVER raise.

    Per architecture §4 enforcement: a broken hook must not
    break the user's session.  Every hook callback is wrapped.
    """
    def decorator(fn):
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception:
                logger.exception("[bmad] %s raised — caught, allowing", handler_name)
                return None
        wrapper.__name__ = fn.__name__
        wrapper.__qualname__ = fn.__qualname__
        return wrapper
    return decorator


def register(ctx) -> None:
    """Register BMAD plugin: hooks, slash commands, CLI commands.

    Called once by the Hermes plugin loader.  If the user's project
    has a ``bmad/config.yaml`` the plugin activates; outside a BMAD
    project all hooks are silent no-ops.
    """
    # Hermes's slash-command signature is ``fn(raw_args: str) -> str | None``
    # — one argument. Our BMAD handlers all use ``def handler(ctx, args)``
    # because the BMAD upstream pattern (Claude Code's Task tool) passes ctx
    # explicitly. Bridge the gap by capturing ctx in a closure at registration
    # time so the wrapped function matches Hermes's expected signature.
    def _bind_ctx(handler_fn):
        def wrapped(raw_args: str = "") -> str:
            try:
                return handler_fn(ctx, raw_args)
            except Exception as exc:
                logger.exception(
                    "[bmad] slash handler %s raised", handler_fn.__module__,
                )
                return f"⚠️  Plugin command error: {exc.__class__.__name__}: {exc}"
        wrapped.__name__ = getattr(handler_fn, "__name__", "wrapped")
        wrapped.__qualname__ = getattr(handler_fn, "__qualname__", "wrapped")
        return wrapped

    # Hook bus signature (hermes_cli/plugins.py:invoke_hook) does NOT pass
    # ctx — only kwargs like tool_name, args, session_id, etc. Bind ctx via
    # closure for hooks that still need it (same pattern as _bind_ctx).
    # Without this, every call to pre_tool_call / post_tool_call /
    # transform_terminal_output / on_session_start / subagent_stop raises
    # TypeError("missing 1 required positional argument: 'ctx'") and the
    # _catch_all wrapper swallows it but floods errors.log.
    def _bind_hook_ctx(handler_fn):
        def wrapped(*args, **kwargs):
            return handler_fn(ctx, *args, **kwargs)
        wrapped.__name__ = getattr(handler_fn, "__name__", "wrapped")
        wrapped.__qualname__ = getattr(handler_fn, "__qualname__", "wrapped")
        return wrapped

    from plugins.bmad.lib import phases, status, templates
    from plugins.bmad.hooks.on_session_start import on_session_start
    from plugins.bmad.hooks.on_session_end import on_session_end
    from plugins.bmad.hooks.transform_terminal_output import (
        transform_terminal_output,
    )
    from plugins.bmad.hooks.pre_tool_call import pre_tool_call
    from plugins.bmad.hooks.post_tool_call import post_tool_call
    from plugins.bmad.hooks.pre_llm_call import pre_llm_call
    from plugins.bmad.hooks.post_llm_call import post_llm_call
    from plugins.bmad.hooks.subagent_stop import subagent_stop

    # ── Hooks ──────────────────────────────────────────
    # 8 hooks declare ctx as their first positional arg → all wrap with _bind_hook_ctx.
    ctx.register_hook("on_session_start",
        _catch_all("on_session_start")(_bind_hook_ctx(on_session_start)))
    ctx.register_hook("on_session_end",
        _catch_all("on_session_end")(_bind_hook_ctx(on_session_end)))
    ctx.register_hook("pre_tool_call",
        _catch_all("pre_tool_call")(_bind_hook_ctx(pre_tool_call)))
    ctx.register_hook("post_tool_call",
        _catch_all("post_tool_call")(_bind_hook_ctx(post_tool_call)))
    ctx.register_hook("pre_llm_call",
        _catch_all("pre_llm_call")(_bind_hook_ctx(pre_llm_call)))
    ctx.register_hook("post_llm_call",
        _catch_all("post_llm_call")(_bind_hook_ctx(post_llm_call)))
    ctx.register_hook("transform_terminal_output",
        _catch_all("transform_terminal_output")(_bind_hook_ctx(transform_terminal_output)))
    ctx.register_hook("subagent_stop",
        _catch_all("subagent_stop")(_bind_hook_ctx(subagent_stop)))

    # ── CLI commands ───────────────────────────────────

    def _register_bmad_init_cli(subparser):
        """Add bmad-init arguments to the subparser."""
        subparser.add_argument(
            "--project-name", type=str, default=None,
            help="Human-readable project name (required).",
        )
        subparser.add_argument(
            "--project-type", type=str, default="other",
            choices=["web-app", "mobile-app", "api", "library", "game", "other"],
            help="Type of project (default: other).",
        )
        subparser.add_argument(
            "--project-level", type=int, default=1, choices=[0, 1, 2, 3, 4],
            help="BMAD rigor level 0–4 (default: 1).",
        )
        subparser.add_argument(
            "--user-name", type=str, default="",
            help="Name of the project owner/user.",
        )
        subparser.add_argument(
            "--force", action="store_true", default=False,
            help="Overwrite existing bmad/config.yaml without prompting.",
        )
        subparser.add_argument(
            "--non-interactive", action="store_true", default=False,
            help="Fail if config exists or required fields are missing.",
        )
        subparser.add_argument(
            "--workspace", action="store_true", default=False,
            help="Enable workspace mode (planning at root, code in worktrees).",
        )
        subparser.add_argument(
            "--worktree", action="append", default=[],
            metavar="NAME:UPSTREAM:BRANCH",
            help="Add a git worktree (repeatable). Requires --workspace.",
        )

    def _run_bmad_init(args):
        """Thin wrapper: pass Namespace values to bootstrap() or bootstrap_workspace().

        M-2 fix (code-review 2026-05-21): the non-interactive guard now
        fires BEFORE any input() call. Previously, the project-name input()
        prompt ran unconditionally, hanging CI/scripted invocations.
        """
        import sys
        from pathlib import Path
        from plugins.bmad.scripts.bmad_init import bootstrap, bootstrap_workspace

        non_interactive = bool(getattr(args, "non_interactive", False))
        workspace_mode = bool(getattr(args, "workspace", False))
        worktree_specs = list(getattr(args, "worktree", []) or [])

        # Resolve project_name with the non-interactive guard fired BEFORE
        # any blocking input() call.
        project_name = args.project_name
        if not project_name:
            if non_interactive or workspace_mode:
                project_name = Path.cwd().name
            else:
                try:
                    project_name = input("Project name: ").strip()
                except (EOFError, KeyboardInterrupt):
                    print(
                        "Error: project name required (stdin closed or interrupted)",
                        file=sys.stderr,
                    )
                    sys.exit(3)
                if not project_name:
                    print("Error: project name required", file=sys.stderr)
                    sys.exit(3)

        try:
            if workspace_mode:
                if not worktree_specs:
                    print(
                        "Error: --workspace requires at least one --worktree NAME:UPSTREAM:BRANCH",
                        file=sys.stderr,
                    )
                    sys.exit(3)
                worktrees = []
                for spec in worktree_specs:
                    parts = spec.split(":")
                    if len(parts) != 3:
                        print(
                            f"Error: invalid --worktree format: {spec}\n"
                            "Expected: NAME:UPSTREAM:BRANCH",
                            file=sys.stderr,
                        )
                        sys.exit(3)
                    worktrees.append({
                        "name": parts[0],
                        "upstream": parts[1],
                        "branch": parts[2],
                    })
                config = bootstrap_workspace(
                    Path.cwd(),
                    project_name=project_name,
                    worktrees=worktrees,
                    project_type=args.project_type,
                    project_level=args.project_level,
                    user_name=args.user_name or "",
                )
                print(f"✅ BMAD workspace '{config['project_name']}' initialized at {Path.cwd()}")
                print(f"   Mode: workspace  |  Worktrees: {len(worktrees)}")
            else:
                config = bootstrap(
                    Path.cwd(),
                    project_name=project_name,
                    project_type=args.project_type,
                    project_level=args.project_level,
                    user_name=args.user_name or "",
                    force=args.force,
                    interactive=not non_interactive,
                )
                print(f"✅ BMAD project '{config['project_name']}' initialized at {Path.cwd()}")
                print(f"   Level: {config['project_level']}  |  Type: {config['project_type']}")
        except RuntimeError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(3)

    def _register_bmad_check_port_cli(subparser):
        """Add bmad-check-port arguments to the subparser."""
        subparser.add_argument(
            "--scope", type=str, default="all",
            help="Scope: analysis, planning, solutioning, implementation, or all (default).",
        )
        subparser.add_argument(
            "--bmad-source", type=str, default=None,
            help="Path to BMAD v6.6.0 source directory.",
        )

    def _run_bmad_check_port(args):
        """Thin wrapper: pass Namespace values to port_completeness."""
        from plugins.bmad.scripts.port_completeness import check_port
        from pathlib import Path
        sys_mod = __import__('sys')
        missing = check_port(
            scope=args.scope or "all",
            bmad_source=Path(args.bmad_source) if args.bmad_source else None,
        )
        if missing:
            print(f"Missing files: {len(missing)}")
            for m in missing:
                print(f"  {m}")
            sys_mod.exit(1)
        else:
            print("✅ All files present within scope.")
            sys_mod.exit(0)

    ctx.register_cli_command(
        name="bmad-init",
        help="Scaffold a new BMAD project in the current directory",
        setup_fn=_register_bmad_init_cli,
        handler_fn=_run_bmad_init,
        description=(
            "Create bmad/config.yaml, planning-artifacts/, "
            "implementation-artifacts/stories/, and a "
            "workflow-status.yaml with level-appropriate slots."
        ),
    )
    ctx.register_cli_command(
        name="bmad-check-port",
        help="Check BMAD port completeness against v6.6.0 source",
        setup_fn=_register_bmad_check_port_cli,
        handler_fn=_run_bmad_check_port,
        description=(
            "Verify that every BMAD v6.6.0 workflow file has a matching "
            "port under ~/.hermes/skills/bmad/."
        ),
    )

    # ── Slash commands ─────────────────────────────────
    from plugins.bmad.commands.init import handler as _init_handler
    from plugins.bmad.commands.status import handler as _status_handler
    from plugins.bmad.commands.help import handler as _help_handler
    from plugins.bmad.commands.dashboard import handler as _dashboard_handler

    # Analysis phase
    from plugins.bmad.commands.product_brief import handler as _product_brief_handler
    from plugins.bmad.commands.research import handler as _research_handler
    from plugins.bmad.commands.brainstorm import handler as _brainstorm_handler
    from plugins.bmad.commands.document_project import handler as _document_project_handler
    from plugins.bmad.commands.quick_spec import handler as _quick_spec_handler

    # Planning phase
    from plugins.bmad.commands.create_prd import handler as _create_prd_handler
    from plugins.bmad.commands.validate_prd import handler as _validate_prd_handler
    from plugins.bmad.commands.edit_prd import handler as _edit_prd_handler
    from plugins.bmad.commands.create_ux_design import handler as _create_ux_design_handler

    # Solutioning phase
    from plugins.bmad.commands.create_architecture import handler as _create_architecture_handler
    from plugins.bmad.commands.epics_stories import handler as _epics_stories_handler
    from plugins.bmad.commands.solutioning_gate_check import handler as _solutioning_gate_check_handler

    # Implementation phase
    from plugins.bmad.commands.sprint_planning import handler as _sprint_planning_handler
    from plugins.bmad.commands.create_story import handler as _create_story_handler
    from plugins.bmad.commands.dev_story import handler as _dev_story_handler
    from plugins.bmad.commands.code_review import handler as _code_review_handler
    from plugins.bmad.commands.correct_course import handler as _correct_course_handler
    from plugins.bmad.commands.quick_dev import handler as _quick_dev_handler

    # TEA phase (ungated)
    from plugins.bmad.commands.test_framework import handler as _test_framework_handler
    from plugins.bmad.commands.atdd import handler as _atdd_handler
    from plugins.bmad.commands.test_design import handler as _test_design_handler
    from plugins.bmad.commands.test_review import handler as _test_review_handler
    from plugins.bmad.commands.trace import handler as _trace_handler
    from plugins.bmad.commands.nfr import handler as _nfr_handler
    from plugins.bmad.commands.ci import handler as _ci_handler
    from plugins.bmad.commands.automate import handler as _automate_handler

    # CIS phase (ungated)
    from plugins.bmad.commands.brainstorming import handler as _brainstorming_handler
    from plugins.bmad.commands.design_thinking import handler as _design_thinking_handler
    from plugins.bmad.commands.problem_solving import handler as _problem_solving_handler
    from plugins.bmad.commands.innovation_strategy import handler as _innovation_strategy_handler
    from plugins.bmad.commands.storytelling import handler as _storytelling_handler
    from plugins.bmad.commands.presentation import handler as _presentation_handler

    # BMB phase (ungated)
    from plugins.bmad.commands.agent_builder import handler as _agent_builder_handler
    from plugins.bmad.commands.module_builder import handler as _module_builder_handler
    from plugins.bmad.commands.workflow_builder import handler as _workflow_builder_handler

    # Meta — multi-persona round table (ungated)
    from plugins.bmad.commands.party_mode import handler as _party_mode_handler

    # Epic 9 — Doctor + Migrate
    from plugins.bmad.commands.doctor import handler as _doctor_handler
    from plugins.bmad.commands.migrate import handler as _migrate_handler

    # bmad:init is now a skill (~/.hermes/skills/bmad/init/SKILL.md)
    # so the LLM continues planning after bootstrap.
    ctx.register_command(
        name="bmad:status",
        handler=_bind_ctx(_status_handler),
        args_hint="",
    )
    ctx.register_command(
        name="bmad:help",
        handler=_bind_ctx(_help_handler),
        args_hint="",
    )
    ctx.register_command(
        name="bmad:dashboard",
        handler=_bind_ctx(_dashboard_handler),
        args_hint="",
    )

    # Analysis commands
    # bmad:research is now a skill (~/.hermes/skills/bmad/research/SKILL.md)
    # so the LLM continues planning after research setup.

    # Planning commands

    # Solutioning commands

    # Implementation commands
    ctx.register_command(
        name="bmad:dev-story",
        handler=_bind_ctx(_dev_story_handler),
        args_hint="",
    )

    # TEA commands (ungated)

    # CIS commands (ungated)