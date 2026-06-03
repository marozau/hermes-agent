"""Handler for /bmad:dev-story — implement a user story.

Story 7.2: Accepts epic-doc anchor format <path>#story-X.Y to extract
a specific story section from an epic document.
Story 12.3: Uses spec: frontmatter + render_command for structured output.
"""

from __future__ import annotations

import re
from pathlib import Path

COMMAND = "dev-story"


def _extract_story_section(epic_path: Path, story_id: str) -> str | None:
    """Extract a story section from an epic document by story ID.

    Looks for patterns like "### 7.3 ..." or "| 7.3 |" and extracts
    the section until the next story heading or end of file.
    """
    text = epic_path.read_text(encoding="utf-8")
    # Find heading for this story
    pattern = re.compile(
        rf"(^###\s+{re.escape(story_id)}\b.*$)",
        re.MULTILINE,
    )
    match = pattern.search(text)
    if not match:
        return None

    start = match.start()
    # Find next story heading (### X.Y) or end of file
    rest = text[match.end():]
    next_heading = re.search(r"^###\s+\d+\.\d+\b", rest, re.MULTILINE)
    end = match.end() + next_heading.start() if next_heading else len(text)

    return text[start:end].strip()


def handler(ctx, args: str) -> str:
    raw_dir = getattr(ctx, "working_directory", None) or "."
    project_dir = Path(raw_dir).resolve()

    config_path = project_dir / "bmad" / "config.yaml"
    if not config_path.exists():
        return "⚠️  Not a BMAD project. Run `/bmad:init` to initialize."

    from plugins.bmad.lib import phases
    from plugins.bmad.lib.status import load

    state = load(project_dir)
    level = state.get("level", 1)
    ok, reason = phases.can_run(COMMAND, state, level)
    if not ok:
        return f"🚫 **{COMMAND} blocked:** {reason}"

    # Story 7.2: Epic-doc anchor support — <path>#story-X.Y
    args_stripped = args.strip()
    if "#story-" in args_stripped:
        parts = args_stripped.split("#story-", 1)
        epic_path_str = parts[0].strip()
        story_id = parts[1].strip()
        if epic_path_str and story_id:
            epic_path = Path(epic_path_str)
            if not epic_path.is_absolute():
                epic_path = project_dir / epic_path
            if epic_path.exists():
                section = _extract_story_section(epic_path, story_id)
                if section:
                    return section
                return f"⚠️  Story {story_id} not found in {epic_path}"
            return f"⚠️  Epic document not found: {epic_path}"

    body_path = Path(__file__).with_name("dev-story.md")
    raw_content = body_path.read_text(encoding="utf-8")

    # Story 12.3: Parse spec and render
    from plugins.bmad.lib.spec_parser import parse_command_body
    from plugins.bmad.lib.render import render_command

    spec, body = parse_command_body(raw_content)
    return render_command(spec, body, args=args_stripped, ctx=ctx)
