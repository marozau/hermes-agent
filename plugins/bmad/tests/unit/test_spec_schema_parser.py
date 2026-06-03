"""Tests for spec_schema + spec_parser (Story 12.1)."""

import pytest
from plugins.bmad.lib.spec_schema import CommandSpec, VerificationItem
from plugins.bmad.lib.spec_parser import parse_command_body


# ── Schema dataclass tests ──────────────────────────────────────────────────


class TestVerificationItem:
    def test_minimal(self):
        v = VerificationItem(description="Tests pass")
        assert v.description == "Tests pass"
        assert v.predicate is None

    def test_with_predicate(self):
        v = VerificationItem(description="Tests pass", predicate="predicates.dev_story.tests_pass")
        assert v.predicate == "predicates.dev_story.tests_pass"

    def test_frozen(self):
        v = VerificationItem(description="x")
        with pytest.raises(AttributeError):
            v.description = "y"


class TestCommandSpec:
    def test_minimal(self):
        s = CommandSpec(
            persona="Dev",
            phase="implementation",
            verification=[VerificationItem(description="Tests pass")],
        )
        assert s.imperative_preamble is True
        assert s.output_artifacts == []
        assert s.metadata == {}

    def test_frozen(self):
        s = CommandSpec(
            persona="Dev",
            phase="implementation",
            verification=[VerificationItem(description="x")],
        )
        with pytest.raises(AttributeError):
            s.persona = "QA"


# ── Parser tests ────────────────────────────────────────────────────────────


class TestParseCommandBody:
    def test_legacy_no_frontmatter(self):
        content = "Create a PRD for the project.\n\n1. Summary\n2. Requirements\n"
        spec, body = parse_command_body(content)
        assert spec is None
        assert body == content

    def test_frontmatter_without_spec(self):
        content = "---\ntitle: foo\n---\nBody text\n"
        spec, body = parse_command_body(content)
        assert spec is None
        assert "Body text" in body

    def test_valid_spec(self):
        content = (
            "---\n"
            "spec:\n"
            "  persona: Dev\n"
            "  phase: implementation\n"
            "  verification:\n"
            "    - description: Tests pass\n"
            "    - description: No regressions\n"
            "      predicate: predicates.dev_story.tests_pass\n"
            "---\n"
            "## Instructions\n"
            "Implement the story.\n"
        )
        spec, body = parse_command_body(content)
        assert spec is not None
        assert spec.persona == "Dev"
        assert spec.phase == "implementation"
        assert len(spec.verification) == 2
        assert spec.verification[0].predicate is None
        assert spec.verification[1].predicate == "predicates.dev_story.tests_pass"
        assert "## Instructions" in body

    def test_spec_with_optional_fields(self):
        content = (
            "---\n"
            "spec:\n"
            "  persona: SM\n"
            "  phase: planning\n"
            "  imperative_preamble: false\n"
            "  predicate_module: plugins.bmad.predicates.sm\n"
            "  output_artifacts:\n"
            "    - planning-artifacts/epics.md\n"
            "  metadata:\n"
            "    level: 2\n"
            "  verification:\n"
            "    - description: Epics decomposed\n"
            "---\n"
            "Body\n"
        )
        spec, body = parse_command_body(content)
        assert spec is not None
        assert spec.imperative_preamble is False
        assert spec.predicate_module == "plugins.bmad.predicates.sm"
        assert spec.output_artifacts == ["planning-artifacts/epics.md"]
        assert spec.metadata == {"level": 2}

    def test_missing_persona_returns_none(self):
        content = (
            "---\n"
            "spec:\n"
            "  phase: implementation\n"
            "  verification:\n"
            "    - description: x\n"
            "---\n"
            "Body\n"
        )
        spec, body = parse_command_body(content)
        assert spec is None

    def test_empty_verification_returns_none(self):
        content = (
            "---\n"
            "spec:\n"
            "  persona: Dev\n"
            "  phase: implementation\n"
            "  verification: []\n"
            "---\n"
            "Body\n"
        )
        spec, body = parse_command_body(content)
        assert spec is None

    def test_invalid_yaml_returns_none(self):
        content = "---\n{{invalid yaml\n---\nBody\n"
        spec, body = parse_command_body(content)
        assert spec is None

    def test_body_preserved_after_frontmatter(self):
        content = "---\nspec:\n  persona: Dev\n  phase: impl\n  verification:\n    - description: x\n---\nLine 1\nLine 2\n"
        spec, body = parse_command_body(content)
        assert spec is not None
        assert body == "Line 1\nLine 2\n"
