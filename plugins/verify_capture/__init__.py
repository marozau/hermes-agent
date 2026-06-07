"""verify_capture plugin — parse agent self-report YAML into typed entries.

Story 12.2: post_llm_call hook that scans the assistant response for a fenced
```yaml self_report: block, validates it through Pydantic, and dispatches to
the canonical writers via lib.verify_dispatch (Story 12.3).

Fail-open everywhere. No exception escapes the hook.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Literal, Optional

logger = logging.getLogger(__name__)

try:
    from pydantic import BaseModel, Field, ValidationError
    import yaml
except ImportError as _imp_err:
    logger.warning("verify_capture: pydantic or yaml not available: %s", _imp_err)
    BaseModel = None  # type: ignore[misc,assignment]
    ValidationError = None  # type: ignore[misc,assignment]
    yaml = None  # type: ignore[misc,assignment]


class FailureEntry(BaseModel):
    """One recoverable failure from the self-report."""
    category: Literal[
        "tool-misuse", "context-overflow", "hallucinated-api",
        "incomplete-context", "edit-error", "requirement-drift",
    ]
    summary: str = Field(min_length=10)


class TrajectoryEntry(BaseModel):
    """One trajectory pattern worth recording."""
    category: str = Field(min_length=1)
    body: str = Field(min_length=20)
    source_refs: list[str] = Field(default_factory=list)


class SelfReport(BaseModel):
    """Pydantic-gated self-report document (Hard Invariant #11)."""
    preflight_applied: Optional[Literal["hit", "miss", "partial", "none"]] = None
    preflight_cited: list[str] = Field(default_factory=list)
    match: Optional[Literal["hit", "miss", "unrelated"]] = None
    failures: list[FailureEntry] = Field(default_factory=list)
    trajectories: list[TrajectoryEntry] = Field(default_factory=list)


# Fenced-block extractor — robust to ```yaml, ~~~yaml, untagged fences
_FENCE_RE = re.compile(
    r"(`{3}|~{3})\s*(?:ya?ml)?\s*\n(.*?)\1",
    re.IGNORECASE | re.DOTALL,
)


def _extract_self_report(content: str) -> Optional[dict]:
    """Scan response for fenced YAML containing a `self_report` key.

    Returns the dict under the `self_report` key, or None if not found.
    Prefers the *last* match — real self-reports are emitted at the end
    of the response per skill instructions.
    """
    if not content or not isinstance(content, str):
        return None
    result = None
    for match in _FENCE_RE.finditer(content):
        body = match.group(2).strip()
        try:
            doc = yaml.safe_load(body)
        except yaml.YAMLError:
            continue
        if isinstance(doc, dict) and "self_report" in doc:
            result = doc["self_report"]
    return result


def on_post_llm_call(
    *,
    session_id: str = "",
    response: str = "",
    content: str = "",
    assistant_response: str = "",
    **_kwargs: Any,
) -> None:
    """Parse the last assistant turn's self_report block; dispatch to writers."""
    text = assistant_response or response or content or ""
    if not isinstance(text, str) or not text:
        return

    raw_report = _extract_self_report(text)
    if raw_report is None:
        return  # No block → no-op (fail-open)

    # Pydantic validation (F15: catch ValidationError separately from Exception)
    if ValidationError is not None:
        try:
            report = SelfReport(**raw_report)
        except ValidationError as e:
            logger.warning("verify_capture: SelfReport validation failed: %s", e)
            return  # Invalid block → log + no-op
        except Exception as e:
            logger.warning("verify_capture: SelfReport construction failed: %s", e)
            return
    else:
        try:
            report = SelfReport(**raw_report)
        except Exception as e:
            logger.warning("verify_capture: SelfReport validation failed: %s", e)
            return  # Invalid block → log + no-op

    # Dispatch to canonical writers
    try:
        from lib.verify_dispatch import dispatch_self_report
        dispatch_self_report(report, session_id=session_id)
    except Exception as e:
        logger.warning("verify_capture: dispatch failed: %s", e)


def register(ctx: Any) -> None:
    """Register the post_llm_call hook."""
    ctx.register_hook("post_llm_call", on_post_llm_call)
    logger.info("verify_capture plugin registered post_llm_call hook")
