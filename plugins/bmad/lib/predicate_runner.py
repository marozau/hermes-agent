"""Predicate runner — execute verification predicates (Story 12.4).

Resolves predicate dotted paths, imports the module, calls the function,
and collects results.  Returns a list of (description, passed, reason)
tuples.
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import Any

from plugins.bmad.lib.spec_schema import CommandSpec, VerificationItem

logger = logging.getLogger(__name__)


def run_predicates(
    spec: CommandSpec,
    project_dir: Path,
    ctx: Any = None,
) -> list[dict[str, Any]]:
    """Run all predicates in a spec's verification list.

    Returns a list of dicts:
        {"description": str, "passed": bool | None, "reason": str}

    For items without a predicate (manual checks), passed=None.
    """
    results = []
    for item in spec.verification:
        if item.predicate is None:
            results.append({
                "description": item.description,
                "passed": None,
                "reason": "manual check — no predicate",
            })
            continue

        passed, reason = _call_predicate(item, project_dir, ctx)
        results.append({
            "description": item.description,
            "passed": passed,
            "reason": reason,
        })

    return results


def _call_predicate(
    item: VerificationItem,
    project_dir: Path,
    ctx: Any = None,
) -> tuple[bool | None, str]:
    """Import and call a single predicate function.

    Supports dotted paths like "predicates.dev_story.tests_pass" or
    "plugins.bmad.predicates.dev_story.tests_pass".
    """
    predicate_path = item.predicate
    if not predicate_path:
        return None, "no predicate"

    # Split into module path and function name
    parts = predicate_path.rsplit(".", 1)
    if len(parts) != 2:
        return None, f"invalid predicate path: {predicate_path}"

    module_path, func_name = parts

    # Try import with and without plugins.bmad. prefix
    module = None
    for prefix in ["", "plugins.bmad."]:
        try:
            module = importlib.import_module(prefix + module_path)
            break
        except ImportError:
            continue

    if module is None:
        return None, f"module not found: {module_path}"

    func = getattr(module, func_name, None)
    if func is None:
        return None, f"function not found: {func_name} in {module_path}"

    try:
        return func(project_dir=project_dir, ctx=ctx)
    except Exception as e:
        logger.warning("[predicate_runner] %s failed: %s", predicate_path, e)
        return None, f"predicate error: {e}"
