#!/usr/bin/env python3
"""CI gate: detect Hard Invariant #11 violations.

A4 from retrospective-epic-10-2026-05-31.md:
Check that every LLMSpec(...) construction in lib/ includes response_model=.
Without it, the LLM output isn't Pydantic-gated — violating HI #11.

Exit 0 if all LLMSpec constructions include response_model.
Exit 1 if any violations found.

Usage:
    python scripts/check_hi11_violations.py [--verbose]
"""
import re
import sys
from pathlib import Path


# Files to check (the three LLM-touching surfaces from the retrospective)
CHECK_FILES = [
    "autodream/dream.py",
    "autodream/preflight.py",
    "autodream/memory.py",
]


def find_llm_calls(content: str, filepath: str) -> list[dict]:
    """Find all LLMSpec(...) constructions and check for response_model.

    Returns list of violations: {line, text, reason}.
    """
    violations = []
    lines = content.splitlines()

    for i, line in enumerate(lines, 1):
        # Match LLMSpec( — the constructor call
        if "LLMSpec(" not in line:
            continue

        # Skip comments and strings
        stripped = line.strip()
        if stripped.startswith("#"):
            continue

        # Collect the full LLMSpec construction (may span multiple lines)
        # Find the opening ( and track to closing )
        start_idx = line.index("LLMSpec(")
        depth = 0
        spec_text = ""
        for j in range(i - 1, min(i + 20, len(lines))):
            spec_text += lines[j] + "\n"
            for ch in lines[j][start_idx if j == i - 1 else 0:]:
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        break
            if depth == 0:
                break
            start_idx = 0  # subsequent lines start from beginning

        # Check for response_model= in the spec construction
        if "response_model=" not in spec_text:
            violations.append({
                "line": i,
                "text": stripped[:100],
                "reason": "LLMSpec without response_model= — HI #11 violation",
            })
        elif "response_model=None" in spec_text:
            violations.append({
                "line": i,
                "text": stripped[:100],
                "reason": "LLMSpec with response_model=None — HI #11 violation",
            })

    return violations


def main():
    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    repo_root = Path(__file__).resolve().parent.parent

    all_violations = []

    for rel_path in CHECK_FILES:
        filepath = repo_root / rel_path
        if not filepath.exists():
            if verbose:
                print(f"  SKIP: {rel_path} — file not found")
            continue

        content = filepath.read_text(encoding="utf-8")
        violations = find_llm_calls(content, str(filepath))

        for v in violations:
            v["file"] = rel_path
            all_violations.append(v)
            if verbose:
                print(f"  VIOLATION: {rel_path}:{v['line']} — {v['reason']}")
                print(f"    {v['text']}")

    if all_violations:
        print(f"\n❌ {len(all_violations)} HI #11 violation(s) found:")
        for v in all_violations:
            print(f"  {v['file']}:{v['line']} — {v['reason']}")
        print("\nFix: add response_model=<PydanticModel> to each LLMSpec(...).")
        sys.exit(1)
    else:
        print(f"✅ {len(CHECK_FILES)} files checked — all LLMSpec constructions have response_model=")
        sys.exit(0)


if __name__ == "__main__":
    main()
