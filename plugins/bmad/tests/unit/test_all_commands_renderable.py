"""Test that all 40 commands render with their spec blocks (G3 gate).

Every command .md file must:
1. Have a spec: frontmatter block
2. Parse to a valid CommandSpec
3. Render without errors
4. Informational commands have imperative_preamble: false
"""

import pytest
from pathlib import Path
from plugins.bmad.lib.spec_parser import parse_command_body
from plugins.bmad.lib.render import render_command

COMMANDS_DIR = Path(__file__).parent.parent.parent / "commands"

# Commands that build output programmatically (no .md body)
PROGRAMMATIC = {"dashboard", "status", "help", "init", "party-mode"}

# Commands with imperative_preamble: false
INFORMATIONAL = {"help", "status", "dashboard", "party-mode"}


def _all_command_mds():
    """Yield (name, path) for all command .md files."""
    for md in sorted(COMMANDS_DIR.glob("*.md")):
        yield md.stem, md


class TestAllCommandsRenderable:
    """G3 gate: 40/40 commands render uniformly."""

    @pytest.mark.parametrize("name,path", list(_all_command_mds()))
    def test_has_spec_frontmatter(self, name, path):
        """Every command .md has a spec: frontmatter block."""
        content = path.read_text()
        spec, body = parse_command_body(content)
        assert spec is not None, f"{name}.md missing spec: frontmatter"

    @pytest.mark.parametrize("name,path", list(_all_command_mds()))
    def test_spec_has_required_fields(self, name, path):
        """Spec has persona, phase, and non-empty verification."""
        content = path.read_text()
        spec, _ = parse_command_body(content)
        assert spec is not None
        assert spec.persona, f"{name}.md spec.persona is empty"
        assert spec.phase, f"{name}.md spec.phase is empty"
        assert len(spec.verification) > 0, f"{name}.md has no verification items"

    @pytest.mark.parametrize("name,path", list(_all_command_mds()))
    def test_renders_without_error(self, name, path):
        """render_command() succeeds for every command."""
        content = path.read_text()
        spec, body = parse_command_body(content)
        result = render_command(spec, body, args="test-args")
        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.parametrize("name,path", list(_all_command_mds()))
    def test_informational_no_preamble(self, name, path):
        """Informational commands have imperative_preamble: false."""
        if name not in INFORMATIONAL:
            pytest.skip("not informational")
        content = path.read_text()
        spec, _ = parse_command_body(content)
        assert spec is not None
        assert spec.imperative_preamble is False, \
            f"{name} is informational but imperative_preamble is True"

    @pytest.mark.parametrize("name,path", list(_all_command_mds()))
    def test_non_informational_has_preamble(self, name, path):
        """Non-informational commands have imperative_preamble: true."""
        if name in INFORMATIONAL:
            pytest.skip("informational")
        content = path.read_text()
        spec, _ = parse_command_body(content)
        assert spec is not None
        assert spec.imperative_preamble is True, \
            f"{name} should have imperative_preamble: true"
