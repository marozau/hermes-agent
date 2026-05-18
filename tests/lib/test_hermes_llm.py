"""
Epic 3 — Provider Routing & LLM Helper.

Covers FR-36..FR-40, NFR-5, NFR-17, plus the code-review patches:
  DN1  optional Prefect @task (no-op fallback)
  DN2  LLMSpec / *Spec as pydantic.BaseModel
  DN3  same_provider_ok escape hatch
  DN5  schema retry on the provider that produced the parse-fail, then chain
  P1   ~/.hermes/dreams/providers.yaml exists with the five canonical workloads
  P4   llm_call returns dict for no-response_model case
  P5   cross-provider fallback chain enforced on schema-fail
  P6   cross-provider invariant ERROR at load time
  P7   failure telemetry row
  P8   atomic telemetry append
  P9   provider-agnostic usage normalization
  P10  JSON-from-markdown extractor
  P11  type-validate max_tokens/timeout on fallback (reject bool)
  P13  cache_breakpoints threaded through
  P14  cache mode threaded through
  P20  failure telemetry + schema escalation + extractor tests
"""
import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from pydantic import BaseModel

from lib.hermes_llm import (
    LLMSpec, ProviderSpec, WorkloadSpec,
    _extract_json_payload, _normalize_usage,
    _LLM_CALL_RETRIES, _LLM_CALL_RETRY_DELAYS, _LLM_CALL_TIMEOUT,
    llm_call, load_providers_config,
)


# ═════════════════════════════════════════════════════════════════════════════
# Story 3.1: providers.yaml loader
# ═════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def providers_yaml_path(tmp_path):
    """Valid providers.yaml — five canonical workloads."""
    cfg = {
        "version": 1,
        "workloads": {
            "classify_intent": {
                "primary": {"provider": "deepseek", "model": "deepseek-v4-flash",
                            "max_tokens": 80, "timeout": 3},
                "fallback": [],
                "cache": "5m",
            },
            "preflight_polish": {
                "primary": {"provider": "deepseek", "model": "deepseek-v4-flash",
                            "max_tokens": 300, "timeout": 8},
                "fallback": [{"provider": "anthropic",
                              "model": "claude-haiku-4-5-20251001",
                              "max_tokens": 300, "timeout": 8}],
                "cache": "5m",
            },
            "skill_dream_reflect": {
                "primary": {"provider": "anthropic", "model": "claude-sonnet-4-6",
                            "max_tokens": 4000, "timeout": 120},
                "fallback": [{"provider": "deepseek", "model": "deepseek-v4-pro",
                              "max_tokens": 4000, "timeout": 120}],
                "cache": "1h",
            },
            "memory_dream_consolidate": {
                "primary": {"provider": "anthropic", "model": "claude-sonnet-4-6",
                            "max_tokens": 6000, "timeout": 240},
                "fallback": [{"provider": "deepseek", "model": "deepseek-v4-pro",
                              "max_tokens": 6000, "timeout": 240}],
                "cache": "1h",
            },
            "board_dream_synthesize": {
                "primary": {"provider": "anthropic", "model": "claude-opus-4-7",
                            "max_tokens": 8000, "timeout": 600},
                "fallback": [{"provider": "anthropic", "model": "claude-sonnet-4-6",
                              "max_tokens": 8000, "timeout": 600}],
                "cache": "1h",
                "same_provider_ok": True,
            },
        },
    }
    p = tmp_path / "providers.yaml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")
    return p


class TestLoaderHappy:
    def test_loads_five_workloads(self, providers_yaml_path):
        config = load_providers_config(providers_yaml_path)
        assert set(config.keys()) == {
            "classify_intent", "preflight_polish", "skill_dream_reflect",
            "memory_dream_consolidate", "board_dream_synthesize",
        }

    def test_primary_populated(self, providers_yaml_path):
        wl = load_providers_config(providers_yaml_path)["classify_intent"]
        assert wl.primary.provider == "deepseek"
        assert wl.primary.max_tokens == 80
        assert wl.primary.timeout == 3

    def test_fallback_chain(self, providers_yaml_path):
        c = load_providers_config(providers_yaml_path)
        assert c["classify_intent"].fallback == []
        assert c["preflight_polish"].fallback[0].provider == "anthropic"

    def test_cache_mode(self, providers_yaml_path):
        c = load_providers_config(providers_yaml_path)
        assert c["classify_intent"].cache == "5m"
        assert c["memory_dream_consolidate"].cache == "1h"

    def test_same_provider_ok_propagates(self, providers_yaml_path):
        """DN3: board_dream_synthesize opts in to Opus→Sonnet."""
        c = load_providers_config(providers_yaml_path)
        assert c["board_dream_synthesize"].same_provider_ok is True
        assert c["preflight_polish"].same_provider_ok is False


class TestLoaderValidation:
    def test_rejects_missing_max_tokens(self, tmp_path):
        cfg = {"workloads": {"test": {
            "primary": {"provider": "x", "model": "y", "timeout": 10},
            "fallback": [], "cache": "5m"}}}
        path = tmp_path / "bad.yaml"
        path.write_text(yaml.dump(cfg), encoding="utf-8")
        with pytest.raises(ValueError, match=r"max_tokens"):
            load_providers_config(path)

    def test_rejects_bool_max_tokens(self, tmp_path):
        """P11: isinstance(True, int) is True — must be rejected explicitly."""
        cfg = {"workloads": {"test": {
            "primary": {"provider": "x", "model": "y",
                        "max_tokens": True, "timeout": 10},
            "fallback": [], "cache": "5m"}}}
        path = tmp_path / "bool.yaml"
        path.write_text(yaml.dump(cfg), encoding="utf-8")
        with pytest.raises(ValueError, match=r"max_tokens.*int"):
            load_providers_config(path)

    def test_rejects_string_max_tokens_in_fallback(self, tmp_path):
        """P11: fallback entries are validated for type too."""
        cfg = {"workloads": {"test": {
            "primary": {"provider": "a", "model": "m",
                        "max_tokens": 100, "timeout": 10},
            "fallback": [{"provider": "b", "model": "m2",
                          "max_tokens": "300", "timeout": 10}],
            "cache": "5m"}}}
        path = tmp_path / "fbstr.yaml"
        path.write_text(yaml.dump(cfg), encoding="utf-8")
        with pytest.raises(ValueError, match=r"max_tokens.*int"):
            load_providers_config(path)

    def test_rejects_same_provider_fallback(self, tmp_path):
        """P6 / Hard Invariant #10: cross-provider invariant errors at load."""
        cfg = {"workloads": {"test": {
            "primary": {"provider": "deepseek", "model": "v4-flash",
                        "max_tokens": 100, "timeout": 10},
            "fallback": [{"provider": "deepseek", "model": "v4-pro",
                          "max_tokens": 100, "timeout": 10}],
            "cache": "5m"}}}
        path = tmp_path / "samep.yaml"
        path.write_text(yaml.dump(cfg), encoding="utf-8")
        with pytest.raises(ValueError, match=r"cross-provider"):
            load_providers_config(path)

    def test_same_provider_ok_escape_hatch(self, tmp_path):
        """DN3: same_provider_ok lets legitimate Opus→Sonnet through."""
        cfg = {"workloads": {"board": {
            "primary": {"provider": "anthropic", "model": "opus",
                        "max_tokens": 100, "timeout": 10},
            "fallback": [{"provider": "anthropic", "model": "sonnet",
                          "max_tokens": 100, "timeout": 10}],
            "cache": "1h",
            "same_provider_ok": True}}}
        path = tmp_path / "opus.yaml"
        path.write_text(yaml.dump(cfg), encoding="utf-8")
        c = load_providers_config(path)
        assert c["board"].same_provider_ok is True

    def test_rejects_unknown_cache_mode(self, tmp_path):
        cfg = {"workloads": {"test": {
            "primary": {"provider": "x", "model": "y",
                        "max_tokens": 100, "timeout": 10},
            "fallback": [], "cache": "24h"}}}
        path = tmp_path / "cache.yaml"
        path.write_text(yaml.dump(cfg), encoding="utf-8")
        with pytest.raises(ValueError, match=r"cache"):
            load_providers_config(path)

    def test_missing_file_returns_empty(self, tmp_path):
        assert load_providers_config(tmp_path / "nope.yaml") == {}


class TestCanonicalProvidersYamlOnDisk:
    """P1: ~/.hermes/dreams/providers.yaml must exist and be valid."""

    def test_canonical_file_loads(self):
        from conftest import HERMES_ROOT
        path = HERMES_ROOT / "dreams" / "providers.yaml"
        assert path.exists(), f"Canonical providers.yaml missing at {path}"
        cfg = load_providers_config(path)
        assert set(cfg.keys()) == {
            "classify_intent", "preflight_polish", "skill_dream_reflect",
            "memory_dream_consolidate", "board_dream_synthesize",
        }


# ═════════════════════════════════════════════════════════════════════════════
# Story 3.2: retry constants exposed for Prefect @task (DN1)
# ═════════════════════════════════════════════════════════════════════════════


class TestRetryConstants:
    """Constants exposed for inspection. Prefect @task (when installed) wires them."""

    def test_retry_defaults(self):
        assert _LLM_CALL_RETRIES == 3
        assert _LLM_CALL_RETRY_DELAYS == [2, 8, 30]
        assert _LLM_CALL_TIMEOUT == 120


# ═════════════════════════════════════════════════════════════════════════════
# LLMSpec / data models (DN2)
# ═════════════════════════════════════════════════════════════════════════════


class TestLLMSpec:
    def test_basemodel_instance(self):
        spec = LLMSpec(workload="x", messages=[{"role": "user", "content": "hi"}])
        assert isinstance(spec, BaseModel)

    def test_field_defaults(self):
        spec = LLMSpec(workload="x", messages=[])
        assert spec.response_model is None
        assert spec.cache_breakpoints == []
        assert spec.idempotency_key is None

    def test_workloadspec_is_frozen(self):
        wl = WorkloadSpec(
            primary=ProviderSpec(provider="a", model="m", max_tokens=10, timeout=1),
            fallback=[], cache="5m",
        )
        with pytest.raises(Exception):
            wl.cache = "1h"  # frozen


# ═════════════════════════════════════════════════════════════════════════════
# Telemetry (NFR-17, P7, P8)
# ═════════════════════════════════════════════════════════════════════════════


def _make_config(*, fallback=None, same_provider_ok=False):
    return {
        "test": WorkloadSpec(
            primary=ProviderSpec(provider="prim", model="prim-m",
                                 max_tokens=100, timeout=10),
            fallback=fallback or [],
            cache="5m",
            same_provider_ok=same_provider_ok,
        ),
    }


class TestTelemetryOK:
    def test_one_row_per_successful_call(self, tmp_path):
        cfg = _make_config()
        spec = LLMSpec(workload="test", messages=[{"role": "user", "content": "hi"}])
        with patch("lib.hermes_llm._call_provider_api") as mock_api:
            mock_api.return_value = {
                "content": "free-text response", "model": "prim-m",
                "usage": {"input_tokens": 11, "output_tokens": 7,
                          "cache_read_input_tokens": 3},
            }
            result = llm_call(spec, observability_dir=str(tmp_path),
                              providers_config=cfg)
        # P4: dict, not bare string
        assert isinstance(result, dict)
        assert result["content"] == "free-text response"
        # Telemetry
        line = (tmp_path / "llm_calls.jsonl").read_text(encoding="utf-8").strip()
        row = json.loads(line)
        assert row["workload"] == "test"
        assert row["outcome"] == "ok"
        # P9: anthropic-style usage keys normalize
        assert row["tokens_in"] == 11
        assert row["tokens_out"] == 7
        assert row["cache_read_tokens"] == 3


class TestTelemetryFailure:
    """P7: failure path also emits telemetry."""

    def test_provider_chain_exhaustion_emits_failure_row(self, tmp_path):
        cfg = _make_config(fallback=[
            ProviderSpec(provider="fb", model="fb-m", max_tokens=50, timeout=5),
        ])
        spec = LLMSpec(workload="test", messages=[{"role": "user", "content": "x"}])
        with patch("lib.hermes_llm._call_provider_api") as mock_api:
            mock_api.side_effect = Exception("provider 503")
            with pytest.raises(RuntimeError, match="exhausted chain"):
                llm_call(spec, observability_dir=str(tmp_path), providers_config=cfg)
        row = json.loads(
            (tmp_path / "llm_calls.jsonl").read_text(encoding="utf-8").strip()
        )
        assert row["outcome"] == "failed_provider"
        assert "tried=" in row["error"]


# ═════════════════════════════════════════════════════════════════════════════
# Story 3.3: cross-provider fallback at runtime
# ═════════════════════════════════════════════════════════════════════════════


class TestCrossProviderFallback:
    def test_falls_back_on_primary_failure(self, tmp_path):
        cfg = _make_config(fallback=[
            ProviderSpec(provider="anth", model="haiku", max_tokens=50, timeout=5),
        ])
        spec = LLMSpec(workload="test", messages=[{"role": "user", "content": "hi"}])
        with patch("lib.hermes_llm._call_provider_api") as mock_api:
            mock_api.side_effect = [
                Exception("503"),
                {"content": "ok-fb", "model": "haiku", "usage": {}},
            ]
            result = llm_call(spec, observability_dir=str(tmp_path),
                              providers_config=cfg)
        assert result["content"] == "ok-fb"

    def test_same_provider_in_chain_is_skipped_by_default(self, tmp_path):
        """At runtime, same-provider fallbacks are filtered when
        same_provider_ok=False (loader would normally reject; we construct
        the spec directly here)."""
        cfg = {"test": WorkloadSpec(
            primary=ProviderSpec(provider="deepseek", model="flash",
                                 max_tokens=10, timeout=1),
            fallback=[
                ProviderSpec(provider="deepseek", model="pro",
                             max_tokens=10, timeout=1),
            ],
            cache="5m",
            same_provider_ok=False,
        )}
        spec = LLMSpec(workload="test", messages=[])
        with patch("lib.hermes_llm._call_provider_api") as mock_api:
            mock_api.side_effect = Exception("primary 503")
            with pytest.raises(RuntimeError, match="exhausted chain"):
                llm_call(spec, observability_dir=str(tmp_path), providers_config=cfg)
        # Only the primary was called (the same-provider fallback was filtered).
        assert mock_api.call_count == 1


# ═════════════════════════════════════════════════════════════════════════════
# Story 3.4: schema gate + cross-provider escalation on parse-fail (P5 / DN5)
# ═════════════════════════════════════════════════════════════════════════════


class _SimpleResult(BaseModel):
    outcome: str
    confidence: float


class TestSchemaGate:
    def test_clean_parse_returns_pydantic(self, tmp_path):
        cfg = _make_config()
        spec = LLMSpec(workload="test", messages=[],
                       response_model=_SimpleResult)
        with patch("lib.hermes_llm._call_provider_api") as mock_api:
            mock_api.return_value = {
                "content": json.dumps({"outcome": "ok", "confidence": 0.9}),
                "model": "prim-m",
                "usage": {"input_tokens": 5, "output_tokens": 3},
            }
            result = llm_call(spec, observability_dir=str(tmp_path),
                              providers_config=cfg)
        assert isinstance(result, _SimpleResult)
        assert result.outcome == "ok"

    def test_in_provider_retry_on_parse_fail(self, tmp_path):
        """DN5: parse-fail → retry on the SAME provider that produced raw."""
        cfg = _make_config()
        spec = LLMSpec(workload="test", messages=[], response_model=_SimpleResult)
        with patch("lib.hermes_llm._call_provider_api") as mock_api:
            mock_api.side_effect = [
                {"content": "not-valid-json", "model": "prim-m", "usage": {}},
                {"content": json.dumps({"outcome": "ok", "confidence": 1.0}),
                 "model": "prim-m", "usage": {}},
            ]
            result = llm_call(spec, observability_dir=str(tmp_path),
                              providers_config=cfg)
        assert result.outcome == "ok"
        assert mock_api.call_count == 2
        # Both calls should be the primary, not a fallback
        for call in mock_api.call_args_list:
            assert call.args[0].provider == "prim"

    def test_cross_provider_escalation_on_parse_retry_fail(self, tmp_path):
        """P5 / Story 3.4 AC #3: retry-then-fallback-on-parse-fail."""
        cfg = _make_config(fallback=[
            ProviderSpec(provider="fb", model="fb-m", max_tokens=50, timeout=5),
        ])
        spec = LLMSpec(workload="test", messages=[], response_model=_SimpleResult)
        with patch("lib.hermes_llm._call_provider_api") as mock_api:
            mock_api.side_effect = [
                # primary first call — bad JSON
                {"content": "not json", "model": "prim-m", "usage": {}},
                # primary retry — still bad JSON
                {"content": "still bad", "model": "prim-m", "usage": {}},
                # fallback — good JSON
                {"content": json.dumps({"outcome": "fb-good", "confidence": 0.5}),
                 "model": "fb-m", "usage": {}},
            ]
            result = llm_call(spec, observability_dir=str(tmp_path),
                              providers_config=cfg)
        assert isinstance(result, _SimpleResult)
        assert result.outcome == "fb-good"
        assert mock_api.call_count == 3

    def test_schema_chain_exhaustion_raises_with_both_errors(self, tmp_path):
        """P17: final ValueError must carry first AND last parse errors."""
        cfg = _make_config(fallback=[
            ProviderSpec(provider="fb", model="fb-m", max_tokens=50, timeout=5),
        ])
        spec = LLMSpec(workload="test", messages=[], response_model=_SimpleResult)
        with patch("lib.hermes_llm._call_provider_api") as mock_api:
            mock_api.side_effect = [
                {"content": "bad-1", "model": "prim-m", "usage": {}},
                {"content": "bad-2", "model": "prim-m", "usage": {}},
                {"content": "bad-3", "model": "fb-m", "usage": {}},
            ]
            with pytest.raises(ValueError) as exc_info:
                llm_call(spec, observability_dir=str(tmp_path),
                         providers_config=cfg)
        msg = str(exc_info.value)
        assert "first_error=" in msg and "final_error=" in msg


# ═════════════════════════════════════════════════════════════════════════════
# JSON extractor (P10)
# ═════════════════════════════════════════════════════════════════════════════


class TestJSONExtractor:
    def test_direct_json(self):
        assert _extract_json_payload('{"a": 1}') == '{"a": 1}'

    def test_json_fenced(self):
        s = 'Here is the answer:\n```json\n{"a": 1}\n```\nDone.'
        assert json.loads(_extract_json_payload(s)) == {"a": 1}

    def test_bare_fence(self):
        s = '```\n{"x": 2}\n```'
        assert json.loads(_extract_json_payload(s)) == {"x": 2}

    def test_prose_around_braces(self):
        s = 'The result was {"answer": 42} according to my analysis.'
        assert json.loads(_extract_json_payload(s)) == {"answer": 42}

    def test_pydantic_parses_fenced(self, tmp_path):
        """End-to-end: fenced JSON in content → Pydantic parse succeeds."""
        cfg = _make_config()
        spec = LLMSpec(workload="test", messages=[], response_model=_SimpleResult)
        with patch("lib.hermes_llm._call_provider_api") as mock_api:
            mock_api.return_value = {
                "content": '```json\n{"outcome": "fenced-ok", "confidence": 0.7}\n```',
                "model": "prim-m", "usage": {},
            }
            result = llm_call(spec, observability_dir=str(tmp_path),
                              providers_config=cfg)
        assert result.outcome == "fenced-ok"


# ═════════════════════════════════════════════════════════════════════════════
# Usage normalization (P9)
# ═════════════════════════════════════════════════════════════════════════════


class TestUsageNormalization:
    def test_anthropic_shape(self):
        ti, to, cr = _normalize_usage({
            "usage": {"input_tokens": 100, "output_tokens": 50,
                      "cache_read_input_tokens": 80}
        })
        assert (ti, to, cr) == (100, 50, 80)

    def test_openai_shape(self):
        ti, to, cr = _normalize_usage({
            "usage": {"prompt_tokens": 200, "completion_tokens": 75,
                      "prompt_tokens_details": {"cached_tokens": 150}}
        })
        assert (ti, to, cr) == (200, 75, 150)

    def test_missing_usage(self):
        assert _normalize_usage({}) == (0, 0, 0)


# ═════════════════════════════════════════════════════════════════════════════
# Story 3.5: idempotency key + cache breakpoints (FR-39, NFR-5)
# ═════════════════════════════════════════════════════════════════════════════


class TestIdempotencyAndCacheBreakpoints:
    def test_idempotency_key_in_telemetry(self, tmp_path):
        cfg = _make_config()
        spec = LLMSpec(workload="test", messages=[{"role": "user", "content": "x"}],
                       idempotency_key="key-abc")
        with patch("lib.hermes_llm._call_provider_api") as mock_api:
            mock_api.return_value = {"content": "ok", "model": "prim-m", "usage": {}}
            llm_call(spec, observability_dir=str(tmp_path), providers_config=cfg)
        row = json.loads(
            (tmp_path / "llm_calls.jsonl").read_text(encoding="utf-8").strip()
        )
        assert row["idempotency_key"] == "key-abc"

    def test_cache_breakpoints_threaded_to_dispatcher(self, tmp_path):
        """P13: spec.cache_breakpoints reaches _call_provider_api."""
        cfg = _make_config()
        spec = LLMSpec(
            workload="test", messages=[{"role": "system", "content": "a"}],
            cache_breakpoints=[0, 1, 2],
        )
        with patch("lib.hermes_llm._call_provider_api") as mock_api:
            mock_api.return_value = {"content": "ok", "model": "prim-m", "usage": {}}
            llm_call(spec, observability_dir=str(tmp_path), providers_config=cfg)
        kwargs = mock_api.call_args.kwargs
        assert kwargs["cache_breakpoints"] == [0, 1, 2]

    def test_cache_mode_threaded_to_dispatcher(self, tmp_path):
        """P14: workload's cache mode reaches _call_provider_api."""
        cfg = _make_config()
        spec = LLMSpec(workload="test", messages=[])
        with patch("lib.hermes_llm._call_provider_api") as mock_api:
            mock_api.return_value = {"content": "ok", "model": "prim-m", "usage": {}}
            llm_call(spec, observability_dir=str(tmp_path), providers_config=cfg)
        assert mock_api.call_args.kwargs["cache_mode"] == "5m"


# ═════════════════════════════════════════════════════════════════════════════
# Atomic telemetry sanity (P8)
# ═════════════════════════════════════════════════════════════════════════════


class TestTelemetryFile:
    def test_file_perms_owner_only(self, tmp_path):
        cfg = _make_config()
        spec = LLMSpec(workload="test", messages=[])
        with patch("lib.hermes_llm._call_provider_api") as mock_api:
            mock_api.return_value = {"content": "ok", "model": "prim-m", "usage": {}}
            llm_call(spec, observability_dir=str(tmp_path), providers_config=cfg)
        f = tmp_path / "llm_calls.jsonl"
        # 0o600 = owner rw only
        assert (f.stat().st_mode & 0o777) == 0o600
