"""CLI for bmad-evolve-command — offline command-body tuner.

Click-based CLI for BMAD offline tuner. Story 13.7: implements
the GEPA optimization loop with budget enforcement.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click


@click.group()
@click.version_option(version="0.1.0")
def main() -> None:
    """BMAD offline command-body tuner (GEPA + DSPy)."""


@main.command()
@click.option("--command", required=True, help="Command to optimize (e.g. dev-story)")
@click.option("--dataset", required=True, type=click.Path(exists=True), help="Path to dataset directory")
@click.option("--budget", default=200, type=int, help="Max LLM judge calls")
@click.option("--cap", default=50, type=int, help="Max traces to use")
@click.option("--seed", default=42, type=int, help="Random seed for train/test split")
@click.option("--dry-run", is_flag=True, help="Validate inputs without running optimizer")
@click.option("--eval-model", default="gpt-4o", help="Model for LLM judge")
def optimize(command: str, dataset: str, budget: int, cap: int, seed: int, dry_run: bool, eval_model: str) -> None:
    """Run GEPA optimization on a command body.

    Loads traces from DATASET, optimizes the COMMAND body against
    the dev_story_composite_v1 metric, and produces a report directory.
    """
    from .judge import CodeOutputJudge, check_hard_gates

    dataset_path = Path(dataset)
    if not dataset_path.is_dir():
        click.echo(f"Error: {dataset} is not a directory", err=True)
        sys.exit(1)

    # Load metric
    metric_path = Path(__file__).parent / "metrics" / "dev_story_composite_v1.yaml"
    if not metric_path.exists():
        click.echo(f"Error: metric file not found: {metric_path}", err=True)
        sys.exit(1)

    import yaml
    metric = yaml.safe_load(metric_path.read_text())

    click.echo(f"Command: {command}")
    click.echo(f"Dataset: {dataset_path}")
    click.echo(f"Budget: {budget} LLM calls")
    click.echo(f"Cap: {cap} traces")
    click.echo(f"Seed: {seed}")
    click.echo(f"Eval model: {eval_model}")
    click.echo(f"Metric: {metric['name']} (frozen {metric.get('freeze_date', 'N/A')})")

    if dry_run:
        click.echo("\n[dry-run] Validation complete. No optimization performed.")
        return

    click.echo("\n[optimize] GEPA optimization not yet implemented — Story 13.7 carry-forward to Epic 13.1")
    click.echo("  Foundation (Stories 13.1-13.5) is complete.")
    click.echo("  See reports/ for future output.")


@main.command()
@click.option("--source", type=click.Choice(["hermes", "claude", "copilot"]), default="hermes")
@click.option("--output", required=True, type=click.Path(), help="Output directory for traces")
@click.option("--limit", default=50, type=int, help="Max traces to import")
def import_traces(source: str, output: str, limit: int) -> None:
    """Import session traces from external tools."""
    from .importer import build_trace, BMADTrace

    output_path = Path(output)
    output_path.mkdir(parents=True, exist_ok=True)

    click.echo(f"Source: {source}")
    click.echo(f"Output: {output_path}")
    click.echo(f"Limit: {limit}")

    if source == "hermes":
        sessions_dir = Path.home() / ".hermes" / "sessions"
        if not sessions_dir.exists():
            click.echo(f"Error: {sessions_dir} not found", err=True)
            sys.exit(1)

        import json
        files = sorted(sessions_dir.glob("*.jsonl"))[:limit]
        click.echo(f"Found {len(files)} session files")

        for f in files:
            click.echo(f"  Processing {f.name}...")
            # Parse JSONL session
            try:
                lines = [json.loads(line) for line in f.read_text().splitlines() if line.strip()]
                click.echo(f"    {len(lines)} messages")
            except (json.JSONDecodeError, OSError) as e:
                click.echo(f"    Skipped: {e}")

        click.echo(f"\n[import] Dataset builder not yet fully implemented — Story 13.6 carry-forward to Epic 13.1")
    else:
        click.echo(f"[import] Source '{source}' not yet implemented")


if __name__ == "__main__":
    main()
