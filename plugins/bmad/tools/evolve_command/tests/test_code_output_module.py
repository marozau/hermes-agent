"""Tests for code_output_module.py."""

from __future__ import annotations

import pytest


class TestCodeOutputSignature:
    """Test the DSPy signature definition."""

    def test_signature_has_required_fields(self) -> None:
        """CodeOutputSignature must have all required input/output fields."""
        from code_output_module import CodeOutputSignature

        # Check input fields
        input_fields = CodeOutputSignature.input_fields
        assert "command_body" in input_fields
        assert "story_spec" in input_fields
        assert "project_context" in input_fields

        # Check output fields
        output_fields = CodeOutputSignature.output_fields
        assert "diff" in output_fields
        assert "test_results" in output_fields
        assert "files_touched" in output_fields
        assert "status_update" in output_fields


class TestCodeOutput:
    """Test the CodeOutput dataclass."""

    def test_frozen_dataclass(self) -> None:
        """CodeOutput must be frozen."""
        from code_output_module import CodeOutput

        output = CodeOutput(
            diff="--- a/foo\n+++ b/foo\n@@ -1 +1 @@\n-old\n+new",
            test_results="1 passed",
            files_touched="foo.py",
            status_update="Done",
        )
        assert output.diff.startswith("---")
        assert output.test_results == "1 passed"

        # Verify frozen
        with pytest.raises(AttributeError):
            output.diff = "modified"  # type: ignore[misc]

    def test_empty_defaults(self) -> None:
        """CodeOutput fields should accept empty strings."""
        from code_output_module import CodeOutput

        output = CodeOutput(
            diff="",
            test_results="",
            files_touched="",
            status_update="",
        )
        assert output.diff == ""
        assert output.status_update == ""


class TestCodeOutputModule:
    """Test the CodeOutputModule class."""

    def test_module_exists(self) -> None:
        """CodeOutputModule class should be importable."""
        from code_output_module import CodeOutputModule

        assert CodeOutputModule is not None

    def test_to_code_output(self) -> None:
        """to_code_output should convert a Prediction to CodeOutput."""
        import dspy
        from code_output_module import CodeOutputModule

        module = CodeOutputModule()
        prediction = dspy.Prediction(
            diff="test diff",
            test_results="1 passed",
            files_touched="foo.py",
            status_update="complete",
        )
        output = module.to_code_output(prediction)
        assert output.diff == "test diff"
        assert output.test_results == "1 passed"
        assert output.files_touched == "foo.py"
        assert output.status_update == "complete"

    def test_to_code_output_handles_missing_fields(self) -> None:
        """to_code_output should handle missing fields gracefully."""
        import dspy
        from code_output_module import CodeOutputModule

        module = CodeOutputModule()
        prediction = dspy.Prediction()
        output = module.to_code_output(prediction)
        assert output.diff == ""
        assert output.test_results == ""
