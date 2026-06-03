"""Doctor — read-only BMAD project diagnostic (Story 9.1).

Scans a BMAD project and produces structured findings across 10 categories:
1. Workspace pattern check
2. Config schema validation
3. Phase overrides check
4. Status drift detection (uses 3-source reconciliation)
5. Missing artifacts
6. Schema mismatch (old vs current)
7. Runtime drift (live vs worktree)
8. Epic structure validation
9. Story consolidation status
10. OCR integration status

Output: list[DoctorFinding] — severity-ranked, actionable.
DI-1: Doctor NEVER mutates. Read-only file/git introspection only.
"""

from __future__ import annotations

import glob
import logging
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

from plugins.bmad.lib.phase_overrides import load_phase_overrides, is_phase_overridden

logger = logging.getLogger(__name__)


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


@dataclass(frozen=True)
class DoctorFinding:
    """A single diagnostic finding."""
    category: str
    severity: Severity
    title: str
    detail: str
    remediation: str = ""
    file_path: str = ""


@dataclass
class DoctorReport:
    """Full diagnostic report for a BMAD project."""
    project_dir: str
    findings: list[DoctorFinding] = field(default_factory=list)
    categories_checked: int = 0

    @property
    def critical_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == Severity.CRITICAL)

    @property
    def high_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == Severity.HIGH)

    def to_markdown(self) -> str:
        """Render report as markdown."""
        lines = [f"# BMAD Doctor Report\n", f"**Project:** `{self.project_dir}`\n"]
        lines.append(f"**Findings:** {len(self.findings)} "
                     f"(🔴 {self.critical_count} critical, 🟠 {self.high_count} high)\n")

        by_cat: dict[str, list[DoctorFinding]] = {}
        for f in self.findings:
            by_cat.setdefault(f.category, []).append(f)

        for cat, findings in sorted(by_cat.items()):
            lines.append(f"\n## {cat}\n")
            for f in findings:
                icon = {"critical": "🔴", "high": "🟠", "medium": "🟡",
                        "low": "🔵", "info": "ℹ️"}.get(f.severity.value, "❓")
                lines.append(f"### {icon} {f.title}\n")
                lines.append(f"{f.detail}\n")
                if f.file_path:
                    lines.append(f"**File:** `{f.file_path}`\n")
                if f.remediation:
                    lines.append(f"**Fix:** {f.remediation}\n")

        if not self.findings:
            lines.append("\n✅ No issues found. Project is up to date.\n")

        return "\n".join(lines)


def run_doctor(project_dir: Path) -> DoctorReport:
    """Run all 10 diagnostic categories. DI-1: read-only.

    Each check is isolated — if one raises, the others still run.
    """
    report = DoctorReport(project_dir=str(project_dir))
    overrides = load_phase_overrides(project_dir)

    checks = [
        ("Workspace Pattern", lambda: _check_workspace_pattern(project_dir, report, overrides)),
        ("Config Schema", lambda: _check_config_schema(project_dir, report)),
        ("Status Drift", lambda: _check_status_drift(project_dir, report)),
        ("Missing Artifacts", lambda: _check_missing_artifacts(project_dir, report, overrides)),
        ("Epic Structure", lambda: _check_epic_structure(project_dir, report)),
        ("Schema Version", lambda: _check_schema_version(project_dir, report)),
        ("Runtime Drift", lambda: _check_runtime_drift(project_dir, report)),
        ("Story Consolidation", lambda: _check_story_consolidation(project_dir, report)),
        ("OCR Integration", lambda: _check_ocr_integration(project_dir, report)),
        ("Spec Blocks", lambda: _check_spec_blocks(project_dir, report)),
    ]

    actual_checked = 0
    for name, check_fn in checks:
        try:
            check_fn()
            actual_checked += 1
        except Exception as e:
            logger.warning("[doctor] check '%s' failed: %s", name, e)
            report.findings.append(DoctorFinding(
                category=name,
                severity=Severity.HIGH,  # L-21: crashing check is a bug, not noise
                title=f"Diagnostic check crashed: {name}",
                detail=f"Exception: {type(e).__name__}: {e}",
                remediation="This is a doctor bug — report it."
            ))
            actual_checked += 1

    report.categories_checked = actual_checked
    return report


# ── Category checks ─────────────────────────────────────────────────────

def _check_workspace_pattern(project_dir: Path, report: DoctorReport,
                              overrides: dict[str, str]):
    """Cat 1: workspace pattern validation.

    Workspace is operational state, not a planning phase — always checked.
    """

    config_path = project_dir / "bmad" / "config.yaml"
    if not config_path.exists():
        report.findings.append(DoctorFinding(
            category="Workspace Pattern",
            severity=Severity.HIGH,
            title="No bmad/config.yaml found",
            detail="Project lacks BMAD configuration.",
            remediation="Run `hermes bmad-init` to initialize."
        ))
        return

    try:
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except (yaml.YAMLError, OSError):
        return

    if not isinstance(config, dict):
        return

    workspace_mode = config.get("workspace_mode", False)
    worktrees = config.get("worktrees", [])

    if workspace_mode and not worktrees:
        report.findings.append(DoctorFinding(
            category="Workspace Pattern",
            severity=Severity.HIGH,
            title="workspace_mode enabled but no worktrees defined",
            detail="Config has workspace_mode: true but worktrees list is empty.",
            remediation="Add worktrees to bmad/config.yaml or disable workspace_mode."
        ))

    if worktrees:
        for wt in worktrees:
            if isinstance(wt, dict):
                wt_name = wt.get("name", "")
                wt_path = project_dir / "worktree" / wt_name
                if not wt_path.exists():
                    report.findings.append(DoctorFinding(
                        category="Workspace Pattern",
                        severity=Severity.MEDIUM,
                        title=f"Worktree directory missing: {wt_name}",
                        detail=f"Config references worktree '{wt_name}' but {wt_path} doesn't exist.",
                        remediation=f"Run `git worktree add worktree/{wt_name}` or remove from config."
                    ))


def _check_config_schema(project_dir: Path, report: DoctorReport):
    """Cat 2: config schema validation."""
    config_path = project_dir / "bmad" / "config.yaml"
    if not config_path.exists():
        return

    try:
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except (yaml.YAMLError, OSError) as e:
        report.findings.append(DoctorFinding(
            category="Config Schema",
            severity=Severity.HIGH,
            title="Config file unreadable",
            detail=f"bmad/config.yaml parse error: {e}",
            remediation="Fix YAML syntax in bmad/config.yaml."
        ))
        return

    if not isinstance(config, dict):
        report.findings.append(DoctorFinding(
            category="Config Schema",
            severity=Severity.HIGH,
            title="Config is not a mapping",
            detail="bmad/config.yaml should be a YAML mapping.",
            remediation="Rewrite bmad/config.yaml as a key-value mapping."
        ))
        return

    if "version" not in config:
        report.findings.append(DoctorFinding(
            category="Config Schema",
            severity=Severity.LOW,
            title="Config missing version field",
            detail="Adding `version: 1` helps future migration tools.",
            remediation="Add `version: 1` to bmad/config.yaml."
        ))


def _check_status_drift(project_dir: Path, report: DoctorReport):
    """Cat 3: status drift detection using 3-source reconciliation."""
    status_path = project_dir / "planning-artifacts" / "sprint-status.yaml"
    if not status_path.exists():
        report.findings.append(DoctorFinding(
            category="Status Drift",
            severity=Severity.MEDIUM,
            title="No sprint-status.yaml found",
            detail="Sprint status tracking file is missing.",
            remediation="Run `/bmad:sprint-planning` to generate."
        ))
        return

    try:
        from plugins.bmad.lib.status_reconciliation import reconcile_project, EvidenceState
        results = reconcile_project(project_dir)
    except Exception as e:
        # Don't swallow — let the per-check wrapper handle it
        raise RuntimeError(f"reconcile_project failed: {e}") from e

    for evidence in results:
        # DI-4: flag stories with no evidence but marked done
        if evidence.current_status == "done" and evidence.evidence_state == EvidenceState.NOT_STARTED:
            report.findings.append(DoctorFinding(
                category="Status Drift",
                severity=Severity.HIGH,
                title=f"Story {evidence.story_id} marked done with no evidence",
                detail=f"No dev notes, no git commits, no tests found. {evidence.details}",
                remediation="Verify completion or update status to 'pending'."
            ))
        elif evidence.current_status == "done" and evidence.evidence_state == EvidenceState.UNCERTAIN:
            report.findings.append(DoctorFinding(
                category="Status Drift",
                severity=Severity.MEDIUM,
                title=f"Story {evidence.story_id} has weak evidence for 'done'",
                detail=evidence.details,
                remediation="Add dev notes or verify completion."
            ))


def _check_missing_artifacts(project_dir: Path, report: DoctorReport,
                              overrides: dict[str, str]):
    """Cat 4: missing planning/implementation artifacts."""
    expected = {
        "analysis": [
            ("planning-artifacts/product-brief.md", "Product brief"),
        ],
        "planning": [
            ("planning-artifacts/prd-*.md", "PRD"),
        ],
        "solutioning": [
            ("planning-artifacts/architecture-*.md", "Architecture doc"),
            ("planning-artifacts/epics-stories-*.md", "Epics & stories"),
        ],
    }

    for phase, artifacts in expected.items():
        if is_phase_overridden(overrides, phase):
            continue
        for pattern, name in artifacts:
            full_pattern = str(project_dir / pattern)
            if not glob.glob(full_pattern):
                report.findings.append(DoctorFinding(
                    category="Missing Artifacts",
                    severity=Severity.MEDIUM if phase == "analysis" else Severity.HIGH,
                    title=f"Missing {name}",
                    detail=f"Expected artifact not found: {pattern}",
                    remediation=f"Run `/bmad:{phase.replace('ing', '')}` to generate."
                ))


def _check_epic_structure(project_dir: Path, report: DoctorReport):
    """Cat 5: epic structure validation."""

    epics_dir = project_dir / "planning-artifacts"
    if not epics_dir.exists():
        return

    epic_files = glob.glob(str(epics_dir / "epics-stories-*.md"))
    if not epic_files:
        report.findings.append(DoctorFinding(
            category="Epic Structure",
            severity=Severity.MEDIUM,
            title="No epics-stories document found",
            detail="Expected planning-artifacts/epics-stories-*.md",
            remediation="Run `/bmad:epics-stories` to generate."
        ))


def _check_schema_version(project_dir: Path, report: DoctorReport):
    """Cat 6: schema mismatch detection."""
    config_path = project_dir / "bmad" / "config.yaml"
    if not config_path.exists():
        return

    try:
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except (yaml.YAMLError, OSError):
        return

    if not isinstance(config, dict):
        return

    version = config.get("version")
    if version is None:
        report.findings.append(DoctorFinding(
            category="Schema Version",
            severity=Severity.LOW,
            title="Config has no version field",
            detail="Schema version helps migration tools detect outdated projects.",
            remediation="Add `version: 1` to bmad/config.yaml."
        ))


def _check_runtime_drift(project_dir: Path, report: DoctorReport):
    """Cat 7: runtime drift detection.

    Checks if plugin files exist and are properly structured.
    Does NOT check for literal strings that don't exist in the API.
    """
    plugin_dir = project_dir / "plugins" / "bmad"
    if not plugin_dir.exists():
        return

    lib_dir = plugin_dir / "lib"
    if not lib_dir.exists():
        report.findings.append(DoctorFinding(
            category="Runtime Drift",
            severity=Severity.MEDIUM,
            title="Plugin lib/ directory missing",
            detail="plugins/bmad/lib/ should contain shared modules.",
            remediation="Check if the plugin was properly structured."
        ))

    init_path = plugin_dir / "__init__.py"
    if init_path.exists():
        try:
            content = init_path.read_text(encoding="utf-8", errors="replace")
            # Check for hook registration (actual API methods)
            has_hooks = any(kw in content for kw in [
                "register_hook", "register_pre_tool_call",
                "register_post_tool_call", "register_command"
            ])
            if not has_hooks:
                report.findings.append(DoctorFinding(
                    category="Runtime Drift",
                    severity=Severity.LOW,
                    title="Plugin has no hook/command registrations",
                    detail="__init__.py doesn't register any hooks or commands.",
                    remediation="Verify plugin initialization is correct."
                ))
        except OSError as e:
            logger.debug("[doctor] can't read __init__.py: %s", e)


def _check_story_consolidation(project_dir: Path, report: DoctorReport):
    """Cat 8: story consolidation status."""
    status_path = project_dir / "planning-artifacts" / "sprint-status.yaml"
    if not status_path.exists():
        return

    try:
        with open(status_path, encoding="utf-8") as f:
            status = yaml.safe_load(f)
    except (yaml.YAMLError, OSError):
        return

    if not isinstance(status, dict):
        return

    stories = status.get("stories", {})
    if not isinstance(stories, dict):
        return

    old_format = [str(sid) for sid in stories
                  if "." in str(sid) and not str(sid)[0].isdigit()]
    if old_format:
        report.findings.append(DoctorFinding(
            category="Story Consolidation",
            severity=Severity.LOW,
            title=f"{len(old_format)} stories with non-standard IDs",
            detail=f"Stories: {', '.join(old_format[:5])}",
            remediation="Consider running `/bmad:migrate-stories-to-epic`."
        ))


def _check_ocr_integration(project_dir: Path, report: DoctorReport):
    """Cat 9: OCR integration status."""
    ocr_runner = project_dir / "plugins" / "bmad" / "lib" / "ocr_runner.py"
    if ocr_runner.exists():
        try:
            result = subprocess.run(
                ["which", "ocr"], capture_output=True, text=True, timeout=5
            )
            if result.returncode != 0:
                report.findings.append(DoctorFinding(
                    category="OCR Integration",
                    severity=Severity.INFO,
                    title="OCR runner exists but CLI not installed",
                    detail="lib/ocr_runner.py found but `ocr` CLI not in PATH.",
                    remediation="Install OCR CLI or ignore if not needed."
                ))
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.debug("[doctor] OCR check skipped: %s", e)


def _check_spec_blocks(project_dir: Path, report: DoctorReport):
    """Cat 10: Epic 12 spec block adoption."""
    commands_dir = project_dir / "plugins" / "bmad" / "commands"
    if not commands_dir.exists():
        return

    md_files = glob.glob(str(commands_dir / "*.md"))
    if not md_files:
        return

    missing_spec = []
    for md_file in md_files:
        try:
            content = Path(md_file).read_text(encoding="utf-8")
            if "---" not in content[:5] or "spec:" not in content[:200]:
                missing_spec.append(Path(md_file).stem)
        except OSError:
            continue

    if missing_spec:
        report.findings.append(DoctorFinding(
            category="Spec Blocks",
            severity=Severity.LOW,
            title=f"{len(missing_spec)} commands missing spec: blocks",
            detail=f"Commands: {', '.join(missing_spec[:10])}",
            remediation="Run `/bmad:migrate` to add spec: frontmatter."
        ))
