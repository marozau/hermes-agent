"""Adversarial verification gate (Story 7.8).

When a story has ``verification_gate: adversarial`` in its spec, spawns
an Opus reviewer via delegation.delegate_one() with model override.
Reviewer checks the story's implementation against its ACs.
Returns pass/fail with findings.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .epic_anchor import StorySpec

logger = logging.getLogger(__name__)

# Default model for adversarial reviewers — strong reasoning model
DEFAULT_ADVERSARIAL_MODEL = "claude-opus-4-20250514"


def _build_review_goal(story: StorySpec, project_dir: Path) -> str:
    """Build the adversarial reviewer's goal prompt.

    AC-7.8.5: Includes explicit no-write tool restrictions (Epic 6
    retrospective action item 6.1).
    """
    acs = "\n".join(
        f"  {i+1}. {p}" for i, p in enumerate(story.success_predicates)
    ) or "  (none)"

    return f"""\
## Adversarial Review: Story {story.id} — {story.title}

You are an adversarial code reviewer. Your job is to find problems.

### ⚠️ Tool Restrictions (MANDATORY)
USE ONLY these tools: Read, Grep, Glob, Bash (for read-only commands ONLY: `ls`, `cat`, `head`, `tail`, `git status`, `git diff`, `wc`).
DO NOT CALL: Write, Edit, NotebookEdit, or any tool that mutates files.
DO NOT use Bash to: cp, mv, rm, touch, > redirect, >> append, sed -i, mkdir, chmod, or any state-changing command.
If you find drift or issues, REPORT them in your VERDICT — DO NOT fix them.

### Story Description
{story.description or story.title}

### Acceptance Criteria
{acs}

### Your Task
1. Read the relevant files in the project
2. Check EVERY acceptance criterion above
3. Look for bugs, edge cases, missing error handling
4. Check for security issues, race conditions, resource leaks

### Output Format
Return EXACTLY this format:

VERDICT: PASS
or
VERDICT: FAIL
FINDINGS:
- finding 1
- finding 2

Be strict. Only PASS if ALL criteria are satisfied with no issues.
Do NOT rationalize away problems. Do NOT give benefit of the doubt.
""".strip()


def _parse_review_result(result_text: str) -> tuple[bool, str]:
    """Parse adversarial review result into (passed, findings).

    Looks for VERDICT: PASS or VERDICT: FAIL followed by FINDINGS: block.
    """
    if not result_text:
        return False, "No review output received"

    text = result_text.strip()
    upper = text.upper()

    # Check for PASS verdict
    if "VERDICT: PASS" in upper:
        # Extract any findings even on pass
        findings = _extract_findings(text)
        return True, findings if findings else "All criteria verified"

    # Check for FAIL verdict
    if "VERDICT: FAIL" in upper:
        findings = _extract_findings(text)
        return False, findings or "Review failed (no specific findings)"

    # Fallback: heuristic analysis
    fail_indicators = ["fail", "issue", "bug", "missing", "broken", "error"]
    pass_indicators = ["pass", "correct", "satisfied", "all criteria"]

    fail_count = sum(1 for ind in fail_indicators if ind in upper.lower())
    pass_count = sum(1 for ind in pass_indicators if ind in upper.lower())

    if fail_count > pass_count:
        return False, f"Review suggests failure (heuristic): {text[:500]}"
    return True, "Review suggests pass (heuristic)"


def _extract_findings(text: str) -> str:
    """Extract the FINDINGS: block from review text."""
    lines = text.split("\n")
    findings_lines = []
    in_findings = False
    for line in lines:
        if line.strip().upper().startswith("FINDINGS:"):
            in_findings = True
            # Check if findings are on the same line
            rest = line.split(":", 1)[1].strip()
            if rest:
                findings_lines.append(rest)
            continue
        if in_findings:
            stripped = line.strip()
            if stripped.startswith("-") or stripped.startswith("*"):
                findings_lines.append(stripped)
            elif stripped:
                findings_lines.append(stripped)
            else:
                break  # Empty line ends findings

    return "\n".join(findings_lines).strip()


def run_adversarial_gate(
    ctx: Any,
    story: StorySpec,
    project_dir: Path,
    model: str = DEFAULT_ADVERSARIAL_MODEL,
) -> tuple[bool, str]:
    """Run adversarial verification for a story.

    Spawns a reviewer sub-agent (using a strong model) to verify
    the story's implementation against its acceptance criteria.

    Args:
        ctx: Hermes plugin context (has dispatch_tool)
        story: The story to verify
        project_dir: Root directory of the BMAD project
        model: Model override for the reviewer (default: Opus)

    Returns:
        (passed: bool, findings: str)
    """
    logger.info(
        "[adversarial] Running adversarial gate for story %s (model=%s)",
        story.id, model,
    )

    goal = _build_review_goal(story, project_dir)

    try:
        from .delegation import delegate_one

        result = delegate_one(
            ctx,
            goal=goal,
            parent_skill="bmad:adversarial-gate",
            context=f"Reviewing story {story.id} implementation",
            model=model,
        )
    except Exception as exc:
        logger.exception("[adversarial] Delegation failed for story %s", story.id)
        return False, f"Adversarial review delegation failed: {exc}"

    # Extract result text
    result_text = ""
    if isinstance(result, dict):
        result_text = result.get("summary", "") or result.get("output", "")
        if not result_text and result.get("status") == "failure":
            return False, f"Adversarial review failed: {result.get('error', 'unknown')}"

    passed, findings = _parse_review_result(result_text)

    logger.info(
        "[adversarial] Story %s: %s — %s",
        story.id, "PASS" if passed else "FAIL", findings[:200],
    )

    return passed, findings
