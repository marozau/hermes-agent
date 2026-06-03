"""Command renderer — Jinja2-based structured output (Story 12.2).

Wraps a command body with imperative preamble + verification checklist +
stop condition.  Pure function: render_command(spec, body, args, ctx) -> str.

For commands without a spec: block (legacy), returns body unchanged.

Body IS a Jinja2 template — {{args}}, {{ctx.foo}}, etc. are substituted.
PreservingUndefined handles missing variables gracefully (renders as
{{var_name}}).  Note: {% if %} branches on undefined vars silently
evaluate to False — avoid Jinja control flow in command bodies.

Set template_body=False for user-supplied content (epic-doc anchor
sections) that may contain {{var}} examples in prose.

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

    # G-12: Filters on PreservingUndefined should be no-ops
    def upper(self): return self
    def lower(self): return self
    def capitalize(self): return self
    def title(self): return self
    def strip(self): return self
    def replace(self, *args, **kwargs): return self

    def __getattr__(self, name: str) -> "PreservingUndefined":
        # G-8: Filter dunder names to prevent Jinja2/MarkupSafe probe issues
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
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
    template_body: bool = True,
) -> str:
    """Render a command with its spec block.

    Args:
        spec: Parsed CommandSpec, or None for legacy commands.
        body: The command body text (after frontmatter).
        args: Raw arguments string from the user.
        ctx: Optional command context (for variable injection).
        template_body: If True (default), body is rendered as a Jinja2
            template.  Set False for user-supplied content (e.g. epic-doc
            anchor sections) that may contain {{var}} examples.

    Returns:
        Rendered command string.  For legacy commands (spec=None),
        returns body unchanged.
    """
    if spec is None:
        return body

    env = Environment(undefined=PreservingUndefined)
    # G-5: When ctx is None, use PreservingUndefined so {{ctx.foo}} preserves the prefix
    safe_ctx = ctx if ctx is not None else PreservingUndefined(
        hint=None, obj=None, name="ctx", exc=None
    )
    # R5-1/R5-2: Override Jinja filters to preserve PreservingUndefined literals.
    # Don't wrap `default` — Jinja's do_default handles Undefined natively.
    # For context-aware filters (jinja_pass_arg), check args[0] for Undefined.
    from jinja2 import Undefined

    _SKIP_WRAPPING = {"default"}  # Jinja handles these correctly on Undefined

    def _make_preserving_filter(original, name):
        """Create a filter wrapper that no-ops on Undefined values."""
        has_pass_arg = hasattr(original, 'jinja_pass_arg')

        def wrapper(value, *args, **kwargs):
            # R5-1: For context-aware filters, value is EvalContext — check args[0]
            if has_pass_arg and args:
                if isinstance(args[0], Undefined):
                    return args[0]
            elif isinstance(value, Undefined):
                return value
            return original(value, *args, **kwargs)

        if has_pass_arg:
            wrapper.jinja_pass_arg = original.jinja_pass_arg
        return wrapper

    for name, func in list(env.filters.items()):
        if name not in _SKIP_WRAPPING and callable(func):
            env.filters[name] = _make_preserving_filter(func, name)

    variables = {
        "args": args,
        "ctx": safe_ctx,
    }

    # 1. Preamble (skip if imperative_preamble is False)
    sections = []
    # F-7: Render body as a template so {{args}} etc. are substituted
    # P0-2: Only template the command's own body, not user-supplied content
    # (anchor mode passes epic-doc sections that contain {{var}} examples)
    if template_body:
        rendered_body = env.from_string(body).render(**variables)
    else:
        rendered_body = body
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
