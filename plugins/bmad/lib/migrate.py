"""Migrate — 5-wave BMAD project upgrade executor (Story 9.4).

Executes migration waves with atomic git commits per wave.
DI-2: One git commit per wave; failure halts before next wave.
DI-5: Compose existing machinery — no re-implementation.

Waves:
1. Config bootstrap (calls bmad-init bootstrap)
2. Config schema upgrade (add version field)
3. Epic structure repair (ensure planning-artifacts/)
4. Story consolidation (calls migrate-stories-to-epic scanner)
5. OCR status check (calls ocr_runner.check_ocr_installed)
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

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
    """Create a migration plan. DI-1: read-only."""
    plan = MigrationPlan(project_dir=str(project_dir))
    plan.waves.append(WaveResult(wave=1, name="Config Bootstrap"))
    plan.waves.append(WaveResult(wave=2, name="Config Schema Upgrade"))
    plan.waves.append(WaveResult(wave=3, name="Epic Structure Repair"))
    plan.waves.append(WaveResult(wave=4, name="Story Audit (diagnostic)"))
    plan.waves.append(WaveResult(wave=5, name="OCR Status Check (diagnostic)"))
    return plan


def _check_dirty_worktree(project_dir: Path) -> str | None:
    """Check for uncommitted changes. Returns output or None if clean."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=project_dir, capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return "NOT_A_GIT_REPO"  # Distinct from "dirty" — must halt
        if result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return "NOT_A_GIT_REPO"  # Can't determine — halt
    return None


def _get_last_wave_from_git(project_dir: Path) -> int:
    """Find last completed migration wave from git log."""
    try:
        result = subprocess.run(
            ["git", "log", "--format=%H %s", "--grep", "\\[bmad-migrate\\]"],
            cwd=project_dir, capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            return 0
        last_wave = 0
        for line in result.stdout.strip().split("\n"):
            for w in range(1, 6):
                if f"wave {w}:" in line.lower():
                    last_wave = max(last_wave, w)
        return last_wave
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return 0


def _get_wave_shas_from_git(project_dir: Path) -> dict[int, str]:
    """Get commit SHAs for each completed migration wave."""
    shas: dict[int, str] = {}
    try:
        result = subprocess.run(
            ["git", "log", "--format=%H %s", "--grep", "\\[bmad-migrate\\]"],
            cwd=project_dir, capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            return shas
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            sha = line.split()[0]  # Full 40-char SHA from --format=%H
            for w in range(1, 6):
                if f"wave {w}:" in line.lower() and w not in shas:
                    shas[w] = sha
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return shas


def execute_migration(plan: MigrationPlan, project_dir: Path,
                      waves: list[int] | None = None,
                      dry_run: bool = False,
                      resume: bool = False) -> MigrationPlan:
    """Execute migration waves. DI-2: atomic commits per wave."""
    plan.dry_run = dry_run
    target_waves = waves or [1, 2, 3, 4, 5]

    # Resume: skip waves already completed in git history
    if resume and not waves:
        last_done = _get_last_wave_from_git(project_dir)
        if last_done > 0:
            # Get actual SHAs from git log for completed waves
            wave_shas = _get_wave_shas_from_git(project_dir)
            target_waves = [w for w in target_waves if w > last_done]
            if not target_waves:
                for wave in plan.waves:
                    wave.status = WaveStatus.DONE
                    wave.commit_sha = wave_shas.get(wave.wave, "")
                    wave.message = f"Already completed (found wave {last_done} in git)"
                return plan

    # Pre-flight: check for dirty worktree
    if not dry_run:
        dirty = _check_dirty_worktree(project_dir)
        if dirty:
            is_not_repo = dirty == "NOT_A_GIT_REPO"
            for wave in plan.waves:
                if wave.wave in target_waves:
                    wave.status = WaveStatus.FAILED
                    wave.message = "Not a git repository — run `git init` first" if is_not_repo else "Dirty worktree — commit or stash changes first"
            return plan

    # Get SHAs for any waves that were already completed
    wave_shas = _get_wave_shas_from_git(project_dir) if not dry_run else {}

    for wave in plan.waves:
        if wave.wave not in target_waves:
            wave.status = WaveStatus.SKIPPED
            wave.commit_sha = wave_shas.get(wave.wave, "")
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
    """Preview what a wave would change. Never raises — dry-run must be safe."""
    try:
        if wave.wave == 1:
            config = project_dir / "bmad" / "config.yaml"
            if not config.exists():
                wave.would_change.append("Create bmad/config.yaml via bmad-init bootstrap")
            else:
                wave.would_change.append("bmad/config.yaml exists — verify structure")
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
            _preview_wave4(wave, project_dir)
        elif wave.wave == 5:
            _preview_wave5(wave, project_dir)
    except Exception as e:
        wave.would_change.append(f"Preview error: {e}")

    wave.status = WaveStatus.DONE
    wave.message = "DRY RUN — preview only"


def _preview_wave4(wave: WaveResult, project_dir: Path):
    """Preview wave 4: scan for legacy stories."""
    stories_dir = project_dir / "implementation-artifacts" / "stories"
    if stories_dir.exists():
        try:
            from plugins.bmad.commands.migrate_stories import _scan_legacy_stories
            legacy = _scan_legacy_stories(stories_dir)
            if legacy:
                wave.would_change.append(f"Found {len(legacy)} legacy story files to consolidate")
                for s in legacy[:5]:
                    wave.would_change.append(f"  - {s.get('id', s.get('title', 'unknown'))}")
            else:
                wave.would_change.append("No legacy story files found")
        except ImportError:
            wave.would_change.append("Cannot import migrate_stories scanner")
    else:
        wave.would_change.append("No implementation-artifacts/stories/ directory")


def _preview_wave5(wave: WaveResult, project_dir: Path):
    """Preview wave 5: check OCR status."""
    try:
        from plugins.bmad.lib.ocr_runner import check_ocr_installed
        if check_ocr_installed():
            wave.would_change.append("OCR CLI is installed")
        else:
            wave.would_change.append("OCR CLI not installed — no action taken")
    except ImportError:
        wave.would_change.append("ocr_runner module not available")


def _execute_wave(wave: WaveResult, project_dir: Path):
    """Execute a single migration wave."""
    if wave.wave == 1:
        _wave_config_bootstrap(project_dir, wave)
    elif wave.wave == 2:
        _wave_config_upgrade(project_dir, wave)
    elif wave.wave == 3:
        _wave_epic_structure(project_dir, wave)
    elif wave.wave == 4:
        _wave_story_consolidation(project_dir, wave)
    elif wave.wave == 5:
        _wave_ocr_check(project_dir, wave)


def _git_commit(project_dir: Path, message: str, files: list[str]) -> str:
    """Stage specific files and commit. Returns full SHA.

    DI-2: Always uses targeted `git add` — never `git add -A`.
    Handles 'nothing to commit' gracefully.
    """
    if files:
        for f in files:
            subprocess.run(["git", "add", f], cwd=project_dir, check=True,
                           capture_output=True, timeout=30)

    result = subprocess.run(
        ["git", "commit", "-m", f"[bmad-migrate] {message}"],
        cwd=project_dir, capture_output=True, text=True, timeout=30
    )

    if result.returncode != 0:
        if "nothing to commit" in result.stdout or "nothing to commit" in result.stderr:
            logger.info("[migrate] nothing to commit for: %s", message)
        else:
            raise RuntimeError(f"git commit failed: {result.stderr}")

    sha_result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=project_dir, capture_output=True, text=True, timeout=10
    )
    return sha_result.stdout.strip()  # Full 40-char SHA


def _wave_config_bootstrap(project_dir: Path, wave: WaveResult):
    """Wave 1: Bootstrap BMAD config via bmad-init machinery.

    DI-5: Composes existing bmad_init.bootstrap() function.
    """
    from plugins.bmad.scripts.bmad_init import bootstrap

    config_path = project_dir / "bmad" / "config.yaml"
    created_files = []

    if config_path.exists():
        wave.message = "bmad/config.yaml already exists — bootstrap skipped"
        wave.commit_sha = _git_commit(project_dir, "wave 1: config bootstrap (exists)", [])
        wave.status = WaveStatus.DONE
        return

    try:
        config = bootstrap(
            project_dir,
            project_name=project_dir.name,
            project_type="other",
            project_level=1,
            user_name="",
            force=False,
            interactive=False,
        )
        # Stage all files created by bootstrap (don't hardcode list)
        result = subprocess.run(
            ["git", "status", "--porcelain"], cwd=project_dir,
            capture_output=True, text=True, timeout=10
        )
        for line in result.stdout.strip().split("\n"):
            if line.strip():
                # Extract filename from porcelain status (XY filename)
                parts = line.strip().split(None, 1)
                if len(parts) == 2:
                    created_files.append(parts[1])
        wave.message = f"Bootstrapped BMAD project: {config.get('project_name', project_dir.name)}"
    except RuntimeError as e:
        wave.message = f"Bootstrap failed: {e}"
        wave.status = WaveStatus.FAILED
        return
    except Exception as e:
        wave.message = f"Bootstrap error: {e}"
        wave.status = WaveStatus.FAILED
        return

    wave.commit_sha = _git_commit(project_dir, "wave 1: config bootstrap", created_files)
    wave.status = WaveStatus.DONE


def _wave_config_upgrade(project_dir: Path, wave: WaveResult):
    """Wave 2: Add version field to config if missing."""
    config_path = project_dir / "bmad" / "config.yaml"

    if not config_path.exists():
        wave.message = "No bmad/config.yaml — run wave 1 first"
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

    if "version" not in config:
        config["version"] = 1
        config_path.write_text(yaml.dump(config, default_flow_style=False, sort_keys=False))
        wave.commit_sha = _git_commit(project_dir, "wave 2: config schema upgrade",
                                       ["bmad/config.yaml"])
        wave.message = "Added version field to config"
    else:
        wave.message = "Config already has version field"
        wave.commit_sha = _git_commit(project_dir, "wave 2: config schema upgrade (no-op)", [])

    wave.status = WaveStatus.DONE


def _wave_epic_structure(project_dir: Path, wave: WaveResult):
    """Wave 3: Ensure planning-artifacts directory exists."""
    pa_dir = project_dir / "planning-artifacts"
    created_files = []

    if not pa_dir.exists():
        pa_dir.mkdir(parents=True)
        (pa_dir / ".gitkeep").write_text("")
        created_files.append("planning-artifacts/.gitkeep")
        wave.message = "Created planning-artifacts/"
    else:
        wave.message = "planning-artifacts/ already exists"

    wave.commit_sha = _git_commit(project_dir, "wave 3: epic structure repair", created_files)
    wave.status = WaveStatus.DONE


def _wave_story_consolidation(project_dir: Path, wave: WaveResult):
    """Wave 4: Scan for legacy stories using migrate-stories-to-epic scanner.

    DI-5: Composes existing migrate_stories._scan_legacy_stories().
    """
    stories_dir = project_dir / "implementation-artifacts" / "stories"
    if not stories_dir.exists():
        wave.message = "No implementation-artifacts/stories/ — nothing to consolidate"
        wave.commit_sha = _git_commit(project_dir, "wave 4: story consolidation (no stories)", [])
        wave.status = WaveStatus.DONE
        return

    try:
        from plugins.bmad.commands.migrate_stories import _scan_legacy_stories
        legacy = _scan_legacy_stories(stories_dir)
    except ImportError as e:
        wave.message = f"Cannot import migrate_stories: {e}"
        wave.status = WaveStatus.FAILED
        return

    if not legacy:
        wave.message = "No legacy story files found"
        wave.commit_sha = _git_commit(project_dir, "wave 4: story consolidation (clean)", [])
    else:
        wave.message = f"Found {len(legacy)} legacy story files"
        wave.details = "\n".join(f"- {s.get('id', s.get('title', '?'))}" for s in legacy[:10])
        # Report only — actual consolidation requires user review
        wave.commit_sha = _git_commit(project_dir, f"wave 4: story scan ({len(legacy)} legacy)", [])

    wave.status = WaveStatus.DONE


def _wave_ocr_check(project_dir: Path, wave: WaveResult):
    """Wave 5: Check OCR integration status.

    DI-5: Composes existing ocr_runner.check_ocr_installed().
    """
    try:
        from plugins.bmad.lib.ocr_runner import check_ocr_installed
        ocr_available = check_ocr_installed()
    except ImportError:
        ocr_available = False

    ocr_runner_exists = (project_dir / "plugins" / "bmad" / "lib" / "ocr_runner.py").exists()

    if ocr_available:
        wave.message = "OCR CLI installed and available"
    elif ocr_runner_exists:
        wave.message = "OCR runner exists but CLI not installed"
        wave.details = "Install OCR CLI: `pip install ocr-cli` or equivalent"
    else:
        wave.message = "OCR not configured (no ocr_runner.py)"

    wave.commit_sha = _git_commit(project_dir, "wave 5: OCR status check", [])
    wave.status = WaveStatus.DONE
