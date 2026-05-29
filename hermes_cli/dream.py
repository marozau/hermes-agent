"""hermes_cli.dream — CLI surface for the Auto-Dream substrate (Epic 4 / FR-13).

Implements `hermes dream {create|status|diff|apply|discard}` by wrapping
`lib.hermes_dream`. The library does the work; this module is argparse glue
+ output formatting only — no business logic.

Story 4.8 (deferred from initial Epic 4 ship): wires the dream verbs into
`hermes_cli/main.py` so they're discoverable via `hermes --help` alongside
`status`, `chat`, `curator`, etc.

Spec mapping:
- create  → lib.hermes_dream.create_dream_artifact     (FR-13, FR-14, NFR-14)
- status  → lib.hermes_dream.list_dreams               (FR-17)
- diff    → lib.hermes_dream.dream_diff                (FR-18)
- apply   → lib.hermes_dream.apply_dream               (FR-19, FR-20, FR-22, NFR-9, Hard #4/#9)
- discard → lib.hermes_dream.discard_dream             (FR-21, NFR-19)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Argparse registration (called from hermes_cli.main)
# ─────────────────────────────────────────────────────────────────────────────


def register_cli(dream_parser: argparse.ArgumentParser) -> None:
    """Wire the `hermes dream` subcommands onto an already-created subparser.

    Mirrors `hermes_cli.curator.register_cli`: main.py builds the parent
    parser via `subparsers.add_parser("dream", ...)`, then defers the verb
    wiring here so main.py stays slim and dream-specific behavior lives
    next to its handlers.
    """
    dream_sub = dream_parser.add_subparsers(
        dest="dream_command",
        metavar="<command>",
    )

    # ── create ────────────────────────────────────────────────────────────
    create_p = dream_sub.add_parser(
        "create",
        help="Create a staged dream artifact (proposals only; never mutates live state)",
        description=(
            "Generate a memory-consolidation dream under ~/.hermes/dreams/<dream_id>/. "
            "Produces manifest.json, REPORT.md, memory.patch, sources.jsonl. "
            "Always staged (FR-14) — apply via `hermes dream apply <id>` after review."
        ),
    )
    create_p.add_argument(
        "--scope",
        default="default",
        choices=["default", "memory", "skill", "board"],
        help="Dream scope (default: %(default)s). skill/board are V2 — currently no-ops.",
    )
    create_p.add_argument(
        "--memory-dir",
        help="Memory dir to read from (default: profile-aware via lib.hermes_memory).",
    )
    create_p.add_argument(
        "--dreams-dir",
        help="Override ~/.hermes/dreams/ (mostly for tests).",
    )
    create_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip LLM reflection; produce an empty-proposals artifact for plumbing tests.",
    )
    create_p.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of human-friendly summary.",
    )
    create_p.set_defaults(func=_cmd_create)

    # ── status ────────────────────────────────────────────────────────────
    status_p = dream_sub.add_parser(
        "status",
        help="List staged dreams with scope, age, and regression verdict",
    )
    status_p.add_argument(
        "--dreams-dir",
        help="Override ~/.hermes/dreams/ (mostly for tests).",
    )
    status_p.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of a formatted table.",
    )
    status_p.set_defaults(func=_cmd_status)

    # ── diff ──────────────────────────────────────────────────────────────
    diff_p = dream_sub.add_parser(
        "diff",
        help="Show REPORT.md + memory.patch + sources summary for a dream",
    )
    diff_p.add_argument("dream_id", help="The dream ID (ULID-style timestamp) to diff.")
    diff_p.add_argument(
        "--dreams-dir",
        help="Override ~/.hermes/dreams/ (mostly for tests).",
    )
    diff_p.set_defaults(func=_cmd_diff)

    # ── apply ─────────────────────────────────────────────────────────────
    apply_p = dream_sub.add_parser(
        "apply",
        help="Apply a dream's patches via the canonical writer (requires --accept)",
    )
    apply_p.add_argument("dream_id", help="The dream ID to apply.")
    apply_p.add_argument(
        "--accept",
        action="store_true",
        help=(
            "Required manual ack (Hard Invariant #4). Without --accept the "
            "command refuses to apply — review with `hermes dream diff` first."
        ),
    )
    apply_p.add_argument(
        "--only",
        metavar="GLOB",
        help="Filter proposals by fnmatch glob (e.g. '*preference*'). FR-20.",
    )
    apply_p.add_argument(
        "--force-recall",
        action="store_true",
        help=(
            "Override the recall-regression gate (Epic 5, FR-27/28). "
            "Requires --force-reason (≥10 chars)."
        ),
    )
    apply_p.add_argument(
        "--force-reason",
        default="",
        help="Justification for --force-recall (recorded in audit; ≥10 chars).",
    )
    apply_p.add_argument(
        "--memory-dir",
        help="Memory dir to write into (default: profile-aware).",
    )
    apply_p.add_argument(
        "--dreams-dir",
        help="Override ~/.hermes/dreams/ (mostly for tests).",
    )
    apply_p.add_argument(
        "--actor",
        help="Actor name recorded in the audit row (default: $USER).",
    )
    apply_p.set_defaults(func=_cmd_apply)

    # ── discard ───────────────────────────────────────────────────────────
    discard_p = dream_sub.add_parser(
        "discard",
        help="Remove a dream artifact and write a `discard` audit row (FR-21)",
    )
    discard_p.add_argument("dream_id", help="The dream ID to discard.")
    discard_p.add_argument(
        "--dreams-dir",
        help="Override ~/.hermes/dreams/ (mostly for tests).",
    )
    discard_p.add_argument(
        "--actor",
        help="Actor name recorded in the audit row (default: $USER).",
    )
    discard_p.set_defaults(func=_cmd_discard)

    # Default: show help when `hermes dream` is invoked bare.
    dream_parser.set_defaults(func=lambda args: dream_parser.print_help())


# ─────────────────────────────────────────────────────────────────────────────
# Handler dispatch (invoked by main.py via args.func(args))
# ─────────────────────────────────────────────────────────────────────────────


def cmd_dream(args: argparse.Namespace) -> None:
    """Top-level dispatcher for `hermes dream <verb>`.

    args.func is set by register_cli to the matching _cmd_* function. This
    wrapper exists so main.py can register `dream_parser.set_defaults(func=cmd_dream)`
    if it ever wants to handle pre-dispatch logic uniformly — for now it
    just delegates.
    """
    sub = getattr(args, "dream_command", None)
    if sub is None:
        # Argparse default printed help; nothing to do.
        return
    args.func(args)


def _resolve_actor(args: argparse.Namespace) -> Optional[str]:
    return getattr(args, "actor", None) or os.environ.get("USER")


# ─────────────────────────────────────────────────────────────────────────────
# Verb implementations — thin wrappers around lib.hermes_dream
# ─────────────────────────────────────────────────────────────────────────────


def _cmd_create(args: argparse.Namespace) -> None:
    from lib.hermes_dream import create_dream_artifact

    try:
        dream_id = create_dream_artifact(
            scope=args.scope,
            memory_dir=args.memory_dir,
            dreams_dir=args.dreams_dir,
            dry_run=args.dry_run,
        )
    except RuntimeError as exc:
        # Attestation pre-flight, soul-guardian carve-out misconfig, etc.
        # Surface to the user — these are configuration problems, not bugs.
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)

    dreams_dir = Path(
        args.dreams_dir or os.path.expanduser("~/.hermes/dreams")
    )
    artifact = dreams_dir / dream_id
    report = artifact / "REPORT.md"

    if args.json:
        print(json.dumps({
            "dream_id": dream_id,
            "artifact_dir": str(artifact),
            "report": str(report),
        }, indent=2))
        return

    print(f"Created dream: {dream_id}")
    print(f"  artifact:  {artifact}")
    print(f"  report:    {report}")
    print()
    print("Next: review with `hermes dream diff " + dream_id + "`")
    print("      apply with  `hermes dream apply " + dream_id + " --accept`")


def _cmd_status(args: argparse.Namespace) -> None:
    from lib.hermes_dream import list_dreams

    dreams = list_dreams(dreams_dir=args.dreams_dir)

    if args.json:
        print(json.dumps(dreams, indent=2, default=str))
        return

    if not dreams:
        print("No staged dreams.")
        print("Create one: `hermes dream create --dry-run`")
        return

    # Compact table — keeps it readable for the typical 1-10 dream case.
    # Field names match list_dreams() output: dream_id, scope, created,
    # regression, apply_eligible, applied.
    header = f"{'DREAM_ID':<28}  {'SCOPE':<10}  {'CREATED':<25}  {'RECALL':<12}  {'APPLIED':<8}"
    print(header)
    print("-" * len(header))
    for d in dreams:
        did = d.get("dream_id", "?")
        scope = d.get("scope", "?")
        created = (d.get("created") or "?")[:25]
        recall = d.get("regression", "skipped")
        applied = "yes" if d.get("applied") else "no"
        print(f"{did:<28}  {scope:<10}  {created:<25}  {recall:<12}  {applied:<8}")


def _cmd_diff(args: argparse.Namespace) -> None:
    from lib.hermes_dream import dream_diff

    try:
        out = dream_diff(args.dream_id, dreams_dir=args.dreams_dir)
    except FileNotFoundError:
        print(f"Error: dream '{args.dream_id}' not found.", file=sys.stderr)
        sys.exit(2)

    print(out)


def _cmd_apply(args: argparse.Namespace) -> None:
    from lib.hermes_dream import apply_dream

    try:
        result = apply_dream(
            args.dream_id,
            dreams_dir=args.dreams_dir,
            memory_dir=args.memory_dir,
            only=args.only,
            force_apply=args.accept,
            force_recall=args.force_recall,
            force_reason=args.force_reason,
            actor=_resolve_actor(args),
        )
    except FileNotFoundError:
        print(f"Error: dream '{args.dream_id}' not found.", file=sys.stderr)
        sys.exit(2)

    # Always emit structured JSON-friendly output — apply is the verb that
    # might be consumed by scripts (audit / verification).
    print(json.dumps(result, indent=2, default=str))

    status = result.get("status")
    if status == "refused":
        sys.exit(3)
    if status == "regression_blocked":
        sys.exit(4)


def _cmd_discard(args: argparse.Namespace) -> None:
    from lib.hermes_dream import discard_dream

    try:
        result = discard_dream(
            args.dream_id,
            dreams_dir=args.dreams_dir,
            actor=_resolve_actor(args),
        )
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)

    print(json.dumps(result, indent=2, default=str))
    # Discard is idempotent per FR-21 — `not_found` is success, exit 0
    # (implicit by not calling sys.exit).
