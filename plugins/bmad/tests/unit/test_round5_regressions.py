"""Round-5 regression tests — TDD approach (test first, fix after).

Tests for the 4 findings the reviewer said to fix in R5.
"""

from plugins.bmad.lib.spec_schema import CommandSpec, VerificationItem
from plugins.bmad.lib.render import render_command
from plugins.bmad.lib.spec_parser import parse_command_body, _freeze_value


def _spec(**overrides):
    defaults = dict(
        persona="Dev",
        phase="implementation",
        verification=(VerificationItem(description="Test"),),
    )
    defaults.update(overrides)
    return CommandSpec(**defaults)


class TestT1_FilterWrapperMultiArg:
    """T1: Jinja filters with extra args (replace, default, join) must work."""

    def test_filter_default_on_undefined(self):
        """{{args | default('main')}} should NOT crash."""
        spec = _spec()
        body = "Topic: {{args | default('main')}}"
        result = render_command(spec, body, args="")
        assert "default" not in result.lower() or "main" in result

    def test_filter_replace_on_real_value(self):
        """{{args | replace('a','b')}} on a real value must work."""
        spec = _spec()
        body = "Output: {{args | replace('hello','world')}}"
        result = render_command(spec, body, args="hello")
        assert "world" in result

    def test_filter_join_on_real_value(self):
        """{{items | join(', ')}} on a real list must work."""
        spec = _spec()
        body = "Items: {{ctx['items'] | join(', ')}}"
        result = render_command(spec, body, args="", ctx={"items": ["a", "b", "c"]})
        assert "a, b, c" in result

    def test_filter_upper_on_undefined_preserves(self):
        """{{missing | upper}} on undefined should preserve placeholder."""
        spec = _spec()
        body = "Name: {{missing_var | upper}}"
        result = render_command(spec, body)
        assert "{{missing_var" in result


class TestT2_FreezeValueNested:
    """T2: _freeze_value must recurse into nested dicts/lists."""

    def test_nested_dict_is_frozen(self):
        nested = {"a": {"nested": [1, 2, 3]}}
        result = _freeze_value(nested)
        assert isinstance(result, tuple)
        # The inner dict should also be frozen
        inner = dict(result)["a"]
        assert isinstance(inner, tuple)
        # The inner list should be a tuple
        inner_dict = dict(inner)
        assert isinstance(inner_dict["nested"], tuple)

    def test_nested_metadata_is_hashable(self):
        """CommandSpec with nested metadata must be hashable."""
        spec = _spec(metadata=(("tags", ("foo", "bar")), ("nested", (("k", "v"),))))
        assert hash(spec) is not None  # must not raise

    def test_deeply_nested_is_hashable(self):
        metadata = (("level1", (("level2", (("level3", (1, 2, 3)),))),),)
        spec = _spec(metadata=metadata)
        assert hash(spec) is not None


class TestT3_SortedMixedTypeKeys:
    """T3: metadata with mixed-type keys must not crash sorted()."""

    def test_int_keys_dont_crash(self):
        content = (
            "---\nspec:\n  persona: Dev\n  phase: impl\n"
            "  verification:\n    - description: x\n"
            "  metadata:\n    2026: milestone\n    label: v1\n---\nBody\n"
        )
        spec, body = parse_command_body(content)
        assert spec is not None
        assert hash(spec) is not None


class TestT6_CodeReviewLegacyRenders:
    """T6: code_review --no-fanout should render through spec."""

    def test_legacy_body_has_no_frontmatter(self):
        from plugins.bmad.commands.code_review import _legacy_body
        body = _legacy_body()
        assert "---" not in body[:20]  # no raw YAML
        assert "spec:" not in body[:50]
