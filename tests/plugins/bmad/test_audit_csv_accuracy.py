"""Tests for audit-inventory CSV accuracy."""

import csv
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
SKILLS_DIR = REPO_ROOT / "skills" / "bmad"
CSV_PATH = REPO_ROOT / "planning-artifacts" / "audit-inventory-epic15-2.csv"


class TestAuditInventoryCsv:
    """Audit CSV must accurately reflect the filesystem."""

    def test_csv_row_count_matches_filesystem(self):
        """CSV row count must equal actual SKILL.md file count."""
        skill_files = list(SKILLS_DIR.rglob("SKILL.md"))
        with open(CSV_PATH) as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == len(skill_files), (
            f"CSV has {len(rows)} rows but filesystem has {len(skill_files)} SKILL.md files. "
            f"Phantom or missing entries detected."
        )

    def test_every_csv_skill_exists_on_disk(self):
        """Every skill listed in CSV must have a corresponding SKILL.md."""
        with open(CSV_PATH) as f:
            rows = list(csv.DictReader(f))
        for row in rows:
            skill_name = row["skill"]
            expected = SKILLS_DIR / skill_name / "SKILL.md"
            assert expected.exists(), (
                f"CSV claims skill '{skill_name}' but {expected} doesn't exist. "
                f"Path resolution bug — skill may have wrong prefix or be phantom."
            )

    def test_every_filesystem_skill_in_csv(self):
        """Every SKILL.md on disk must be listed in the CSV."""
        skill_files = list(SKILLS_DIR.rglob("SKILL.md"))
        with open(CSV_PATH) as f:
            csv_skills = {r["skill"] for r in csv.DictReader(f)}
        for skill_file in skill_files:
            rel = str(skill_file.relative_to(SKILLS_DIR).parent)
            assert rel in csv_skills, (
                f"Filesystem skill '{rel}' not found in CSV. "
                f"Audit missed this skill — add it to the inventory."
            )

    def test_no_bmad_prefix_on_root_skills(self):
        """Root-level skills (critics, status) must not have 'bmad/' prefix in CSV."""
        with open(CSV_PATH) as f:
            rows = list(csv.DictReader(f))
        for row in rows:
            if row["skill"].startswith("bmad/"):
                assert False, (
                    f"CSV has '{row['skill']}' with 'bmad/' prefix. "
                    f"Root-level skills should be just 'critics', 'status', etc."
                )

    def test_nested_skills_have_full_path(self):
        """Nested skills must include parent subdir prefix (e.g. bmm/research/...)."""
        with open(CSV_PATH) as f:
            rows = list(csv.DictReader(f))
        nested = [
            "bmm/research/bmad-domain-research",
            "bmm/research/bmad-market-research",
            "bmm/research/bmad-technical-research",
            "bmm/research/gds-domain-research",
            "bmb/module-builder/assets/setup-skill-template",
        ]
        csv_skills = {r["skill"] for r in rows}
        for skill in nested:
            assert skill in csv_skills, (
                f"Nested skill '{skill}' missing from CSV or has truncated path. "
                f"Must include full parent-subdir prefix."
            )
