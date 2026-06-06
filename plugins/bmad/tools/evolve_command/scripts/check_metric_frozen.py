#!/usr/bin/env python3
from __future__ import annotations
"""check_metric_frozen.py — TI-3 metric freeze gate (Epic 15.2 extension).

Verifies that ALL metric definition files in metrics/ haven't been modified
after their declared freeze_date. If any file was modified after freeze, CI fails.

Exit 0 = PASS (all frozen metrics unmodified)
Exit 1 = FAIL (one or more frozen metrics modified after freeze)
Exit 2 = ERROR (parse error, missing file, etc.)
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
        ts = result.stdout.strip()
        return datetime.fromisoformat(ts).date()
    except (subprocess.CalledProcessError, ValueError, OSError):
        return None


def check_metric(repo_root: Path, metric_path: Path) -> tuple[str, int]:
    """Check a single metric. Returns (message, exit_code)."""
    rel_path = metric_path.relative_to(repo_root)

    try:
        with open(metric_path, "r", encoding="utf-8") as f:
            metric = yaml.safe_load(f)
    except (yaml.YAMLError, OSError, UnicodeDecodeError) as e:
        return f"ERROR: Cannot parse {rel_path}: {e}", 2

    freeze_date_str = metric.get("freeze_date")
    if not freeze_date_str:
        return f"SKIP: {rel_path} — no freeze_date, not yet frozen", 0

    try:
        freeze_date = datetime.strptime(str(freeze_date_str), "%Y-%m-%d").date()
    except ValueError:
        return f"ERROR: {rel_path} — invalid freeze_date: {freeze_date_str}", 2

    last_modified = get_last_git_commit_date(repo_root, str(rel_path))
    if last_modified is None:
        return f"SKIP: {rel_path} — cannot determine last git commit date", 0

    if last_modified >= freeze_date:
        return (
            f"METRIC FREEZE VIOLATION: {rel_path}\n"
            f"  last modified: {last_modified}\n"
            f"  freeze_date:   {freeze_date}\n"
            f"\n  Create a new metric version instead of modifying a frozen one.\n"
            f"  See docs/metric-versioning.md for the unfreeze procedure.",
            1,
        )

    return f"PASS: {rel_path} frozen at {freeze_date}, last modified {last_modified}.", 0


def main() -> int:
    repo_root = find_repo_root()
    metrics_dir = repo_root / "plugins" / "bmad" / "tools" / "evolve_command" / "metrics"

    if not metrics_dir.exists():
        print(f"ERROR: Metrics directory not found: {metrics_dir}")
        return 2

    metric_files = sorted(metrics_dir.glob("*.yaml"))
    if not metric_files:
        print("SKIP: No metric YAML files found")
        return 0

    violations = 0
    errors = 0
    passes = 0
    skips = 0

    for metric_path in metric_files:
        msg, code = check_metric(repo_root, metric_path)
        print(msg)
        if code == 1:
            violations += 1
        elif code == 2:
            errors += 1
        elif "PASS" in msg:
            passes += 1
        else:
            skips += 1

    print(f"\n─── Summary ───")
    print(f"  Metrics checked: {len(metric_files)}")
    print(f"  PASS:  {passes}")
    print(f"  SKIP:  {skips}")
    print(f"  FAIL:  {violations}")
    print(f"  ERROR: {errors}")

    if errors > 0:
        return 2
    if violations > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
