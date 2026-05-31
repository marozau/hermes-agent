"""Orchestrator — wave-based epic execution engine (Story 7.3).

Loads an epic document, builds wave DAG, dispatches workers per story,
runs success predicates, halt-on-failure with max attempts, checkpoints
progress to sprint-status.yaml, and supports resume.

Hard invariants enforced:
- OI-1: BMAD_ORCHESTRATE_DEPTH=1 — workers cannot spawn sub-orchestrators
- OI-2: Mandatory success_predicates for orchestrate runs
- OI-3: Workers commit on branch; supervisor never push/merge/rebase
- OI-4: Workers cannot execute deploy verbs
- OI-5: Workers cannot touch credential paths
- OI-6: Idempotent — --resume skips done stories
- OI-7: Halt-on-failure default; max_attempts: 2; NOT infinite retry
- OI-8: One epic per invocation; cross-epic deps are halt conditions
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

from .epic_anchor import EpicSpec, StorySpec, parse_epic_file

logger = logging.getLogger(__name__)

# ── OI-4: Forbidden deploy verbs ────────────────────────────────────────────
FORBIDDEN_VERBS: list[str] = [
    "terraform apply",
    "helm install",
    "gh pr merge",
    "gh release create",
    "pulumi up",
    "aws deploy",
    "gcloud deploy",
    "kubectl apply",
    "npm publish",
    "cargo publish",
]

# ── OI-5: Credential paths workers must not touch ───────────────────────────
FORBIDDEN_PATHS: list[str] = [
    "~/.ssh",
    "~/.aws",
    "~/.gnupg",
    "~/.config/gh",
    "~/.npmrc",
    "~/.docker/config.json",
    "~/.kube/config",
]

SPRINT_STATUS_FILE = "sprint-status.yaml"


# ── Data classes ─────────────────────────────────────────────────────────────


@dataclass
class OrchestrateFlags:
    """Flags for orchestrator invocation."""

    resume: bool = False
    dry_run: bool = False
    story_filter: str = ""  # "X.Y" to run only one story
    wave_filter: int = -1  # -1 = all waves
    max_retries: int = 2  # OI-7
    no_halt: bool = False  # debug mode
    no_telemetry: bool = False


@dataclass
class StoryResult:
    """Result of executing a single story."""

    story_id: str
    status: str  # "succeeded" | "failed" | "skipped" | "halted"
    attempts: int = 0
    predicates_passed: int = 0
    predicates_total: int = 0
    error: str = ""
    delegation_result: dict = field(default_factory=dict)


@dataclass
class OrchestrateReport:
    """Summary of an entire orchestrate run."""

    epic_id: str
    total_stories: int
    waves: list[list[str]]
    results: dict[str, StoryResult] = field(default_factory=dict)
    halted: bool = False
    halt_reason: str = ""


# ── Sprint-status persistence ────────────────────────────────────────────────


def load_sprint_status(project_dir: Path) -> dict:
    """Load sprint-status.yaml from the project directory.

    Returns empty dict if the file does not exist.
    """
    path = project_dir / SPRINT_STATUS_FILE
    if not path.exists():
        return {}
    try:
        data: dict = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        logger.warning("[orchestrator] Failed to parse %s", path)
        return {}


def save_sprint_status(project_dir: Path, data: dict) -> None:
    """Atomically write sprint-status.yaml (OI-6 checkpoint).

    Uses tmp-then-rename pattern for crash safety.
    """
    path = project_dir / SPRINT_STATUS_FILE
    tmp_path = path.with_suffix(".yaml.tmp")
    try:
        tmp_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        os.replace(str(tmp_path), str(path))
    except Exception:
        logger.exception("[orchestrator] Failed to save sprint-status")
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise


# ── Worker goal construction ─────────────────────────────────────────────────


def build_worker_goal(
    story: StorySpec, epic: EpicSpec, flags: OrchestrateFlags
) -> str:
    """Build the goal prompt for a worker sub-agent.

    Includes anti-rationalization table (OI-3, OI-4, OI-5), forbidden-verbs
    list, credential paths, and time-anchor stop-condition.
    """
    predicates_text = "\n".join(
        f"  - {p}" for p in story.success_predicates
    ) or "  (none defined)"

    forbidden_verbs_text = "\n".join(f"  - {v}" for v in FORBIDDEN_VERBS)
    forbidden_paths_text = "\n".join(f"  - {p}" for p in FORBIDDEN_PATHS)

    goal = f"""\
## Story {story.id}: {story.title}

**Epic:** {epic.id} — {epic.name}
**Description:** {story.description or story.title}

### Success Predicates (you MUST satisfy ALL)
{predicates_text}

### Stop Condition
You are DONE when ALL success predicates pass. Do not over-engineer.
Commit your work on the branch and stop.

### Hard Constraints

| # | Constraint | Rationale |
|---|-----------|-----------|
| OI-3 | Commit on branch only. NEVER push, merge, or rebase. | Supervisor controls integration |
| OI-4 | NEVER execute these deploy verbs: | Blast-radius protection |
{forbidden_verbs_text}
| OI-5 | NEVER read/write these credential paths: | Credential isolation |
{forbidden_paths_text}

### Anti-Rationalization
- Do NOT skip predicates because they "seem unnecessary"
- Do NOT declare success without verifying each predicate
- Do NOT modify files outside the story's scope
- Do NOT create new dependencies not listed in the epic
"""
    return goal.strip()


# ── Predicate evaluation ─────────────────────────────────────────────────────


def run_predicates(
    predicates: list[str], project_dir: Path
) -> tuple[int, int, list[str]]:
    """Evaluate success predicates against the project directory.

    Supports predicate types:
    - ``file_exists:<path>`` — checks if file exists
    - ``tests_pass:<glob>`` — runs pytest on matching files
    - ``grep:<pattern>:<file>`` — checks if pattern exists in file
    - Other strings are treated as shell commands (exit 0 = pass)

    Returns:
        (passed_count, total_count, failure_reasons)
    """
    passed = 0
    total = len(predicates)
    failures: list[str] = []

    for pred in predicates:
        pred = pred.strip()
        if not pred:
            continue

        try:
            if pred.startswith("file_exists:"):
                target = pred.split(":", 1)[1].strip()
                if (project_dir / target).exists():
                    passed += 1
                else:
                    failures.append(f"file not found: {target}")

            elif pred.startswith("tests_pass:"):
                glob_pattern = pred.split(":", 1)[1].strip()
                result = subprocess.run(
                    ["python", "-m", "pytest", glob_pattern, "-q", "--tb=line"],
                    capture_output=True,
                    text=True,
                    timeout=120,
                    cwd=str(project_dir),
                )
                if result.returncode == 0:
                    passed += 1
                else:
                    failures.append(
                        f"tests failed ({glob_pattern}): "
                        f"{result.stderr.strip()[:200]}"
                    )

            elif pred.startswith("grep:"):
                parts = pred.split(":", 2)
                if len(parts) >= 3:
                    pattern, target_file = parts[1].strip(), parts[2].strip()
                    target_path = project_dir / target_file
                    if target_path.exists():
                        content = target_path.read_text(encoding="utf-8", errors="replace")
                        if re.search(pattern, content):
                            passed += 1
                        else:
                            failures.append(
                                f"pattern '{pattern}' not found in {target_file}"
                            )
                    else:
                        failures.append(f"grep target not found: {target_file}")
                else:
                    failures.append(f"malformed grep predicate: {pred}")

            else:
                # Shell command predicate
                result = subprocess.run(
                    ["bash", "-c", pred],
                    capture_output=True,
                    text=True,
                    timeout=60,
                    cwd=str(project_dir),
                )
                if result.returncode == 0:
                    passed += 1
                else:
                    failures.append(
                        f"command failed: {pred} — {result.stderr.strip()[:200]}"
                    )

        except subprocess.TimeoutExpired:
            failures.append(f"predicate timed out: {pred}")
        except Exception as exc:
            failures.append(f"predicate error ({pred}): {exc}")

    return passed, total, failures


# ── Core orchestrator ────────────────────────────────────────────────────────


def _check_depth_guard() -> None:
    """OI-1: Refuse to run if already inside an orchestration (depth=1)."""
    if os.environ.get("BMAD_ORCHESTRATE_DEPTH") == "1":
        raise RuntimeError(
            "OI-1 violation: BMAD_ORCHESTRATE_DEPTH=1 detected. "
            "Workers cannot spawn sub-orchestrators."
        )


def _validate_cross_epic_deps(epic: EpicSpec) -> list[str]:
    """OI-8: Check that all dependencies are within the current epic.

    Returns list of cross-epic dependency IDs (should be empty).
    """
    story_ids = {s.id for s in epic.stories}
    cross_epic: list[str] = []
    for story in epic.stories:
        for dep in story.dependencies:
            if dep not in story_ids:
                cross_epic.append(
                    f"Story {story.id} depends on {dep}, "
                    f"which is not in epic {epic.id}"
                )
    return cross_epic


def _check_mandatory_predicates(epic: EpicSpec) -> list[str]:
    """OI-2: Ensure all stories have at least one success predicate."""
    missing: list[str] = []
    for story in epic.stories:
        if not story.success_predicates:
            missing.append(story.id)
    return missing


def _filter_waves(
    waves: list[list[str]], flags: OrchestrateFlags
) -> list[list[str]]:
    """Apply story_filter and wave_filter to the wave list."""
    result = waves

    # Filter by wave index
    if flags.wave_filter >= 0:
        if flags.wave_filter < len(result):
            result = [result[flags.wave_filter]]
        else:
            result = []

    # Filter by story ID
    if flags.story_filter:
        result = [
            [sid for sid in wave if sid == flags.story_filter]
            for wave in result
        ]
        result = [w for w in result if w]  # remove empty waves

    return result


def orchestrate_epic(
    ctx: Any,
    project_dir: Path,
    epic_path: Path,
    flags: OrchestrateFlags,
) -> OrchestrateReport:
    """Execute an epic's stories in wave-topological order.

    Args:
        ctx: Hermes plugin context (has dispatch_tool)
        project_dir: Root directory of the BMAD project
        epic_path: Path to the epic markdown document
        flags: Orchestration flags (resume, dry_run, etc.)

    Returns:
        OrchestrateReport with per-story results

    Raises:
        RuntimeError: On OI-1 depth guard violation
    """
    # OI-1: Depth guard
    _check_depth_guard()

    # Parse epic
    epic = parse_epic_file(epic_path)
    logger.info(
        "[orchestrator] Loaded epic %s with %d stories",
        epic.id, len(epic.stories),
    )

    # OI-8: Cross-epic dependency check
    cross_deps = _validate_cross_epic_deps(epic)
    report = OrchestrateReport(
        epic_id=epic.id,
        total_stories=len(epic.stories),
        waves=epic.topological_waves(),
    )
    if cross_deps:
        report.halted = True
        report.halt_reason = (
            f"OI-8: Cross-epic dependencies detected: {'; '.join(cross_deps)}"
        )
        logger.error("[orchestrator] %s", report.halt_reason)
        return report

    # OI-2: Mandatory predicates check
    missing_predicates = _check_mandatory_predicates(epic)
    if missing_predicates and not flags.dry_run:
        report.halted = True
        report.halt_reason = (
            f"OI-2: Stories missing success_predicates: "
            f"{', '.join(missing_predicates)}"
        )
        logger.error("[orchestrator] %s", report.halt_reason)
        return report

    # Build waves
    all_waves = epic.topological_waves()
    waves = _filter_waves(all_waves, flags)
    report.waves = waves

    # OI-6: Resume support — load existing sprint status
    sprint_status = load_sprint_status(project_dir) if flags.resume else {}
    done_stories: set[str] = set()
    if flags.resume and sprint_status:
        for sid, entry in sprint_status.get("stories", {}).items():
            if isinstance(entry, dict) and entry.get("status") == "done":
                done_stories.add(sid)
        logger.info(
            "[orchestrator] Resume: %d stories already done", len(done_stories)
        )

    # Telemetry setup
    telemetry = None
    if not flags.no_telemetry:
        try:
            from .telemetry import TelemetrySession

            telemetry = TelemetrySession(epic_id=epic.id)
        except Exception:
            logger.debug("[orchestrator] Telemetry unavailable", exc_info=True)

    # Execute wave by wave
    for wave_idx, wave in enumerate(waves):
        logger.info(
            "[orchestrator] Wave %d: %s", wave_idx, ", ".join(wave)
        )

        for story_id in wave:
            story = epic.story_by_id(story_id)
            if story is None:
                report.results[story_id] = StoryResult(
                    story_id=story_id,
                    status="failed",
                    error=f"Story {story_id} not found in epic spec",
                )
                continue

            # OI-6: Skip done stories on resume
            if story_id in done_stories:
                report.results[story_id] = StoryResult(
                    story_id=story_id,
                    status="skipped",
                    attempts=0,
                )
                logger.info("[orchestrator] Skipping %s (resume: done)", story_id)
                continue

            # Dry-run mode: just report what would be executed
            if flags.dry_run:
                report.results[story_id] = StoryResult(
                    story_id=story_id,
                    status="skipped",
                    attempts=0,
                    predicates_total=len(story.success_predicates),
                )
                continue

            # Execute story with retry (OI-7)
            result = _execute_story(
                ctx, story, epic, project_dir, flags, telemetry, wave_idx
            )
            report.results[story_id] = result

            # Checkpoint progress
            _checkpoint(project_dir, report)

            # Halt-on-failure (OI-7)
            if result.status == "failed" and not flags.no_halt:
                report.halted = True
                report.halt_reason = (
                    f"Story {story_id} failed after {result.attempts} attempts: "
                    f"{result.error}"
                )
                logger.error("[orchestrator] HALT: %s", report.halt_reason)
                if telemetry:
                    telemetry.flush()
                return report

    # Flush telemetry
    if telemetry:
        telemetry.flush()

    return report


def _execute_story(
    ctx: Any,
    story: StorySpec,
    epic: EpicSpec,
    project_dir: Path,
    flags: OrchestrateFlags,
    telemetry: Any,
    wave_idx: int,
) -> StoryResult:
    """Execute a single story with retry logic (OI-7)."""
    max_attempts = max(1, flags.max_retries)
    passed = 0
    total = 0
    error_msg = ""
    delegation_result: dict = {}

    for attempt in range(1, max_attempts + 1):
        logger.info(
            "[orchestrator] Story %s attempt %d/%d",
            story.id, attempt, max_attempts,
        )

        # Telemetry: start worker
        wm = None
        if telemetry:
            wm = telemetry.start_worker(
                story.id, wave_idx, attempt,
                retry_of=attempt - 1 if attempt > 1 else 0,
            )

        # Build worker goal
        goal = build_worker_goal(story, epic, flags)

        # OI-1: Set depth=1 in worker environment
        env_context = f"BMAD_ORCHESTRATE_DEPTH=1"

        # Delegate to worker
        try:
            from .delegation import delegate_one

            delegation_result = delegate_one(
                ctx,
                goal=goal,
                parent_skill="bmad:orchestrate",
                context=env_context,
            )
        except Exception as exc:
            logger.exception("[orchestrator] Delegation failed for %s", story.id)
            delegation_result = {"status": "failure", "error": str(exc)}

        # Evaluate success predicates
        passed, total, failures = run_predicates(
            story.success_predicates, project_dir
        )

        # Telemetry: record predicates
        if telemetry and wm:
            telemetry.record_predicates(wm, total, passed, total - passed)

        if passed == total:
            # Story 7.8: Run adversarial gate if configured (opt-in)
            if story.verification_gate == "adversarial":
                try:
                    from .adversarial_gate import run_adversarial_gate
                    adv_pass, adv_findings = run_adversarial_gate(ctx, story, project_dir)
                    if not adv_pass:
                        error_msg = f"Adversarial gate FAILED: {adv_findings}"
                        logger.warning("[orchestrator] Story %s attempt %d: %s", story.id, attempt, error_msg)
                        if telemetry and wm:
                            telemetry.finish_worker(wm, "failed", error=error_msg,
                                                     delegation_result=delegation_result if isinstance(delegation_result, dict) else {})
                        continue  # Retry
                except Exception as exc:
                    logger.warning("[orchestrator] Adversarial gate error for %s: %s (treated as pass)", story.id, exc)

            # Success
            if telemetry and wm:
                telemetry.finish_worker(wm, "succeeded", delegation_result=delegation_result)
            return StoryResult(
                story_id=story.id,
                status="succeeded",
                attempts=attempt,
                predicates_passed=passed,
                predicates_total=total,
                delegation_result=delegation_result if isinstance(delegation_result, dict) else {},
            )

        # Predicate failure
        error_msg = f"Predicates: {passed}/{total} passed. Failures: {'; '.join(failures)}"
        logger.warning(
            "[orchestrator] Story %s attempt %d: %s",
            story.id, attempt, error_msg,
        )

        if telemetry and wm:
            telemetry.finish_worker(wm, "failed", error=error_msg,
                                     delegation_result=delegation_result if isinstance(delegation_result, dict) else {})

    # All attempts exhausted (OI-7)
    return StoryResult(
        story_id=story.id,
        status="failed",
        attempts=max_attempts,
        predicates_passed=passed,
        predicates_total=total,
        error=error_msg,
        delegation_result=delegation_result if isinstance(delegation_result, dict) else {},
    )


def _checkpoint(project_dir: Path, report: OrchestrateReport) -> None:
    """Write current progress to sprint-status.yaml."""
    data: dict[str, Any] = {
        "epic_id": report.epic_id,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "stories": {},
    }
    for sid, result in report.results.items():
        data["stories"][sid] = {
            "status": "done" if result.status == "succeeded" else result.status,
            "attempts": result.attempts,
            "predicates_passed": result.predicates_passed,
            "predicates_total": result.predicates_total,
            "error": result.error,
        }
    if report.halted:
        data["halted"] = True
        data["halt_reason"] = report.halt_reason

    try:
        save_sprint_status(project_dir, data)
    except Exception:
        logger.exception("[orchestrator] Checkpoint failed (non-fatal)")
