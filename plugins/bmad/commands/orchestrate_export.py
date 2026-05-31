"""CLI for bmad-orchestrate-export — export Prefect flow without running (Story 7.10).

Usage: hermes bmad-orchestrate-export <epic-number-or-path> [--output PATH]
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def handler(args) -> None:
    """Export an orchestrate run as a Prefect flow .py file."""
    import sys
    from plugins.bmad.lib.epic_anchor import parse_epic_file
    from plugins.bmad.lib.orchestrator import OrchestrateReport, OrchestrateFlags
    from plugins.bmad.lib.prefect_bridge import export_prefect_flow

    # Parse args
    epic_str = ""
    output_str = ""
    argv = args if isinstance(args, list) else str(args).split()
    i = 0
    while i < len(argv):
        tok = str(argv[i])
        if tok == "--output" and i + 1 < len(argv):
            i += 1
            output_str = str(argv[i])
        elif not tok.startswith("-") and not epic_str:
            epic_str = tok
        i += 1

    if not epic_str:
        print("Error: epic number or path required", file=sys.stderr)
        sys.exit(1)

    # Resolve epic path
    epic_path = _resolve_epic(epic_str)
    if not epic_path or not epic_path.exists():
        print(f"Error: epic not found: {epic_str}", file=sys.stderr)
        sys.exit(1)

    epic = parse_epic_file(epic_path)

    # Build a minimal report for the flow structure
    waves = epic.topological_waves()
    report = OrchestrateReport(
        epic_id=epic.id,
        total_stories=len(epic.stories),
        waves=waves,
        results={},
    )

    if not output_str:
        output_str = f"prefect-flow-epic-{epic.id}.py"

    output_path = Path(output_str)
    try:
        flow_path = export_prefect_flow(epic, report, output_path)
        print(f"✅ Prefect flow exported: {flow_path}")
        print(f"   Stories: {len(epic.stories)}, Waves: {len(waves)}")
        print(f"   Run: prefect deploy {flow_path}")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def _resolve_epic(epic_str: str) -> Path:
    """Resolve epic string to a file path."""
    # Direct path
    p = Path(epic_str)
    if p.exists():
        return p

    # Try as epic number in planning-artifacts
    candidates = [
        Path.cwd() / "planning-artifacts" / f"epics-stories-{epic_str}-*.md",
    ]
    import glob
    for pattern in candidates:
        matches = sorted(glob.glob(str(pattern)))
        if matches:
            return Path(matches[-1])

    return p


def main():
    import sys
    handler(sys.argv[1:] if len(sys.argv) > 1 else [])


if __name__ == "__main__":
    main()
