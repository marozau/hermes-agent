"""Story 8.1 — Vendor YAKE keyword extractor tests."""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure lib/ is importable
HERMES_ROOT = Path.home() / ".hermes"
if str(HERMES_ROOT) not in sys.path:
    sys.path.insert(0, str(HERMES_ROOT))

from lib._yake import extract_keywords


class TestExtractKeywords:
    """AC1: extract_keywords returns list[str] of up to 8 candidates, stdlib-only."""

    def test_returns_list_of_strings(self):
        result = extract_keywords("kubernetes cluster deployment with docker containers")
        assert isinstance(result, list)
        assert all(isinstance(k, str) for k in result)

    def test_max_eight_candidates(self):
        text = (
            "kubernetes cluster deployment with docker containers and k3d "
            "configuration for local development. prefect workflow orchestration "
            "with hermes agent memory management and trajectory recording. "
            "typescript type error remediation in monorepo workspace. "
            "rust tauri application development with async runtime patterns."
        )
        result = extract_keywords(text)
        assert len(result) <= 8

    def test_empty_string_returns_empty(self):
        assert extract_keywords("") == []
        assert extract_keywords("   ") == []

    def test_stopwords_excluded(self):
        result = extract_keywords("the is a an of to in for on with at by from")
        # Pure stopwords → no keywords
        assert result == []

    def test_single_word(self):
        result = extract_keywords("kubernetes")
        assert len(result) >= 1
        assert "kubernetes" in result[0].lower()

    def test_no_third_party_imports(self):
        """Verify _yake uses only stdlib."""
        import importlib
        mod = importlib.import_module("lib._yake")
        source_file = Path(mod.__file__)
        source = source_file.read_text()
        # No import of third-party packages
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if stripped.startswith("import ") or stripped.startswith("from "):
                # Only stdlib allowed
                for pkg in ("math", "re", "collections", "typing", "__future__"):
                    if pkg in stripped:
                        break
                else:
                    if "lib._yake" not in stripped and "lib.hermes" not in stripped:
                        assert False, f"Non-stdlib import found: {stripped}"

    def test_multiword_ngram(self):
        """Keywords should include multi-word phrases when relevant."""
        result = extract_keywords(
            "Set up k3d cluster for local kubernetes development. "
            "Configure k3d api-port and registry settings."
        )
        # Should find something related to k3d
        keywords_lower = " ".join(result).lower()
        assert "k3d" in keywords_lower

    def test_technical_terms_ranked_high(self):
        """Technical terms should rank above common words."""
        result = extract_keywords(
            "Debug hermes memory consolidation pipeline. "
            "The pipeline uses prefact workflow engine. "
            "Check trajectory recording in raw JSONL layer."
        )
        keywords_lower = " ".join(result).lower()
        # Technical terms should appear
        assert any(kw in keywords_lower for kw in ["hermes", "pipeline", "trajectory", "memory", "prefact"])
