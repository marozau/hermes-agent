#!/usr/bin/env python3
from __future__ import annotations

"""check_vendor_attribution.py — Verify vendored files have attribution headers.

Vendored files from NousResearch/hermes-agent-self-evolution must retain
attribution per the upstream license. This script checks that all .py files
under _vendor/ (excluding __init__.py and _attribution.py) contain an
attribution comment.

Exit 0 = PASS
Exit 1 = FAIL
"""

import sys
from pathlib import Path


def find_repo_root() -> Path:
    """Walk up from this script to find the worktree root."""
    return Path(__file__).resolve().parents[5]


def main() -> int:
    script_dir = Path(__file__).resolve().parent
    vendor_dir = script_dir.parent / "_vendor"

    if not vendor_dir.exists():
        print(f"SKIP: Vendor directory not found at {vendor_dir}")
        return 0

    attribution_pattern = "hermes-agent-self-evolution"
    skip_files = {"__init__.py", "_attribution.py"}

    violations: list[str] = []

    for py_file in sorted(vendor_dir.rglob("*.py")):
        if py_file.name in skip_files:
            continue
        if py_file.is_dir():
            continue

        try:
            content = py_file.read_text(encoding="utf-8")
        except Exception as e:
            violations.append(f"{py_file}: cannot read ({e})")
            continue

        if attribution_pattern not in content:
            violations.append(str(py_file.relative_to(vendor_dir.parent.parent.parent.parent.parent)))

    if violations:
        print("VENDOR ATTRIBUTION VIOLATION: Missing attribution header in:")
        for v in violations:
            print(f"  - {v}")
        print(f"\nAll vendored .py files must contain '{attribution_pattern}' in a comment.")
        return 1

    print("VENDOR ATTRIBUTION PASS: All vendored files have attribution headers.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
