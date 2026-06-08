"""verify_dispatch — route parsed SelfReport into Epic 1/9 canonical writers.

Story 12.3: Dispatches a validated SelfReport to:
  - reinforce_entry (Story 9.1) for cited-hit entries
  - classify_trajectory_with_manifest + add_entry (Story 9.2) for new trajectories
  - add_entry (Epic 1) for failure records

All writes flow through canonical writers; no bypasses (Hard Invariant #1).
"""
from __future__ import annotations

import atexit
import concurrent.futures
import hashlib
import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

try:
    import fcntl
except ImportError:
    fcntl = None  # type: ignore[assignment]

from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from plugins.verify_capture import SelfReport

# F4: ThreadPoolExecutor for offloading trajectory/failure processing
_DISPATCH_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="verify-dispatch"
)
atexit.register(lambda: _DISPATCH_EXECUTOR.shutdown(wait=True, cancel_futures=False))

# F9: Content-hash dedup set (process-local; prevents duplicates on replay)
_SEEN_TRAJECTORY_HASHES: set[str] = set()
_SEEN_HASHES_LOCK = threading.Lock()


def _derive_project_role() -> tuple[str, str]:
    """F14: Derive project and role from HERMES_HOME and working directory."""
    home = get_hermes_home()
    role = "default"
    try:
        if home.parent.name == "profiles":
            role = home.name
    except Exception:
        pass
    project = "unknown"
    try:
        cwd = os.getcwd()
        project = os.path.basename(cwd) if cwd else "unknown"
    except Exception:
        pass
    return project, role


def _file_lock(file_obj) -> None:
    """F13: Acquire an exclusive fcntl.flock on a file object (best-effort)."""
    if fcntl is None:
        return
    try:
        fcntl.flock(file_obj.fileno(), fcntl.LOCK_EX)
    except Exception:
        pass


def _file_unlock(file_obj) -> None:
    """F13: Release the fcntl.flock on a file object (best-effort)."""
    if fcntl is None:
        return
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
    from autodream.memory import reinforce_entry

    # F7: Derive project/role in main thread (os.getcwd() may be thread-local)
    project, role = _derive_project_role()

    # 1. Process cited-hit citations → reinforce_entry per Story 9.1 AC3
    if report.match == "hit" and report.preflight_cited:
        for cited_id in report.preflight_cited:
            if not cited_id:
                continue
            try:
                reinforce_entry(cited_id, source="verify-cited-hit", session_id=session_id)
            except Exception as e:
                logger.warning("reinforce_entry(%s) failed: %s", cited_id, e)

    # 2. Process miss/unrelated → emit verify_citation telemetry (Story 9.3 input)
    if report.match in ("miss", "unrelated") and report.preflight_cited:
        non_empty_ids = [cid for cid in report.preflight_cited if cid]
        if non_empty_ids:
            _emit_verify_citation(session_id=session_id, cited_ids=non_empty_ids,
                                  match=report.match, project=project, role=role)

    # F4: Offload trajectory + failure processing to background thread
    if report.trajectories or report.failures:
        fut = _DISPATCH_EXECUTOR.submit(
            _dispatch_trajectories_and_failures, report, session_id, project, role
        )
        # F4: Log any unhandled exception from background dispatch
        fut.add_done_callback(lambda f: (
            exc := f.exception()
        ) and logger.warning("Background dispatch failed: %s", exc))


def _dispatch_trajectories_and_failures(
    report: "SelfReport", session_id: str, project: str, role: str
) -> None:
    """Process trajectories and failures (F4: offloaded to background thread)."""
    from autodream.memory import (
        add_entry, reinforce_entry, build_manifest, classify_trajectory_with_manifest,
    )

    # 3. Process new trajectories → manifest-dedup + add_entry
    if report.trajectories:
        try:
            manifest = build_manifest()
        except Exception as e:
            logger.warning("build_manifest failed: %s", e)
            manifest = ""

        for traj in report.trajectories:
            traj_hash = hashlib.sha256(traj.body.encode("utf-8")).hexdigest()

            try:
                cls = classify_trajectory_with_manifest(traj.body, manifest)
            except Exception as e:
                logger.warning("classify_trajectory_with_manifest failed: %s", e)
                continue

            # F9: Dedup check BEFORE write — prevent duplicate writes, not duplicate classifies
            with _SEEN_HASHES_LOCK:
                if traj_hash in _SEEN_TRAJECTORY_HASHES:
                    logger.debug("Skipping duplicate write (hash=%s)", traj_hash[:12])
                    continue
                _SEEN_TRAJECTORY_HASHES.add(traj_hash)

            manifest_len = len(manifest) if manifest else 0
            outcome = cls.get("action")

            if outcome == "reinforce":
                entry_id = cls.get("id", "")
                if entry_id:
                    try:
                        reinforce_entry(entry_id, source="trajectory-rematch",
                                        session_id=session_id)
                    except Exception as e:
                        logger.warning("reinforce_entry(%s) failed: %s", entry_id, e)
                _emit_trajectory_outcome("reinforced-existing", manifest_size=manifest_len,
                                         session_id=session_id, project=project, role=role)
            elif outcome == "new":
                try:
                    add_entry(type="trajectory", body=traj.body,
                              source="agent-self-report", category=traj.category)
                except Exception as e:
                    logger.warning("add_entry(trajectory) failed: %s", e)
                _emit_trajectory_outcome("new-entry", manifest_size=manifest_len,
                                         session_id=session_id, project=project, role=role)
            else:
                # F2: classifier error or unknown action → write as new trajectory (fail-safe)
                logger.warning(
                    "Trajectory classifier returned %s (reason=%s); writing as new entry",
                    outcome, cls.get("reason", "unknown"),
                )
                try:
                    add_entry(type="trajectory", body=traj.body,
                              source="agent-self-report", category=traj.category)
                except Exception as e:
                    logger.warning("add_entry(fallback) failed: %s", e)
                _emit_trajectory_outcome("classifier-failed", manifest_size=manifest_len,
                                         session_id=session_id, project=project, role=role)

    # 4. Process failures (length-gated) → add_entry as trajectory
    for failure in report.failures:
        if len(failure.summary) < 50:
            continue
        body = f"[{failure.category}] {failure.summary}"
        try:
            add_entry(type="trajectory", body=body, source="agent-failure",
                      category=failure.category)
        except Exception as e:
            logger.warning("add_entry(failure) failed: %s", e)


def _emit_verify_citation(
    *, session_id: str, cited_ids: list[str], match: str,
    project: str = "", role: str = "",
) -> None:
    """Emit a verify_citation telemetry row to the preflight log directory."""
    if not project:
        project, role = _derive_project_role()
    log_dir = get_hermes_home() / "preflight" / "log"
    log_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    row = {
        "event": "verify_citation", "session_id": session_id,
        "cited_ids": cited_ids, "match": match,
        "project": project, "role": role,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    try:
        with open(log_dir / f"{today}.jsonl", "a", encoding="utf-8") as f:
            _file_lock(f)
            try:
                f.write(json.dumps(row) + "\n")
            finally:
                _file_unlock(f)
    except OSError as e:
        logger.warning("verify_citation write failed: %s", e)


def _emit_trajectory_outcome(
    outcome: str, *, manifest_size: int, session_id: str = "",
    project: str = "", role: str = "",
) -> None:
    """Emit a trajectory_outcome telemetry row to the observability directory."""
    if not project:
        project, role = _derive_project_role()
    obs_dir = get_hermes_home() / "observability"
    obs_dir.mkdir(parents=True, exist_ok=True)
    row = {
        "event": "trajectory_outcome", "outcome": outcome,
        "manifest_size": manifest_size, "session_id": session_id,
        "project": project, "role": role,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    try:
        with open(obs_dir / "advisory.jsonl", "a", encoding="utf-8") as f:
            _file_lock(f)
            try:
                f.write(json.dumps(row) + "\n")
            finally:
                _file_unlock(f)
    except OSError as e:
        logger.warning("trajectory_outcome write failed: %s", e)
