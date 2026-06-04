"""CLI for bmad-evolve-command — offline command-body tuner.

Click-based CLI skeleton for the BMAD offline tuner.
"""

from __future__ import annotations

from pathlib import Path

import click


@click.group()
@click.version_option(version="0.1.0")
def main() -> None:
    """BMAD offline command-body tuner.

    Evolutionary optimization for BMAD command bodies using DSPy.
    """


@main.command()
@click.option("--story", "-s", required=True, type=click.Path(exists=True), help="Path to story.md")
@click.option("--command-body", "-c", required=True, type=click.Path(exists=True), help="Path to command_body.md")
@click.option("--project-context", "-p", type=click.Path(exists=True), default=None, help="Path to project_context.yaml")
@click.option("--output", "-o", type=click.Path(), default="./output", help="Output directory")
@click.option("--iterations", "-n", type=int, default=10, help="Number of optimization iterations")
@click.option("--eval-model", type=str, default="openai/gpt-4.1-mini", help="LiteLLM model for LLM judge")
def optimize(
    story: str,
    command_body: str,
    project_context: str | None,
    output: str,
    iterations: int,
    eval_model: str,
) -> None:
    """Optimize a command body for a given story."""
    click.echo(f"Story: {story}")
    click.echo(f"Command body: {command_body}")
    click.echo(f"Iterations: {iterations}")
    click.echo(f"Eval model: {eval_model}")
    click.echo("⚠ Not yet implemented — CLI skeleton only.")


@main.command()
@click.option("--trace-dir", "-t", required=True, type=click.Path(exists=True), help="Path to trace directory")
def score(trace_dir: str) -> None:
    """Score an existing trace using the composite judge."""
    from .importer import BMADTrace
    from .judge import CodeOutputJudge

    trace = BMADTrace.load(Path(trace_dir))
    click.echo(f"Loaded trace from {trace_dir}")
    click.echo(f"Story: {len(trace.story_md)} chars")
    click.echo(f"Diff: {len(trace.diff_patch)} chars")
    click.echo("⚠ Scoring not yet wired — CLI skeleton only.")


@main.command()
@click.option("--source", type=click.Choice(["hermes", "claude-code", "copilot", "all"]), default="all")
@click.option("--output", "-o", type=click.Path(), default="./traces")
def import_traces(source: str, output: str) -> None:
    """Import session data into BMAD trace format."""
    click.echo(f"Source: {source}")
    click.echo(f"Output: {output}")
    click.echo("⚠ Import not yet implemented — CLI skeleton only.")


if __name__ == "__main__":
    main()
