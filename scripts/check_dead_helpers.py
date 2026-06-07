#!/usr/bin/env python3
"""CI gate: detect dead helper functions in autodream/*.py.

A3 from retrospective-epic-10-2026-05-31.md:
Flag new helper functions in autodream/ with zero production callers.
This catches the Epic 9 + Epic 10 dead-code pattern: helper defined,
tests pass, but never wired into production code.

Exit 0 if all public functions have ≥ 1 production caller.
Exit 1 if any dead helpers found.

Usage:
    python scripts/check_dead_helpers.py [--verbose] [--exclude PATTERN]
"""
import os
import re
import sys
from pathlib import Path


# Known public API functions called from outside autodream/ (CLI, plugins, agent runtime).
# These are entry points by design — not dead code.
ALLOWED_PUBLIC_API = {
    # autodream.dream.py — called by `hermes dream` CLI
    "create_dream_artifact", "list_dreams", "dream_diff", "apply_dream", "discard_dream",
    # autodream.preflight.py — called by preflight plugin
    "should_run_preflight", "record_verify_citations", "read_citations", "persist_citations",
    "get_or_create_gate", "write_preflight_telemetry",
    # autodream.llm.py — called by provider dispatch
    "load_providers_config", "classify_exception",
    # autodream.providers_*.py — called by register_all
    "anthropic_chat", "chat_completions", "embeddings",
    # autodream.recall.py — called by dream CLI + tests
    "build_recall_set", "run_regression_check", "memory_token_count",
    "recall_artifact_path", "write_recall_artifact",
    # autodream.trust.py — called by dream apply + audit CLI
    "verify_signature", "append_audit_row", "write_advisory",
}


def find_public_functions(lib_dir: Path) -> dict[str, list[str]]:
    """Find all public function definitions in autodream/*.py.

    Returns {filepath: [function_name, ...]}.
    Skips private functions (starting with _) and test files.
    Skips known public API (allowlisted).
    """
    functions: dict[str, list[str]] = {}
    for py_file in sorted(lib_dir.glob("*.py")):
        if py_file.name.startswith("_") or py_file.name.startswith("test_"):
            continue
        content = py_file.read_text(encoding="utf-8")
        for match in re.finditer(r"^def ([a-zA-Z]\w*)\(", content, re.MULTILINE):
            fn_name = match.group(1)
            # Skip private/test helpers
            if fn_name.startswith("_"):
                continue
            # Skip known public API
            if fn_name in ALLOWED_PUBLIC_API:
                continue
            functions.setdefault(str(py_file), []).append(fn_name)
    return functions


def count_callers(lib_dir: Path, fn_name: str, defining_file: str) -> int:
    """Count production callers of fn_name.

    Checks: intra-file callers (same file), other autodream/*.py files,
    scripts/*.py, plugins/**/*.py. Excludes test files and the
    bare definition line.
    """
    search_dirs = [lib_dir, lib_dir.parent / "scripts", lib_dir.parent / "plugins"]
    count = 0
    for search_dir in search_dirs:
        if not search_dir.exists():
            continue
        for py_file in search_dir.rglob("*.py"):
            if py_file.name.startswith("test_") or "_test" in py_file.name:
                continue
            content = py_file.read_text(encoding="utf-8")
            in_comment = False
            for line_num, line in enumerate(content.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                # Skip the definition line itself
                if str(py_file) == defining_file and re.match(rf"^def {fn_name}\(", stripped):
                    continue
                if f"{fn_name}(" in line:
                    count += 1
    return count


def main():
    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    exclude_patterns = []
    for i, arg in enumerate(sys.argv):
        if arg == "--exclude" and i + 1 < len(sys.argv):
            exclude_patterns.append(sys.argv[i + 1])

    repo_root = Path(__file__).resolve().parent.parent
    autodream_dir = repo_root / "autodream"
    scripts_dir = repo_root / "scripts"

    if not autodream_dir.exists():
        print(f"ERROR: autodream/ not found at {autodream_dir}")
        sys.exit(1)

    functions = find_public_functions(autodream_dir)
    dead_helpers = []

    for filepath, fns in functions.items():
        for fn_name in fns:
            # Check exclusion patterns
            if any(p in fn_name for p in exclude_patterns):
                continue

            callers = count_callers(autodream_dir, fn_name, filepath)

            if callers == 0:
                dead_helpers.append((filepath, fn_name))
                if verbose:
                    print(f"  DEAD: {Path(filepath).name}::{fn_name} — no production callers")
            elif verbose:
                print(f"  OK:   {Path(filepath).name}::{fn_name} — {callers} caller(s)")

    if dead_helpers:
        print(f"\n❌ {len(dead_helpers)} dead helper(s) found:")
        for filepath, fn_name in dead_helpers:
            print(f"  {Path(filepath).name}::{fn_name}")
        print("\nFix: wire each helper into a production caller, or remove it.")
        sys.exit(1)
    else:
        total = sum(len(fns) for fns in functions.values())
        print(f"✅ {total} public functions in autodream/ — all have ≥ 1 production caller")
        sys.exit(0)


if __name__ == "__main__":
    main()
