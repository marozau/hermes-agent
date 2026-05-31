"""Tests for Epic 8 — OCR Integration.

Covers:
- 8.1: OCRConfig validation
- 8.3: ocr_runner.py (parse, normalize, edge cases)
- 8.5: ocr_triage.py (consensus classification, merge)
- 8.6: Profile schema for OCR overrides
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from plugins.bmad.lib.ocr_runner import (
    OCRFinding,
    OCRResult,
    check_ocr_installed,
    normalize_finding,
    parse_ocr_json,
    run_ocr_review,
    SEVERITY_MAP,
)
from plugins.bmad.lib.ocr_triage import (
    CONSENSUS_TABLE,
    TriageFinding,
    classify_consensus,
    merge_findings,
    normalize_ocr_finding,
)
from plugins.bmad.lib.config import OCRConfig, load_workspace_config


# ── Story 8.1: Config schema ────────────────────────────────────────────────


class TestOCRConfig:
    def test_default_disabled(self):
        """OI-9: OCR disabled by default."""
        cfg = OCRConfig()
        assert cfg.enabled is False

    def test_enabled(self):
        """OCR can be enabled."""
        cfg = OCRConfig(enabled=True)
        assert cfg.enabled is True

    def test_custom_timeout(self):
        """Custom timeout accepted."""
        cfg = OCRConfig(timeout_seconds=60)
        assert cfg.timeout_seconds == 60

    def test_invalid_timeout(self):
        """Timeout must be positive."""
        with pytest.raises(Exception):
            OCRConfig(timeout_seconds=0)

    def test_custom_languages(self):
        """Custom language list accepted."""
        cfg = OCRConfig(languages=["python", "rust"])
        assert cfg.languages == ["python", "rust"]

    def test_rule_path(self):
        """Custom rule path accepted."""
        cfg = OCRConfig(rule_path=".opencodereview/rule.json")
        assert cfg.rule_path == ".opencodereview/rule.json"


class TestConfigParsing:
    def test_parse_no_ocr_block(self, tmp_path):
        """Missing code_review block → default OCRConfig (disabled)."""
        (tmp_path / "bmad").mkdir()
        (tmp_path / "bmad" / "config.yaml").write_text("project_name: test\n")
        cfg = load_workspace_config(tmp_path)
        assert cfg.code_review_ocr.enabled is False

    def test_parse_ocr_enabled(self, tmp_path):
        """code_review.ocr.enabled: true parsed correctly."""
        (tmp_path / "bmad").mkdir()
        (tmp_path / "bmad" / "config.yaml").write_text(
            "project_name: test\ncode_review:\n  ocr:\n    enabled: true\n"
        )
        cfg = load_workspace_config(tmp_path)
        assert cfg.code_review_ocr.enabled is True

    def test_parse_ocr_with_timeout(self, tmp_path):
        """code_review.ocr.timeout_seconds parsed."""
        (tmp_path / "bmad").mkdir()
        (tmp_path / "bmad" / "config.yaml").write_text(
            "project_name: test\ncode_review:\n  ocr:\n    enabled: true\n    timeout_seconds: 30\n"
        )
        cfg = load_workspace_config(tmp_path)
        assert cfg.code_review_ocr.timeout_seconds == 30


# ── Story 8.3: OCR runner ───────────────────────────────────────────────────


class TestParseOCRJson:
    def test_parse_list_format(self):
        """OCR output as list of findings."""
        raw = json.dumps([
            {"rule_id": "PY001", "severity": "HIGH", "file": "x.py", "line": 10, "message": "test"},
        ])
        findings = parse_ocr_json(raw)
        assert len(findings) == 1
        assert findings[0]["rule_id"] == "PY001"

    def test_parse_dict_format(self):
        """OCR output as {findings: [...]}."""
        raw = json.dumps({
            "findings": [
                {"rule_id": "PY002", "severity": "MED", "file": "y.py", "line": 5, "message": "test2"},
            ]
        })
        findings = parse_ocr_json(raw)
        assert len(findings) == 1
        assert findings[0]["rule_id"] == "PY002"

    def test_parse_invalid_json(self):
        """OI-14: Invalid JSON raises ValueError."""
        with pytest.raises(ValueError, match="not valid JSON"):
            parse_ocr_json("not json")

    def test_parse_schema_changed(self):
        """OI-14: Schema change raises ValueError."""
        with pytest.raises(ValueError, match="not valid|schema|expected"):
            parse_ocr_json(json.dumps(42))

    def test_parse_missing_fields(self):
        """OI-14: Missing required fields raises ValueError."""
        with pytest.raises(ValueError, match="missing fields"):
            parse_ocr_json(json.dumps([{"rule_id": "X"}]))


class TestNormalizeFinding:
    def test_high_to_major(self):
        """OD-8: HIGH → MAJOR."""
        f = normalize_finding({"rule_id": "X", "severity": "HIGH", "file": "a.py", "line": 1, "message": "m"})
        assert f.severity == "MAJOR"
        assert f.raw_severity == "HIGH"

    def test_med_to_minor(self):
        """OD-8: MED → MINOR."""
        f = normalize_finding({"rule_id": "X", "severity": "MED", "file": "a.py", "line": 1, "message": "m"})
        assert f.severity == "MINOR"

    def test_low_to_nit(self):
        """OD-8: LOW → NIT."""
        f = normalize_finding({"rule_id": "X", "severity": "LOW", "file": "a.py", "line": 1, "message": "m"})
        assert f.severity == "NIT"

    def test_medium_to_minor(self):
        """MEDIUM → MINOR."""
        f = normalize_finding({"rule_id": "X", "severity": "MEDIUM", "file": "a.py", "line": 1, "message": "m"})
        assert f.severity == "MINOR"

    def test_source_is_ocr(self):
        """All normalized findings have source=ocr."""
        f = normalize_finding({"rule_id": "X", "severity": "LOW", "file": "a.py", "line": 1, "message": "m"})
        assert f.source == "ocr"


class TestRunOCRReview:
    def test_empty_diff(self):
        """Empty diff returns success with no findings."""
        result = run_ocr_review("")
        assert result.success is True
        assert result.findings == []

    @patch("plugins.bmad.lib.ocr_runner.check_ocr_installed", return_value=False)
    def test_not_installed(self, mock_check):
        """OI-10: OCR not installed → warn + empty findings."""
        result = run_ocr_review("some diff")
        assert result.success is True
        assert result.findings == []
        assert result.installed is False

    @patch("plugins.bmad.lib.ocr_runner.check_ocr_installed", return_value=True)
    @patch("plugins.bmad.lib.ocr_runner.subprocess.run")
    def test_successful_review(self, mock_run, mock_check):
        """Successful OCR review returns normalized findings."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps([{
                "rule_id": "PY001", "severity": "HIGH",
                "file": "x.py", "line": 10, "message": "env mutation",
            }]),
            stderr="",
        )
        result = run_ocr_review("diff --git a/x.py")
        assert result.success is True
        assert len(result.findings) == 1
        assert result.findings[0].severity == "MAJOR"

    @patch("plugins.bmad.lib.ocr_runner.check_ocr_installed", return_value=True)
    @patch("plugins.bmad.lib.ocr_runner.subprocess.run")
    def test_timeout(self, mock_run, mock_check):
        """OCR timeout returns error result."""
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="ocr", timeout=120)
        result = run_ocr_review("large diff")
        assert result.success is False
        assert "timed out" in result.error

    @patch("plugins.bmad.lib.ocr_runner.check_ocr_installed", return_value=True)
    @patch("plugins.bmad.lib.ocr_runner.subprocess.run")
    def test_nonzero_exit(self, mock_run, mock_check):
        """OCR nonzero exit returns error result."""
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="parse error")
        result = run_ocr_review("bad diff")
        assert result.success is False
        assert "exited 1" in result.error


# ── Story 8.5: Triage consensus ─────────────────────────────────────────────


class TestClassifyConsensus:
    def test_ocr_only(self):
        """OCR only → patch."""
        assert classify_consensus({"ocr"}) == "patch"

    def test_blind_only(self):
        """Blind only → decision_needed."""
        assert classify_consensus({"blind"}) == "decision_needed"

    def test_blind_plus_ocr(self):
        """Blind + OCR → patch (high confidence)."""
        assert classify_consensus({"blind", "ocr"}) == "patch"

    def test_blind_edge_ocr(self):
        """3 sources → patch_strong."""
        assert classify_consensus({"blind", "edge", "ocr"}) == "patch_strong"

    def test_unanimous(self):
        """All 4 → must_fix."""
        assert classify_consensus({"blind", "edge", "auditor", "ocr"}) == "must_fix"

    def test_edge_only(self):
        """Edge only → decision_needed."""
        assert classify_consensus({"edge"}) == "decision_needed"

    def test_blind_edge_auditor(self):
        """3 LLM sources → patch_strong."""
        assert classify_consensus({"blind", "edge", "auditor"}) == "patch_strong"


class TestMergeFindings:
    def test_no_findings(self):
        """No findings → empty result."""
        assert merge_findings() == []

    def test_single_ocr_finding(self):
        """Single OCR finding → patch classification."""
        ocr = [{"file": "x.py", "line": 10, "message": "test", "severity": "MAJOR"}]
        results = merge_findings(ocr_findings=ocr)
        assert len(results) == 1
        assert results[0].classification == "patch"
        assert results[0].sources == {"ocr"}

    def test_blind_plus_ocr_same_location(self):
        """Same location flagged by blind + OCR → patch with 2 sources."""
        blind = [{"file": "x.py", "line": 10, "message": "env mutation", "severity": "MAJOR"}]
        ocr = [{"file": "x.py", "line": 10, "message": "os.environ direct write", "severity": "MINOR"}]
        results = merge_findings(blind_findings=blind, ocr_findings=ocr)
        assert len(results) == 1
        assert results[0].classification == "patch"
        assert results[0].consensus_count == 2
        assert results[0].severity == "MAJOR"  # Takes highest severity

    def test_unanimous_finding(self):
        """All 4 sources → must_fix."""
        base = {"file": "x.py", "line": 5, "message": "shell injection", "severity": "MAJOR"}
        results = merge_findings(
            blind_findings=[base],
            edge_findings=[base],
            auditor_findings=[base],
            ocr_findings=[base],
        )
        assert len(results) == 1
        assert results[0].classification == "must_fix"
        assert results[0].consensus_count == 4

    def test_different_locations_separate(self):
        """Different locations → separate findings."""
        blind = [{"file": "a.py", "line": 1, "message": "m1", "severity": "MAJOR"}]
        ocr = [{"file": "b.py", "line": 2, "message": "m2", "severity": "MINOR"}]
        results = merge_findings(blind_findings=blind, ocr_findings=ocr)
        assert len(results) == 2

    def test_sort_order(self):
        """Must-fix first, then by severity."""
        ocr_major = [{"file": "a.py", "line": 1, "message": "m", "severity": "MAJOR"}]
        all_four = [{"file": "b.py", "line": 1, "message": "m", "severity": "MINOR"}]
        results = merge_findings(
            blind_findings=[{"file": "b.py", "line": 1, "message": "m", "severity": "MINOR"}],
            edge_findings=[{"file": "b.py", "line": 1, "message": "m", "severity": "MINOR"}],
            auditor_findings=[{"file": "b.py", "line": 1, "message": "m", "severity": "MINOR"}],
            ocr_findings=[{"file": "a.py", "line": 1, "message": "m", "severity": "MAJOR"}, {"file": "b.py", "line": 1, "message": "m", "severity": "MINOR"}],
        )
        assert results[0].classification == "must_fix"


class TestNormalizeOCRFinding:
    def test_normalizes_to_triage_format(self):
        """OCR finding normalized to triage input format."""
        f = OCRFinding(
            rule_id="PY001", severity="MAJOR", file="x.py",
            line=10, message="test", source="ocr",
        )
        result = normalize_ocr_finding(f)
        assert result["source"] == "ocr"
        assert result["file"] == "x.py"
        assert result["severity"] == "MAJOR"
