"""verify_dispatch — route parsed SelfReport into Epic 1/9 canonical writers.

Story 12.3: Dispatches a validated SelfReport to:
  - reinforce_entry (Story 9.1) for cited-hit entries
  - classify_trajectory_with_manifest + add_entry (Story 9.2) for new trajectories
  - add_entry (Epic 1) for failure records

All writes flow through canonical writers; no bypasses (Hard Invariant #1).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from plugins.verify_capture import SelfReport


def dispatch_self_report(report: "SelfReport", *, session_id: str) -> None:
    """Dispatch a validated SelfReport to canonical writers.

    Args:
        report: Pydantic-validated SelfReport instance.
        session_id: Current session ID for idempotency keys.
    """
    from lib.hermes_memory import (
        add_entry,
        reinforce_entry,
        build_manifest,
        classify_trajectory_with_manifest,
    )

    # 1. Process cited-hit citations → reinforce_entry per Story 9.1 AC3
    if report.match == "hit" and report.preflight_cited:
        for cited_id in report.preflight_cited:
            if not cited_id:
                continue
            try:
                reinforce_entry(
                    cited_id,
                    source="verify-cited-hit",
                    session_id=session_id,
                )
            except Exception as e:
                logger.warning("reinforce_entry(%s) failed: %s", cited_id, e)

    # 2. Process miss/unrelated → emit verify_citation telemetry (Story 9.3 input)
    if report.match in ("miss", "unrelated") and report.preflight_cited:
        _emit_verify_citation(
            session_id=session_id,
            cited_ids=report.preflight_cited,
            match=report.match,
        )

    # 3. Process new trajectories → manifest-dedup + add_entry
    if report.trajectories:
        try:
            manifest = build_manifest()
        except Exception as e:
            logger.warning("build_manifest failed: %s", e)
            manifest = ""

        for traj in report.trajectories:
            try:
                cls = classify_trajectory_with_manifest(traj.body, manifest)
            except Exception as e:
                logger.warning("classify_trajectory_with_manifest failed: %s", e)
                continue

            outcome = cls.get("action")
            if outcome == "reinforce":
                entry_id = cls.get("id", "")
                if entry_id:
                    try:
                        reinforce_entry(
                            entry_id,
                            source="trajectory-rematch",
                            session_id=session_id,
                        )
                    except Exception as e:
                        logger.warning("reinforce_entry(%s) failed: %s", entry_id, e)
                _emit_trajectory_outcome(
                    "reinforced-existing",
                    manifest_size=len(manifest) if manifest else 0,
                )
            elif outcome == "new":
                try:
                    add_entry(
                        type="trajectory",
                        body=traj.body,
                        source="agent-self-report",
                        category=traj.category,
                    )
                except Exception as e:
                    logger.warning("add_entry(trajectory) failed: %s", e)
                _emit_trajectory_outcome(
                    "new-entry",
                    manifest_size=len(manifest) if manifest else 0,
                )
            else:
                # Unknown action — log and skip
                logger.debug("Trajectory classification returned: %s", cls)

    # 4. Process failures (length-gated) → add_entry as trajectory
    for failure in report.failures:
        if len(failure.summary) < 50:
            continue  # sub-threshold; drop silently
        body = f"[{failure.category}] {failure.summary}"
        try:
            add_entry(
                type="trajectory",
                body=body,
                source="agent-failure",
                category=failure.category,
            )
        except Exception as e:
            logger.warning("add_entry(failure) failed: %s", e)


def _emit_verify_citation(
    *,
    session_id: str,
    cited_ids: list[str],
    match: str,
) -> None:
    """Emit a verify_citation telemetry row to the preflight log directory."""
    log_dir = Path(os.path.expanduser("~/.hermes/preflight/log"))
    log_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    row = {
        "event": "verify_citation",
        "session_id": session_id,
        "cited_ids": cited_ids,
        "match": match,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    try:
        with open(log_dir / f"{today}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except OSError as e:
        logger.warning("verify_citation write failed: %s", e)


def _emit_trajectory_outcome(outcome: str, *, manifest_size: int) -> None:
    """Emit a trajectory_outcome telemetry row to the observability directory."""
    obs_dir = Path(os.path.expanduser("~/.hermes/observability"))
    obs_dir.mkdir(parents=True, exist_ok=True)
    row = {
        "event": "trajectory_outcome",
        "outcome": outcome,
        "manifest_size": manifest_size,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    try:
        with open(obs_dir / "advisory.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except OSError as e:
        logger.warning("trajectory_outcome write failed: %s", e)
