"""Handler for /bmad:migrate-stories-to-epic — legacy story migration (Story 7.6).

Scans implementation-artifacts/stories/ for legacy story files and reports
what would be consolidated into epic format.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

COMMAND = "migrate-stories-to-epic"


def _parse_args(args: str) -> dict:
    """Parse migrate-stories-to-epic arguments."""
    result = {"epic": "", "dry_run": False}
    tokens = args.strip().split()
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--epic" and i + 1 < len(tokens):
            i += 1
            result["epic"] = tokens[i]
        elif tok.startswith("--epic="):
            result["epic"] = tok.split("=", 1)[1]
        elif tok == "--dry-run":
            result["dry_run"] = True
        i += 1
    return result


def _scan_legacy_stories(stories_dir: Path) -> list[dict]:
    """Scan a stories directory for legacy story files.

    Returns list of dicts with id, title, path, content_preview.
    """
    if not stories_dir.exists():
        return []

    stories = []
    story_pattern = re.compile(r"(?:story[-_]?|s)(\d+(?:\.\d+)?)", re.IGNORECASE)
    heading_pattern = re.compile(r"^#\s+(?:Story\s+)?(\d+(?:\.\d+)?)\s*[:\-—]?\s*(.*)", re.MULTILINE)

    for path in sorted(stories_dir.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower() not in (".md", ".yaml", ".yml", ".txt"):
            continue

        content = path.read_text(encoding="utf-8", errors="replace")

        # Try to extract story ID from filename
        story_id = ""
        m = story_pattern.search(path.stem)
        if m:
            story_id = m.group(1)

        # Try to extract from heading
        title = ""
        if not story_id:
            hm = heading_pattern.search(content)
            if hm:
                story_id = hm.group(1)
                title = hm.group(2).strip()

        if not story_id:
            story_id = path.stem

        if not title:
            # Try first heading
            hm = heading_pattern.search(content)
            if hm:
                title = hm.group(2).strip()
            else:
                title = path.stem.replace("-", " ").replace("_", " ").title()

        stories.append({
            "id": story_id,
            "title": title,
            "path": str(path),
            "size": path.stat().st_size,
            "preview": content[:200].strip(),
        })

    return stories


def handler(ctx, args: str) -> str:
    """Handle /bmad:migrate-stories-to-epic.

    Scans implementation-artifacts/stories/ for legacy story files and
    reports what would be consolidated into epic format.
    """
    raw_dir = getattr(ctx, "working_directory", None) or "."
    project_dir = Path(raw_dir).resolve()

    config_path = project_dir / "bmad" / "config.yaml"
    if not config_path.exists():
        return "⚠️  Not a BMAD project. Run `/bmad:init` to initialize."

    parsed = _parse_args(args)
    epic_num = parsed["epic"]
    dry_run = parsed["dry_run"]

    stories_dir = project_dir / "implementation-artifacts" / "stories"
    if not stories_dir.exists():
        return f"⚠️  No stories directory found at: `{stories_dir}`"

    legacy_stories = _scan_legacy_stories(stories_dir)

    if not legacy_stories:
        return "✅ No legacy story files found in implementation-artifacts/stories/"

    # Build report
    lines = [
        f"# Legacy Story Migration{' (DRY RUN)' if dry_run else ''}",
        "",
        f"Found **{len(legacy_stories)}** legacy story files in `implementation-artifacts/stories/`",
        "",
    ]

    if epic_num:
        lines.append(f"Target epic: **{epic_num}**")
        lines.append("")

    lines.append("## Files to Migrate")
    lines.append("")

    for story in legacy_stories:
        lines.append(f"- **{story['id']}**: {story['title']}")
        lines.append(f"  - Path: `{story['path']}`")
        lines.append(f"  - Size: {story['size']} bytes")
        lines.append("")

    if dry_run:
        lines.extend([
            "---",
            "",
            "🔍 **This was a dry run.** No files were modified.",
            "",
            "To perform the migration, run without `--dry-run`.",
        ])
    else:
        lines.extend([
            "---",
            "",
            "⚠️  **Migration not yet implemented.**",
            "This command currently reports what would be migrated.",
            "Actual file consolidation will be added in a future version.",
            "",
            "For now, manually create an epic document referencing these stories.",
        ])

    return "\n".join(lines)
