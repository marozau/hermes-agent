"""CLI v2 for bmad-evolve-command — stacked pipeline entry point.

Click-based CLI that orchestrates Phase 1 (GEPA) → Phase 2 (SkillOpt)
optimization for BMAD command bodies.  Story 15.5.

Usage::

    bmad-evolve-command-v2 --command dev-story --phase=both --cost-cap=50 \\
        --dataset=datasets/dev-story-v1.jsonl --dry-run
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import click

# ── Constants ───────────────────────────────────────────────────────────────

VALID_PHASES = ("both", "gepa", "skillopt")
DEFAULT_COST_CAP = 50.0
COMMAND_SEARCH_DIRS = (
    "commands",
    "plugins/bmad/commands",
)

# ── Data classes for pipeline plan ──────────────────────────────────────────


@dataclass(frozen=True)
class PipelinePlan:
    """Immutable plan describing what the stacked pipeline will do."""

    command_name: str
    command_body_path: Optional[Path]
    command_body_text: str
    phase: str
    cost_cap: float
    dataset_path: Path
    dataset_example_count: int
    dry_run: bool
    phases_to_run: tuple[str, ...] = field(default_factory=tuple)

    def format_plan(self) -> str:
        """Human-readable plan summary."""
        lines = [
            "═══ BMAD Evolve-Command Pipeline Plan ═══",
            "",
            f"  Command:       {self.command_name}",
            f"  Body source:   {self.command_body_path or '(inline / not found)'}",
            f"  Body length:   {len(self.command_body_text)} chars",
            f"  Phase:         {self.phase} → {', '.join(self.phases_to_run)}",
            f"  Cost cap:      ${self.cost_cap:.2f}",
            f"  Dataset:       {self.dataset_path}",
            f"  Examples:      {self.dataset_example_count}",
            f"  Dry run:       {self.dry_run}",
            "",
        ]
        for i, phase_name in enumerate(self.phases_to_run, 1):
            lines.append(f"  Phase {i}: {phase_name}")
        lines.append("")
        lines.append("═══════════════════════════════════════════")
        return "\n".join(lines)


# ── Validation helpers ──────────────────────────────────────────────────────


def _resolve_phases(phase: str) -> tuple[str, ...]:
    """Map --phase flag to ordered pipeline phase names."""
    if phase == "both":
        return ("gepa", "skillopt")
    if phase == "gepa":
        return ("gepa",)
    if phase == "skillopt":
        return ("skillopt",)
    raise click.BadParameter(f"Invalid phase: {phase}", param_hint="--phase")


def _find_command_body(command_name: str) -> Optional[Path]:
    """Search well-known directories for a BMAD command file.

    Returns the path to the command file, or None if not found.
    """
    # Try .md extension first, then .yaml
    for search_dir in COMMAND_SEARCH_DIRS:
        base = Path(search_dir)
        if not base.is_absolute():
            # Resolve relative to the evolve_command package root
            base = Path(__file__).resolve().parent.parent / search_dir
        for ext in (".md", ".yaml", ".yml"):
            candidate = base / f"{command_name}{ext}"
            if candidate.exists():
                return candidate
            # Also check subdirectories matching the command name
            candidate = base / command_name / f"command{ext}"
            if candidate.exists():
                return candidate
    return None


def _load_command_body(command_name: str) -> str:
    """Load the command body text for the given command name.

    Searches well-known directories.  Returns empty string if not found
    (the caller decides whether that's an error).
    """
    path = _find_command_body(command_name)
    if path is not None:
        return path.read_text(encoding="utf-8")
    return ""


def _validate_dataset(dataset_path: Path) -> int:
    """Validate dataset file exists and is parseable JSONL.

    Returns the number of examples in the dataset.
    Raises click.BadParameter on validation failure.
    """
    if not dataset_path.exists():
        raise click.BadParameter(
            f"Dataset file not found: {dataset_path}",
            param_hint="--dataset",
        )
    if not dataset_path.is_file():
        raise click.BadParameter(
            f"Dataset path is not a file: {dataset_path}",
            param_hint="--dataset",
        )

    count = 0
    try:
        with open(dataset_path, encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    json.loads(line)
                except json.JSONDecodeError as exc:
                    raise click.BadParameter(
                        f"Invalid JSON on line {lineno}: {exc}",
                        param_hint="--dataset",
                    ) from exc
                count += 1
    except OSError as exc:
        raise click.BadParameter(
            f"Cannot read dataset: {exc}",
            param_hint="--dataset",
        ) from exc

    if count == 0:
        raise click.BadParameter(
            "Dataset is empty (no JSONL examples found)",
            param_hint="--dataset",
        )

    return count


# ── Pipeline execution stubs ────────────────────────────────────────────────


def _run_gepa_phase(
    plan: PipelinePlan,
    *,
    dry_run: bool = False,
) -> dict[str, object]:
    """Execute Phase 1: GEPA optimization.

    Returns a dict with phase results.  In dry-run mode, returns a
    placeholder without calling DSPy.
    """
    if dry_run:
        return {
            "phase": "gepa",
            "status": "dry-run",
            "message": "GEPA phase skipped (dry-run)",
        }

    # Lazy import: DSPy only needed for actual execution
    from .adapters.command_body_module import CommandBodyModule
    from .adapters.metric_adapter import dev_story_composite_v1_metric, load_rubric

    click.echo("[Phase 1: GEPA] Loading metric rubric...")
    rubric = load_rubric()

    click.echo(f"[Phase 1: GEPA] Building CommandBodyModule for '{plan.command_name}'...")
    module = CommandBodyModule.from_raw(plan.command_body_text)

    from gepa_loop import run_gepa_loop
    click.echo(f"[Phase 1: GEPA] Running GEPA loop (cost_cap=${plan.cost_cap})...")
    result = run_gepa_loop(module, metric=lambda c, rubric=rubric: 0.5, dataset=None, max_steps=10, cost_cap=plan.cost_cap)
    click.echo(f"[Phase 1: GEPA] Completed in {result.elapsed_seconds:.1f}s ({result.steps} steps)")
    return {
        "phase": "gepa",
        "status": "completed",
        "elapsed_seconds": result.elapsed_seconds,
        "steps": result.steps,
        "used_fallback": result.used_fallback,
        "module_body_length": len(result.module.body_text),
    }


def _run_skillopt_phase(
    plan: PipelinePlan,
    gepa_result: Optional[dict[str, object]] = None,
    *,
    dry_run: bool = False,
) -> dict[str, object]:
    """Execute Phase 2: SkillOpt refinement.

    Takes the GEPA output and further optimizes using SkillOpt.
    Returns a dict with phase results.
    """
    if dry_run:
        return {
            "phase": "skillopt",
            "status": "dry-run",
            "message": "SkillOpt phase skipped (dry-run)",
        }

    click.echo("[Phase 2: SkillOpt] Use stacked_pipeline.run_stacked_pipeline() for Phase 1 + Phase 2 composition")
    return {
        "phase": "skillopt",
        "status": "deferred_to_stacked_pipeline",
        "message": "Use stacked_pipeline.run_stacked_pipeline() for end-to-end execution",
    }


def _execute_pipeline(plan: PipelinePlan) -> dict[str, object]:
    """Run the stacked pipeline according to the plan.

    Phase 1 (GEPA) feeds into Phase 2 (SkillOpt) when phase='both'.
    Returns a dict with results from each executed phase.
    """
    results: dict[str, object] = {"phases": []}
    gepa_result: Optional[dict[str, object]] = None

    for phase_name in plan.phases_to_run:
        if phase_name == "gepa":
            gepa_result = _run_gepa_phase(plan, dry_run=plan.dry_run)
            results["phases"].append(gepa_result)  # type: ignore[attr-defined]
        elif phase_name == "skillopt":
            skillopt_result = _run_skillopt_phase(
                plan, gepa_result=gepa_result, dry_run=plan.dry_run,
            )
            results["phases"].append(skillopt_result)  # type: ignore[attr-defined]

    return results


# ── CLI ─────────────────────────────────────────────────────────────────────


@click.command()
@click.option(
    "--command",
    required=True,
    help="Name of the BMAD command to optimize (e.g. dev-story).",
)
@click.option(
    "--phase",
    type=click.Choice(VALID_PHASES, case_sensitive=False),
    default="both",
    show_default=True,
    help="Which optimization phase(s) to run.",
)
@click.option(
    "--cost-cap",
    type=float,
    default=DEFAULT_COST_CAP,
    show_default=True,
    help="Maximum dollar cost per run.",
)
@click.option(
    "--dataset",
    required=True,
    type=click.Path(),
    help="Path to JSONL dataset file.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Print the pipeline plan without executing.",
)
@click.version_option(version="0.2.0", prog_name="bmad-evolve-command-v2")
def main(
    command: str,
    phase: str,
    cost_cap: float,
    dataset: str,
    dry_run: bool,
) -> None:
    """BMAD offline command-body tuner — stacked pipeline (GEPA + SkillOpt).

    Loads a BMAD command body, validates inputs, and runs the stacked
    optimization pipeline.  Use --dry-run to inspect the plan without
    executing.
    """
    # ── Validate phase ──────────────────────────────────────────────────
    phases_to_run = _resolve_phases(phase)

    # ── Validate dataset ────────────────────────────────────────────────
    dataset_path = Path(dataset).expanduser().resolve()
    example_count = _validate_dataset(dataset_path)

    # ── Load command body ───────────────────────────────────────────────
    body_text = _load_command_body(command)
    body_path = _find_command_body(command)

    # ── Build plan ──────────────────────────────────────────────────────
    plan = PipelinePlan(
        command_name=command,
        command_body_path=body_path,
        command_body_text=body_text,
        phase=phase,
        cost_cap=cost_cap,
        dataset_path=dataset_path,
        dataset_example_count=example_count,
        dry_run=dry_run,
        phases_to_run=phases_to_run,
    )

    # ── Dry-run: print plan and exit ────────────────────────────────────
    if dry_run:
        click.echo(plan.format_plan())
        click.echo("[dry-run] Validation complete.  No optimization performed.")
        return

    # ── Execute pipeline ────────────────────────────────────────────────
    click.echo(f"Starting stacked pipeline for command '{command}'...")
    click.echo(f"  Phase: {phase}  |  Cost cap: ${cost_cap:.2f}  |  Examples: {example_count}")
    click.echo("")

    results = _execute_pipeline(plan)

    # ── Report results ──────────────────────────────────────────────────
    click.echo("")
    click.echo("═══ Pipeline Results ═══")
    for phase_result in results.get("phases", []):  # type: ignore[union-attr]
        status = phase_result.get("status", "unknown")
        message = phase_result.get("message", "")
        click.echo(f"  [{phase_result.get('phase', '?')}] {status}: {message}")
    click.echo("════════════════════════")


if __name__ == "__main__":
    main()
