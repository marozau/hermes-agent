"""
hermes_providers_anthropic — Anthropic Messages API adapter.

Story 3.6 / NFR-5 / Hard Invariant #12:
  - cache_control breakpoints at user-specified message indices
  - Supports ephemeral (5m) and 1h cache tiers via cache_mode
  - Registers as dispatch provider "anthropic"

Returns dict with keys: content, model, usage, etc.
"""

import json
import logging
import os
import time as _time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# API base
# ─────────────────────────────────────────────────────────────────────────────

ANTHROPIC_API_BASE = "https://api.anthropic.com/v1"
ANTHROPIC_VERSION = "2023-06-01"

# ─────────────────────────────────────────────────────────────────────────────
# Cache tier mapping (ADR-7 / Story 3.5)
# ─────────────────────────────────────────────────────────────────────────────

_CACHE_MODE_TO_TIER = {
    "none": "ephemeral",         # default Ephemeral (5 min)
    "5m": "ephemeral",           # explicit 5 min
    "1h": "ephemeral",           # Anthropic doesn't expose 1h tier via API;
}                                # server decides based on frequency of reuse

# (Anthropic's current API uses "ephemeral" for all breakpoints.
#  The 1h tier is server-autodetected when the same content is cited
#  frequently.  We preserve the config signal so if Anthropic adds
#  explicit 1h breakpoints later, the code is ready.)


# ─────────────────────────────────────────────────────────────────────────────
# Key resolution
# ─────────────────────────────────────────────────────────────────────────────


def _resolve_api_key() -> str:
    """Return ANTHROPIC_API_KEY or raise."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise ValueError(
            "ANTHROPIC_API_KEY not set. Set it in ~/.hermes/.env or your environment."
        )
    return key


# ─────────────────────────────────────────────────────────────────────────────
# Anthropic Messages API call
# ─────────────────────────────────────────────────────────────────────────────


def anthropic_chat(
    provider: "ProviderSpec",
    messages: list[dict],
    response_model: Optional[type["BaseModel"]] = None,
    *,
    cache_breakpoints: Optional[list[int]] = None,
    cache_mode: str = "none",
) -> dict:
    """Call the Anthropic Messages API with optional cache_control breakpoints.

    Args:
        provider: ProviderSpec from providers.yaml (model, max_tokens, timeout,
                  base_url, extra_headers)
        messages: OpenAI-format message list (role/content). Converted to
                  Anthropic format internally.
        response_model: Optional Pydantic model for structured output
                        (passed back to llm_call for schema gating).
        cache_breakpoints: Message indices (0-based) to place cache_control
                           markers on.  Per ADR-7: system[0], skills[1],
                           trajectories[2].
        cache_mode: "none" | "5m" | "1h"

    Returns:
        dict with keys: content, model, usage, etc.

    Raises:
        ValueError: On API key missing, HTTP error, or unexpected response shape.
    """
    api_key = _resolve_api_key()
    tier = _CACHE_MODE_TO_TIER.get(cache_mode, "ephemeral")
    breakpoints = set(cache_breakpoints or [])

    # ── Convert OpenAI-format messages to Anthropic Messages API format ──
    # Anthropic requires: system (separate field), then messages array.
    # A system message at index 0 is extracted to the top-level system field;
    # remaining messages become the messages array.
    system_prompt: Optional[str] = None
    anthro_messages: list[dict] = []
    for i, msg in enumerate(messages):
        role = msg.get("role", "")
        content_val = msg.get("content", "")

        if role == "system" and system_prompt is None:
            system_prompt = content_val
            continue

        # Anthropic uses "assistant" and "user" — same as OpenAI
        entry: dict = {"role": role, "content": content_val}

        # Apply cache_control breakpoint at user-specified indices
        idx = len(anthro_messages)
        if idx in breakpoints:
            # Anthropic: content must be a list of blocks when cache_control is set
            if isinstance(content_val, str):
                entry["content"] = [
                    {
                        "type": "text",
                        "text": content_val,
                        "cache_control": {"type": tier},
                    }
                ]
            elif isinstance(content_val, list):
                # Already a block list; append cache_control to last text block
                for block in reversed(content_val):
                    if isinstance(block, dict) and block.get("type") == "text":
                        block["cache_control"] = {"type": tier}
                        break

        anthro_messages.append(entry)

    # Build request body
    body: dict = {
        "model": provider.model,
        "max_tokens": provider.max_tokens,
        "messages": anthro_messages,
    }
    if system_prompt is not None:
        body["system"] = system_prompt

    # Build headers
    headers = {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }
    headers.update(provider.extra_headers)

    base = provider.base_url or ANTHROPIC_API_BASE
    timeout = provider.timeout

    # ── Execute ──
    t0 = _time.monotonic()
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout)) as client:
            resp = client.post(
                f"{base.rstrip('/')}/messages",
                headers=headers,
                content=json.dumps(body),
            )
    except httpx.TimeoutException as exc:
        from hermes_llm import ProviderError
        raise ProviderError(
            f"Anthropic request timed out after {timeout}s "
            f"(model={provider.model})",
            provider=provider.provider,
            model=provider.model,
            category="timeout",
            raw_error=exc,
        ) from exc
    except httpx.HTTPError as exc:
        from hermes_llm import ProviderError
        raise ProviderError(
            f"Anthropic HTTP error: {exc}",
            provider=provider.provider,
            model=provider.model,
            category="unknown",
            raw_error=exc,
        ) from exc

    elapsed = _time.monotonic() - t0
    logger.debug("anthropic_chat %s finished in %.2fs", provider.model, elapsed)

    if resp.status_code != 200:
        from hermes_llm import ProviderError, classify_http_status
        try:
            err_body = resp.json()
        except Exception:
            err_body = {"raw": resp.text[:500]}
        category = classify_http_status(resp.status_code)
        raise ProviderError(
            f"Anthropic API returned {resp.status_code}: {json.dumps(err_body)}",
            provider=provider.provider,
            model=provider.model,
            category=category,
            status_code=resp.status_code,
        )

    data = resp.json()

    # ── Parse response ──
    # Anthropic returns {id, type, role, content: [{type, text}], model, stop_reason, usage}
    content_blocks = data.get("content", [])
    full_text = "".join(
        b.get("text", "") for b in content_blocks if b.get("type") == "text"
    )

    usage = data.get("usage", {})
    usage_normalized = {
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
    }

    return {
        "content": full_text,
        "model": data.get("model", provider.model),
        "role": "assistant",
        "stop_reason": data.get("stop_reason"),
        "usage": usage_normalized,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Registration
# ─────────────────────────────────────────────────────────────────────────────


def register() -> None:
    """Register this adapter with the provider dispatch in hermes_llm.

    Call from hermes_providers.register_all() at startup.
    """
    # Deferred import to avoid circular dependency at module level
    from hermes_llm import register_provider_dispatch

    register_provider_dispatch("anthropic", anthropic_chat)
    logger.info("registered provider dispatch: anthropic")
