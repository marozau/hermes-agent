"""BMADImporter — loads BMAD session traces into 8-file trace format.

Adapts the vendored external_importers.py for BMAD's session format.
Produces 8-file traces for offline tuning:

    story.md              — Story specification
    command_body.md       — Current command body
    project_context.yaml  — Project metadata
    diff.patch            — Generated diff
    test_results.txt      — Test execution output
    status_update.yaml    — Status update metadata
    success_predicates.yaml — Success criteria
    metadata.yaml         — Trace metadata
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

# Re-export from vendored module for convenience.
# Support both package-relative and standalone imports.
try:
    from ._vendor.external_importers import (  # type: ignore[no-redef]
        SECRET_PATTERNS,
        _contains_secret,
        _is_relevant_to_skill,
        ClaudeCodeImporter,
        CopilotImporter,
        HermesSessionImporter,
    )
except ImportError:
    from evolve_command._vendor.external_importers import (  # type: ignore[no-redef]
        SECRET_PATTERNS,
        _contains_secret,
        _is_relevant_to_skill,
        ClaudeCodeImporter,
        CopilotImporter,
        HermesSessionImporter,
    )


@dataclass(frozen=True)
class TraceMetadata:
    """Metadata for a BMAD trace file."""
    trace_id: str
    story_id: str
    created_at: str
    source: str
    iteration: int = 0
    variant_id: str = ""


@dataclass(frozen=True)
class TraceFile:
    """A single file in the 8-file trace format."""
    name: str
    content: str


@dataclass
class BMADTrace:
    """Complete 8-file BMAD trace."""
    story_md: str = ""
    command_body_md: str = ""
    project_context_yaml: str = ""
    diff_patch: str = ""
    test_results_txt: str = ""
    status_update_yaml: str = ""
    success_predicates_yaml: str = ""
    metadata_yaml: str = ""

    def to_files(self) -> list[TraceFile]:
        """Convert to list of TraceFile objects.

        Returns:
            List of 8 TraceFile objects in canonical order.
        """
        return [
            TraceFile("story.md", self.story_md),
            TraceFile("command_body.md", self.command_body_md),
            TraceFile("project_context.yaml", self.project_context_yaml),
            TraceFile("diff.patch", self.diff_patch),
            TraceFile("test_results.txt", self.test_results_txt),
            TraceFile("status_update.yaml", self.status_update_yaml),
            TraceFile("success_predicates.yaml", self.success_predicates_yaml),
            TraceFile("metadata.yaml", self.metadata_yaml),
        ]

    def save(self, output_dir: Path) -> Path:
        """Save trace to an output directory.

        Args:
            output_dir: Directory to write trace files into.

        Returns:
            Path to the created trace directory.
        """
        trace_dir = output_dir / f"trace_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        trace_dir.mkdir(parents=True, exist_ok=True)

        for tf in self.to_files():
            (trace_dir / tf.name).write_text(tf.content)

        return trace_dir

    @classmethod
    def load(cls, trace_dir: Path) -> BMADTrace:
        """Load a trace from a directory.

        Args:
            trace_dir: Directory containing trace files.

        Returns:
            BMADTrace with loaded contents.
        """
        def _read(name: str) -> str:
            p = trace_dir / name
            return p.read_text() if p.exists() else ""

        return cls(
            story_md=_read("story.md"),
            command_body_md=_read("command_body.md"),
            project_context_yaml=_read("project_context.yaml"),
            diff_patch=_read("diff.patch"),
            test_results_txt=_read("test_results.txt"),
            status_update_yaml=_read("status_update.yaml"),
            success_predicates_yaml=_read("success_predicates.yaml"),
            metadata_yaml=_read("metadata.yaml"),
        )


def build_trace(
    story_md: str,
    command_body_md: str,
    project_context: dict[str, object],
    diff: str,
    test_results: str,
    status_update: dict[str, object],
    success_predicates: dict[str, object],
    trace_id: str = "",
    story_id: str = "",
    source: str = "bmad",
    iteration: int = 0,
    variant_id: str = "",
) -> BMADTrace:
    """Build a BMADTrace from component data.

    Args:
        story_md: Story specification markdown.
        command_body_md: Current command body markdown.
        project_context: Project metadata dict.
        diff: Unified diff patch.
        test_results: Test output text.
        status_update: Status update dict.
        success_predicates: Success criteria dict.
        trace_id: Unique trace ID (auto-generated if empty).
        story_id: Story identifier.
        source: Source system (default: "bmad").
        iteration: Optimization iteration number.
        variant_id: DSPy variant identifier.

    Returns:
        BMADTrace with all fields populated.
    """
    now = datetime.now(timezone.utc).isoformat()
    if not trace_id:
        trace_id = f"trace_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"

    metadata = TraceMetadata(
        trace_id=trace_id,
        story_id=story_id,
        created_at=now,
        source=source,
        iteration=iteration,
        variant_id=variant_id,
    )

    return BMADTrace(
        story_md=story_md,
        command_body_md=command_body_md,
        project_context_yaml=yaml.dump(project_context, default_flow_style=False),
        diff_patch=diff,
        test_results_txt=test_results,
        status_update_yaml=yaml.dump(status_update, default_flow_style=False),
        success_predicates_yaml=yaml.dump(success_predicates, default_flow_style=False),
        metadata_yaml=yaml.dump(
            {
                "trace_id": metadata.trace_id,
                "story_id": metadata.story_id,
                "created_at": metadata.created_at,
                "source": metadata.source,
                "iteration": metadata.iteration,
                "variant_id": metadata.variant_id,
            },
            default_flow_style=False,
        ),
    )


def parse_test_results(test_output: str) -> dict[str, object]:
    """Parse test output into structured results.

    Args:
        test_output: Raw test output text.

    Returns:
        Dict with 'passed', 'failed', 'total', 'pass_rate', 'raw' keys.
    """
    pass_match = re.search(r'(\d+)\s+passed', test_output)
    fail_match = re.search(r'(\d+)\s+failed', test_output)

    passed = int(pass_match.group(1)) if pass_match else 0
    failed = int(fail_match.group(1)) if fail_match else 0
    total = passed + failed
    pass_rate = passed / total if total > 0 else 0.0

    return {
        "passed": passed,
        "failed": failed,
        "total": total,
        "pass_rate": pass_rate,
        "raw": test_output,
    }
