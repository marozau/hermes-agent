"""Telemetry for orchestrate runs (Story 7.9).

Collects 12 per-worker metrics and writes to ~/.hermes/observability/orchestrate.jsonl.
Opt-out via --no-telemetry flag.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_OBSERVABILITY_DIR = Path.home() / ".hermes" / "observability"
DEFAULT_OUTPUT_FILE = "orchestrate.jsonl"


@dataclass
class WorkerMetrics:
    """12 metrics collected per worker story execution."""
    story_id: str = ""
    epic_id: str = ""
    wave: int = 0
    attempt: int = 1
    status: str = "pending"           # 1. pending|running|succeeded|failed|halted
    start_time: str = ""              # 2. ISO timestamp
    end_time: str = ""                # 3. ISO timestamp
    duration_seconds: float = 0.0     # 4. Wall clock
    predicates_total: int = 0         # 5. Total predicates
    predicates_passed: int = 0        # 6. Passed predicates
    predicates_failed: int = 0        # 7. Failed predicates
    delegation_task_id: str = ""      # 8. Hermes task ID
    delegation_model: str = ""        # 9. Model used
    error_message: str = ""           # 10. Error if failed
    retry_of_attempt: int = 0         # 11. Original attempt if retry
    worktree: str = ""                # 12. Target worktree

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TelemetrySession:
    """Aggregates metrics for an orchestrate run."""
    run_id: str = ""
    epic_id: str = ""
    started_at: str = ""
    workers: list[WorkerMetrics] = field(default_factory=list)
    _output_path: Optional[Path] = None
    _enabled: bool = True

    def __post_init__(self):
        if not self.started_at:
            self.started_at = datetime.now(timezone.utc).isoformat()
        if not self.run_id:
            self.run_id = f"run-{int(time.time())}"

    def set_output_path(self, path: Path) -> None:
        self._output_path = path

    def start_worker(self, story_id: str, wave: int, attempt: int = 1,
                     worktree: str = "", retry_of: int = 0) -> WorkerMetrics:
        """Begin tracking a worker."""
        wm = WorkerMetrics(
            story_id=story_id,
            epic_id=self.epic_id,
            wave=wave,
            attempt=attempt,
            status="running",
            start_time=datetime.now(timezone.utc).isoformat(),
            worktree=worktree,
            retry_of_attempt=retry_of,
        )
        self.workers.append(wm)
        return wm

    def finish_worker(self, wm: WorkerMetrics, status: str,
                      error: str = "", delegation_result: Optional[dict] = None) -> None:
        """Record worker completion."""
        wm.status = status
        wm.end_time = datetime.now(timezone.utc).isoformat()
        if wm.start_time:
            try:
                start = datetime.fromisoformat(wm.start_time)
                wm.duration_seconds = (datetime.now(timezone.utc) - start).total_seconds()
            except (ValueError, TypeError):
                pass
        wm.error_message = error
        if delegation_result:
            wm.delegation_task_id = delegation_result.get("task_id", "")
            wm.delegation_model = delegation_result.get("model", "")

    def record_predicates(self, wm: WorkerMetrics, total: int, passed: int, failed: int) -> None:
        wm.predicates_total = total
        wm.predicates_passed = passed
        wm.predicates_failed = failed

    def flush(self) -> None:
        """Write all metrics to JSONL file."""
        if not self._enabled:
            return

        output_dir = self._output_path or (DEFAULT_OBSERVABILITY_DIR / DEFAULT_OUTPUT_FILE)
        output_dir.parent.mkdir(parents=True, exist_ok=True)

        try:
            with open(output_dir, "a", encoding="utf-8") as f:
                for wm in self.workers:
                    record = wm.as_dict()
                    record["run_id"] = self.run_id
                    record["flushed_at"] = datetime.now(timezone.utc).isoformat()
                    f.write(json.dumps(record) + "\n")
            logger.info("[telemetry] Flushed %d worker metrics to %s", len(self.workers), output_dir)
        except OSError as e:
            logger.warning("[telemetry] Failed to write metrics: %s", e)

    def summary(self) -> dict[str, Any]:
        """Return summary stats for the run."""
        statuses = {}
        for wm in self.workers:
            statuses[wm.status] = statuses.get(wm.status, 0) + 1
        return {
            "run_id": self.run_id,
            "epic_id": self.epic_id,
            "total_workers": len(self.workers),
            "statuses": statuses,
            "total_duration": sum(w.duration_seconds for w in self.workers),
        }
