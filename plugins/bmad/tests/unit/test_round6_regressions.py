"""Round-6 regression tests — TDD (test first, fix after).

R5-4: _legacy_body should strip frontmatter only, not render through spec.
R5-1: Context-aware filters (jinja_pass_arg) must check args[0] for Undefined.
R5-2: default filter must apply on Undefined values.
R5-3: nested metadata sorted() must handle mixed-type keys.
R5-5: fix tautological tests.
"""

import pytest
from plugins.bmad.lib.spec_schema import CommandSpec, VerificationItem
from plugins.bmad.lib.render import render_command
from plugins.bmad.lib.spec_parser import parse_command_body, _freeze_value, _build_spec


def _spec(**overrides):
    defaults = dict(
        persona="Dev",
        phase="implementation",
        verification=(VerificationItem(description="Test"),),
    )
    defaults.update(overrides)
    return CommandSpec(**defaults)


# ── R5-4: _legacy_body should strip frontmatter, not render spec ────────

class TestR5_4_LegacyBody:
    def test_legacy_body_no_preamble(self):
        """--no-fanout must NOT include 'EXECUTE NOW' preamble."""
        from plugins.bmad.commands.code_review import _legacy_body
        from pathlib import Path
        body = _legacy_body(Path("."))
        assert "EXECUTE NOW" not in body

    def test_legacy_body_no_stop_condition(self):
        """--no-fanout must NOT include stop condition."""
        from plugins.bmad.commands.code_review import _legacy_body
        from pathlib import Path
        body = _legacy_body(Path("."))
        assert "## Stop Condition" not in body

    def test_legacy_body_has_no_frontmatter(self):
        """--no-fanout must strip YAML frontmatter."""
        from plugins.bmad.commands.code_review import _legacy_body
        from pathlib import Path
        body = _legacy_body(Path("."))
        assert not body.startswith("---")


# ── R5-1: Context-aware filters ─────────────────────────────────────────

class TestR5_1_ContextAwareFilters:
    def test_tojson_on_undefined_preserves(self):
        """{{missing | tojson}} should preserve, not crash."""
        spec = _spec()
        body = "Data: {{missing_var | tojson}}"
        result = render_command(spec, body)
        assert "{{missing_var" in result

    def test_replace_on_real_value_works(self):
        """{{args | replace('a','b')}} on real value must substitute."""
        spec = _spec()
        body = "Out: {{args | replace('hello','world')}}"
        result = render_command(spec, body, args="hello")
        assert "world" in result


# ── R5-2: default filter on Undefined ───────────────────────────────────

class TestR5_2_DefaultFilter:
    def test_default_applies_on_undefined(self):
        """{{missing | default('fallback')}} should render 'fallback'."""
        spec = _spec()
        body = "Topic: {{missing_var | default('fallback')}}"
        result = render_command(spec, body, args="")
        assert "fallback" in result

    def test_default_not_applied_on_defined(self):
        """{{args | default('fallback')}} with args='real' should render 'real'."""
        spec = _spec()
        body = "Topic: {{args | default('fallback')}}"
        result = render_command(spec, body, args="real")
        assert "real" in result


# ── R5-3: nested metadata mixed-type keys ───────────────────────────────

class TestR5_3_NestedMetadataKeys:
    def test_nested_int_keys_dont_crash(self):
        content = (
            "---\nspec:\n  persona: Dev\n  phase: impl\n"
            "  verification:\n    - description: x\n"
            "  metadata:\n    milestones:\n      2026: foo\n      label: bar\n---\nBody\n"
        )
        spec, body = parse_command_body(content)
        assert spec is not None
        assert hash(spec) is not None


# ── R5-5: fix tautological tests ────────────────────────────────────────

class TestR5_5_TautologicalFixes:
    def test_freeze_value_actually_recurses(self):
        """_freeze_value must freeze nested dicts — not just return them."""
        nested = {"a": {"b": [1, 2, 3]}}
        result = _freeze_value(nested)
        inner = dict(result)["a"]
        assert isinstance(inner, tuple), "Inner dict should be frozen to tuple"
        inner_dict = dict(inner)
        assert isinstance(inner_dict["b"], tuple), "Inner list should be frozen to tuple"

    def test_parse_with_nested_metadata_is_hashable(self):
        """parse_command_body with nested metadata must produce hashable spec."""
        content = (
            "---\nspec:\n  persona: Dev\n  phase: impl\n"
            "  verification:\n    - description: x\n"
            "  metadata:\n    a:\n      b: [1, 2]\n---\nBody\n"
        )
        spec, body = parse_command_body(content)
        assert spec is not None
        assert hash(spec) is not None  # must not raise
