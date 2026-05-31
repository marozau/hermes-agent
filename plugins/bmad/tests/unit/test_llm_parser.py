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
