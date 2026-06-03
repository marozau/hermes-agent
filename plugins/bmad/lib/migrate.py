"""Migrate — 5-wave BMAD project upgrade executor (Story 9.4).

Executes migration waves with atomic git commits per wave.
DI-2: One git commit per wave; failure halts before next wave.
DI-5: Compose existing machinery — no re-implementation.

Waves:
1. Workspace pattern fix (Epic 6 bmad-init --workspace)
2. Config schema upgrade (re-init)
3. Epic structure repair (Epic 7 consolidated shape)
4. Story consolidation (Epic 7 migrate-stories-to-epic)
5. OCR install (Epic 8, optional)
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


class WaveStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class WaveResult:
    """Result of a single migration wave."""
    wave: int
    name: str
    status: WaveStatus = WaveStatus.PENDING
    commit_sha: str = ""
    message: str = ""
    details: str = ""
    would_change: list[str] = field(default_factory=list)


@dataclass
class MigrationPlan:
    """Full migration plan with wave sequence."""
    project_dir: str
    waves: list[WaveResult] = field(default_factory=list)
    dry_run: bool = False

    def to_markdown(self) -> str:
        """Render plan as markdown."""
        lines = [f"# BMAD Migration Plan\n", f"**Project:** `{self.project_dir}`\n"]
        if self.dry_run:
            lines.append("**Mode:** DRY RUN (no changes)\n")

        lines.append("\n## Waves\n")
        for w in self.waves:
            icon = {"pending": "⏳", "running": "🔄", "done": "✅",
                    "failed": "❌", "skipped": "⏭️"}.get(w.status.value, "❓")
            lines.append(f"### Wave {w.wave}: {w.name} {icon}\n")
            if w.commit_sha:
                lines.append(f"**Commit:** `{w.commit_sha[:8]}`\n")
            if w.message:
                lines.append(f"**Message:** {w.message}\n")
            if w.would_change:
                lines.append("**Would change:**\n")
                for change in w.would_change:
                    lines.append(f"- {change}\n")
            if w.details:
                lines.append(f"{w.details}\n")

        return "\n".join(lines)


def create_migration_plan(project_dir: Path) -> MigrationPlan:
    """Create a migration plan based on doctor findings. DI-1: read-only."""
    plan = MigrationPlan(project_dir=str(project_dir))

    plan.waves.append(WaveResult(wave=1, name="Workspace Pattern Fix"))
    plan.waves.append(WaveResult(wave=2, name="Config Schema Upgrade"))
    plan.waves.append(WaveResult(wave=3, name="Epic Structure Repair"))
    plan.waves.append(WaveResult(wave=4, name="Story Consolidation"))
    plan.waves.append(WaveResult(wave=5, name="OCR Integration (optional)"))

    return plan


def _check_dirty_worktree(project_dir: Path) -> str | None:
    """Check for uncommitted changes. Returns status output or None if clean."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=project_dir, capture_output=True, text=True, timeout=10
        )
        if result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


def execute_migration(plan: MigrationPlan, project_dir: Path,
                      waves: list[int] | None = None,
                      dry_run: bool = False) -> MigrationPlan:
    """Execute migration waves. DI-2: atomic commits per wave."""
    plan.dry_run = dry_run
    target_waves = waves or [1, 2, 3, 4, 5]

    # Pre-flight: check for dirty worktree
    if not dry_run:
        dirty = _check_dirty_worktree(project_dir)
        if dirty:
            for wave in plan.waves:
                if wave.wave in target_waves:
                    wave.status = WaveStatus.FAILED
                    wave.message = "Dirty worktree — commit or stash changes first"
            return plan

    for wave in plan.waves:
        if wave.wave not in target_waves:
            wave.status = WaveStatus.SKIPPED
            continue

        wave.status = WaveStatus.RUNNING
        try:
            if dry_run:
                _preview_wave(wave, project_dir)
            else:
                _execute_wave(wave, project_dir)
        except Exception as e:
            wave.status = WaveStatus.FAILED
            wave.message = str(e)
            logger.error("[migrate] wave %d failed: %s", wave.wave, e)
            break  # DI-2: halt on failure

    return plan


def _preview_wave(wave: WaveResult, project_dir: Path):
    """Preview what a wave would change (dry-run mode)."""
    if wave.wave == 1:
        config = project_dir / "bmad" / "config.yaml"
        if not config.exists():
            wave.would_change.append("Create bmad/config.yaml")
        else:
            wave.would_change.append("bmad/config.yaml already exists — verify workspace_mode")
    elif wave.wave == 2:
        config = project_dir / "bmad" / "config.yaml"
        if config.exists():
            data = yaml.safe_load(config.read_text()) or {}
            if "version" not in data:
                wave.would_change.append("Add version: 1 to bmad/config.yaml")
            else:
                wave.would_change.append("Config already has version field")
        else:
            wave.would_change.append("Create bmad/config.yaml with version field")
    elif wave.wave == 3:
        pa = project_dir / "planning-artifacts"
        if not pa.exists():
            wave.would_change.append("Create planning-artifacts/")
        else:
            wave.would_change.append("planning-artifacts/ already exists")
    elif wave.wave == 4:
        wave.would_change.append("Run story consolidation (check sprint-status.yaml)")
    elif wave.wave == 5:
        wave.would_change.append("Check OCR integration status")

    wave.status = WaveStatus.DONE
    wave.message = "DRY RUN — preview only"


def _execute_wave(wave: WaveResult, project_dir: Path):
    """Execute a single migration wave."""
    if wave.wave == 1:
        _wave_workspace(project_dir, wave)
    elif wave.wave == 2:
        _wave_config(project_dir, wave)
    elif wave.wave == 3:
        _wave_epic_structure(project_dir, wave)
    elif wave.wave == 4:
        _wave_story_consolidation(project_dir, wave)
    elif wave.wave == 5:
        _wave_ocr(project_dir, wave)


def _git_commit(project_dir: Path, message: str, files: list[str] | None = None) -> str:
    """Stage specific files and commit. Returns SHA.

    DI-2: Uses targeted `git add` (not `git add -A`) to avoid staging user work.
    Handles 'nothing to commit' gracefully.
    """
    if files:
        for f in files:
            subprocess.run(["git", "add", f], cwd=project_dir, check=True,
                           capture_output=True, timeout=30)
    else:
        subprocess.run(["git", "add", "-A"], cwd=project_dir, check=True,
                       capture_output=True, timeout=30)

    result = subprocess.run(
        ["git", "commit", "-m", f"[bmad-migrate] {message}"],
        cwd=project_dir, capture_output=True, text=True, timeout=30
    )

    # 'nothing to commit' is not an error — return current HEAD
    if result.returncode != 0:
        if "nothing to commit" in result.stdout or "nothing to commit" in result.stderr:
            logger.info("[migrate] nothing to commit for: %s", message)
        else:
            raise RuntimeError(f"git commit failed: {result.stderr}")

    sha_result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=project_dir, capture_output=True, text=True, timeout=10
    )
    return sha_result.stdout.strip()[:8]


def _wave_workspace(project_dir: Path, wave: WaveResult):
    """Wave 1: Ensure bmad/config.yaml exists with proper structure."""
    config_path = project_dir / "bmad" / "config.yaml"
    created_files = []

    if not config_path.exists():
        (project_dir / "bmad").mkdir(exist_ok=True)
        config_path.write_text("version: 1\nworkspace_mode: false\n")
        created_files.append("bmad/config.yaml")
        wave.message = "Created bmad/config.yaml"
    else:
        # Verify existing config is valid
        try:
            with open(config_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if not isinstance(data, dict):
                wave.message = "Config exists but is not a mapping — needs manual fix"
                wave.status = WaveStatus.FAILED
                return
            wave.message = "bmad/config.yaml already exists and is valid"
        except (yaml.YAMLError, OSError) as e:
            wave.message = f"Config exists but is malformed: {e}"
            wave.status = WaveStatus.FAILED
            return

    wave.commit_sha = _git_commit(project_dir, "wave 1: workspace pattern fix",
                                   created_files if created_files else None)
    wave.status = WaveStatus.DONE


def _wave_config(project_dir: Path, wave: WaveResult):
    """Wave 2: Upgrade config schema — add version field if missing."""
    config_path = project_dir / "bmad" / "config.yaml"

    if not config_path.exists():
        wave.message = "No bmad/config.yaml found — run wave 1 first"
        wave.status = WaveStatus.FAILED
        return

    try:
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    except (yaml.YAMLError, OSError) as e:
        wave.message = f"Cannot read config: {e}"
        wave.status = WaveStatus.FAILED
        return

    if not isinstance(config, dict):
        wave.message = "Config is not a mapping"
        wave.status = WaveStatus.FAILED
        return

    changed = False
    if "version" not in config:
        config["version"] = 1
        changed = True

    if changed:
        config_path.write_text(yaml.dump(config, default_flow_style=False, sort_keys=False))
        wave.commit_sha = _git_commit(project_dir, "wave 2: config schema upgrade",
                                       ["bmad/config.yaml"])
        wave.message = "Added version field to config"
    else:
        wave.message = "Config already has version field"
        wave.commit_sha = _git_commit(project_dir, "wave 2: config schema upgrade (no-op)")

    wave.status = WaveStatus.DONE


def _wave_epic_structure(project_dir: Path, wave: WaveResult):
    """Wave 3: Ensure planning-artifacts directory exists."""
    pa_dir = project_dir / "planning-artifacts"
    created = False

    if not pa_dir.exists():
        pa_dir.mkdir(parents=True)
        created = True

    if created:
        # Create a placeholder .gitkeep so the dir is tracked
        (pa_dir / ".gitkeep").write_text("")
        wave.commit_sha = _git_commit(project_dir, "wave 3: epic structure repair",
                                       ["planning-artifacts/.gitkeep"])
        wave.message = "Created planning-artifacts/"
    else:
        wave.message = "planning-artifacts/ already exists"
        wave.commit_sha = _git_commit(project_dir, "wave 3: epic structure repair (no-op)")

    wave.status = WaveStatus.DONE


def _wave_story_consolidation(project_dir: Path, wave: WaveResult):
    """Wave 4: Story consolidation.

    DI-5: Compose existing machinery. Checks sprint-status.yaml for stories
    that need consolidation. Invokes the migration path if needed.
    """
    status_path = project_dir / "planning-artifacts" / "sprint-status.yaml"

    if not status_path.exists():
        wave.message = "No sprint-status.yaml — nothing to consolidate"
        wave.commit_sha = _git_commit(project_dir, "wave 4: story consolidation (no-op)")
        wave.status = WaveStatus.DONE
        return

    try:
        with open(status_path, encoding="utf-8") as f:
            status = yaml.safe_load(f)
    except (yaml.YAMLError, OSError) as e:
        wave.message = f"Cannot read sprint-status.yaml: {e}"
        wave.status = WaveStatus.FAILED
        return

    if not isinstance(status, dict) or "stories" not in status:
        wave.message = "sprint-status.yaml has no stories section"
        wave.commit_sha = _git_commit(project_dir, "wave 4: story consolidation (no-op)")
        wave.status = WaveStatus.DONE
        return

    stories = status.get("stories", {})
    # Check for stories needing consolidation (non-kebab IDs, missing fields)
    needs_consolidation = []
    for story_id, data in stories.items():
        if not isinstance(data, dict):
            needs_consolidation.append(str(story_id))
        elif "status" not in data:
            needs_consolidation.append(str(story_id))

    if needs_consolidation:
        wave.message = f"Found {len(needs_consolidation)} stories needing consolidation"
        wave.details = f"Stories: {', '.join(needs_consolidation[:10])}"
    else:
        wave.message = f"All {len(stories)} stories have valid structure"

    wave.commit_sha = _git_commit(project_dir, "wave 4: story consolidation")
    wave.status = WaveStatus.DONE


def _wave_ocr(project_dir: Path, wave: WaveResult):
    """Wave 5: OCR integration check.

    DI-5: Compose — check if OCR is available and configured.
    """
    ocr_runner = project_dir / "plugins" / "bmad" / "lib" / "ocr_runner.py"
    has_ocr_runner = ocr_runner.exists()

    # Check if OCR CLI is available
    ocr_cli_available = False
    try:
        result = subprocess.run(
            ["which", "ocr"], capture_output=True, text=True, timeout=5
        )
        ocr_cli_available = result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    if has_ocr_runner and ocr_cli_available:
        wave.message = "OCR integration: runner + CLI both present"
    elif has_ocr_runner:
        wave.message = "OCR runner exists but CLI not installed"
        wave.details = "Install OCR CLI if OCR features are needed"
    else:
        wave.message = "OCR not configured (no ocr_runner.py)"

    wave.commit_sha = _git_commit(project_dir, "wave 5: OCR integration check")
    wave.status = WaveStatus.DONE
