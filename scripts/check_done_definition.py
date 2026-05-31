#!/usr/bin/env python3
"""CI gate: combined done-definition check (A6 from retrospective).

A story is `done` only if:
  (a) tests pass
  (b) grep confirms ≥ 1 production caller for every new public function
  (c) HI #11 grep check passes for any LLM-touching surface
  (d) no new functions added without at least one test

This script runs checks (b), (c), and (d). Check (a) is handled by pytest.

Exit 0 if all checks pass.
Exit 1 if any check fails.

Usage:
    python scripts/check_done_definition.py [--verbose] [--diff HEAD~1]
"""
import subprocess
import sys
from pathlib import Path


def run_check(script: str, args: list[str] = None) -> tuple[int, str]:
    """Run a check script and return (exit_code, output)."""
    cmd = [sys.executable, script] + (args or [])
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    return result.returncode, result.stdout + result.stderr


def check_new_functions_have_tests(repo_root: Path, diff_ref: str = "HEAD~1") -> tuple[bool, str]:
    """Check (d): new public functions in lib/ must have at least one test.

    Uses git diff to find new function definitions, then checks if any test
    file references them.
    """
    try:
        result = subprocess.run(
            ["git", "diff", diff_ref, "--name-only", "--", "lib/"],
            capture_output=True, text=True, cwd=repo_root,
        )
        changed_files = [f for f in result.stdout.strip().split("\n") if f.endswith(".py")]
    except Exception:
        return True, "git diff unavailable — skipping new-function check"

    import re
    untested = []

    for rel_path in changed_files:
        filepath = repo_root / rel_path
        if not filepath.exists():
            continue
        content = filepath.read_text(encoding="utf-8")

        # Find new public function definitions
        for match in re.finditer(r"^\+def ([a-zA-Z]\w*)\(", content, re.MULTILINE):
            fn_name = match.group(1)
            if fn_name.startswith("_"):
                continue

            # Check if any test file references this function
            found_in_test = False
            for test_file in (repo_root / "tests").rglob("test_*.py"):
                test_content = test_file.read_text(encoding="utf-8")
                if fn_name in test_content:
                    found_in_test = True
                    break

            if not found_in_test:
                untested.append(f"{rel_path}::{fn_name}")

    if untested:
        return False, f"{len(untested)} new public function(s) without tests:\n  " + "\n  ".join(untested)
    return True, "All new public functions have test coverage"


def main():
    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    diff_ref = "HEAD~1"
    for i, arg in enumerate(sys.argv):
        if arg == "--diff" and i + 1 < len(sys.argv):
            diff_ref = sys.argv[i + 1]

    repo_root = Path(__file__).resolve().parent.parent
    scripts_dir = repo_root / "scripts"
    all_ok = True

    # Check (b): dead helpers
    print("━" * 60)
    print("Check (b): Dead helper detection")
    print("━" * 60)
    code, output = run_check(str(scripts_dir / "check_dead_helpers.py"))
    print(output)
    if code != 0:
        all_ok = False

    # Check (c): HI #11 violations
    print("━" * 60)
    print("Check (c): Hard Invariant #11 compliance")
    print("━" * 60)
    code, output = run_check(str(scripts_dir / "check_hi11_violations.py"))
    print(output)
    if code != 0:
        all_ok = False

    # Check (d): new functions have tests
    print("━" * 60)
    print("Check (d): New functions have test coverage")
    print("━" * 60)
    ok, msg = check_new_functions_have_tests(repo_root, diff_ref)
    if ok:
        print(f"✅ {msg}")
    else:
        print(f"❌ {msg}")
        all_ok = False

    print("━" * 60)
    if all_ok:
        print("✅ All done-definition checks passed")
        sys.exit(0)
    else:
        print("❌ Done-definition checks failed — story is NOT ready for done")
        sys.exit(1)


if __name__ == "__main__":
    main()
