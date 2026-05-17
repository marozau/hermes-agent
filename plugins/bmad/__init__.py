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
    from plugins.bmad.lib import phases, status, templates
    from plugins.bmad.hooks.on_session_start import on_session_start
    from plugins.bmad.hooks.transform_terminal_output import (
        transform_terminal_output,
    )
    from plugins.bmad.hooks.pre_tool_call import pre_tool_call
    from plugins.bmad.hooks.post_tool_call import post_tool_call
    from plugins.bmad.scripts.bmad_init import cli_main as bmad_init_cli
    from plugins.bmad.scripts.port_completeness import cli_main as bmad_check_port_cli

    from plugins.bmad.hooks.subagent_stop import subagent_stop

    # ── Hooks ──────────────────────────────────────────
    ctx.register_hook("on_session_start", _catch_all("on_session_start")(on_session_start))
    ctx.register_hook("pre_tool_call", _catch_all("pre_tool_call")(pre_tool_call))
    ctx.register_hook("post_tool_call", _catch_all("post_tool_call")(post_tool_call))
    ctx.register_hook(
        "transform_terminal_output",
        _catch_all("transform_terminal_output")(transform_terminal_output),
    )
    ctx.register_hook("subagent_stop", _catch_all("subagent_stop")(subagent_stop))

    # ── CLI commands ───────────────────────────────────
    ctx.register_cli_command(
        name="bmad-init",
        help="Scaffold a new BMAD project in the current directory",
        handler_fn=bmad_init_cli,
        description=(
            "Create bmad/config.yaml, planning-artifacts/, "
            "implementation-artifacts/stories/, and a "
            "workflow-status.yaml with level-appropriate slots."
        ),
    )
    ctx.register_cli_command(
        name="bmad-check-port",
        help="Check BMAD port completeness against v6.6.0 source",
        handler_fn=bmad_check_port_cli,
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

    ctx.register_command(
        name="bmad:init",
        handler=_init_handler,
        args_hint="[--force]",
    )
    ctx.register_command(
        name="bmad:status",
        handler=_status_handler,
        args_hint="",
    )
    ctx.register_command(
        name="bmad:help",
        handler=_help_handler,
        args_hint="",
    )
    ctx.register_command(
        name="bmad:dashboard",
        handler=_dashboard_handler,
        args_hint="",
    )

    # Analysis commands
    ctx.register_command(
        name="bmad:product-brief",
        handler=_product_brief_handler,
        args_hint="",
    )
    ctx.register_command(
        name="bmad:research",
        handler=_research_handler,
        args_hint="",
    )
    ctx.register_command(
        name="bmad:brainstorm",
        handler=_brainstorm_handler,
        args_hint="",
    )
    ctx.register_command(
        name="bmad:document-project",
        handler=_document_project_handler,
        args_hint="",
    )
    ctx.register_command(
        name="bmad:quick-spec",
        handler=_quick_spec_handler,
        args_hint="",
    )

    # Planning commands
    ctx.register_command(
        name="bmad:create-prd",
        handler=_create_prd_handler,
        args_hint="",
    )
    ctx.register_command(
        name="bmad:validate-prd",
        handler=_validate_prd_handler,
        args_hint="",
    )
    ctx.register_command(
        name="bmad:edit-prd",
        handler=_edit_prd_handler,
        args_hint="",
    )
    ctx.register_command(
        name="bmad:create-ux-design",
        handler=_create_ux_design_handler,
        args_hint="",
    )

    # Solutioning commands
    ctx.register_command(
        name="bmad:create-architecture",
        handler=_create_architecture_handler,
        args_hint="",
    )
    ctx.register_command(
        name="bmad:epics-stories",
        handler=_epics_stories_handler,
        args_hint="",
    )
    ctx.register_command(
        name="bmad:solutioning-gate-check",
        handler=_solutioning_gate_check_handler,
        args_hint="",
    )

    # Implementation commands
    ctx.register_command(
        name="bmad:sprint-planning",
        handler=_sprint_planning_handler,
        args_hint="",
    )
    ctx.register_command(
        name="bmad:create-story",
        handler=_create_story_handler,
        args_hint="",
    )
    ctx.register_command(
        name="bmad:dev-story",
        handler=_dev_story_handler,
        args_hint="",
    )
    ctx.register_command(
        name="bmad:code-review",
        handler=_code_review_handler,
        args_hint="",
    )
    ctx.register_command(
        name="bmad:correct-course",
        handler=_correct_course_handler,
        args_hint="",
    )
    ctx.register_command(
        name="bmad:quick-dev",
        handler=_quick_dev_handler,
        args_hint="",
    )

    # TEA commands (ungated)
    ctx.register_command(
        name="bmad:test-framework",
        handler=_test_framework_handler,
        args_hint="",
    )
    ctx.register_command(
        name="bmad:atdd",
        handler=_atdd_handler,
        args_hint="",
    )
    ctx.register_command(
        name="bmad:test-design",
        handler=_test_design_handler,
        args_hint="",
    )
    ctx.register_command(
        name="bmad:test-review",
        handler=_test_review_handler,
        args_hint="",
    )
    ctx.register_command(
        name="bmad:trace",
        handler=_trace_handler,
        args_hint="",
    )
    ctx.register_command(
        name="bmad:nfr",
        handler=_nfr_handler,
        args_hint="",
    )
    ctx.register_command(
        name="bmad:ci",
        handler=_ci_handler,
        args_hint="",
    )
    ctx.register_command(
        name="bmad:automate",
        handler=_automate_handler,
        args_hint="",
    )

    # CIS commands (ungated)
    ctx.register_command(
        name="bmad:brainstorming",
        handler=_brainstorming_handler,
        args_hint="",
    )
    ctx.register_command(
        name="bmad:design-thinking",
        handler=_design_thinking_handler,
        args_hint="",
    )
    ctx.register_command(
        name="bmad:problem-solving",
        handler=_problem_solving_handler,
        args_hint="",
    )
    ctx.register_command(
        name="bmad:innovation-strategy",
        handler=_innovation_strategy_handler,
        args_hint="",
    )
    ctx.register_command(
        name="bmad:storytelling",
        handler=_storytelling_handler,
        args_hint="",
    )
    ctx.register_command(
        name="bmad:presentation",
        handler=_presentation_handler,
        args_hint="",
    )

    # BMB commands (ungated)
    ctx.register_command(
        name="bmad:agent-builder",
        handler=_agent_builder_handler,
        args_hint="",
    )
    ctx.register_command(
        name="bmad:module-builder",
        handler=_module_builder_handler,
        args_hint="",
    )
    ctx.register_command(
        name="bmad:workflow-builder",
        handler=_workflow_builder_handler,
        args_hint="",
    )

    logger.info("[bmad] plugin registered: hooks=5, cli=2, slash=39")