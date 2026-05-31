"""OCR runner — subprocess wrapper, JSON parser, normalizer (Story 8.3).

Wraps the `ocr review` CLI tool. Pure-functional core + thin subprocess
boundary. Handles edge cases: not installed (OI-10), schema change (OI-14),
timeout, empty diff.
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Data models ──────────────────────────────────────────────────────────────

SEVERITY_MAP = {
    "HIGH": "MAJOR",
    "MEDIUM": "MINOR",
    "MED": "MINOR",
    "LOW": "NIT",
    "INFO": "NIT",
}


@dataclass
class OCRFinding:
    """Normalized finding from OCR output."""

    rule_id: str
    severity: str  # MAJOR, MINOR, NIT (normalized)
    file: str
    line: int
    message: str
    source: str = "ocr"
    category: str = ""
    raw_severity: str = ""  # Original HIGH/MED/LOW before normalization


@dataclass
class OCRResult:
    """Result of an OCR review run."""

    findings: list[OCRFinding] = field(default_factory=list)
    success: bool = True
    error: str = ""
    wall_clock_ms: int = 0
    installed: bool = True


# ── Core functions ───────────────────────────────────────────────────────────


def check_ocr_installed() -> bool:
    """Check if `ocr` CLI is available on PATH."""
    try:
        r = subprocess.run(
            ["ocr", "--version"],
            capture_output=True,
            timeout=10,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def resolve_rule_path(project_dir: Path, rule_path: str | None = None) -> Path | None:
    """Resolve OCR rule file path relative to project directory.

    Checks:
    1. Explicit rule_path from config
    2. bmad/.opencodereview/rule.json in project
    3. None (use OCR built-in rules)
    """
    if rule_path:
        resolved = project_dir / rule_path
        if resolved.exists():
            return resolved
        logger.warning("[ocr_runner] Rule path not found: %s", resolved)
        return None

    default = project_dir / "bmad" / ".opencodereview" / "rule.json"
    if default.exists():
        return default

    return None


def parse_ocr_json(raw: str) -> list[dict[str, Any]]:
    """Parse OCR JSON output. OI-14: fails LOUDLY on schema change."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"OCR output is not valid JSON: {e}") from e

    # OCR outputs {"findings": [...]} or just [...]
    if isinstance(data, list):
        findings_raw = data
    elif isinstance(data, dict):
        findings_raw = data.get("findings", data.get("issues", data.get("results", [])))
        if not isinstance(findings_raw, list):
            raise ValueError(
                f"OCR JSON schema changed: expected list under 'findings', got {type(findings_raw).__name__}"
            )
    else:
        raise ValueError(f"OCR JSON schema changed: expected dict or list, got {type(data).__name__}")

    # OI-14: Validate each finding has required fields
    required = {"rule_id", "severity", "file", "line", "message"}
    for i, f in enumerate(findings_raw):
        if not isinstance(f, dict):
            raise ValueError(f"OCR finding {i} is not a dict: {type(f).__name__}")
        missing = required - set(f.keys())
        if missing:
            raise ValueError(f"OCR finding {i} missing fields: {missing}")

    return findings_raw


def normalize_finding(raw: dict[str, Any]) -> OCRFinding:
    """Normalize a single OCR finding: severity mapping (OD-8)."""
    raw_sev = str(raw.get("severity", "LOW")).upper()
    normalized = SEVERITY_MAP.get(raw_sev, "NIT")
    return OCRFinding(
        rule_id=str(raw.get("rule_id", "")),
        severity=normalized,
        file=str(raw.get("file", "")),
        line=int(raw.get("line", 0)),
        message=str(raw.get("message", "")),
        source="ocr",
        category=str(raw.get("category", "")),
        raw_severity=raw_sev,
    )


def run_ocr_review(
    diff_text: str,
    rule_path: str | Path | None = None,
    timeout_seconds: int = 120,
) -> OCRResult:
    """Run OCR review on a diff. OI-10: warns + returns empty if not installed."""
    import time

    if not diff_text.strip():
        return OCRResult(success=True, findings=[], error="empty diff")

    if not check_ocr_installed():
        logger.warning("[ocr_runner] OCR not installed — returning empty findings (OI-10)")
        return OCRResult(success=True, findings=[], installed=False, error="ocr not installed")

    cmd = ["ocr", "review", "--format", "json"]
    if rule_path:
        cmd.extend(["--rule", str(rule_path)])

    start = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            input=diff_text,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        elapsed_ms = int((time.monotonic() - start) * 1000)

        if proc.returncode != 0:
            stderr = proc.stderr.strip()
            logger.warning("[ocr_runner] OCR exited %d: %s", proc.returncode, stderr[:200])
            return OCRResult(
                success=False,
                error=f"OCR exited {proc.returncode}: {stderr[:200]}",
                wall_clock_ms=elapsed_ms,
            )

        raw_findings = parse_ocr_json(proc.stdout)
        findings = [normalize_finding(f) for f in raw_findings]
        return OCRResult(success=True, findings=findings, wall_clock_ms=elapsed_ms)

    except subprocess.TimeoutExpired:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        logger.warning("[ocr_runner] OCR timed out after %ds", timeout_seconds)
        return OCRResult(
            success=False,
            error=f"OCR timed out after {timeout_seconds}s",
            wall_clock_ms=elapsed_ms,
        )
    except ValueError as e:
        # OI-14: Schema change — fail loud
        logger.error("[ocr_runner] OCR schema error: %s", e)
        return OCRResult(success=False, error=str(e))
