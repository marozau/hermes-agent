"""Handler for /bmad:code-review — adversarial 3-reviewer fan-out.

This handler ACTIVELY invokes ``lib/delegation.fan_out()`` to spawn three
parallel sub-agents matching the upstream BMAD code-review protocol:

  1. **Blind Hunter** — receives diff only, no spec, no context (uses the
     ``review-adversarial-general`` skill).
  2. **Edge Case Hunter** — receives diff + project read access (uses the
     ``review-edge-case-hunter`` skill).
  3. **Acceptance Auditor** — receives diff + spec + context docs.

Modes (parsed from args):
  - default (no flag): full 3-reviewer fan-out against the current branch diff
  - ``--no-fanout``: legacy behavior — return the prompt body and let the
    host LLM orchestrate (Claude Code's ``Task`` tool pattern)
  - ``--spec <path>``: explicit spec file for the Acceptance Auditor
  - ``--diff <git-revspec>``: override default diff source (default: ``HEAD~1..HEAD``)
  - ``--model <id>``: override the reviewer model (default: see below)

Reviewer model resolution (precedence):
  1. ``--model <id>`` flag on the slash command
  2. ``ctx.profile_config["delegation"]["skill_overrides"]["bmad-code-review"]``
     — per-profile override read from the active Hermes profile config
  3. Default constant ``_DEFAULT_REVIEWER_MODEL`` (``"claude-opus-4-7"``)

The reviewer model is independent of the profile's default ``delegation.model``
(which is typically set to a cheaper model for other delegation patterns).
Code review uses a stronger model by default because adversarial review is
where reasoning depth pays off.

Per-profile schema (added to your Hermes profile's config.yaml under the root
``delegation:`` block — same block whose ``model`` field already governs
default delegation):

    delegation:
      model: deepseek-v4-pro       # profile default for every other delegation
      provider: custom
      base_url: http://localhost:4000/v1
      api_key: sk-litellm-...
      skill_overrides:                       # NEW — per-skill overrides
        bmad-code-review:
          model: claude-opus-4-7
          provider: anthropic
          base_url: https://api.anthropic.com
          api_key: sk-ant-...
          api_mode: messages

Different profiles can have different reviewer policies (e.g. a
``security-audit`` profile can use a heterogeneous-vendor judge like
``gemini-3.1-pro`` to mitigate same-vendor preference leakage).
"""

from __future__ import annotations

import logging
import shlex
import subprocess
from pathlib import Path

COMMAND = "code-review"

logger = logging.getLogger(__name__)

# Default diff source — caller can override with --diff
_DEFAULT_DIFF_REV = "HEAD~1..HEAD"

# Default reviewer model — adversarial review is reasoning-bound; favor depth.
# Per planning-artifacts/research/technical-llm-accuracy-and-judge-2026-05-18.md
# Opus 4.7 with extended thinking is recommended for review tasks (confidence 0.7).
_DEFAULT_REVIEWER_MODEL = "claude-opus-4-7"

# Per-reviewer config: (id, role_phrase, requires_context)
_REVIEWERS = [
    {
        "id": "blind-hunter",
        "skill": "review-adversarial-general",
        "role": "Blind Hunter (adversarial general review)",
        "needs_spec": False,
        "needs_context": False,
    },
    {
        "id": "edge-case-hunter",
        "skill": "review-edge-case-hunter",
        "role": "Edge Case Hunter (path enumeration)",
        "needs_spec": False,
        "needs_context": True,  # has project read access
    },
    {
        "id": "acceptance-auditor",
        "skill": None,  # inline prompt, no dedicated skill upstream
        "role": "Acceptance Auditor (spec compliance)",
        "needs_spec": True,
        "needs_context": True,
    },
]


def handler(ctx, args: str) -> str:
    """Main entry point — orchestrates the 3-reviewer fan-out."""
    raw_args = (args or "").strip()
    project_dir = _resolve_project_dir(ctx)

    # Guard 1: BMAD project
    if not (project_dir / "bmad" / "config.yaml").exists():
        return "⚠️  Not a BMAD project. Run `/bmad:init` to initialize."

    # Guard 2: phase gate (defense-in-depth; pre_tool_call hook also gates)
    from plugins.bmad.lib import phases
    from plugins.bmad.lib.status import load
    state = load(project_dir)
    level = state.get("level", 1)
    ok, reason = phases.can_run(COMMAND, state, level)
    if not ok:
        return f"🚫 **{COMMAND} blocked:** {reason}"

    # Parse args
    parsed = _parse_args(raw_args)
    if parsed.get("_error"):
        return f"⚠️  {parsed['_error']}"
    if parsed["no_fanout"]:
        return _legacy_body(project_dir)

    # Gather diff
    diff_text, diff_meta = _capture_diff(project_dir, parsed["diff_rev"])
    if not diff_text.strip():
        # M-4 fix: distinguish a real git failure from an empty (but valid) diff
        if diff_meta.get("error"):
            return (
                f"⚠️  `git diff {parsed['diff_rev']}` failed: "
                f"{diff_meta['error']}. "
                "Use `--diff <git-revspec>` to override the source range."
            )
        return (
            f"⚠️  No diff found at `{parsed['diff_rev']}`. "
            "Use `--diff <git-revspec>` to override the source range."
        )

    # Resolve spec path (M-5: validate the path is inside project_dir)
    spec_path = parsed.get("spec_path")
    spec_text = ""
    if spec_path:
        sp_raw = Path(spec_path)
        sp = (sp_raw if sp_raw.is_absolute() else project_dir / sp_raw).resolve()
        project_resolved = project_dir.resolve()
        try:
            sp.relative_to(project_resolved)
        except ValueError:
            return (
                f"⚠️  Spec path `{spec_path}` escapes the project root "
                f"(`{project_resolved}`). Spec must be inside the project."
            )
        if not sp.exists():
            return f"⚠️  Spec file not found: `{spec_path}`"
        if sp.is_dir():
            return f"⚠️  Spec path is a directory, not a file: `{spec_path}`"
        try:
            spec_text = sp.read_text(encoding="utf-8", errors="replace")
        except (PermissionError, OSError) as exc:
            return f"⚠️  Cannot read spec `{spec_path}`: {exc}"

    # Build per-reviewer goals
    goals = _build_goals(diff_text, diff_meta, spec_text, project_dir)
    review_mode = "full" if spec_text else "no-spec"

    # Skip Acceptance Auditor if no spec
    active_reviewers = [r for r in _REVIEWERS if not (r["needs_spec"] and not spec_text)]
    active_goals = [g for r, g in zip(_REVIEWERS, goals) if not (r["needs_spec"] and not spec_text)]

    # Resolve reviewer model + provider overrides (from profile_config + CLI)
    model_overrides = _resolve_reviewer_model(ctx, cli_model=parsed.get("model"))

    # ── Epic 8: OCR 4th reviewer (OI-9, OI-10, OI-11) ─────────────────
    ocr_profile_overrides = _read_ocr_profile_override(ctx)
    ocr_subprocess_tasks = _build_ocr_task(project_dir, diff_text, ocr_profile_overrides)

    # Fan out
    from plugins.bmad.lib import delegation
    total_reviewers = len(active_reviewers) + len(ocr_subprocess_tasks)
    logger.info(
        "[bmad:code-review] spawning %d reviewers (mode=%s, diff=%s, model=%s, ocr=%s)",
        total_reviewers, review_mode, parsed["diff_rev"],
        model_overrides.get("model", "<profile-default>"),
        "enabled" if ocr_subprocess_tasks else "disabled",
    )
    results = delegation.fan_out(
        ctx,
        active_goals,
        parent_skill="bmad-code-review",
        context=(
            f"Mode: {review_mode}\n"
            f"Diff range: {parsed['diff_rev']}\n"
            f"Project: {project_dir.name}\n"
        ),
        subprocess_tasks=ocr_subprocess_tasks or None,
        **model_overrides,
    )

    # Aggregate
    return _aggregate(active_reviewers, results, diff_meta, review_mode,
                      reviewer_model=model_overrides.get("model"))


# ── Args parsing ────────────────────────────────────────────────────────────


def _parse_args(raw: str) -> dict:
    """Parse the args string into a structured dict.

    M-3 fix (code-review 2026-05-21): wraps shlex.split with try/except so
    unbalanced quotes return a user-facing usage hint instead of crashing
    the slash command with a ValueError traceback.
    """
    parsed = {
        "no_fanout": False,
        "diff_rev": _DEFAULT_DIFF_REV,
        "spec_path": None,
        "model": None,
        "_error": None,
    }
    if not raw:
        return parsed
    try:
        parts = shlex.split(raw)
    except ValueError as exc:
        parsed["_error"] = (
            f"could not parse args (`{raw}`): {exc}. "
            "Hint: check for unbalanced quotes."
        )
        return parsed

    i = 0
    while i < len(parts):
        tok = parts[i]
        if tok == "--no-fanout":
            parsed["no_fanout"] = True
            i += 1
        elif tok == "--diff" and i + 1 < len(parts):
            parsed["diff_rev"] = parts[i + 1]
            i += 2
        elif tok == "--spec" and i + 1 < len(parts):
            parsed["spec_path"] = parts[i + 1]
            i += 2
        elif tok == "--model" and i + 1 < len(parts):
            parsed["model"] = parts[i + 1]
            i += 2
        else:
            i += 1
    return parsed


# ── Reviewer model resolution ───────────────────────────────────────────────

# Skill name used to look up the per-skill override in profile config.
# Must match what gets recorded in subagent logs.
_SKILL_KEY_FOR_OVERRIDES = "bmad-code-review"


def _resolve_reviewer_model(ctx, cli_model: str | None) -> dict:
    """Resolve which model the 3 reviewers should run on.

    Precedence:
      1. ``cli_model`` (from ``--model <id>``)
      2. ``ctx.profile_config["delegation"]["skill_overrides"]["bmad-code-review"]``
         — per-profile override; ``model`` plus optional ``provider``,
         ``base_url``, ``api_key``, ``api_mode``
      3. ``_DEFAULT_REVIEWER_MODEL`` (``"claude-opus-4-7"``)

    Returns a kwargs dict suitable for ``delegation.fan_out(**overrides)``.
    Only non-None override keys are returned.

    The previous ``bmad/config.yaml`` per-project layer was removed in favor
    of profile-only resolution — per-profile is the right variation unit for
    BMAD-Hermes users (same project, different review policy per profile).
    """
    if cli_model:
        return {"model": cli_model}

    override = _read_profile_override(ctx)
    if override is None:
        return {"model": _DEFAULT_REVIEWER_MODEL}

    model = override.get("model") or _DEFAULT_REVIEWER_MODEL
    overrides: dict = {"model": model}
    for key in ("provider", "base_url", "api_key", "api_mode"):
        if override.get(key):
            overrides[key] = override[key]
    return overrides


def _read_profile_override(ctx) -> dict | None:
    """Read ``delegation.skill_overrides[bmad-code-review]`` from profile_config.

    Returns the override dict, or None if not configured. Never raises.
    """
    try:
        profile_cfg = getattr(ctx, "profile_config", None) or {}
        if not isinstance(profile_cfg, dict):
            return None
        delegation = profile_cfg.get("delegation") or {}
        if not isinstance(delegation, dict):
            return None
        skill_overrides = delegation.get("skill_overrides") or {}
        if not isinstance(skill_overrides, dict):
            return None
        override = skill_overrides.get(_SKILL_KEY_FOR_OVERRIDES)
        if not isinstance(override, dict):
            return None
        return override
    except Exception:
        logger.exception(
            "[bmad:code-review] failed to read profile_config skill_overrides",
        )
        return None


def _read_ocr_profile_override(ctx) -> dict:
    """Read OCR overrides from profile config.

    Looks at ``delegation.skill_overrides.bmad-code-review.ocr`` for
    per-profile OCR overrides (enable, timeout, etc.).

    Story 8.6: Different profiles can override OCR independently.

    Returns:
        Dict with OCR override fields. Empty dict if not configured.
        Never raises.
    """
    try:
        override = _read_profile_override(ctx)
        if override is None:
            return {}
        ocr = override.get("ocr")
        if not isinstance(ocr, dict):
            return {}
        return ocr
    except Exception:
        logger.exception(
            "[bmad:code-review] failed to read OCR profile override",
        )
        return {}


# ── Diff capture ────────────────────────────────────────────────────────────


def _capture_diff(project_dir: Path, rev: str) -> tuple[str, dict]:
    """Run git diff; return (text, metadata).

    M-6 fix (code-review 2026-05-21): decode with ``errors="replace"`` so
    binary diff hunks (non-UTF-8 bytes) don't crash ``subprocess.run``.

    Exception tuple widened to cover ``PermissionError`` / ``OSError`` so
    the handler can produce a readable error instead of a bare traceback.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(project_dir), "diff", rev],
            capture_output=True, text=True, errors="replace", timeout=30,
        )
    except subprocess.TimeoutExpired:
        return ("", {"rev": rev, "error": f"git diff timed out after 30s on `{rev}`"})
    except FileNotFoundError:
        return ("", {"rev": rev, "error": "git executable not found in PATH"})
    except (PermissionError, OSError) as exc:
        return ("", {"rev": rev, "error": f"OS error invoking git: {exc}"})

    if result.returncode != 0:
        return ("", {"rev": rev, "error": (result.stderr or "git exited non-zero")[:200]})

    diff_text = result.stdout
    lines = diff_text.splitlines()
    files_changed = sum(1 for line in lines if line.startswith("diff --git "))
    insertions = sum(1 for line in lines if line.startswith("+") and not line.startswith("+++"))
    deletions = sum(1 for line in lines if line.startswith("-") and not line.startswith("---"))
    return (diff_text, {
        "rev": rev,
        "files_changed": files_changed,
        "insertions": insertions,
        "deletions": deletions,
        "total_lines": len(lines),
    })


# ── Goal building ───────────────────────────────────────────────────────────


def _build_goals(
    diff_text: str,
    diff_meta: dict,
    spec_text: str,
    project_dir: Path,
) -> list[str]:
    """Compose one goal string per reviewer."""
    # Truncate diff in goal to keep prompt bounded; full diff still available
    # to children that have read access to the project.
    diff_preview = (
        diff_text if len(diff_text) <= 12_000
        else diff_text[:12_000] + "\n[... diff truncated ...]"
    )

    # Blind Hunter — diff only, no project context
    blind_goal = (
        "Act as the Blind Hunter reviewer. Use the bmad-review-adversarial-general "
        "skill discipline: cynical, adversarial, find at least ten issues. "
        "You receive only the diff below — DO NOT read project files or look up context. "
        "Output: a Markdown list of findings, descriptions only.\n\n"
        f"--- DIFF ({diff_meta.get('files_changed', '?')} files, "
        f"+{diff_meta.get('insertions', '?')}/-{diff_meta.get('deletions', '?')}) ---\n"
        f"{diff_preview}"
    )

    # Edge Case Hunter — diff + project read access
    edge_goal = (
        "Act as the Edge Case Hunter reviewer. Use the bmad-review-edge-case-hunter "
        "skill discipline: walk every branching path, report ONLY unhandled boundaries. "
        "You may use Read/Grep/Glob on the project at "
        f"`{project_dir}` to understand referenced symbols. "
        "Output: a JSON array of objects with fields {location, trigger_condition, "
        "guard_snippet, potential_consequence}. Empty array `[]` is valid.\n\n"
        f"--- DIFF ---\n{diff_preview}"
    )

    # Acceptance Auditor — diff + spec + project context
    spec_section = ""
    if spec_text:
        spec_preview = (
            spec_text if len(spec_text) <= 8_000
            else spec_text[:8_000] + "\n[... spec truncated ...]"
        )
        spec_section = f"\n--- SPEC ---\n{spec_preview}\n"
    audit_goal = (
        "Act as the Acceptance Auditor. Review the diff against the spec below. "
        "Check: violations of acceptance criteria, deviations from spec intent, "
        "missing implementation of specified behavior, contradictions between "
        "spec constraints and actual code. "
        "Output: a Markdown list. Each finding: one-line title, which AC/constraint "
        "it violates, evidence from the diff.\n\n"
        f"--- DIFF ---\n{diff_preview}{spec_section}"
    )

    return [blind_goal, edge_goal, audit_goal]


# ── Aggregation ─────────────────────────────────────────────────────────────


def _build_ocr_task(project_dir: Path, diff_text: str, profile_overrides: dict | None = None) -> list[dict]:
    """Build OCR subprocess task if OCR is enabled for this project.

    OI-9: OCR is OPT-IN — only runs if bmad/config.yaml has code_review.ocr.enabled=true.
    OI-10: If OCR binary not installed, returns empty (warn + continue).
    OI-11: OCR runs independently; findings never injected into LLM prompts.

    Story 8.6: profile_overrides can enable/disable OCR per profile.

    Returns:
        List with one subprocess task dict (for fan_out), or empty list.
    """
    from plugins.bmad.lib.config import load_workspace_config
    from plugins.bmad.lib.ocr_runner import run_ocr_review, resolve_rule_path

    ws_cfg = load_workspace_config(project_dir)
    ocr_cfg = ws_cfg.code_review_ocr

    # Story 8.6: Profile overrides can enable OCR even if project config doesn't.
    # Profile can also disable/override timeout.
    if profile_overrides:
        profile_enabled = profile_overrides.get("enabled")
        if profile_enabled is True and not ocr_cfg.enabled:
            # Profile enables OCR even though project default is off
            from plugins.bmad.lib.config import OCRConfig
            ocr_cfg = OCRConfig(
                enabled=True,
                rule_path=profile_overrides.get("rule_path", ocr_cfg.rule_path),
                timeout_seconds=profile_overrides.get("timeout_seconds", ocr_cfg.timeout_seconds),
                languages=profile_overrides.get("languages", ocr_cfg.languages),
            )
        elif profile_enabled is False:
            # Profile explicitly disables OCR
            return []
        elif ocr_cfg.enabled:
            # OCR enabled in project; apply profile overrides for timeout etc.
            timeout_override = profile_overrides.get("timeout_seconds")
            if timeout_override:
                from plugins.bmad.lib.config import OCRConfig
                ocr_cfg = OCRConfig(
                    enabled=True,
                    rule_path=ocr_cfg.rule_path,
                    timeout_seconds=timeout_override,
                    languages=ocr_cfg.languages,
                )

    # OI-9: default off
    if not ocr_cfg.enabled:
        return []

    # Resolve rule path (OI-13)
    rule_path = resolve_rule_path(
        project_dir,
        ocr_cfg.rule_path,
    )

    return [{
        "kind": "subprocess",
        "fn": run_ocr_review,
        "kwargs": {
            "diff_text": diff_text,
            "rule_path": rule_path,
            "timeout_seconds": ocr_cfg.timeout_seconds,
        },
        "label": "ocr",
    }]


def _aggregate(
    reviewers: list[dict],
    results: list[dict],
    diff_meta: dict,
    review_mode: str,
    reviewer_model: str | None = None,
) -> str:
    """Format the reviewer output into canonical Markdown.

    Handles both LLM-delegated and subprocess (OCR) results.
    F-4 fix: runs consensus classification via ocr_triage.merge_findings.
    """
    from plugins.bmad.lib.ocr_triage import merge_findings, normalize_ocr_finding

    lines: list[str] = []
    lines.append("# Code Review — adversarial multi-reviewer fan-out")
    lines.append("")
    lines.append(
        f"**Diff:** {diff_meta.get('rev', '?')} — "
        f"{diff_meta.get('files_changed', '?')} files, "
        f"+{diff_meta.get('insertions', '?')}/-{diff_meta.get('deletions', '?')} lines"
    )
    lines.append(f"**Mode:** {review_mode}")

    # Separate LLM and OCR results
    llm_results = [r for r in results if r.get("kind") != "subprocess"]
    ocr_results = [r for r in results if r.get("kind") == "subprocess"]

    total = len(llm_results) + len(ocr_results)
    reviewer_ids = [r["id"] for r in reviewers] + (
        ["ocr"] if ocr_results else []
    )
    lines.append(f"**Reviewers:** {total} ({', '.join(reviewer_ids)})")
    if reviewer_model:
        lines.append(f"**Reviewer model:** `{reviewer_model}`")
    lines.append("")
    lines.append("---")

    # LLM reviewer sections
    for reviewer, result in zip(reviewers, llm_results):
        lines.append(f"## {reviewer['role']}")
        lines.append("")
        if result.get("error"):
            lines.append(
                f"_⚠️ Sub-agent failed: {result.get('summary', 'unknown error')}_"
            )
        else:
            summary = result.get("summary", "")
            lines.append(summary if summary else "_(no findings returned)_")
        lines.append("")

    # OCR section (Epic 8 — OI-12: one of 4 independent sources)
    ocr_findings_raw: list[dict] = []
    for ocr_result in ocr_results:
        elapsed = ocr_result.get("elapsed_seconds", "?")
        lines.append(f"## OCR (Open Code Review)")
        lines.append("")
        if ocr_result.get("error"):
            lines.append(
                f"_⚠️ OCR failed: {ocr_result.get('summary', 'unknown error')}_"
            )
        else:
            summary = ocr_result.get("summary", "")
            if summary and summary != "[]":
                lines.append(summary)
                # Parse for consensus
                try:
                    import json
                    ocr_findings_raw = json.loads(summary)
                except (json.JSONDecodeError, TypeError):
                    pass
            else:
                lines.append("_(no findings returned)_")
        lines.append(f"_OCR completed in {elapsed}s_")
        lines.append("")

    # F-4: Consensus classification (OI-12)
    if ocr_findings_raw:
        try:
            merged = merge_findings(ocr_findings=ocr_findings_raw)
            if merged:
                lines.append("---")
                lines.append("## Consensus Signal")
                lines.append("")
                for f in merged:
                    sources_str = "+".join(sorted(f.sources))
                    lines.append(
                        f"- **{f.classification}** [{f.severity}] "
                        f"`{f.file}:{f.line}` — {f.message} "
                        f"_(source: {sources_str})_"
                    )
                lines.append("")
        except Exception as e:
            logger.debug("[bmad:code-review] Consensus classification failed: %s", e)

    # Triage hint at the end
    lines.append("---")
    lines.append("## Triage")
    lines.append("")
    lines.append(
        "Next step: walk through the findings above. For each, classify as:\n"
        "- **MUST FIX** (blocks merge)\n"
        "- **SHOULD FIX** (file an issue)\n"
        "- **CONSIDER** (worth a thought)\n"
        "- **IGNORE** (false positive — note why)\n"
    )
    return "\n".join(lines)


# ── Helpers ─────────────────────────────────────────────────────────────────


def _resolve_project_dir(ctx) -> Path:
    raw = getattr(ctx, "working_directory", None) or "."
    return Path(raw).resolve()


def _legacy_body(project_dir: Path) -> str:
    """Return the original prompt body — for users who prefer host-LLM orchestration."""
    body_path = Path(__file__).with_name(f"{COMMAND}.md")
    if body_path.exists():
        return body_path.read_text(encoding="utf-8")
    return f"# {COMMAND}\n\nBody file not found."
