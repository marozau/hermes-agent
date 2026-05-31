"""Tests for _extract_llm_findings / _parse_llm_summary (NN-5).

Covers the LLM markdown parser that feeds multi-source consensus.
"""

from __future__ import annotations

import re

import pytest

# Import the helpers via the plugin module
from plugins.bmad.commands.code_review import _parse_llm_summary, _extract_llm_findings, _FILE_LINE_RE


# ── _FILE_LINE_RE (NN-1) ────────────────────────────────────────────────────


class TestFileLineRegex:
    def test_python_file(self):
        assert _FILE_LINE_RE.search("foo.py:42")

    def test_go_file(self):
        """NN-1: Go files recognized."""
        assert _FILE_LINE_RE.search("main.go:10")

    def test_ruby_file(self):
        """NN-1: Ruby files recognized."""
        assert _FILE_LINE_RE.search("app/models/user.rb:55")

    def test_java_file(self):
        """NN-1: Java files recognized."""
        assert _FILE_LINE_RE.search("src/Main.java:123")

    def test_rust_file(self):
        assert _FILE_LINE_RE.search("lib.rs:7")

    def test_typescript_file(self):
        assert _FILE_LINE_RE.search("index.ts:20")

    def test_dockerfile_not_matched(self):
        """Dockerfile has no extension — not in regex (acceptable trade-off)."""
        assert not _FILE_LINE_RE.search("Dockerfile:42")

    def test_makefile_not_matched(self):
        """Makefile has no extension — not in regex (acceptable trade-off)."""
        assert not _FILE_LINE_RE.search("Makefile:10")

    def test_yaml_file(self):
        assert _FILE_LINE_RE.search("config.yaml:5")

    def test_json_file(self):
        assert _FILE_LINE_RE.search("package.json:3")

    def test_cpp_file(self):
        """NN-1: C++ files recognized."""
        assert _FILE_LINE_RE.search("main.cpp:99")

    def test_sql_file(self):
        """NN-1: SQL files recognized."""
        assert _FILE_LINE_RE.search("migrations/001.sql:15")


# ── _parse_llm_summary ──────────────────────────────────────────────────────


class TestParseLLMSummary:
    def test_empty_summary(self):
        assert _parse_llm_summary("", "blind") == []

    def test_no_file_references(self):
        assert _parse_llm_summary("No issues found.", "blind") == []

    def test_single_finding(self):
        summary = "- The `foo.py:42` has a missing null check."
        findings = _parse_llm_summary(summary, "blind")
        assert len(findings) == 1
        assert findings[0]["file"] == "foo.py"
        assert findings[0]["line"] == 42
        assert findings[0]["source"] == "blind"

    def test_multiple_findings(self):
        summary = (
            "- Critical issue in `auth.py:10` — bypasses validation\n"
            "- Minor: `utils.py:55` unused import\n"
        )
        findings = _parse_llm_summary(summary, "edge")
        assert len(findings) == 2
        assert findings[0]["source"] == "edge"
        assert findings[1]["source"] == "edge"

    def test_severity_critical(self):
        """NN-2: 'critical' keyword → MAJOR."""
        summary = "- Critical bug in `x.py:1` — crashes on startup"
        findings = _parse_llm_summary(summary, "blind")
        assert findings[0]["severity"] == "MAJOR"

    def test_severity_default_major(self):
        """NN-2: no keyword → MAJOR (conservative default)."""
        summary = "- The `x.py:1` drops the audit log entry silently"
        findings = _parse_llm_summary(summary, "blind")
        assert findings[0]["severity"] == "MAJOR"

    def test_severity_nit(self):
        summary = "- Suggestion: `x.py:1` consider using f-strings"
        findings = _parse_llm_summary(summary, "blind")
        assert findings[0]["severity"] == "NIT"

    def test_preserves_parens_in_message(self):
        """NN-4: parens preserved — open(file, w) not mangled."""
        summary = "- `x.py:1` The deprecated open(file, w) call should use Path.write_text(content)"
        findings = _parse_llm_summary(summary, "blind")
        assert "(" in findings[0]["message"]
        assert ")" in findings[0]["message"]

    def test_full_block_text_preserved(self):
        """NN-3: full block text preserved, not just first line."""
        summary = "- `x.py:1` This silently drops the audit log entry — system can't reconstruct who did what."
        findings = _parse_llm_summary(summary, "blind")
        assert "audit log" in findings[0]["message"]

    def test_go_file_finding(self):
        """NN-1: Go file finding extracted."""
        summary = "- `main.go:42` goroutine leak — never cancelled"
        findings = _parse_llm_summary(summary, "blind")
        assert len(findings) == 1
        assert findings[0]["file"] == "main.go"
        assert findings[0]["line"] == 42

    def test_markdown_stripped(self):
        """Markdown formatting removed from message."""
        summary = "- **Critical** `x.py:1` — issue with `code` and [link]"
        findings = _parse_llm_summary(summary, "blind")
        assert "**" not in findings[0]["message"]
        assert "`" not in findings[0]["message"]
        assert "[" not in findings[0]["message"]


# ── _extract_llm_findings (R4-1) ────────────────────────────────────────────


class TestExtractLLMFindings:
    def test_role_mapping_blind(self):
        """Blind Hunter role maps to 'blind' source key."""
        reviewers = [{"role": "Blind Hunter"}]
        results = [{"summary": "- `foo.py:42` critical issue"}]
        out = _extract_llm_findings(results, reviewers)
        assert "blind" in out
        assert out["blind"][0]["source"] == "blind"

    def test_role_mapping_edge(self):
        """Edge Case Hunter role maps to 'edge' source key."""
        reviewers = [{"role": "Edge Case Hunter"}]
        results = [{"summary": "- `foo.py:10` edge case"}]
        out = _extract_llm_findings(results, reviewers)
        assert "edge" in out

    def test_role_mapping_auditor(self):
        """Acceptance Auditor role maps to 'auditor' source key."""
        reviewers = [{"role": "Acceptance Auditor"}]
        results = [{"summary": "- `foo.py:5` missing AC"}]
        out = _extract_llm_findings(results, reviewers)
        assert "auditor" in out

    def test_unrecognized_role_fallback(self):
        """Unrecognized role falls back to 'llm_N' key."""
        reviewers = [{"role": "Custom Reviewer"}]
        results = [{"summary": "- `foo.py:1` issue"}]
        out = _extract_llm_findings(results, reviewers)
        assert "llm_0" in out

    def test_errored_reviewer_skipped(self):
        """Reviewer with error=True is skipped, not included in output."""
        reviewers = [{"role": "Blind Hunter"}, {"role": "Edge Case Hunter"}]
        results = [
            {"summary": "- `foo.py:1` issue"},
            {"error": True, "summary": "timeout"},
        ]
        out = _extract_llm_findings(results, reviewers)
        assert "blind" in out
        assert "edge" not in out

    def test_empty_inputs(self):
        """Empty reviewers and results return empty dict."""
        assert _extract_llm_findings([], []) == {}
        assert _extract_llm_findings([{"summary": "..."}], []) == {}

    def test_more_results_than_reviewers(self):
        """Extra results beyond reviewer count are safely ignored."""
        reviewers = [{"role": "Blind Hunter"}]
        results = [
            {"summary": "- `foo.py:1` issue"},
            {"summary": "- `bar.py:2` extra"},
        ]
        out = _extract_llm_findings(results, reviewers)
        assert len(out) == 1
        assert "blind" in out
