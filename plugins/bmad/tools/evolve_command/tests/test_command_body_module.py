"""Tests for adapters/command_body_module.py."""

from __future__ import annotations

import pytest


# ── Fixtures ────────────────────────────────────────────────────────────

SAMPLE_FRONTMATTER = (
    "name: my-command\n"
    "description: Does something useful\n"
    "version: 1.0"
)

SAMPLE_BODY = (
    "## Instructions\n"
    "1. Read the task input.\n"
    "2. Produce the requested output.\n"
)

SAMPLE_RAW = f"---\n{SAMPLE_FRONTMATTER}\n---\n\n{SAMPLE_BODY}"


# ── parse_command ───────────────────────────────────────────────────────

class TestParseCommand:
    """Tests for the parse_command() helper."""

    def test_parses_frontmatter_and_body(self) -> None:
        """parse_command should split frontmatter from body."""
        from adapters.command_body_module import parse_command

        parsed = parse_command(SAMPLE_RAW)
        assert "name: my-command" in parsed.frontmatter
        assert "## Instructions" in parsed.body

    def test_no_frontmatter_returns_full_text_as_body(self) -> None:
        """When there are no --- markers, body is the full text."""
        from adapters.command_body_module import parse_command

        text = "Just some plain markdown.\n"
        parsed = parse_command(text)
        assert parsed.frontmatter == ""
        assert "Just some plain markdown" in parsed.body

    def test_raw_is_preserved(self) -> None:
        """The original raw text is stored on the result."""
        from adapters.command_body_module import parse_command

        parsed = parse_command(SAMPLE_RAW)
        assert parsed.raw == SAMPLE_RAW


# ── reassemble_command ──────────────────────────────────────────────────

class TestReassembleCommand:
    """Tests for the reassemble_command() helper."""

    def test_roundtrip_preserves_frontmatter(self) -> None:
        """Parsing then reassembling should preserve the frontmatter."""
        from adapters.command_body_module import parse_command, reassemble_command

        parsed = parse_command(SAMPLE_RAW)
        result = reassemble_command(parsed.frontmatter, parsed.body)
        assert result.strip().startswith("---")
        assert "name: my-command" in result

    def test_evolved_body_is_used(self) -> None:
        """Reassembly should use the evolved body, not the original."""
        from adapters.command_body_module import reassemble_command

        evolved = "## New instructions\nDo it differently."
        result = reassemble_command("name: foo", evolved)
        assert "New instructions" in result
        assert "name: foo" in result

    def test_empty_frontmatter(self) -> None:
        """When frontmatter is empty, only the body is returned."""
        from adapters.command_body_module import reassemble_command

        result = reassemble_command("", "some body")
        assert result == "some body"


# ── CommandBodyModule ──────────────────────────────────────────────────

class TestCommandBodyModule:
    """Tests for the CommandBodyModule DSPy module."""

    def test_module_is_dspy_module(self) -> None:
        """CommandBodyModule must be a dspy.Module subclass."""
        import dspy
        from adapters.command_body_module import CommandBodyModule

        assert issubclass(CommandBodyModule, dspy.Module)

    def test_init_stores_frontmatter_and_body(self) -> None:
        """Constructor should store frontmatter and body_text."""
        from adapters.command_body_module import CommandBodyModule

        mod = CommandBodyModule(
            frontmatter=SAMPLE_FRONTMATTER,
            body_text=SAMPLE_BODY,
        )
        assert mod.frontmatter == SAMPLE_FRONTMATTER
        assert mod.body_text == SAMPLE_BODY

    def test_copy_produces_independent_clone(self) -> None:
        """copy() must return a module with independent mutable state."""
        from adapters.command_body_module import CommandBodyModule

        original = CommandBodyModule(
            frontmatter=SAMPLE_FRONTMATTER,
            body_text=SAMPLE_BODY,
        )
        clone = original.copy()

        # Same content initially
        assert clone.frontmatter == original.frontmatter
        assert clone.body_text == original.body_text

        # Mutating the clone must not affect the original
        clone.body_text = "mutated body"
        assert original.body_text == SAMPLE_BODY
        assert clone.body_text == "mutated body"

    def test_from_raw_parses_correctly(self) -> None:
        """from_raw() should parse raw text into a module."""
        from adapters.command_body_module import CommandBodyModule

        mod = CommandBodyModule.from_raw(SAMPLE_RAW)
        assert "name: my-command" in mod.frontmatter
        assert "## Instructions" in mod.body_text

    def test_to_raw_reassembles(self) -> None:
        """to_raw() should reassemble frontmatter + body."""
        from adapters.command_body_module import CommandBodyModule

        mod = CommandBodyModule(
            frontmatter=SAMPLE_FRONTMATTER,
            body_text=SAMPLE_BODY,
        )
        raw = mod.to_raw()
        assert raw.strip().startswith("---")
        assert "name: my-command" in raw
        assert "## Instructions" in raw

    def test_from_raw_to_raw_roundtrip(self) -> None:
        """from_raw → to_raw should produce equivalent output."""
        from adapters.command_body_module import CommandBodyModule

        mod = CommandBodyModule.from_raw(SAMPLE_RAW)
        reconstructed = mod.to_raw()
        # The content should be equivalent (modulo whitespace normalization)
        assert "name: my-command" in reconstructed
        assert "description: Does something useful" in reconstructed
        assert "## Instructions" in reconstructed

    def test_copy_then_to_raw_preserves_frontmatter(self) -> None:
        """After copying and mutating body, frontmatter survives roundtrip."""
        from adapters.command_body_module import CommandBodyModule

        original = CommandBodyModule.from_raw(SAMPLE_RAW)
        clone = original.copy()
        clone.body_text = "## Evolved\nNew and improved."

        raw = clone.to_raw()
        assert "name: my-command" in raw
        assert "## Evolved" in raw
