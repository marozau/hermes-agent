"""Tests for hermes_providers_chat — DeepSeek/OpenAI shared adapter."""

import json
import os
from unittest.mock import patch

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def deepseek_spec():
    from hermes_llm import ProviderSpec

    return ProviderSpec(
        provider="deepseek",
        model="deepseek-v4-flash",
        max_tokens=500,
        timeout=10,
    )


@pytest.fixture
def openai_spec():
    from hermes_llm import ProviderSpec

    return ProviderSpec(
        provider="openai",
        model="gpt-4o",
        max_tokens=500,
        timeout=10,
    )


@pytest.fixture
def custom_base_spec():
    from hermes_llm import ProviderSpec

    return ProviderSpec(
        provider="deepseek",
        model="deepseek-v4-flash",
        max_tokens=500,
        timeout=10,
        base_url="https://custom-endpoint.example.com/v1",
    )


_MOCK_CHAT_RESPONSE = {
    "id": "chatcmpl-abc123",
    "object": "chat.completion",
    "created": 1700000000,
    "model": "deepseek-v4-flash",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "Hello from DeepSeek!"},
            "finish_reason": "stop",
        }
    ],
    "usage": {
        "prompt_tokens": 25,
        "completion_tokens": 10,
        "total_tokens": 35,
        "prompt_tokens_details": {"cached_tokens": 0},
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Import smoke tests
# ─────────────────────────────────────────────────────────────────────────────


class TestImport:
    """Verify module imports and registers correctly."""

    def test_import(self):
        import hermes_providers_chat

        assert hermes_providers_chat.register is not None
        assert hermes_providers_chat.chat_completions is not None

    def test_register_wires_both(self):
        import hermes_llm
        from hermes_providers_chat import register

        # Clean slate
        hermes_llm._PROVIDER_DISPATCH.pop("deepseek", None)
        hermes_llm._PROVIDER_DISPATCH.pop("openai", None)

        register()

        assert "deepseek" in hermes_llm._PROVIDER_DISPATCH
        assert "openai" in hermes_llm._PROVIDER_DISPATCH


# ─────────────────────────────────────────────────────────────────────────────
# Request building tests
# ─────────────────────────────────────────────────────────────────────────────


class TestRequestBody:
    """Verify the correct request body is sent to /chat/completions."""

    def test_basic_request(self, deepseek_spec, sample_messages):
        """Standard request → correct model, messages, headers."""
        from hermes_providers_chat import chat_completions

        with patch("hermes_providers_chat.httpx.Client") as mock_client:
            mock_instance = mock_client.return_value.__enter__.return_value
            mock_instance.post.return_value.status_code = 200
            mock_instance.post.return_value.json.return_value = _MOCK_CHAT_RESPONSE

            os.environ["DEEPSEEK_API_KEY"] = "sk-ds-test-key"
            try:
                chat_completions(deepseek_spec, sample_messages)

                call_args = mock_instance.post.call_args
                url = str(call_args[0][0])
                headers = call_args[1]["headers"]
                body = json.loads(call_args[1]["content"])

                assert url == "https://api.deepseek.com/v1/chat/completions"
                assert headers["Authorization"] == "Bearer sk-ds-test-key"
                assert body["model"] == "deepseek-v4-flash"
                assert body["max_tokens"] == 500
                assert len(body["messages"]) == 2
            finally:
                del os.environ["DEEPSEEK_API_KEY"]

    def test_custom_base_url(self, custom_base_spec, sample_messages):
        """Custom base_url → request goes to the custom endpoint."""
        from hermes_providers_chat import chat_completions

        with patch("hermes_providers_chat.httpx.Client") as mock_client:
            mock_instance = mock_client.return_value.__enter__.return_value
            mock_instance.post.return_value.status_code = 200
            mock_instance.post.return_value.json.return_value = _MOCK_CHAT_RESPONSE

            os.environ["DEEPSEEK_API_KEY"] = "sk-ds-test-key"
            try:
                chat_completions(custom_base_spec, sample_messages)

                url = str(mock_instance.post.call_args[0][0])
                assert url.startswith("https://custom-endpoint.example.com/v1")
            finally:
                del os.environ["DEEPSEEK_API_KEY"]

    def test_openai_request(self, openai_spec, sample_messages):
        """OpenAI provider → uses correct key env var and default base URL."""
        from hermes_providers_chat import chat_completions

        with patch("hermes_providers_chat.httpx.Client") as mock_client:
            mock_instance = mock_client.return_value.__enter__.return_value
            mock_instance.post.return_value.status_code = 200
            mock_instance.post.return_value.json.return_value = {
                **_MOCK_CHAT_RESPONSE,
                "model": "gpt-4o",
            }

            os.environ["OPENAI_API_KEY"] = "sk-oa-test-key"
            try:
                chat_completions(openai_spec, sample_messages)

                url = str(mock_instance.post.call_args[0][0])
                headers = mock_instance.post.call_args[1]["headers"]

                assert url == "https://api.openai.com/v1/chat/completions"
                assert headers["Authorization"] == "Bearer sk-oa-test-key"
            finally:
                del os.environ["OPENAI_API_KEY"]


# ─────────────────────────────────────────────────────────────────────────────
# Error handling tests
# ─────────────────────────────────────────────────────────────────────────────


class TestErrors:
    """Verify error handling for missing keys, HTTP errors, timeouts."""

    def test_missing_api_key(self, deepseek_spec, sample_messages):
        """No API key env var → ValueError with clear message."""
        from hermes_providers_chat import chat_completions

        os.environ.pop("DEEPSEEK_API_KEY", None)

        with pytest.raises(ValueError, match="DEEPSEEK_API_KEY not set"):
            chat_completions(deepseek_spec, sample_messages)

    def test_http_401(self, deepseek_spec, sample_messages):
        """HTTP 401 → ValueError with status code."""
        from hermes_providers_chat import chat_completions

        with patch("hermes_providers_chat.httpx.Client") as mock_client:
            mock_instance = mock_client.return_value.__enter__.return_value
            mock_instance.post.return_value.status_code = 401
            mock_instance.post.return_value.json.return_value = {
                "error": {"message": "Invalid API key"}
            }

            os.environ["DEEPSEEK_API_KEY"] = "sk-ds-test-key"
            try:
                with pytest.raises(ValueError, match="401"):
                    chat_completions(deepseek_spec, sample_messages)
            finally:
                del os.environ["DEEPSEEK_API_KEY"]

    def test_timeout(self, deepseek_spec, sample_messages):
        """httpx timeout → ValueError mentioning timeout."""
        from hermes_providers_chat import chat_completions

        with patch("hermes_providers_chat.httpx.Client") as mock_client:
            mock_instance = mock_client.return_value.__enter__.return_value
            mock_instance.post.side_effect = __import__("httpx").TimeoutException(
                "timed out"
            )

            os.environ["DEEPSEEK_API_KEY"] = "sk-ds-test-key"
            try:
                with pytest.raises(ValueError, match="timed out"):
                    chat_completions(deepseek_spec, sample_messages)
            finally:
                del os.environ["DEEPSEEK_API_KEY"]


# ─────────────────────────────────────────────────────────────────────────────
# Response parsing tests
# ─────────────────────────────────────────────────────────────────────────────


class TestResponseParsing:
    """Verify response parsing returns correct dict shape."""

    def test_successful_response(self, deepseek_spec, sample_messages):
        """Standard success → returns content, model, usage."""
        from hermes_providers_chat import chat_completions

        with patch("hermes_providers_chat.httpx.Client") as mock_client:
            mock_instance = mock_client.return_value.__enter__.return_value
            mock_instance.post.return_value.status_code = 200
            mock_instance.post.return_value.json.return_value = _MOCK_CHAT_RESPONSE

            os.environ["DEEPSEEK_API_KEY"] = "sk-ds-test-key"
            try:
                result = chat_completions(deepseek_spec, sample_messages)
                assert result["content"] == "Hello from DeepSeek!"
                assert result["model"] == "deepseek-v4-flash"
                assert result["role"] == "assistant"
                assert result["finish_reason"] == "stop"
                assert result["usage"]["input_tokens"] == 25
                assert result["usage"]["output_tokens"] == 10
            finally:
                del os.environ["DEEPSEEK_API_KEY"]

    def test_empty_choices(self, deepseek_spec, sample_messages):
        """Response with no choices → ValueError."""
        from hermes_providers_chat import chat_completions

        with patch("hermes_providers_chat.httpx.Client") as mock_client:
            mock_instance = mock_client.return_value.__enter__.return_value
            mock_instance.post.return_value.status_code = 200
            mock_instance.post.return_value.json.return_value = {
                "id": "chatcmpl-xxx",
                "object": "chat.completion",
                "created": 1700000000,
                "model": "deepseek-v4-flash",
                "choices": [],
                "usage": {},
            }

            os.environ["DEEPSEEK_API_KEY"] = "sk-ds-test-key"
            try:
                with pytest.raises(ValueError, match="no choices"):
                    chat_completions(deepseek_spec, sample_messages)
            finally:
                del os.environ["DEEPSEEK_API_KEY"]
