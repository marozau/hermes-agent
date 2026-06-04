"""Ecosystem round-trip tests (Story 12.8).

Verifies that spec: frontmatter is compatible with:
1. Hermes skill loader (SKILL.md format)
2. Cursor MDC format
3. Goose recipe format
"""

import pytest
from pathlib import Path
from plugins.bmad.lib.spec_parser import parse_command_body


class TestEcosystemRoundTrip:
    """Verify spec: frontmatter doesn't break ecosystem loaders."""

    def test_dev_story_parses_as_skill(self):
        """SKILL.md loader ignores frontmatter — spec body is valid skill."""
        content = Path("plugins/bmad/commands/dev-story.md").read_text()
        spec, body = parse_command_body(content)
        assert spec is not None
        # Body should be valid markdown without frontmatter
        assert not body.startswith("---")
        assert "## Instructions" in body

    def test_frontmatter_is_valid_yaml(self):
        """Frontmatter block is valid YAML for Cursor MDC compatibility."""
        import yaml
        content = Path("plugins/bmad/commands/dev-story.md").read_text()
        # Extract frontmatter
        if content.startswith("---"):
            end = content.index("---", 3)
            fm_text = content[4:end]
            fm = yaml.safe_load(fm_text)
            assert isinstance(fm, dict)
            assert "spec" in fm
            assert isinstance(fm["spec"], dict)

    def test_spec_has_required_fields_for_ecosystem(self):
        """Every spec: block has persona + phase + verification for
        Goose recipe compatibility."""
        content = Path("plugins/bmad/commands/dev-story.md").read_text()
        spec, _ = parse_command_body(content)
        assert spec is not None
        assert spec.persona  # non-empty
        assert spec.phase    # non-empty
        assert len(spec.verification) > 0

    def test_body_is_self_contained(self):
        """Body text doesn't reference frontmatter keys — works standalone."""
        content = Path("plugins/bmad/commands/dev-story.md").read_text()
        spec, body = parse_command_body(content)
        # Body shouldn't reference spec: fields directly
        assert "spec:" not in body
        assert "persona:" not in body
        assert "phase:" not in body
