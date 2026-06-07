"""Review-trajectory dataset extractor (Story 15.13).

Extracts labeled training data from code-review round files found in
``planning-artifacts/code-review-epic-*-round*.md``.

Each round produces a ``ReviewRound`` with P0/P1/P2 counts.  Adjacent
rounds form a ``ReviewTrajectory`` that can be fed into
``build_dataset_from_review_trajectories`` to produce ``EvalDataset``
splits.  Binary labels follow OI-6: P0 → 0.0, non-P0 → 1.0.

The extractor also enforces the **p0-monotonic-drop** invariant: once a
trajectory reaches P0=0, no subsequent round may report P0 > 0.  Violations
are logged and the offending round is flagged via ``RoundParseResult``.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from adapters.dataset_builder import (
    EvalDataset,
    ReviewRound,
    ReviewTrajectory,
    build_dataset_from_review_trajectories,
)

logger = logging.getLogger(__name__)

# ── Regex patterns ──────────────────────────────────────────────────────────

# Filename: code-review-epic-{N}-round{R}-{date}.md
#   or:    code-review-epic-{N}-{date}.md  (round 1, no "round" suffix)
_FILENAME_RE = re.compile(
    r"code-review-epic-(\d+)(?:-round(\d+))?-[\d-]+\.md$"
)

# Heading: "# Code Review Round {R} — Epic {N}"  or  "# Code Review — Epic {N}"
_HEADING_RE = re.compile(
    r"^#\s+Code\s+Review\s+(?:Round\s+(\d+)\s+[—–-]\s+)?Epic\s+(\d+)",
    re.MULTILINE,
)

# Scope line: "**Scope:** Commit `SHA ...`"
_SCOPE_COMMIT_RE = re.compile(
    r"\*\*Scope:\*\*.*?(?:Commit\s+`([0-9a-f]{6,40})\b)",
    re.DOTALL,
)

# Alternative scope: "Scope:** Uncommitted fixes ..." (no SHA)
_SCOPE_UNCOMMITTED_RE = re.compile(
    r"\*\*Scope:\*\*.*?Uncommitted",
    re.DOTALL,
)

# JSON findings block at end of file
_JSON_BLOCK_RE = re.compile(r"```json\s*\n(\[[\s\S]*?\])\s*\n```", re.MULTILINE)

# Findings summary line: "7 BLOCKER, 22 MAJOR, 9 MINOR, 4 NIT"
_FINDINGS_SUMMARY_RE = re.compile(
    r"\*\*Findings:\*\*\s*.*?(\d+)\s+BLOCKER.*?(\d+)\s+MAJOR.*?(\d+)\s+MINOR.*?(\d+)\s+NIT(?:[^A-Z]|$)",
    re.DOTALL,
)

# Convergence summary table rows: "| 1 (initial review ...) | 15 (5 BLOCKER, 4 P1, 6 P2) |"
_CONVERGENCE_ROW_RE = re.compile(
    r"\|\s*(\d+)\s*\([^)]*\)\s*\|\s*(\d+)\s*\((\d+)\s*BLOCKER",
)


# ── Severity classification helpers ─────────────────────────────────────────

def _classify_finding_severity(summary: str) -> str:
    """Classify a JSON finding's severity from its summary text.

    Returns 'P0', 'P1', or 'P2'.
    """
    s = summary.lower()
    # P0 indicators
    if any(kw in s for kw in (
        "blocker", "p0", "merge-blocker", "crash", "importerror",
        "typeerror", "attributeerror", "non-functional", "security",
        "shell injection", "fails open", "fails closed",
    )):
        return "P0"
    # P1 indicators
    if any(kw in s for kw in (
        "major", "p1", "architectural", "regression",
    )):
        return "P1"
    # Default to P2 (minor/nit)
    return "P2"


def _count_findings_from_json(findings: list[dict[str, Any]]) -> tuple[int, int, int]:
    """Count P0/P1/P2 from a parsed JSON findings list.

    Returns (p0_count, p1_count, p2_count).
    """
    p0 = p1 = p2 = 0
    for f in findings:
        summary = f.get("summary", "")
        sev = _classify_finding_severity(summary)
        if sev == "P0":
            p0 += 1
        elif sev == "P1":
            p1 += 1
        else:
            p2 += 1
    return p0, p1, p2


# ── File-level parsing ──────────────────────────────────────────────────────

@dataclass
class RoundParseResult:
    """Result of parsing a single code-review round file."""

    file_path: Path
    epic_id: int = 0
    round_number: int = 0
    fix_commit_sha: str = ""
    p0_count: int = 0
    p1_count: int = 0
    p2_count: int = 0
    findings_json: list[dict[str, Any]] = field(default_factory=list)
    parse_errors: list[str] = field(default_factory=list)
    p0_monotonic_violation: bool = False

    @property
    def ok(self) -> bool:
        return not self.parse_errors


def parse_round_file(path: Path) -> RoundParseResult:
    """Parse a single code-review round markdown file.

    Extracts epic ID, round number, fix commit SHA, and finding counts.
    """
    result = RoundParseResult(file_path=path)

    if not path.is_file():
        result.parse_errors.append(f"File not found: {path}")
        return result

    text = path.read_text(encoding="utf-8")

    # ── Epic ID + round number from filename ──
    m = _FILENAME_RE.search(path.name)
    if m:
        result.epic_id = int(m.group(1))
        result.round_number = int(m.group(2)) if m.group(2) else 1
    else:
        result.parse_errors.append(f"Cannot parse epic/round from filename: {path.name}")

    # ── Heading (fallback / validation) ──
    hm = _HEADING_RE.search(text)
    if hm:
        if hm.group(1):
            result.round_number = int(hm.group(1))
        elif result.round_number == 0:
            result.round_number = 1
        result.epic_id = int(hm.group(2))

    # ── Fix commit SHA ──
    sm = _SCOPE_COMMIT_RE.search(text)
    if sm:
        result.fix_commit_sha = sm.group(1)
    elif _SCOPE_UNCOMMITTED_RE.search(text):
        result.fix_commit_sha = ""

    # ── Findings: prefer JSON block, fall back to summary line ──
    jm = _JSON_BLOCK_RE.search(text)
    if jm:
        try:
            findings = json.loads(jm.group(1))
            result.findings_json = findings
            result.p0_count, result.p1_count, result.p2_count = (
                _count_findings_from_json(findings)
            )
        except json.JSONDecodeError as exc:
            result.parse_errors.append(f"Invalid JSON block: {exc}")
            # Fall through to summary-line parsing

    if not result.findings_json:
        # Try summary line
        fm = _FINDINGS_SUMMARY_RE.search(text)
        if fm:
            result.p0_count = int(fm.group(1))
            result.p1_count = int(fm.group(2))
            # p2 = MINOR + NIT
            result.p2_count = int(fm.group(3)) + int(fm.group(4))
        else:
            # Last resort: count section headings
            result.p0_count = len(re.findall(r"###\s+(?:P0|B-)\d*", text))
            # Only count if we found P0 sections
            if result.p0_count == 0:
                result.parse_errors.append(
                    "No JSON block, no findings summary, no P0 sections found"
                )

    return result


# ── Trajectory assembly ─────────────────────────────────────────────────────

def _build_round(pr: RoundParseResult) -> ReviewRound:
    """Convert a RoundParseResult into a ReviewRound."""
    return ReviewRound(
        round_id=f"R{pr.round_number}",
        p0_count=pr.p0_count,
        p1_count=pr.p1_count,
        p2_count=pr.p2_count,
        fix_commit_sha=pr.fix_commit_sha,
        notes=f"extracted from {pr.file_path.name}",
    )


def _check_p0_monotonic_drop(
    rounds: list[ReviewRound],
    parse_results: list[RoundParseResult],
) -> None:
    """Enforce p0-monotonic-drop invariant.

    Once P0 reaches 0, it must never increase.  Logs violations and flags
    the offending parse result.
    """
    seen_zero = False
    for rr, pr in zip(rounds, parse_results):
        if seen_zero and rr.p0_count > 0:
            pr.p0_monotonic_violation = True
            logger.warning(
                "p0-monotonic-drop violation: %s reports P0=%d after "
                "a prior round reached P0=0",
                pr.file_path.name,
                rr.p0_count,
            )
        if rr.p0_count == 0:
            seen_zero = True


def extract_trajectories_from_files(
    file_paths: list[Path],
) -> tuple[list[ReviewTrajectory], list[RoundParseResult]]:
    """Extract ReviewTrajectories from a list of code-review round files.

    Groups files by epic ID, sorts by round number within each epic, and
    builds one trajectory per epic.

    Args:
        file_paths: List of paths to code-review round markdown files.

    Returns:
        Tuple of (trajectories, parse_results).  Parse results include
        any errors or p0-monotonic-drop violations encountered.
    """
    # Parse all files
    parse_results = [parse_round_file(p) for p in file_paths]

    # Group by epic ID
    by_epic: dict[int, list[RoundParseResult]] = {}
    for pr in parse_results:
        if pr.epic_id > 0:
            by_epic.setdefault(pr.epic_id, []).append(pr)

    # Build trajectories
    trajectories: list[ReviewTrajectory] = []
    for epic_id in sorted(by_epic):
        rounds_pr = sorted(by_epic[epic_id], key=lambda r: r.round_number)
        rounds = [_build_round(pr) for pr in rounds_pr]

        # Enforce p0-monotonic-drop invariant
        _check_p0_monotonic_drop(rounds, rounds_pr)

        spec_path = f"planning-artifacts/code-review-epic-{epic_id}"
        trajectories.append(
            ReviewTrajectory(
                spec_path=spec_path,
                rounds=tuple(rounds),
            )
        )

    return trajectories, parse_results


def extract_dataset_from_files(
    file_paths: list[Path],
    train_ratio: float = 0.7,
    val_ratio: float = 0.15,
    seed: int = 42,
) -> tuple[EvalDataset, list[RoundParseResult]]:
    """Full pipeline: parse files → build trajectories → produce EvalDataset.

    Args:
        file_paths: List of paths to code-review round markdown files.
        train_ratio: Fraction for train split.
        val_ratio: Fraction for val split.
        seed: Random seed for reproducible splits.

    Returns:
        Tuple of (dataset, parse_results).
    """
    trajectories, parse_results = extract_trajectories_from_files(file_paths)
    dataset = build_dataset_from_review_trajectories(
        trajectories,
        train_ratio=train_ratio,
        val_ratio=val_ratio,
        seed=seed,
    )
    return dataset, parse_results


def discover_round_files(base_dir: Path) -> list[Path]:
    """Discover code-review round files under a directory.

    Searches for files matching ``code-review-epic-*-*.md`` in
    ``base_dir`` (non-recursive).
    """
    if not base_dir.is_dir():
        return []
    return sorted(base_dir.glob("code-review-epic-*-*.md"))
