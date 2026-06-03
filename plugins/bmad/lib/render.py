"""Command renderer — Jinja2-based structured output (Story 12.2).

Wraps a command body with imperative preamble + verification checklist +
stop condition.  Pure function: render_command(spec, body, args, ctx) -> str.

For commands without a spec: block (legacy), returns body unchanged.

Body IS a Jinja2 template — {{args}}, {{ctx.foo}}, etc. are substituted.
{% ... %} control flow in bodies is safe because PreservingUndefined
handles missing variables gracefully.

≤150 LOC target.  No DSPy, no external dependencies beyond jinja2.
"""

from __future__ import annotations

import logging
from typing import Any

from jinja2 import Environment, StrictUndefined, Undefined

from plugins.bmad.lib.spec_schema import CommandSpec

logger = logging.getLogger(__name__)

# ── PreservingUndefined (per D-2 locked decision) ──────────────────────────


class PreservingUndefined(Undefined):
    """Render missing variables as {{variable_name}} instead of raising.

    This lets command authors reference template variables that may not
    be available at render time (e.g. project-specific context) without
    breaking the renderer.
    """
    def __str__(self) -> str:
        return "{{" + self._undefined_name + "}}"

    def __iter__(self):
        return iter([])

    def __bool__(self) -> bool:
        return False

    def __getattr__(self, name: str) -> "PreservingUndefined":
        return PreservingUndefined(
            hint=self._undefined_hint,
            obj=self._undefined_obj,
            name=self._undefined_name + "." + name,
            exc=self._undefined_exception,
        )

    def __getitem__(self, name: str) -> "PreservingUndefined":
        return PreservingUndefined(
            hint=self._undefined_hint,
            obj=self._undefined_obj,
            name=self._undefined_name + "[" + str(name) + "]",
            exc=self._undefined_exception,
        )


# ── Templates ───────────────────────────────────────────────────────────────

_PREAMBLE_TPL = """\
EXECUTE NOW. You are {{persona}}.

Phase: {{phase}}

{{body}}
"""

_VERIFICATION_TPL = """

## Verification Checklist

{% for item in verification -%}
- [ ] {{item.description}}{% if item.predicate %} `{{item.predicate}}`{% endif %}
{% endfor %}
"""

_STOP_TPL = """

## Stop Condition

{% if output_artifacts -%}
Produce these artifacts before declaring done:
{% for a in output_artifacts -%}
- `{{a}}`
{% endfor %}
{% else -%}
Complete all verification checklist items above.
{% endif -%}
Report results and halt.
"""


# ── Public API ──────────────────────────────────────────────────────────────


def render_command(
    spec: CommandSpec | None,
    body: str,
    args: str = "",
    ctx: Any = None,
) -> str:
    """Render a command with its spec block.

    Args:
        spec: Parsed CommandSpec, or None for legacy commands.
        body: The command body text (after frontmatter).
        args: Raw arguments string from the user.
        ctx: Optional command context (for variable injection).

    Returns:
        Rendered command string.  For legacy commands (spec=None),
        returns body unchanged.
    """
    if spec is None:
        return body

    env = Environment(undefined=PreservingUndefined)
    variables = {
        "args": args,
        "ctx": ctx,
    }

    # 1. Preamble (skip if imperative_preamble is False)
    sections = []
    # F-7: Render body as a template so {{args}} etc. are substituted
    rendered_body = env.from_string(body).render(**variables)
    if spec.imperative_preamble:
        preamble = env.from_string(_PREAMBLE_TPL).render(
            persona=spec.persona,
            phase=spec.phase,
            body=rendered_body,
            **variables,
        )
        sections.append(preamble)
    else:
        sections.append(rendered_body)

    # 2. Verification checklist
    verification = env.from_string(_VERIFICATION_TPL).render(
        verification=spec.verification,
        **variables,
    )
    sections.append(verification)

    # 3. Stop condition
    stop = env.from_string(_STOP_TPL).render(
        output_artifacts=spec.output_artifacts,
        **variables,
    )
    sections.append(stop)

    return "".join(sections)
