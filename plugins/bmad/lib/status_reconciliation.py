"""Status reconciliation — 3-source evidence classification (Story 9.3).

Reconciles story status from 3 evidence sources:
- file_exists: implementation artifacts exist
- git_commit: story-related commits in git history
- predicates_pass: verification predicates pass

DI-4: Conservative reconciliation. Ambiguous evidence → don't promote silently.
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


class EvidenceState(str, Enum):
    """Evidence strength for a story's completion."""
    CONFIRMED = "confirmed"      # All sources agree: done
    PROBABLE = "probable"        # 2/3 sources agree
    UNCERTAIN = "uncertain"      # 1/3 or conflicting
    NOT_STARTED = "not_started"  # No evidence


# Canonical status vocabulary (kebab-case per project convention)
VALID_STATUSES = {"not-started", "in-progress", "done", "blocked", "deferred"}


@dataclass(frozen=True)
class StoryEvidence:
    """Evidence for a single story's status."""
    story_id: str
    file_exists: bool = False
    has_commits: bool = False
    predicates_pass: bool = False
    current_status: str = ""
    recommended_status: str = ""
    evidence_state: EvidenceState = EvidenceState.NOT_STARTED
    details: str = ""


def reconcile_project(project_dir: Path) -> list[StoryEvidence]:
    """Reconcile all stories in a project. DI-1: read-only."""
    status_path = project_dir / "planning-artifacts" / "sprint-status.yaml"
    if not status_path.exists():
        return []

    try:
        with open(status_path, encoding="utf-8") as f:
            status = yaml.safe_load(f)
    except (yaml.YAMLError, OSError):
        return []

    if not isinstance(status, dict):
        return []

    stories = status.get("stories", {})
    if not isinstance(stories, dict):
        return []

    results = []
    for story_id, story_data in stories.items():
        if not isinstance(story_data, dict):
            continue

        story_id_str = str(story_id)  # YAML may parse "9.1" as float
        evidence = _gather_evidence(project_dir, story_id_str, story_data)
        results.append(evidence)

    return results


def _normalize_status(raw: str) -> str:
    """Normalize status to canonical kebab-case form."""
    return raw.replace("_", "-").lower().strip()


def _gather_evidence(project_dir: Path, story_id: str,
                     story_data: dict) -> StoryEvidence:
    """Gather 3-source evidence for a single story."""
    # Source 1: File exists
    dev_notes = project_dir / "implementation-artifacts" / f"{story_id}-dev-notes.md"
    file_exists = dev_notes.exists()

    # Source 2: Git commits (word-boundary match to avoid substring false positives)
    has_commits = _check_git_commits(project_dir, story_id)

    # Source 3: Predicates (stub — checks if tests directory exists)
    predicates_pass = _check_predicates(project_dir, story_id)

    # Classification (DI-4: conservative)
    sources = [file_exists, has_commits, predicates_pass]
    true_count = sum(sources)

    current = _normalize_status(story_data.get("status", ""))

    if true_count == 3:
        state = EvidenceState.CONFIRMED
        recommended = "done"
    elif true_count == 2:
        state = EvidenceState.PROBABLE
        # DI-4: Don't promote silently — only recommend if current is empty/pending
        if current in ("", "pending"):
            recommended = "in-progress"
        else:
            recommended = current
    elif true_count == 1:
        state = EvidenceState.UNCERTAIN
        # DI-4: If marked done but only 1 source, flag for review
        if current == "done":
            recommended = "in-progress"  # Suggest demotion
        else:
            recommended = current  # Don't change
    else:
        state = EvidenceState.NOT_STARTED
        # DI-4: If marked done with ZERO evidence, flag for demotion
        if current == "done":
            recommended = "not-started"  # Clearly stale
        else:
            recommended = "not-started" if current == "" else current

    details = (
        f"files={'✓' if file_exists else '✗'} "
        f"commits={'✓' if has_commits else '✗'} "
        f"predicates={'✓' if predicates_pass else '✗'}"
    )

    return StoryEvidence(
        story_id=story_id,
        file_exists=file_exists,
        has_commits=has_commits,
        predicates_pass=predicates_pass,
        current_status=current,
        recommended_status=recommended,
        evidence_state=state,
        details=details,
    )


def _check_git_commits(project_dir: Path, story_id: str) -> bool:
    """Check if git history has commits mentioning this story.

    Uses extended-regexp with negative lookahead to avoid substring matches.
    """
    try:
        # Negative lookahead: match story ID not followed by .digit (avoids v9.1.0)
        pattern = rf"(?<!\d){re.escape(story_id)}(?!\.\d)"
        result = subprocess.run(
            ["git", "log", "--oneline", "--extended-regexp", "--grep", pattern, "-1"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return bool(result.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def _check_predicates(project_dir: Path, story_id: str) -> bool:
    """Check if test artifacts exist for this story."""
    test_dir = project_dir / "tests"
    if not test_dir.exists():
        return False

    try:
        result = subprocess.run(
            ["grep", "-rl", story_id, str(test_dir)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return bool(result.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False
