"""CommandBodyModule — wraps a BMAD command body as a DSPy module.

Adapts the vendored SkillModule (from evolution/core/evolve_skill.py)
so that GEPA can mutate a command body text while preserving the
spec: frontmatter block (Epic 12 OI-2).

Pattern:
  • ``body_text`` is the optimisable parameter.
  • ``forward()`` feeds the body into a ChainOfThought predictor.
  • ``copy()`` returns a deep clone so GEPA can fork variants.
  • ``reassemble()`` re-joins preserved frontmatter + evolved body.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Optional

from typing import Any, Callable, Optional, Protocol

try:
    import dspy
except ImportError:
    dspy = None  # type: ignore[assignment]
# ── Frontmatter helpers ────────────────────────────────────────────────

_FRONTMATTER_RE = re.compile(
    r"\A---\s*\n(?P<fm>.*?)\n---\s*\n(?P<body>.*)",
    re.DOTALL,
)


@dataclass(frozen=True)
class ParsedCommand:
    """Result of parsing a command file into frontmatter + body.

    Attributes:
        frontmatter: YAML block between the ``---`` markers (may be empty).
        body: Everything after the closing ``---``.
        raw: The original unmodified text.
    """

    frontmatter: str
    body: str
    raw: str


def parse_command(text: str) -> ParsedCommand:
    """Split a command file into frontmatter and body.

    Args:
        text: Raw command file content.

    Returns:
        A :class:`ParsedCommand` with ``frontmatter`` and ``body`` parts.
        If no frontmatter markers are found, ``frontmatter`` is empty and
        ``body`` equals the full text.
    """
    text = text.lstrip('\ufeff')
    m = _FRONTMATTER_RE.match(text.strip())
    if m:
        return ParsedCommand(
            frontmatter=m.group("fm").strip(),
            body=m.group("body").strip(),
            raw=text,
        )
    return ParsedCommand(frontmatter="", body=text.strip(), raw=text)


def reassemble_command(frontmatter: str, evolved_body: str) -> str:
    """Reassemble a command from preserved frontmatter and evolved body.

    Preserves the original YAML frontmatter (OI-2 spec) and replaces
    only the body with the evolved version.

    Args:
        frontmatter: The YAML frontmatter text (between ``---`` markers).
        evolved_body: The mutated body text produced by GEPA.

    Returns:
        Full command file content with frontmatter intact.
    """
    if frontmatter:
        return f"---\n{frontmatter}\n---\n\n{evolved_body}\n"
    return evolved_body


# ── DSPy Module ────────────────────────────────────────────────────────


class CommandBodyModule(dspy.Module):
    """A DSPy module wrapping a BMAD command body for GEPA optimisation.

    The command body text is the parameter that GEPA mutates.  On each
    ``forward()`` pass the module feeds the body (along with optional
    context) into a ChainOfThought predictor and returns the result.

    The frontmatter block is preserved separately so that mutations only
    affect the body (OI-2 constraint).

    Example::

        module = CommandBodyModule(
            frontmatter="name: my-cmd\\ndescription: Does stuff",
            body_text="## Instructions\\nDo the thing.",
        )
        # Mutate for GEPA:
        variant = module.copy()
        variant.body_text = "## Instructions\\nDo the thing better."
    """

    class CommandBodySignature(dspy.Signature):
        """Execute a BMAD command body against a task.

        You are an AI agent following BMAD command instructions.
        Read the command body carefully and carry out the task.
        """
        command_body: str = dspy.InputField(
            desc="The BMAD command body (markdown instructions)"
        )
        task_input: str = dspy.InputField(
            desc="The task or request to execute"
        )
        output: str = dspy.OutputField(
            desc="Result of executing the command against the task"
        )

    def __init__(
        self,
        frontmatter: str = "",
        body_text: str = "",
    ) -> None:
        """Initialise the module.

        Args:
            frontmatter: YAML frontmatter string to preserve (OI-2).
            body_text: The command body text that GEPA will mutate.
        """
        super().__init__()
        self.frontmatter: str = frontmatter
        self.body_text: str = body_text
        self.predictor = dspy.ChainOfThought(self.CommandBodySignature)

    # ── DSPy interface ──────────────────────────────────────────────────

    def forward(self, task_input: str) -> dspy.Prediction:
        """Run the command body against a task.

        Args:
            task_input: The task or user request.

        Returns:
            A :class:`dspy.Prediction` with an ``output`` field.
        """
        result = self.predictor(
            command_body=self.body_text,
            task_input=task_input,
        )
        return dspy.Prediction(output=result.output)

    # ── GEPA mutation helpers ───────────────────────────────────────────

    def copy(self) -> CommandBodyModule:
        """Return a deep copy of this module for GEPA mutation.

        The copy shares no mutable state with the original, so GEPA
        can safely mutate ``body_text`` on the clone.
        """
        clone = CommandBodyModule(
            frontmatter=self.frontmatter,
            body_text=self.body_text,
        )
        # Deep-copy predictor weights so DSPy parameter state is independent
        clone.predictor = copy.deepcopy(self.predictor)
        return clone

    # ── Serialisation helpers ───────────────────────────────────────────

    @classmethod
    def from_raw(cls, raw_text: str) -> CommandBodyModule:
        """Parse raw command text and build a module.

        Args:
            raw_text: Full command file content (frontmatter + body).

        Returns:
            A new :class:`CommandBodyModule` with frontmatter preserved.
        """
        parsed = parse_command(raw_text)
        return cls(frontmatter=parsed.frontmatter, body_text=parsed.body)

    def to_raw(self) -> str:
        """Reassemble the full command text (frontmatter + evolved body).

        Returns:
            The complete command file content ready to write back.
        """
        return reassemble_command(self.frontmatter, self.body_text)
