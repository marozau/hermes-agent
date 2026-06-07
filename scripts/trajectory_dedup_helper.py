#!/usr/bin/env python3
"""trajectory_dedup_helper — Story 9.2 manifest-based dedup for trajectory recorder.

When the verify/trajectory-memory skill wants to record a failure pattern,
this helper:
1. Builds a MANIFEST of existing trajectory entries
2. Classifies the pattern against the manifest via LLM
3. Dispatches: reinforce existing OR create new entry
4. Emits trajectory_outcome telemetry

Usage:
    python trajectory_dedup_helper.py "docker build fails with exit code 137"
    python trajectory_dedup_helper.py "k3d cluster timeout" --session-id abc123
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from autodream.memory import (
    build_manifest, classify_trajectory_with_manifest, reinforce_entry, add_entry,
)

def _emit_telemetry(outcome: str, manifest_size: int, entry_id: str = "") -> None:
    """Emit trajectory_outcome telemetry row to preflight log."""
    import os
    from autodream._paths import resolve_hermes_home
    hermes_home = str(resolve_hermes_home())
    log_dir = Path(hermes_home) / "preflight" / "log"
    log_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    row = json.dumps({
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": "trajectory_outcome",
        "trajectory_outcome": outcome,
        "manifest_size": manifest_size,
        "entry_id": entry_id,
    }, ensure_ascii=False, sort_keys=True) + "\n"
    fd = os.open(str(log_dir / f"{today}.jsonl"), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, row.encode("utf-8"))
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("failure_pattern", help="The failure pattern text to classify")
    parser.add_argument("--session-id", default="", help="Session ID for idempotency")
    args = parser.parse_args()

    # 1. Build manifest
    manifest = build_manifest()
    manifest_lines = [l for l in manifest.strip().split("\n") if l.startswith("[")]
    manifest_size = len(manifest_lines)

    # 2. Classify
    result = classify_trajectory_with_manifest(args.failure_pattern, manifest)

    # 3. Dispatch
    if result["action"] == "reinforce":
        entry_id = result["id"]
        reinforce_entry(
            entry_id,
            source="trajectory-rematch",
            session_id=args.session_id,
        )
        _emit_telemetry("reinforced-existing", manifest_size, entry_id)
        print(f"trajectory_outcome: reinforced-existing (id={entry_id})")

    elif result["action"] == "new":
        entry_id = add_entry(
            type=result.get("type", "trajectory"),
            body=result["body"],
            source="trajectory-recorder",
        )
        _emit_telemetry("new-entry", manifest_size, entry_id)
        print(f"trajectory_outcome: new-entry (id={entry_id})")

    else:
        # Fail-open: treat as new entry
        print(f"trajectory_dedup: classification failed ({result.get('reason', 'unknown')}), "
              f"falling back to new entry", file=sys.stderr)
        entry_id = add_entry(
            type="trajectory",
            body=args.failure_pattern,
            source="trajectory-recorder-fallback",
        )
        _emit_telemetry("new-entry-fallback", manifest_size, entry_id)
        print(f"trajectory_outcome: new-entry-fallback (id={entry_id})")


if __name__ == "__main__":
    main()
