"""
hermes_providers_chat — OpenAI-compatible /chat/completions adapter.

Story 3.7: Shared adapter for DeepSeek and OpenAI (and any other provider
using the standard /chat/completions API format).

Registers dispatchers for:
  - "deepseek"  → DEEPSEEK_API_KEY + base_url from config or default
  - "openai"    → OPENAI_API_KEY + base_url from config or default

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
# Default base URLs per provider
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com/v1",
    # Extensible: add "xai": "https://api.x.ai/v1", etc.
}

_ENV_VAR_MAP = {
    "openai": "OPENAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}

# ─────────────────────────────────────────────────────────────────────────────
# Key resolution
# ─────────────────────────────────────────────────────────────────────────────


def _resolve_api_key(provider_name: str) -> str:
    """Return API key for the named provider from environment."""
    env_var = _ENV_VAR_MAP.get(provider_name)
    if env_var is None:
        raise ValueError(
            f"No API key env var configured for provider '{provider_name}'. "
            f"Expected: {_ENV_VAR_MAP}"
        )
    key = os.environ.get(env_var)
    if not key:
        raise ValueError(
            f"{env_var} not set. Set it in ~/.hermes/.env or your environment."
        )
    return key


# ─────────────────────────────────────────────────────────────────────────────
# Shared /chat/completions call
# ─────────────────────────────────────────────────────────────────────────────


def chat_completions(
    provider: "ProviderSpec",
    messages: list[dict],
    response_model: Optional[type["BaseModel"]] = None,
    *,
    cache_breakpoints: Optional[list[int]] = None,
    cache_mode: str = "none",
) -> dict:
    """Call an OpenAI-compatible /chat/completions API endpoint.

    Works for DeepSeek, OpenAI, and any provider implementing the same
    POST /chat/completions JSON schema.

    Args:
        provider: ProviderSpec from providers.yaml (model, max_tokens, timeout,
                  base_url, extra_headers). The provider.name determines which
                  env var to read for the API key.
        messages: OpenAI-format message list (role/content).
        response_model: Optional Pydantic model (passed back to llm_call).
        cache_breakpoints: Not supported by /chat/completions API (OpenAI has
                           prompt_tokens_details.cached_tokens on response but
                           no request-side breakpoint markers).  Accepted as
                           a no-op for interface compatibility.
        cache_mode: Not supported by this API format. No-op.

    Returns:
        dict with keys: content, model, usage, error (if present)

    Raises:
        ValueError: On missing API key, HTTP error, or unexpected response shape.
    """
    api_key = _resolve_api_key(provider.provider)
    base = provider.base_url or _DEFAULT_BASE_URLS.get(
        provider.provider,
        f"https://api.{provider.provider}.com/v1",  # fallback guess
    )
    timeout = provider.timeout

    # ── Build request body ──
    body: dict = {
        "model": provider.model,
        "max_tokens": provider.max_tokens,
        "messages": messages,
    }

    # ── Build headers ──
    headers = {
        "Authorization": f"Bearer {api_key}",
        "content-type": "application/json",
    }
    headers.update(provider.extra_headers)

    # ── Execute ──
    t0 = _time.monotonic()
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout)) as client:
            resp = client.post(
                f"{base.rstrip('/')}/chat/completions",
                headers=headers,
                content=json.dumps(body),
            )
    except httpx.TimeoutException as exc:
        from hermes_llm import ProviderError
        raise ProviderError(
            f"Chat completions request timed out after {timeout}s "
            f"(provider={provider.provider}, model={provider.model})",
            provider=provider.provider,
            model=provider.model,
            category="timeout",
            raw_error=exc,
        ) from exc
    except httpx.HTTPError as exc:
        from hermes_llm import ProviderError
        raise ProviderError(
            f"Chat completions HTTP error ({provider.provider}): {exc}",
            provider=provider.provider,
            model=provider.model,
            category="unknown",
            raw_error=exc,
        ) from exc

    elapsed = _time.monotonic() - t0
    logger.debug(
        "chat_completions %s/%s finished in %.2fs",
        provider.provider, provider.model, elapsed,
    )

    if resp.status_code != 200:
        from hermes_llm import ProviderError, classify_http_status
        try:
            err_body = resp.json()
        except Exception:
            err_body = {"raw": resp.text[:500]}
        category = classify_http_status(resp.status_code)
        raise ProviderError(
            f"Chat completions API ({provider.provider}) returned "
            f"{resp.status_code}: {json.dumps(err_body)}",
            provider=provider.provider,
            model=provider.model,
            category=category,
            status_code=resp.status_code,
        )

    data = resp.json()

    # ── Parse response ──
    # OpenAI / DeepSeek return:
    # {
    #   id, object, created, model,
    #   choices: [{index, message: {role, content}, finish_reason}],
    #   usage: {prompt_tokens, completion_tokens, total_tokens,
    #           prompt_tokens_details: {cached_tokens}}
    # }
    if not data.get("choices"):
        raise ValueError(
            f"Chat completions response ({provider.provider}) has no choices: "
            f"{json.dumps(data)[:500]}"
        )

    choice = data["choices"][0]
    message = choice.get("message", {})
    content = message.get("content", "")

    raw_usage = data.get("usage", {})
    details = raw_usage.get("prompt_tokens_details") or {}
    usage_normalized = {
        "input_tokens": raw_usage.get("prompt_tokens", 0),
        "prompt_tokens": raw_usage.get("prompt_tokens", 0),
        "output_tokens": raw_usage.get("completion_tokens", 0),
        "completion_tokens": raw_usage.get("completion_tokens", 0),
        "total_tokens": raw_usage.get("total_tokens", 0),
        "cached_tokens": details.get("cached_tokens", 0),
    }

    return {
        "content": content,
        "model": data.get("model", provider.model),
        "role": message.get("role", "assistant"),
        "finish_reason": choice.get("finish_reason"),
        "usage": usage_normalized,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Registration helpers
# ─────────────────────────────────────────────────────────────────────────────


def _register_one(provider_name: str) -> None:
    """Register a single provider name with the chat_completions adapter."""
    from hermes_llm import register_provider_dispatch

    register_provider_dispatch(provider_name, chat_completions)
    logger.info("registered provider dispatch: %s (chat completions)", provider_name)


def register() -> None:
    """Register DeepSeek and OpenAI dispatchers."""
    _register_one("deepseek")
    _register_one("openai")
