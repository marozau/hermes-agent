"""Spec parser — frontmatter-aware YAML parser for command specs (Story 12.1).

Parses a command .md file's content into (CommandSpec | None, raw_body).

If the file starts with ``---`` YAML frontmatter containing a ``spec:``
key, the parser extracts and validates the spec.  Otherwise returns
(None, raw_content) for legacy commands.

Pure stdlib + yaml — no Pydantic.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import yaml

from plugins.bmad.lib.spec_schema import CommandSpec, VerificationItem

logger = logging.getLogger(__name__)

# ── Frontmatter extraction ──────────────────────────────────────────────────

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_command_body(content: str) -> tuple[CommandSpec | None, str]:
    """Parse a command .md file into (spec, body).

    Returns:
        (spec, body_text) if frontmatter with spec: is present.
        (None, original_content) if no spec: block (legacy command).
    """
    m = _FRONTMATTER_RE.match(content)
    if not m:
        return None, content

    frontmatter_str = m.group(1)
    body = content[m.end():]

    try:
        fm = yaml.safe_load(frontmatter_str)
    except yaml.YAMLError as e:
        logger.warning("[bmad:spec_parser] malformed YAML frontmatter: %s", e)
        return None, content

    if not isinstance(fm, dict) or "spec" not in fm:
        return None, content

    spec_raw = fm["spec"]
    if not isinstance(spec_raw, dict):
        return None, content

    spec = _build_spec(spec_raw)
    if spec is None:
        return None, content

    return spec, body


# ── Spec validation + construction ──────────────────────────────────────────

def _build_spec(raw: dict[str, Any]) -> CommandSpec | None:
    """Build a CommandSpec from raw YAML dict.

    Returns None if required fields are missing or malformed.
    """
    persona = raw.get("persona")
    phase = raw.get("phase")
    verification_raw = raw.get("verification")

    if not persona or not phase or not verification_raw:
        return None

    if not isinstance(verification_raw, list) or len(verification_raw) == 0:
        return None

    verification = []
    for item in verification_raw:
        if isinstance(item, str):
            verification.append(VerificationItem(description=item))
        elif isinstance(item, dict):
            desc = item.get("description", "")
            pred = item.get("predicate")
            if desc:
                verification.append(VerificationItem(
                    description=desc,
                    predicate=pred if isinstance(pred, str) else None,
                ))

    if not verification:
        return None

    return CommandSpec(
        persona=str(persona),
        phase=str(phase),
        verification=tuple(verification),
        imperative_preamble=bool(raw.get("imperative_preamble", True)),
        predicate_module=raw.get("predicate_module"),
        output_artifacts=tuple(str(a) for a in raw.get("output_artifacts") or []),
        metadata=dict(raw.get("metadata") or {}),
    )
