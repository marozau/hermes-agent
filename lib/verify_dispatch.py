"""verify_dispatch — route parsed SelfReport into Epic 1/9 canonical writers.

Story 12.3: Dispatches a validated SelfReport to:
  - reinforce_entry (Story 9.1) for cited-hit entries
  - classify_trajectory_with_manifest + add_entry (Story 9.2) for new trajectories
  - add_entry (Epic 1) for failure records

All writes flow through canonical writers; no bypasses (Hard Invariant #1).
"""
from __future__ import annotations

import concurrent.futures
import fcntl
import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from plugins.verify_capture import SelfReport

# F4: ThreadPoolExecutor for offloading trajectory/failure processing
_DISPATCH_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="verify-dispatch"
)

# F9: Content-hash dedup set (process-local; prevents duplicates on replay)
_SEEN_TRAJECTORY_HASHES: set[str] = set()


def _derive_project_role() -> tuple[str, str]:
    """F14: Derive project and role from HERMES_HOME and working directory.

    Uses hermes_constants.get_hermes_home() instead of hardcoded ``~/.hermes``.
    Role is derived from the profile name when HERMES_HOME is under
    ``profiles/<name>/``; otherwise defaults to ``"default"``.
    Project is derived from the current working directory basename.
    """
    home = get_hermes_home()
    # Role: use profile name if under profiles/, otherwise "default"
    role = "default"
    try:
        if home.parent.name == "profiles":
            role = home.name
    except Exception:
        pass
    # Project: derive from cwd basename
    project = "unknown"
    try:
        cwd = os.getcwd()
        project = os.path.basename(cwd) if cwd else "unknown"
    except Exception:
        pass
    return project, role


def _file_lock(file_obj) -> None:
    """F13: Acquire an exclusive fcntl.flock on a file object (best-effort)."""
    try:
        fcntl.flock(file_obj.fileno(), fcntl.LOCK_EX)
    except Exception:
        pass


def _file_unlock(file_obj) -> None:
    """F13: Release the fcntl.flock on a file object (best-effort)."""
    try:
        fcntl.flock(file_obj.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass


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
        # F6: Filter empty cited_ids before emitting
        non_empty_ids = [cid for cid in report.preflight_cited if cid]
        if non_empty_ids:
            _emit_verify_citation(
                session_id=session_id,
                cited_ids=non_empty_ids,
                match=report.match,
            )

    # F4: Offload trajectory + failure processing to background thread.
    # cited-hit and telemetry are fast (local disk) — keep inline.
    # trajectories and failures involve LLM calls — offload.
    if report.trajectories or report.failures:
        _DISPATCH_EXECUTOR.submit(
            _dispatch_trajectories_and_failures, report, session_id
        )


def _dispatch_trajectories_and_failures(report: "SelfReport", session_id: str) -> None:
    """Process trajectories and failures (F4: offloaded to background thread)."""
    from lib.hermes_memory import (
        add_entry,
        reinforce_entry,
        build_manifest,
        classify_trajectory_with_manifest,
    )

    # 3. Process new trajectories → manifest-dedup + add_entry
    if report.trajectories:
        try:
            manifest = build_manifest()
        except Exception as e:
            logger.warning("build_manifest failed: %s", e)
            manifest = ""

        for traj in report.trajectories:
            # F9: Content-hash dedup — skip duplicates on replay
            traj_hash = hashlib.sha256(traj.body.encode("utf-8")).hexdigest()
            if traj_hash in _SEEN_TRAJECTORY_HASHES:
                logger.debug("Skipping duplicate trajectory (hash=%s)", traj_hash[:12])
                continue
            _SEEN_TRAJECTORY_HASHES.add(traj_hash)

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
                    session_id=session_id,
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
                    session_id=session_id,
                )
            else:
                # F2: classifier error or unknown action → write as new trajectory
                # (fail-safe; don't lose the pattern)
                # F5: WARNING level (was debug before)
                logger.warning(
                    "Trajectory classifier returned %s (reason=%s); writing as new entry",
                    outcome, cls.get("reason", "unknown"),
                )
                try:
                    add_entry(
                        type="trajectory",
                        body=traj.body,
                        source="agent-self-report",
                        category=traj.category,
                    )
                except Exception as e:
                    logger.warning("add_entry(fallback) failed: %s", e)
                _emit_trajectory_outcome(
                    "classifier-failed",
                    manifest_size=len(manifest) if manifest else 0,
                    session_id=session_id,
                )

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
    # F14: Derive project/role from HERMES_HOME, not hardcoded env vars
    project, role = _derive_project_role()
    log_dir = get_hermes_home() / "preflight" / "log"
    log_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    row = {
        "event": "verify_citation",
        "session_id": session_id,
        "cited_ids": cited_ids,
        "match": match,
        "project": project,
        "role": role,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    try:
        # F13: fcntl.flock on telemetry file writes
        with open(log_dir / f"{today}.jsonl", "a", encoding="utf-8") as f:
            _file_lock(f)
            try:
                f.write(json.dumps(row) + "\n")
            finally:
                _file_unlock(f)
    except OSError as e:
        logger.warning("verify_citation write failed: %s", e)


def _emit_trajectory_outcome(
    outcome: str,
    *,
    manifest_size: int,
    session_id: str = "",
) -> None:
    """Emit a trajectory_outcome telemetry row to the observability directory."""
    # F14: Derive project/role from HERMES_HOME, not hardcoded env vars
    project, role = _derive_project_role()
    obs_dir = get_hermes_home() / "observability"
    obs_dir.mkdir(parents=True, exist_ok=True)
    row = {
        "event": "trajectory_outcome",
        "outcome": outcome,
        "manifest_size": manifest_size,
        "session_id": session_id,
        "project": project,
        "role": role,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    try:
        # F13: fcntl.flock on telemetry file writes
        with open(obs_dir / "advisory.jsonl", "a", encoding="utf-8") as f:
            _file_lock(f)
            try:
                f.write(json.dumps(row) + "\n")
            finally:
                _file_unlock(f)
    except OSError as e:
        logger.warning("trajectory_outcome write failed: %s", e)
