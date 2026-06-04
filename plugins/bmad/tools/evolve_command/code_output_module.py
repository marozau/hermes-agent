"""CodeOutputModule — DSPy module for generating command-body diffs.

Adapts the vendored SkillModule to produce structured code outputs
(diff, test results, files touched, status update) from a BMAD story spec.
"""

from __future__ import annotations

from dataclasses import dataclass

import dspy


@dataclass(frozen=True)
class CodeOutput:
    """Structured output from a code generation pass."""
    diff: str
    test_results: str
    files_touched: str
    status_update: str


class CodeOutputSignature(dspy.Signature):
    """Generate a code diff for a BMAD command body given a story spec.

    You are a senior engineer implementing a story. Given the command body,
    the story specification, and the project context, produce:
    1. A unified diff (patch format) implementing the story
    2. Test results (pass/fail for each test case)
    3. A list of files touched
    4. A brief status update
    """
    command_body: str = dspy.InputField(
        desc="Current command body (markdown) to be evolved"
    )
    story_spec: str = dspy.InputField(
        desc="Story specification with acceptance criteria"
    )
    project_context: str = dspy.InputField(
        desc="YAML-formatted project context (repo structure, conventions, deps)"
    )
    diff: str = dspy.OutputField(
        desc="Unified diff patch implementing the story"
    )
    test_results: str = dspy.OutputField(
        desc="Test execution results: one line per test case with PASS/FAIL"
    )
    files_touched: str = dspy.OutputField(
        desc="Comma-separated list of files modified"
    )
    status_update: str = dspy.OutputField(
        desc="Brief status: what was done, any blockers, next steps"
    )


class CodeOutputModule(dspy.Module):
    """DSPy module that generates code outputs for BMAD stories.

    Wraps a ChainOfThought predictor with the CodeOutputSignature.
    On forward(), takes command_body + story_spec + project_context
    and returns a structured CodeOutput.

    This is the module that gets optimized by DSPy's GEPA — the
    command body is the optimizable parameter.
    """

    def __init__(self) -> None:
        super().__init__()
        self.predictor = dspy.ChainOfThought(CodeOutputSignature)

    def forward(
        self,
        command_body: str,
        story_spec: str,
        project_context: str,
    ) -> dspy.Prediction:
        """Generate code output for a story.

        Args:
            command_body: Current command body markdown.
            story_spec: Story specification with acceptance criteria.
            project_context: YAML-formatted project context.

        Returns:
            dspy.Prediction with fields: diff, test_results, files_touched, status_update.
        """
        result = self.predictor(
            command_body=command_body,
            story_spec=story_spec,
            project_context=project_context,
        )
        return dspy.Prediction(
            diff=result.diff,
            test_results=result.test_results,
            files_touched=result.files_touched,
            status_update=result.status_update,
        )

    def to_code_output(self, prediction: dspy.Prediction) -> CodeOutput:
        """Convert a DSPy Prediction to a frozen CodeOutput dataclass.

        Args:
            prediction: The prediction from forward().

        Returns:
            CodeOutput with extracted fields.
        """
        return CodeOutput(
            diff=str(getattr(prediction, "diff", "")),
            test_results=str(getattr(prediction, "test_results", "")),
            files_touched=str(getattr(prediction, "files_touched", "")),
            status_update=str(getattr(prediction, "status_update", "")),
        )
