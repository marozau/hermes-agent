#!/usr/bin/env python3
from __future__ import annotations
"""check_metric_frozen.py — TI-3 metric freeze gate.

Verifies that the metric definition file hasn't been modified after its
declared freeze date. If the file was modified after freeze_date, CI fails.

Exit 0 = PASS (metric is frozen or not yet frozen)
Exit 1 = FAIL (metric modified after freeze)
"""

import subprocess
import sys
from datetime import datetime, date
from pathlib import Path

try:
    import yaml
except ImportError:
    print("SKIP: pyyaml not installed, cannot parse metric YAML")
    sys.exit(0)


METRIC_REL_PATH = "plugins/bmad/tools/evolve_command/metrics/dev_story_composite_v1.yaml"


def find_repo_root() -> Path:
    """Walk up from this script to find the worktree root."""
    return Path(__file__).resolve().parents[5]


def get_last_git_commit_date(repo_root: Path, file_path: str) -> date | None:
    """Get the date of the last git commit that modified the file."""
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%aI", "--", file_path],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        # Parse ISO datetime, extract date
        ts = result.stdout.strip()
        return datetime.fromisoformat(ts).date()
    except Exception:
        return None


def main() -> int:
    repo_root = find_repo_root()
    metric_path = repo_root / METRIC_REL_PATH

    if not metric_path.exists():
        print(f"SKIP: Metric file not found at {metric_path}")
        return 0

    try:
        with open(metric_path, "r", encoding="utf-8") as f:
            metric = yaml.safe_load(f)
    except Exception as e:
        print(f"ERROR: Cannot parse metric YAML: {e}")
        return 1

    freeze_date_str = metric.get("freeze_date")
    if not freeze_date_str:
        print("SKIP: No freeze_date field in metric — not yet frozen")
        return 0

    try:
        freeze_date = datetime.strptime(str(freeze_date_str), "%Y-%m-%d").date()
    except ValueError:
        print(f"ERROR: Invalid freeze_date format: {freeze_date_str} (expected YYYY-MM-DD)")
        return 1

    # Check git log for last modification
    last_modified = get_last_git_commit_date(repo_root, METRIC_REL_PATH)

    if last_modified is None:
        print(f"SKIP: Cannot determine last git commit date for {METRIC_REL_PATH}")
        return 0

    if last_modified >= freeze_date:
        print(
            f"METRIC FREEZE VIOLATION: {METRIC_REL_PATH}\n"
            f"  last modified: {last_modified}\n"
            f"  freeze_date:   {freeze_date}\n"
            f"\n  Create a new metric version (v2) instead of modifying a frozen one.\n"
            f"  See docs/metric-versioning.md for the unfreeze procedure."
        )
        return 1

    print(f"TI-3 PASS: Metric frozen at {freeze_date}, last modified {last_modified}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
