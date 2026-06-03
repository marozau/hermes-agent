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
        (None, body_without_frontmatter) if frontmatter exists but no spec.
        (None, original_content) if no frontmatter at all.
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
        # G-9: Still strip the malformed frontmatter
        return None, body

    if not isinstance(fm, dict):
        return None, body

    if "spec" not in fm:
        # G-9: Strip non-spec frontmatter (title, version, etc.)
        return None, body

    spec_raw = fm["spec"]
    if not isinstance(spec_raw, dict):
        return None, body

    spec = _build_spec(spec_raw)
    if spec is None:
        return None, body

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

    # G-6: Guard against YAML scalar strings (common typo: missing list-dash)
    oa_raw = raw.get("output_artifacts") or []
    if isinstance(oa_raw, str):
        logger.warning("[bmad:spec_parser] output_artifacts should be a list, got string — wrapping")
        oa_raw = [oa_raw]
    elif not isinstance(oa_raw, list):
        oa_raw = []

    meta_raw = raw.get("metadata") or {}
    if not isinstance(meta_raw, dict):
        logger.warning("[bmad:spec_parser] metadata should be a dict, got %s — ignoring", type(meta_raw).__name__)
        meta_raw = {}

    return CommandSpec(
        persona=str(persona),
        phase=str(phase),
        verification=tuple(verification),
        imperative_preamble=bool(raw.get("imperative_preamble", True)),
        predicate_module=raw.get("predicate_module"),
        output_artifacts=tuple(str(a) for a in oa_raw),
        metadata=tuple((k, v) for k, v in meta_raw.items()),
    )
