"""
hermes_llm — Canonical LLM call site for the Hermes Auto-Dream substrate.

FR-37: All LLM calls route through llm_call(LLMSpec). No direct provider
       SDK imports in skill/plugin code.
FR-36: Workload-keyed routing via ~/.hermes/dreams/providers.yaml.
FR-38: Cross-provider fallback (default-enforced; per-workload escape hatch).
FR-39: Idempotency key + Prefect cache_policy=INPUTS (when Prefect available).
FR-40: Pydantic schemas gate effectful LLM output.
NFR-5: Anthropic prompt caching via cache_control breakpoints.
NFR-17: Per-call telemetry (success AND failure) to observability/llm_calls.jsonl.

Hard Invariants (CLAUDE.md):
  #2  Only this module calls LLM providers.
  #10 Cross-provider fallback enforced at config-load (escape hatch:
      per-workload `same_provider_ok: true`).
  #11 Pydantic schemas gate every effectful output.
  #12 Anthropic prompt caching uses three explicit breakpoints.
"""
from __future__ import annotations

import json as _json
import logging
import os
import re as _re
import time as _time
from datetime import datetime as _dt, timedelta, timezone as _tz
from pathlib import Path
from typing import Any, Callable, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Optional Prefect @task wrapper (DN1)
# ─────────────────────────────────────────────────────────────────────────────

_LLM_CALL_RETRIES = 3
_LLM_CALL_RETRY_DELAYS = [2, 8, 30]
_LLM_CALL_TIMEOUT = 120

try:  # pragma: no cover — exercised only when Prefect is installed
    from prefect import task as _prefect_task  # type: ignore
    try:
        from prefect.cache_policies import INPUTS as _PREFECT_INPUTS  # type: ignore
    except ImportError:
        _PREFECT_INPUTS = None  # older Prefect versions

    def _task(fn):
        return _prefect_task(
            retries=_LLM_CALL_RETRIES,
            retry_delay_seconds=_LLM_CALL_RETRY_DELAYS,
            cache_policy=_PREFECT_INPUTS,
            cache_expiration=timedelta(minutes=10),
            timeout_seconds=_LLM_CALL_TIMEOUT,
        )(fn)
except ImportError:
    # Prefect not installed — llm_call runs as a plain function. The dream
    # orchestrator (Epic 4) will add Prefect to deps and the same decorator
    # will then take effect with zero call-site changes.
    def _task(fn):
        return fn


# ─────────────────────────────────────────────────────────────────────────────
# Data models (Story 3.1)
# ─────────────────────────────────────────────────────────────────────────────


class ProviderSpec(BaseModel):
    """A single provider endpoint with model and limits."""
    model_config = ConfigDict(frozen=True)

    provider: str
    model: str
    max_tokens: int
    timeout: int  # seconds
    base_url: Optional[str] = None
    extra_headers: dict = Field(default_factory=dict)


class WorkloadSpec(BaseModel):
    """Routing for one named workload: primary + ordered fallbacks + cache."""
    model_config = ConfigDict(frozen=True)

    primary: ProviderSpec
    fallback: list[ProviderSpec]
    cache: str = "none"             # "5m" | "1h" | "none"
    same_provider_ok: bool = False  # DN3 escape hatch for board_dream_synthesize


class LLMSpec(BaseModel):
    """Spec for a single LLM call via the canonical helper (FR-37).

    DN2: pydantic.BaseModel so Prefect cache_policy=INPUTS can hash inputs
    via Pydantic JSON serialization.
    """
    model_config = ConfigDict(arbitrary_types_allowed=True)

    workload: str
    messages: list[dict]
    response_model: Optional[type[BaseModel]] = None
    cache_breakpoints: list[int] = Field(default_factory=list)
    idempotency_key: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# Path resolution helpers
# ─────────────────────────────────────────────────────────────────────────────


def _env(key: str) -> Optional[str]:
    """Read env var, treating empty string as unset (P12)."""
    v = os.environ.get(key)
    return v if v else None


def _resolve_providers_path(override: Optional[Union[Path, str]] = None) -> Path:
    if override is not None:
        return Path(override)
    env_p = _env("HERMES_PROVIDERS_PATH")
    if env_p:
        return Path(env_p)
    from lib._hermes_paths import resolve_hermes_home
    return Path(resolve_hermes_home()) / "dreams" / "providers.yaml"


def _resolve_observability_dir(override: Optional[str] = None) -> Path:
    if override:
        return Path(override)
    from lib._hermes_paths import resolve_hermes_home
    return Path(resolve_hermes_home()) / "observability"


# ─────────────────────────────────────────────────────────────────────────────
# Loader (Story 3.1 / FR-36; cross-provider enforcement = Hard Invariant #10)
# ─────────────────────────────────────────────────────────────────────────────

_REQUIRED_PROVIDER_FIELDS = ("provider", "model", "max_tokens", "timeout")
_VALID_CACHE_MODES = ("5m", "1h", "none")

# F13: module-level cache for load_providers_config keyed by (path, mtime)
_providers_cache: dict[tuple, dict[str, WorkloadSpec]] = {}


def _validate_provider_dict(d: dict, *, scope: str) -> ProviderSpec:
    """P11: validate provider entry (primary OR fallback). Rejects bool sneak-in."""
    if not isinstance(d, dict):
        raise ValueError(f"{scope}: provider entry must be a dict, got {type(d).__name__}")
    for key in _REQUIRED_PROVIDER_FIELDS:
        if key not in d:
            raise ValueError(f"{scope}: missing required field '{key}'")
    # Reject bool (which is technically int in Python).
    for int_field in ("max_tokens", "timeout"):
        v = d[int_field]
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError(
                f"{scope}: '{int_field}' must be an int (got {type(v).__name__}={v!r})"
            )
    return ProviderSpec(
        provider=d["provider"],
        model=d["model"],
        max_tokens=d["max_tokens"],
        timeout=d["timeout"],
        base_url=d.get("base_url"),
        extra_headers=d.get("extra_headers", {}),
    )


def load_providers_config(
    path: Optional[Union[Path, str]] = None,
) -> dict[str, WorkloadSpec]:
    """Load + validate providers.yaml (FR-36, Hard Invariant #10).

    Returns dict[workload_name → WorkloadSpec].
    Raises ValueError on validation failure.
    Returns empty dict when the file is missing (callers see a clear
    "Unknown workload" error pointing at the missing file path).
    """
    import yaml as _yaml

    filepath = _resolve_providers_path(path)

    # F13: check cache keyed by (resolved_path, mtime)
    cache_key = None
    try:
        mtime = filepath.stat().st_mtime if filepath.exists() else -1
        cache_key = (str(filepath), mtime)
        if cache_key in _providers_cache:
            return _providers_cache[cache_key]
    except OSError:
        pass

    if not filepath.exists():
        logger.warning("providers.yaml not found at %s", filepath)
        return {}

    with filepath.open(encoding="utf-8") as f:
        raw = _yaml.safe_load(f) or {}

    if not isinstance(raw, dict):
        raise ValueError("providers.yaml must be a mapping with a 'workloads' key")

    workloads_raw = raw.get("workloads")
    if workloads_raw is None:
        workloads_raw = {}
    if not isinstance(workloads_raw, dict):
        raise ValueError("providers.yaml: 'workloads' must be a mapping")

    # P18: two-phase load — validate everything before populating result.
    staged: list[tuple[str, WorkloadSpec]] = []
    for name, wl in workloads_raw.items():
        if not isinstance(wl, dict):
            raise ValueError(f"Workload '{name}': must be a mapping")

        primary = _validate_provider_dict(
            wl.get("primary", {}), scope=f"Workload '{name}' primary",
        )

        fallback_raw = wl.get("fallback")
        if fallback_raw is None:
            fallback_raw = []
        if not isinstance(fallback_raw, list):
            raise ValueError(f"Workload '{name}': 'fallback' must be a list")
        fallbacks: list[ProviderSpec] = [
            _validate_provider_dict(fb, scope=f"Workload '{name}' fallback[{i}]")
            for i, fb in enumerate(fallback_raw)
        ]

        cache = wl.get("cache", "none")
        if cache not in _VALID_CACHE_MODES:
            raise ValueError(
                f"Workload '{name}': 'cache' must be one of {_VALID_CACHE_MODES} "
                f"(got {cache!r})"
            )

        same_provider_ok = wl.get("same_provider_ok", False) in (True, "true", "True", 1)

        # Hard Invariant #10: cross-provider fallback ENFORCED at load time.
        # DN3: per-workload `same_provider_ok: true` opt-in for Opus→Sonnet.
        if not same_provider_ok:
            offenders = [fb.provider for fb in fallbacks if fb.provider == primary.provider]
            if offenders:
                raise ValueError(
                    f"Workload '{name}': cross-provider invariant violated (FR-38). "
                    f"primary={primary.provider!r}, fallback includes same provider. "
                    f"If intentional (e.g. Opus→Sonnet), set 'same_provider_ok: true'."
                )

        staged.append((name, WorkloadSpec(
            primary=primary,
            fallback=fallbacks,
            cache=cache,
            same_provider_ok=same_provider_ok,
        )))

    result = dict(staged)
    # F13: populate cache
    if cache_key is not None:
        _providers_cache.clear()  # single-entry cache (only one config file)
        _providers_cache[cache_key] = result
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Telemetry (NFR-17)
# ─────────────────────────────────────────────────────────────────────────────


def _write_telemetry(
    *,
    workload: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
    latency_ms: float,
    schema_status: str,
    idempotency_key: Optional[str],
    cache_read_tokens: int = 0,
    outcome: str = "ok",        # "ok" | "failed_provider" | "failed_schema" | "failed_unwired"
    error: Optional[str] = None,
    observability_dir: Optional[str] = None,
) -> None:
    """Atomic, owner-only telemetry append (P8)."""
    obs_dir = _resolve_observability_dir(observability_dir)
    obs_dir.mkdir(parents=True, exist_ok=True)
    row = _json.dumps({
        "ts": _dt.now(_tz.utc).isoformat(),
        "workload": workload,
        "model": model,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cache_read_tokens": cache_read_tokens,
        "latency_ms": round(latency_ms, 2),
        "schema_status": schema_status,
        "outcome": outcome,
        "error": error,
        "idempotency_key": idempotency_key,
    }, ensure_ascii=False, sort_keys=True) + "\n"
    fd = os.open(
        str(obs_dir / "llm_calls.jsonl"),
        os.O_WRONLY | os.O_CREAT | os.O_APPEND,
        0o600,
    )
    try:
        os.write(fd, row.encode("utf-8"))
    finally:
        os.close(fd)


# ─────────────────────────────────────────────────────────────────────────────
# Token / usage normalization (P9)
# ─────────────────────────────────────────────────────────────────────────────


def _normalize_usage(raw: dict) -> tuple[int, int, int]:
    """Return (tokens_in, tokens_out, cache_read_tokens) across providers.

    Anthropic:       usage.input_tokens / output_tokens / cache_read_input_tokens
    OpenAI / compat: usage.prompt_tokens / completion_tokens
                     usage.prompt_tokens_details.cached_tokens
    """
    usage = raw.get("usage") or {}
    tokens_in = usage.get("input_tokens") or usage.get("prompt_tokens") or 0
    tokens_out = usage.get("output_tokens") or usage.get("completion_tokens") or 0
    cache_read = (
        usage.get("cache_read_input_tokens")
        or (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
        or raw.get("cache_read_tokens")
        or 0
    )
    return int(tokens_in), int(tokens_out), int(cache_read)


# ─────────────────────────────────────────────────────────────────────────────
# JSON-from-content extractor (P10)
# ─────────────────────────────────────────────────────────────────────────────

_JSON_FENCE_RE = _re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", _re.DOTALL)


def _extract_json_payload(content: str) -> str:
    """Try direct JSON, then ```json fence```, then prose-stripped braces."""
    s = content.strip()
    if s.startswith("{") or s.startswith("["):
        return s
    m = _JSON_FENCE_RE.search(s)
    if m:
        return m.group(1).strip()
    first = s.find("{")
    last = s.rfind("}")
    if first != -1 and last != -1 and last > first:
        return s[first : last + 1]
    return s  # let pydantic raise the canonical error


# ─────────────────────────────────────────────────────────────────────────────
# Provider error classification (fallback-visibility)
# ─────────────────────────────────────────────────────────────────────────────


class ProviderError(Exception):
    """Exception carrying provider error classification metadata.

    Raised by provider dispatchers instead of bare ValueError when the
    upstream API returns a distinguishable error.  The fallback chain in
    llm_call uses these fields to emit structured WARN-level logs.
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        model: str,
        category: str,  # timeout | rate-limit | model-unavailable | server-error | unknown
        status_code: Optional[int] = None,
        raw_error: Optional[Exception] = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.model = model
        self.category = category
        self.status_code = status_code
        self.raw_error = raw_error


def classify_http_status(status_code: int) -> str:
    """Classify an HTTP status code into an error category string."""
    if status_code == 429:
        return "rate-limit"
    if status_code in (400, 404):
        return "model-unavailable"
    if 500 <= status_code < 600:
        return "server-error"
    return "unknown"


def classify_exception(exc: BaseException) -> str:
    """Classify a caught exception into an error category string.

    ProviderError carries its own category; httpx timeouts map to 'timeout';
    everything else is 'unknown'.
    """
    try:
        import httpx as _httpx
        if isinstance(exc, _httpx.TimeoutException):
            return "timeout"
    except ImportError:
        pass
    if isinstance(exc, ProviderError):
        return exc.category
    return "unknown"


# ─────────────────────────────────────────────────────────────────────────────
# Provider dispatch seam (DN4)
# ─────────────────────────────────────────────────────────────────────────────

_PROVIDER_DISPATCH: dict[str, Callable[..., dict]] = {}
_EMBEDDING_DISPATCH: dict[str, Callable[..., list[list[float]]]] = {}


def register_provider_dispatch(provider: str, fn: Callable[..., dict]) -> None:
    """Register a provider implementation. Epic 4 (dream-orchestrator) calls
    this at startup to wire anthropic / openai / deepseek transports."""
    _PROVIDER_DISPATCH[provider] = fn


def register_embedding_dispatch(provider: str, fn: Callable[..., list[list[float]]]) -> None:
    """Story 8.4/11.1: register a batched embedding provider implementation."""
    _EMBEDDING_DISPATCH[provider] = fn


def _call_provider_api(
    provider: ProviderSpec,
    messages: list[dict],
    response_model: Optional[type[BaseModel]] = None,
    *,
    cache_breakpoints: Optional[list[int]] = None,
    cache_mode: str = "none",
) -> dict:
    """Dispatch to a registered provider impl, or raise a clear error.

    Production wiring lives in Epic 4 (dream-orchestrator). Tests inject mocks
    via `mock.patch('lib.hermes_llm._call_provider_api')` or
    `register_provider_dispatch(...)`.
    """
    fn = _PROVIDER_DISPATCH.get(provider.provider)
    if fn is None:
        raise NotImplementedError(
            f"_call_provider_api: no dispatcher registered for provider "
            f"'{provider.provider}'. Either: "
            f"(a) register one via register_provider_dispatch() — Epic 4 wires this; "
            f"(b) mock the seam in tests."
        )
    return fn(
        provider,
        messages,
        response_model,
        cache_breakpoints=cache_breakpoints or [],
        cache_mode=cache_mode,
    )


# ─────────────────────────────────────────────────────────────────────────────
# llm_call (Story 3.2 / FR-37, FR-40, NFR-17)
# ─────────────────────────────────────────────────────────────────────────────


def _attempt_call(provider: ProviderSpec, spec: LLMSpec, cache_mode: str) -> dict:
    """Single provider attempt — shim so retries/fallbacks share one code path."""
    return _call_provider_api(
        provider, spec.messages, spec.response_model,
        cache_breakpoints=spec.cache_breakpoints, cache_mode=cache_mode,
    )


def _validate_response(
    raw: dict, response_model: Optional[type[BaseModel]],
) -> "tuple[Any, Optional[Exception]]":
    """Parse `raw['content']` against `response_model`. Returns (parsed, err)."""
    if response_model is None:
        return raw, None  # P4: dict, not bare content
    content = raw.get("content", "")
    payload = _extract_json_payload(content)
    try:
        return response_model.model_validate_json(payload), None
    except Exception as exc:
        return None, exc


def _emit_telemetry_for(
    spec: LLMSpec, raw: Optional[dict], t0: float,
    schema_status: str, outcome: str, error: Optional[str],
    observability_dir: Optional[str],
) -> None:
    latency_ms = (_time.monotonic() - t0) * 1000
    if raw is None:
        _write_telemetry(
            workload=spec.workload, model="unknown",
            tokens_in=0, tokens_out=0, cache_read_tokens=0,
            latency_ms=latency_ms, schema_status=schema_status,
            outcome=outcome, error=error,
            idempotency_key=spec.idempotency_key,
            observability_dir=observability_dir,
        )
        return
    tokens_in, tokens_out, cache_read = _normalize_usage(raw)
    _write_telemetry(
        workload=spec.workload, model=raw.get("model", "unknown"),
        tokens_in=tokens_in, tokens_out=tokens_out,
        cache_read_tokens=cache_read,
        latency_ms=latency_ms, schema_status=schema_status,
        outcome=outcome, error=error,
        idempotency_key=spec.idempotency_key,
        observability_dir=observability_dir,
    )


@_task
def llm_call(
    spec: LLMSpec,
    *,
    observability_dir: Optional[str] = None,
    providers_config: Optional[dict[str, WorkloadSpec]] = None,
) -> Union[dict, BaseModel]:
    """Canonical LLM call site (FR-37, FR-40, NFR-17, Hard Invariants #2/#10/#11).

    Returns either a Pydantic instance (when spec.response_model is set) or
    a dict `{content, model, usage, ...}` for free-text outputs.
    """
    config = providers_config if providers_config is not None else load_providers_config()
    wl = config.get(spec.workload)
    if wl is None:
        if not config:
            raise ValueError(
                f"Unknown workload '{spec.workload}'. No providers.yaml is loaded "
                f"(checked {_resolve_providers_path()}). Create it from the "
                f"canonical config in CLAUDE.md."
            )
        raise ValueError(
            f"Unknown workload '{spec.workload}'. Known: {sorted(config.keys())}"
        )

    t0 = _time.monotonic()

    # ── First-attempt chain: primary, then cross-provider fallbacks ──
    raw: Optional[dict] = None
    served_by: Optional[ProviderSpec] = None
    last_exc: Optional[BaseException] = None
    tried: list[str] = []

    chain: list[ProviderSpec] = [wl.primary] + [
        fb for fb in wl.fallback
        if wl.same_provider_ok or fb.provider != wl.primary.provider
    ]
    for idx, prov in enumerate(chain):
        try:
            raw = _attempt_call(prov, spec, cache_mode=wl.cache)
            served_by = prov
            break
        except NotImplementedError:
            _emit_telemetry_for(
                spec, raw=None, t0=t0,
                schema_status="not_applicable", outcome="failed_unwired",
                error=f"no dispatcher for {prov.provider}",
                observability_dir=observability_dir,
            )
            raise
        except Exception as exc:
            last_exc = exc
            tried.append(prov.provider)
            # ── Fallback activation logging (WARN level) ──
            category = classify_exception(exc)
            status_code = getattr(exc, "status_code", None)
            if idx + 1 < len(chain):
                fallback_target = f"{chain[idx + 1].provider}/{chain[idx + 1].model}"
            else:
                fallback_target = "none (exhausted)"
            logger.warning(
                "Fallback triggered: %s/%s failed (category=%s, status=%s) -> %s",
                prov.provider, prov.model, category, status_code, fallback_target,
            )
            continue

    if raw is None:
        _emit_telemetry_for(
            spec, raw=None, t0=t0,
            schema_status="not_applicable", outcome="failed_provider",
            error=f"primary+fallbacks failed: tried={tried}",
            observability_dir=observability_dir,
        )
        logger.warning(
            "All providers exhausted for workload '%s': tried=%s",
            spec.workload, tried,
        )
        raise RuntimeError(
            f"llm_call: workload '{spec.workload}' exhausted chain "
            f"(tried providers={tried})"
        ) from last_exc

    # ── Schema gate (FR-40, NFR-12) ──
    if spec.response_model is None:
        _emit_telemetry_for(
            spec, raw=raw, t0=t0,
            schema_status="not_applicable", outcome="ok", error=None,
            observability_dir=observability_dir,
        )
        return raw  # P4: dict

    parsed, parse_err_1 = _validate_response(raw, spec.response_model)
    if parse_err_1 is None:
        _emit_telemetry_for(
            spec, raw=raw, t0=t0,
            schema_status="ok", outcome="ok", error=None,
            observability_dir=observability_dir,
        )
        return parsed

    # P5 / Story 3.4 AC #3: in-provider retry on the provider that produced
    # the parse-failing response (NOT necessarily the primary).
    parse_err_2: Optional[Exception] = None
    try:
        retry_raw = _attempt_call(served_by, spec, cache_mode=wl.cache)
        parsed, parse_err_2 = _validate_response(retry_raw, spec.response_model)
        if parse_err_2 is None:
            _emit_telemetry_for(
                spec, raw=retry_raw, t0=t0,
                schema_status="ok_after_retry", outcome="ok", error=None,
                observability_dir=observability_dir,
            )
            return parsed
    except Exception as retry_exc:
        parse_err_2 = retry_exc

    # P5 cont: walk the cross-provider fallback chain enforcing the schema.
    fallback_chain = [
        fb for fb in wl.fallback
        if (wl.same_provider_ok or fb.provider != wl.primary.provider)
        and fb is not served_by
    ]
    parse_err_3: Optional[Exception] = None
    for prov in fallback_chain:
        try:
            fb_raw = _attempt_call(prov, spec, cache_mode=wl.cache)
        except Exception as e:
            parse_err_3 = e
            category = classify_exception(e)
            logger.warning(
                "Schema fallback: %s/%s failed (category=%s)",
                prov.provider, prov.model, category,
            )
            continue
        parsed, parse_err_3 = _validate_response(fb_raw, spec.response_model)
        if parse_err_3 is None:
            _emit_telemetry_for(
                spec, raw=fb_raw, t0=t0,
                schema_status="ok_after_fallback", outcome="ok", error=None,
                observability_dir=observability_dir,
            )
            return parsed

    # P17: preserve BOTH parse errors in the exception chain.
    final_err = parse_err_3 or parse_err_2 or parse_err_1
    _emit_telemetry_for(
        spec, raw=raw, t0=t0,
        schema_status="failed", outcome="failed_schema",
        error=f"first: {parse_err_1!r}; last: {final_err!r}",
        observability_dir=observability_dir,
    )
    raise ValueError(
        f"llm_call: Pydantic validation failed for "
        f"'{spec.response_model.__name__}'. first_error={parse_err_1!r}; "
        f"final_error={final_err!r}"
    ) from final_err


# ─────────────────────────────────────────────────────────────────────────────
# Story 8.4: Embedding calls via llm_embed (FR-37, Hard Invariant #2)
# ─────────────────────────────────────────────────────────────────────────────


def llm_embed(
    texts: "Union[str, list[str]]",
    workload: str = "recall_embed",
    *,
    observability_dir: Optional[str] = None,
    providers_config: Optional[dict[str, WorkloadSpec]] = None,
) -> "Union[Optional[list[float]], list[Optional[list[float]]]]":
    """Story 11.1: batched embedding call site (back-compat with single string).

    Routes through providers.yaml like llm_call.  Returns a list of embedding
    vectors (one per input text) or ``None`` on failure.

    Back-compat: if *texts* is a plain ``str`` it is wrapped in a list and
    the return value is a single ``list[float] | None`` (the old behaviour).

    Hard Invariant #2: this is the only sanctioned embedding call site.
    Cross-provider fallback is enforced (Hard Invariant #10).
    """
    # Back-compat shim: single string in → single vector out
    _single = isinstance(texts, str)
    if _single:
        texts = [texts]  # type: ignore[list-item]

    config = providers_config if providers_config is not None else load_providers_config()
    wl = config.get(workload)
    if wl is None:
        logger.warning("llm_embed: workload '%s' not in providers.yaml", workload)
        fallback: list[Optional[list[float]]] = [None] * len(texts)
        return None if _single else fallback

    t0 = _time.monotonic()
    chain: list[ProviderSpec] = [wl.primary] + [
        fb for fb in wl.fallback
        if wl.same_provider_ok or fb.provider != wl.primary.provider
    ]

    last_exc: Optional[BaseException] = None
    tried: list[str] = []

    for prov in chain:
        fn = _EMBEDDING_DISPATCH.get(prov.provider)
        if fn is None:
            tried.append(prov.provider)
            last_exc = NotImplementedError(
                f"No embedding dispatch for provider '{prov.provider}'"
            )
            continue
        try:
            # F4: backward-compat guard — old dispatch functions may have
            # signature fn(prov, text: str) instead of fn(prov, texts: list).
            try:
                vecs = fn(prov, texts)  # type: list[list[float]]
            except TypeError:
                if len(texts) == 1:
                    vecs = [fn(prov, texts[0])]  # type: ignore[reportAssignmentType]  # old sig returns list[float]
                else:
                    raise
            elapsed_ms = (_time.monotonic() - t0) * 1000
            _write_telemetry(
                workload=workload, model=prov.model,
                tokens_in=sum(len(t.split()) for t in texts), tokens_out=0,
                cache_read_tokens=0, latency_ms=elapsed_ms,
                schema_status="embedding", outcome="ok", error=None,
                idempotency_key=None, observability_dir=observability_dir,
            )
            return vecs[0] if _single else list(vecs)  # list[float] | list[list[float]]
        except Exception as exc:
            last_exc = exc
            tried.append(prov.provider)
            category = classify_exception(exc)
            logger.warning(
                "Embedding fallback: %s/%s failed (category=%s) -> %s",
                prov.provider, prov.model, category,
                "next" if len(tried) < len(chain) else "exhausted",
            )
            continue

    elapsed_ms = (_time.monotonic() - t0) * 1000
    _write_telemetry(
        workload=workload, model="unknown",
        tokens_in=sum(len(t.split()) for t in texts), tokens_out=0,
        cache_read_tokens=0, latency_ms=elapsed_ms,
        schema_status="embedding", outcome="failed",
        error=f"exhausted: tried={tried}",
        idempotency_key=None, observability_dir=observability_dir,
    )
    logger.warning(
        "llm_embed: all providers exhausted for workload '%s': tried=%s",
        workload, tried,
    )
    fallback2: list[Optional[list[float]]] = [None] * len(texts)
    return None if _single else fallback2  # fail-open


def llm_embed_one(
    text: str,
    workload: str = "recall_embed",
    *,
    observability_dir: Optional[str] = None,
    providers_config: Optional[dict[str, WorkloadSpec]] = None,
) -> Optional[list[float]]:
    """Back-compat shim: embed a single text, return one vector or None."""
    result = llm_embed(
        [text], workload,
        observability_dir=observability_dir,
        providers_config=providers_config,
    )
    if result is None or not isinstance(result, list):
        return None
    # result is list[list[float] | None] with one element
    val = result[0]
    return val  # type: ignore[return-value]
