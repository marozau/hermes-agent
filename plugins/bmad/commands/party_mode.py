"""Handler for /bmad:party-mode — round-table BMAD persona discussion.

Two modes:

- **inline (default)**: returns the prompt body with the user's topic
  substituted. The LLM produces the round-table response in-context by
  reading the agent manifest itself.
- **fan-out** (``--fan-out`` flag in args): uses ``lib/delegation.fan_out``
  to spawn one child sub-agent per selected persona, then aggregates.
  More expensive, more genuinely "multi-agent."

Skill: ``~/.hermes/skills/bmad/core/party-mode/``
Body : ``commands/party-mode.md``
"""

from __future__ import annotations

import logging
from pathlib import Path

COMMAND = "party-mode"

# Where the persona manifest lives (read by both modes).
_MANIFEST_PATH = Path.home() / ".hermes" / "skills" / "bmad" / "_shared" / "agent-manifest.yaml"

# Cap concurrent children in fan-out mode so we don't blow the Hermes
# max_concurrent_children budget (typical default = 3).
_FAN_OUT_PERSONA_CAP = 5

logger = logging.getLogger(__name__)


def handler(ctx, args: str) -> str:
    """Return the round-table prompt for /bmad:party-mode.

    Parses ``args`` for an optional leading ``--fan-out`` flag, then either
    returns the inline prompt body (default) or spawns delegated sub-agents.
    """
    raw_args = (args or "").strip()
    fan_out_mode = False
    topic = raw_args
    if raw_args.startswith("--fan-out"):
        fan_out_mode = True
        topic = raw_args[len("--fan-out"):].strip()
    if not topic:
        topic = "(no topic specified — ask the user what they want the round table to discuss)"

    if fan_out_mode:
        return _fan_out(ctx, topic)
    return _inline(topic)


# ── Inline mode ───────────────────────────────────────────────────────────


def _inline(topic: str) -> str:
    body_file = Path(__file__).with_name(f"{COMMAND}.md")
    if not body_file.exists():
        return f"# {COMMAND}\n\nBody file not found at {body_file}."
    from plugins.bmad.lib.spec_parser import parse_command_body
    from plugins.bmad.lib.render import render_command
    spec, body = parse_command_body(body_file.read_text(encoding="utf-8"))
    return render_command(spec, body, args=topic, template_body=True)


# ── Fan-out mode ──────────────────────────────────────────────────────────


def _fan_out(ctx, topic: str) -> str:
    """Spawn one delegate per selected persona; aggregate results."""
    try:
        import yaml
    except ImportError:
        return "⚠️  fan-out mode requires PyYAML. Falling back to inline.\n\n" + _inline(topic)

    if not _MANIFEST_PATH.exists():
        return (
            "⚠️  Agent manifest not found at "
            f"`{_MANIFEST_PATH}`. Falling back to inline.\n\n" + _inline(topic)
        )

    try:
        manifest = yaml.safe_load(_MANIFEST_PATH.read_text(encoding="utf-8")) or []
    except Exception:
        logger.exception("[bmad:party-mode] failed to parse manifest; falling back to inline")
        return _inline(topic)

    personas = _select_personas(manifest, _FAN_OUT_PERSONA_CAP)
    if not personas:
        return "⚠️  No personas available in the manifest. Falling back to inline.\n\n" + _inline(topic)

    # Build per-persona goals
    goals = [
        _build_persona_goal(p, topic)
        for p in personas
    ]

    # Delegate
    try:
        from plugins.bmad.lib import delegation
    except ImportError:
        return "⚠️  delegation lib unavailable. Falling back to inline.\n\n" + _inline(topic)

    results = delegation.fan_out(
        ctx,
        goals,
        parent_skill="bmad-party-mode",
        context=f"Topic: {topic}\nMode: party-mode round table",
    )

    return _format_results(topic, personas, results)


def _select_personas(manifest: list[dict], cap: int) -> list[dict]:
    """Choose up to *cap* personas, preferring cross-module diversity.

    We deliberately don't try to be smart about topic relevance here — that's
    the LLM's job inside each child task. We just pick a diverse roster.
    """
    if not isinstance(manifest, list):
        return []

    # Bucket by module so we can round-robin
    by_module: dict[str, list[dict]] = {}
    for entry in manifest:
        if not isinstance(entry, dict):
            continue
        module = entry.get("module", "core")
        by_module.setdefault(module, []).append(entry)

    # Round-robin: take one from each module until cap reached
    selected: list[dict] = []
    module_order = ["core", "bmm", "tea", "cis", "bmb"]
    module_order += [m for m in by_module if m not in module_order]
    cursors = {m: 0 for m in module_order}
    while len(selected) < cap:
        progressed = False
        for module in module_order:
            bucket = by_module.get(module, [])
            idx = cursors[module]
            if idx < len(bucket):
                selected.append(bucket[idx])
                cursors[module] = idx + 1
                progressed = True
                if len(selected) >= cap:
                    break
        if not progressed:
            break
    return selected


def _build_persona_goal(persona: dict, topic: str) -> str:
    """Compose the sub-agent goal for one persona."""
    name = persona.get("displayName") or persona.get("name", "Agent")
    title = persona.get("title", "")
    style = persona.get("communicationStyle", "")
    principles = persona.get("principles", "")
    role = persona.get("role", "")
    return (
        f"You are {name}, {title}. Speak in this voice: {style}. "
        f"Apply these principles: {principles}. "
        f"Your role: {role}. "
        f"Respond to the following round-table topic in 2-4 paragraphs, "
        f"staying strictly in character. Be specific to the topic — no generic "
        f"platitudes. Topic: {topic}"
    )


def _format_results(topic: str, personas: list[dict], results: list[dict]) -> str:
    """Render fan-out results into the canonical round-table format."""
    lines: list[str] = []
    lines.append(f"🎉 PARTY MODE (fan-out) — {len(personas)} personas convened on \"{topic}\"")
    icons = " ".join(p.get("icon", "•") for p in personas)
    names = ", ".join(p.get("displayName", p.get("name", "?")) for p in personas)
    lines.append(f"Selected: {icons}  ({names})")
    lines.append("---")
    for persona, result in zip(personas, results):
        icon = persona.get("icon", "•")
        display = persona.get("displayName", persona.get("name", "Agent"))
        title = persona.get("title", "")
        summary = result.get("summary", "") if isinstance(result, dict) else str(result)
        if not summary or result.get("error"):
            summary = f"_(sub-agent failed: {result.get('summary', 'unknown error')})_"
        lines.append(f"{icon} **{display}** *({title})*")
        lines.append("")
        lines.append(summary)
        lines.append("")
    lines.append("---")
    lines.append("**Convergence / Open tensions / Recommended next action:**")
    lines.append("_(synthesize from the responses above)_")
    return "\n".join(lines)
