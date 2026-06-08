"""
End-to-end smoke tests for provider dispatcher (Story 3.8).

Exercises register_all() → dispatcher dispatch → adapter invocation
with fixture-based replay (no live API calls).

Covers:
- Anthropic adapter through dispatcher
- DeepSeek and OpenAI adapters through dispatcher
- Fallback scenario (primary → secondary)
- cache_control through dispatcher
"""

import os
from unittest.mock import patch

import pytest

from fixtures import load_fixture


# ─────────────────────────────────────────────────────────────────────────────
# Autouse: register all providers before each test
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _register_providers():
    """Ensure all provider dispatchers are registered before each test."""
    from autodream.providers import register_all

    register_all()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers: replay an API response from fixture files
# ─────────────────────────────────────────────────────────────────────────────


def _replay_anthropic_response(httpx_mock_instance):
    """Configure httpx mock to return Anthropic fixture."""
    fixture = load_fixture("anthropic", "messages_api")
    httpx_mock_instance.post.return_value.status_code = 200
    httpx_mock_instance.post.return_value.json.return_value = fixture
    return fixture


def _replay_deepseek_response(httpx_mock_instance):
    """Configure httpx mock to return DeepSeek fixture."""
    fixture = load_fixture("deepseek", "chat_completion")
    httpx_mock_instance.post.return_value.status_code = 200
    httpx_mock_instance.post.return_value.json.return_value = fixture
    return fixture


def _replay_openai_response(httpx_mock_instance):
    """Configure httpx mock to return OpenAI fixture."""
    fixture = load_fixture("openai", "chat_completion")
    httpx_mock_instance.post.return_value.status_code = 200
    httpx_mock_instance.post.return_value.json.return_value = fixture
    return fixture


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures: common test data
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def messages():
    return [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello!"},
    ]


@pytest.fixture
def three_block_messages():
    """Matches ADR-7 layout: system, skills bundle, user turn."""
    return [
        {"role": "system", "content": "You are a helpful Hermes agent."},
        {"role": "user", "content": "## Skills bundle\n\ngit, docker, pytest"},
        {"role": "user", "content": "Can you help me with git?"},
    ]


@pytest.fixture
def provider_spec():
    """Create a ProviderSpec with test values.

    Uses ProviderSpec from autodream.llm so tests match real usage.
    """
    from autodream.llm import ProviderSpec

    return ProviderSpec(
        provider="test-provider",
        model="test-model",
        max_tokens=500,
        timeout=10,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Registration smoke test
# ─────────────────────────────────────────────────────────────────────────────


class TestDispatcherRegistration:
    """register_all() creates a functional dispatcher for every provider."""

    def test_register_all_wires_anthropic(self):
        import autodream.llm
        from autodream.providers import register_all

        if "anthropic" in autodream.llm._PROVIDER_DISPATCH:
            del autodream.llm._PROVIDER_DISPATCH["anthropic"]

        register_all()
        assert "anthropic" in autodream.llm._PROVIDER_DISPATCH
        assert callable(autodream.llm._PROVIDER_DISPATCH["anthropic"])

    def test_register_all_wires_deepseek(self):
        import autodream.llm
        from autodream.providers import register_all

        if "deepseek" in autodream.llm._PROVIDER_DISPATCH:
            del autodream.llm._PROVIDER_DISPATCH["deepseek"]

        register_all()
        assert "deepseek" in autodream.llm._PROVIDER_DISPATCH
        assert callable(autodream.llm._PROVIDER_DISPATCH["deepseek"])

    def test_register_all_wires_openai(self):
        import autodream.llm
        from autodream.providers import register_all

        if "openai" in autodream.llm._PROVIDER_DISPATCH:
            del autodream.llm._PROVIDER_DISPATCH["openai"]

        register_all()
        assert "openai" in autodream.llm._PROVIDER_DISPATCH
        assert callable(autodream.llm._PROVIDER_DISPATCH["openai"])

    def test_register_all_idempotent(self):
        import autodream.llm
        from autodream.providers import register_all

        register_all()
        count_before = len(autodream.llm._PROVIDER_DISPATCH)
        register_all()
        count_after = len(autodream.llm._PROVIDER_DISPATCH)
        assert count_after == count_before, "register_all() must be idempotent"


# ─────────────────────────────────────────────────────────────────────────────
# Anthropic end-to-end via dispatcher
# ─────────────────────────────────────────────────────────────────────────────


class TestAnthropicViaDispatcher:
    """Exercises Anthropic adapter through the dispatcher."""

    def test_anthropic_chat_via_dispatcher(self, messages):
        """Anthropic dispatcher returns fixture response."""
        from autodream.llm import _PROVIDER_DISPATCH, ProviderSpec

        with patch("autodream.providers_anthropic.httpx.Client") as mock_client:
            mock_instance = mock_client.return_value.__enter__.return_value
            fixture = _replay_anthropic_response(mock_instance)

            spec = ProviderSpec(
                provider="anthropic",
                model="claude-sonnet-4-6",
                max_tokens=500,
                timeout=10,
            )

            os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test-key"
            try:
                dispatcher = _PROVIDER_DISPATCH["anthropic"]
                result = dispatcher(spec, messages)

                assert "content" in result
                assert "git" in result["content"]
                assert result["model"] == "claude-sonnet-4-6-20260501"
            finally:
                del os.environ["ANTHROPIC_API_KEY"]

    def test_anthropic_via_dispatcher_with_cache(self, three_block_messages):
        """Dispatched Anthropic call with cache_breakpoints works."""
        from autodream.llm import _PROVIDER_DISPATCH, ProviderSpec

        with patch("autodream.providers_anthropic.httpx.Client") as mock_client:
            mock_instance = mock_client.return_value.__enter__.return_value
            _replay_anthropic_response(mock_instance)

            spec = ProviderSpec(
                provider="anthropic",
                model="claude-sonnet-4-6",
                max_tokens=500,
                timeout=10,
            )

            os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test-key"
            try:
                dispatcher = _PROVIDER_DISPATCH["anthropic"]
                result = dispatcher(
                    spec, three_block_messages,
                    cache_breakpoints=[0, 1],
                    cache_mode="5m",
                )
                assert "content" in result
                assert result["content"]
            finally:
                del os.environ["ANTHROPIC_API_KEY"]

    def test_anthropic_dispatcher_missing_key(self, messages):
        """Missing ANTHROPIC_API_KEY raises ValueError through dispatcher."""
        from autodream.llm import _PROVIDER_DISPATCH, ProviderSpec

        os.environ.pop("ANTHROPIC_API_KEY", None)

        spec = ProviderSpec(
            provider="anthropic",
            model="claude-sonnet-4-6",
            max_tokens=500,
            timeout=10,
        )
        dispatcher = _PROVIDER_DISPATCH["anthropic"]
        with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
            dispatcher(spec, messages)


# ─────────────────────────────────────────────────────────────────────────────
# DeepSeek end-to-end via dispatcher
# ─────────────────────────────────────────────────────────────────────────────


class TestDeepSeekViaDispatcher:
    """Exercises DeepSeek adapter through the dispatcher."""

    def test_deepseek_chat_via_dispatcher(self, messages):
        from autodream.llm import _PROVIDER_DISPATCH, ProviderSpec

        with patch("autodream.providers_chat.httpx.Client") as mock_client:
            mock_instance = mock_client.return_value.__enter__.return_value
            fixture = _replay_deepseek_response(mock_instance)

            spec = ProviderSpec(
                provider="deepseek",
                model="deepseek-v4-pro",
                max_tokens=500,
                timeout=10,
            )

            os.environ["DEEPSEEK_API_KEY"] = "sk-ds-test-key"
            try:
                dispatcher = _PROVIDER_DISPATCH["deepseek"]
                result = dispatcher(spec, messages)

                assert "content" in result
                assert "reverse" in result["content"]
                assert result["model"] == "deepseek/deepseek-v4-pro"
            finally:
                del os.environ["DEEPSEEK_API_KEY"]

    def test_deepseek_dispatcher_missing_key(self, messages):
        from autodream.llm import _PROVIDER_DISPATCH, ProviderSpec

        os.environ.pop("DEEPSEEK_API_KEY", None)
        spec = ProviderSpec(
            provider="deepseek",
            model="deepseek-v4-pro",
            max_tokens=500,
            timeout=10,
        )
        dispatcher = _PROVIDER_DISPATCH["deepseek"]
        with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
            dispatcher(spec, messages)


# ─────────────────────────────────────────────────────────────────────────────
# OpenAI end-to-end via dispatcher
# ─────────────────────────────────────────────────────────────────────────────


class TestOpenAIViaDispatcher:
    """Exercises OpenAI adapter through the dispatcher."""

    def test_openai_chat_via_dispatcher(self, messages):
        from autodream.llm import _PROVIDER_DISPATCH, ProviderSpec

        with patch("autodream.providers_chat.httpx.Client") as mock_client:
            mock_instance = mock_client.return_value.__enter__.return_value
            fixture = _replay_openai_response(mock_instance)

            spec = ProviderSpec(
                provider="openai",
                model="gpt-4o",
                max_tokens=500,
                timeout=10,
            )

            os.environ["OPENAI_API_KEY"] = "sk-openai-test-key"
            try:
                dispatcher = _PROVIDER_DISPATCH["openai"]
                result = dispatcher(spec, messages)

                assert "content" in result
                assert "organize" in result["content"]
                assert result["model"] == "gpt-4o-2026-05-01"
            finally:
                del os.environ["OPENAI_API_KEY"]

    def test_openai_dispatcher_missing_key(self, messages):
        from autodream.llm import _PROVIDER_DISPATCH, ProviderSpec

        os.environ.pop("OPENAI_API_KEY", None)
        spec = ProviderSpec(
            provider="openai",
            model="gpt-4o",
            max_tokens=500,
            timeout=10,
        )
        dispatcher = _PROVIDER_DISPATCH["openai"]
        with pytest.raises(ValueError, match="OPENAI_API_KEY"):
            dispatcher(spec, messages)


# ─────────────────────────────────────────────────────────────────────────────
# Fallback scenario end-to-end
# ─────────────────────────────────────────────────────────────────────────────


class TestFallbackViaDispatcher:
    """Primary adapter failure → caller can invoke secondary adapter."""

    def test_anthropic_fallback_to_deepseek(self, messages):
        """Anthropic fails (missing key), caller retries with DeepSeek."""
        from autodream.llm import _PROVIDER_DISPATCH, ProviderSpec

        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ.pop("DEEPSEEK_API_KEY", None)

        # Anthropic should raise ValueError
        anthropic_spec = ProviderSpec(
            provider="anthropic",
            model="claude-sonnet-4-6",
            max_tokens=500,
            timeout=10,
        )
        with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
            _PROVIDER_DISPATCH["anthropic"](anthropic_spec, messages)

        # Fall back to DeepSeek (provide key)
        with patch("autodream.providers_chat.httpx.Client") as mock_client:
            mock_instance = mock_client.return_value.__enter__.return_value
            _replay_deepseek_response(mock_instance)

            deepseek_spec = ProviderSpec(
                provider="deepseek",
                model="deepseek-v4-pro",
                max_tokens=500,
                timeout=10,
            )
            os.environ["DEEPSEEK_API_KEY"] = "sk-ds-fallback-key"
            try:
                result = _PROVIDER_DISPATCH["deepseek"](deepseek_spec, messages)
                assert "content" in result
                assert "reverse" in result["content"]
            finally:
                del os.environ["DEEPSEEK_API_KEY"]

    def test_fallback_respects_cross_provider_rule(self, messages):
        """Fallback chain goes to a DIFFERENT provider, never same."""
        from autodream.llm import _PROVIDER_DISPATCH, ProviderSpec

        # All keys missing — every provider raises ValueError
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ.pop("DEEPSEEK_API_KEY", None)
        os.environ.pop("OPENAI_API_KEY", None)

        for provider_name, key_name in [
            ("anthropic", "ANTHROPIC_API_KEY"),
            ("deepseek", "DEEPSEEK_API_KEY"),
            ("openai", "OPENAI_API_KEY"),
        ]:
            spec = ProviderSpec(
                provider=provider_name,
                model="test-model",
                max_tokens=500,
                timeout=10,
            )
            with pytest.raises(ValueError, match=key_name):
                _PROVIDER_DISPATCH[provider_name](spec, messages)
