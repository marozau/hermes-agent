"""Handler for /bmad:dev-story — implement a user story.

Story 7.2: Accepts epic-doc anchor format <path>#story-X.Y to extract
a specific story section from an epic document.
Story 12.3: Uses spec: frontmatter + render_command for structured output.
Story 13.8: Wire predicate_runner.run_predicates to dev-story handler (T-11).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

COMMAND = "dev-story"
logger = logging.getLogger(__name__)


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


def _write_predicate_results(
    project_dir: Path, story_id: str, results: list[dict],
) -> None:
    """T-11: Write predicate results to sprint-status.yaml.

    Stores under predicate_results.<story_id>.<predicate_name>.
    Creates the file if it doesn't exist.
    """
    import yaml

    status_path = project_dir / "planning-artifacts" / "sprint-status.yaml"
    try:
        if status_path.exists():
            data = yaml.safe_load(status_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        else:
            data = {}
    except (yaml.YAMLError, OSError, PermissionError, UnicodeDecodeError) as e:
        logger.warning("[dev_story] Failed to load sprint-status.yaml: %s", e)
        data = {}

    pred_results = data.setdefault("predicate_results", {})
    story_results = pred_results.setdefault(story_id, {})
    for r in results:
        desc = r.get("description", "unknown")
        # Use a sanitized key from the description
        key = re.sub(r"[^a-z0-9]+", "_", desc.lower()).strip("_")
        story_results[key] = {
            "passed": r.get("passed"),
            "reason": r.get("reason", ""),
        }

    try:
        import tempfile, os
        status_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=str(status_path.parent), suffix=".tmp"
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as tmp_f:
                tmp_f.write(yaml.safe_dump(data, sort_keys=False))
                tmp_f.flush()
                os.fsync(tmp_f.fileno())
            os.replace(tmp_path, str(status_path))
        except Exception:
            os.unlink(tmp_path)
            raise
    except (yaml.YAMLError, OSError, PermissionError) as e:
        logger.warning("[dev_story] Failed to write predicate results: %s", e)


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
    body_path = Path(__file__).with_name("dev-story.md")
    story_id = None
    spec = None

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
                    from plugins.bmad.lib.spec_parser import parse_command_body
                    from plugins.bmad.lib.render import render_command
                    spec, _ = parse_command_body(body_path.read_text(encoding="utf-8"))
                    result = render_command(spec, section, args=args_stripped, ctx=ctx, template_body=False)
                    # T-11: Run predicates after rendering
                    if spec and spec.predicate_module:
                        _run_and_record_predicates(project_dir, spec, story_id, ctx)
                    return result
                return f"⚠️  Story {story_id} not found in {epic_path}"
            return f"⚠️  Epic document not found: {epic_path}"

    raw_content = body_path.read_text(encoding="utf-8")

    # Story 12.3: Parse spec and render
    from plugins.bmad.lib.spec_parser import parse_command_body
    from plugins.bmad.lib.render import render_command

    spec, body = parse_command_body(raw_content)
    result = render_command(spec, body, args=args_stripped, ctx=ctx)

    # T-11: Run predicates after rendering (non-anchor path)
    if spec and spec.predicate_module:
        # Use a fallback story_id derived from args or "default"
        fallback_id = story_id or args_stripped.split()[0] if args_stripped.strip() else "default"
        _run_and_record_predicates(project_dir, spec, fallback_id, ctx)

    return result


def _run_and_record_predicates(
    project_dir: Path, spec, story_id: str, ctx=None,
) -> None:
    """T-11: Run predicates and record results to sprint-status.yaml.

    Backward-compat: skipped if spec has no predicate_module.
    """
    from plugins.bmad.lib.predicate_runner import run_predicates

    # T-11: Backward-compat guard — skip if no predicate_module
    if not getattr(spec, "predicate_module", None):
        return

    try:
        results = run_predicates(spec, project_dir, ctx)
        if results:
            _write_predicate_results(project_dir, story_id, results)
            logger.info(
                "[dev_story] T-11: Recorded %d predicate results for story %s",
                len(results), story_id,
            )
    except Exception:
        logger.warning(
            "[dev_story] T-11: Predicate run failed for story %s",
            story_id, exc_info=True,
        )
