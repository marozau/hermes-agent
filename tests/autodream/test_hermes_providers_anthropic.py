"""Tests for autodream.providers_anthropic — Anthropic adapter with cache_control."""

import json
import os
from unittest.mock import patch

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def anthropic_spec():
    from autodream.llm import ProviderSpec

    return ProviderSpec(
        provider="anthropic",
        model="claude-sonnet-4-6",
        max_tokens=500,
        timeout=10,
    )


@pytest.fixture
def three_block_messages():
    """Three-block messages matching ADR-7 layout: system, skills, user."""
    return [
        {"role": "system", "content": "You are a helpful Hermes agent."},
        {"role": "user", "content": "## Skills bundle (byte-stable)\n\n- git\n- docker"},
        {"role": "user", "content": "Can you help me with git?"},
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Import smoke test
# ─────────────────────────────────────────────────────────────────────────────


class TestImport:
    """Verify the module imports cleanly and registers correctly."""

    def test_import(self):
        import autodream.providers_anthropic

        assert autodream.providers_anthropic.register is not None
        assert autodream.providers_anthropic.anthropic_chat is not None

    def test_register_wires_dispatch(self):
        import autodream.llm
        from autodream.providers_anthropic import register

        # Ensure clean slate
        if "anthropic" in autodream.llm._PROVIDER_DISPATCH:
            del autodream.llm._PROVIDER_DISPATCH["anthropic"]

        register()
        assert "anthropic" in autodream.llm._PROVIDER_DISPATCH
        assert autodream.llm._PROVIDER_DISPATCH["anthropic"] is not None


# ─────────────────────────────────────────────────────────────────────────────
# Cache breakpoint tests
# ─────────────────────────────────────────────────────────────────────────────


class TestCacheBreakpoints:
    """Verify cache_control placement at specified message indices."""

    def test_cache_breakpoints_applied(self, anthropic_spec, three_block_messages):
        """cache_control: {type: "ephemeral"} on indices 0 and 1."""
        from autodream.providers_anthropic import anthropic_chat

        with patch("autodream.providers_anthropic.httpx.Client") as mock_client:
            mock_instance = mock_client.return_value.__enter__.return_value
            mock_instance.post.return_value.status_code = 200
            mock_instance.post.return_value.json.return_value = {
                "id": "msg_01",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": "Sure, I can help!"}],
                "model": "claude-sonnet-4-6",
                "stop_reason": "end_turn",
                "usage": {
                    "input_tokens": 42,
                    "output_tokens": 8,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 42,
                },
            }

            # Monkey-patch key for the adapter
            os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test-key"

            try:
                anthropic_chat(
                    anthropic_spec,
                    three_block_messages,
                    cache_breakpoints=[0, 1],
                    cache_mode="5m",
                )

                # Verify the request body
                call_kwargs = mock_instance.post.call_args[1]
                body = json.loads(call_kwargs["content"])

                # System should have been extracted to top-level
                assert "system" in body
                assert body["system"] == "You are a helpful Hermes agent."

                # Messages should have 2 entries (system was extracted)
                messages = body["messages"]
                assert len(messages) == 2

                # First message (index 0 of messages= skills bundle) should have cache_control
                msg0_blocks = messages[0]["content"]
                assert isinstance(msg0_blocks, list)
                assert msg0_blocks[0]["type"] == "text"
                assert msg0_blocks[0]["cache_control"] == {"type": "ephemeral"}

                # Second message (index 1) also has cache_control per [0, 1]
                msg1_blocks = messages[1]["content"]
                assert isinstance(msg1_blocks, list)
                assert msg1_blocks[0]["cache_control"] == {"type": "ephemeral"}
            finally:
                del os.environ["ANTHROPIC_API_KEY"]

    def test_no_breakpoints_unchanged(self, anthropic_spec, three_block_messages):
        """No cache_breakpoints → content stays as plain string."""
        from autodream.providers_anthropic import anthropic_chat

        with patch("autodream.providers_anthropic.httpx.Client") as mock_client:
            mock_instance = mock_client.return_value.__enter__.return_value
            mock_instance.post.return_value.status_code = 200
            mock_instance.post.return_value.json.return_value = {
                "id": "msg_01",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": "OK"}],
                "model": "claude-sonnet-4-6",
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 10, "output_tokens": 2},
            }

            os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test-key"
            try:
                anthropic_chat(
                    anthropic_spec,
                    three_block_messages,
                    cache_breakpoints=[],
                )

                call_kwargs = mock_instance.post.call_args[1]
                body = json.loads(call_kwargs["content"])
                messages = body["messages"]

                for msg in messages:
                    assert isinstance(msg["content"], str), (
                        f"Expected string content, got {type(msg['content'])}"
                    )
            finally:
                del os.environ["ANTHROPIC_API_KEY"]


# ─────────────────────────────────────────────────────────────────────────────
# Error handling tests
# ─────────────────────────────────────────────────────────────────────────────


class TestErrors:
    """Verify error handling for missing keys, timeouts, HTTP errors."""

    def test_missing_api_key(self, anthropic_spec, sample_messages):
        """No ANTHROPIC_API_KEY → ValueError with clear message."""
        from autodream.providers_anthropic import anthropic_chat

        # Ensure env var is absent
        os.environ.pop("ANTHROPIC_API_KEY", None)

        with pytest.raises(ValueError, match="ANTHROPIC_API_KEY not set"):
            anthropic_chat(anthropic_spec, sample_messages)

    def test_http_error(self, anthropic_spec, sample_messages):
        """HTTP 503 → ValueError with status code in message."""
        from autodream.providers_anthropic import anthropic_chat

        with patch("autodream.providers_anthropic.httpx.Client") as mock_client:
            mock_instance = mock_client.return_value.__enter__.return_value
            mock_instance.post.return_value.status_code = 503
            mock_instance.post.return_value.json.return_value = {
                "error": {"type": "overloaded_error", "message": "Overloaded"}
            }

            os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test-key"
            try:
                with pytest.raises(ValueError, match="503"):
                    anthropic_chat(anthropic_spec, sample_messages)
            finally:
                del os.environ["ANTHROPIC_API_KEY"]

    def test_timeout(self, anthropic_spec, sample_messages):
        """httpx timeout → ValueError mentioning timeout."""
        from autodream.providers_anthropic import anthropic_chat

        with patch("autodream.providers_anthropic.httpx.Client") as mock_client:
            mock_instance = mock_client.return_value.__enter__.return_value
            mock_instance.post.side_effect = __import__("httpx").TimeoutException(
                "timed out"
            )

            os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test-key"
            try:
                with pytest.raises(ValueError, match="timed out"):
                    anthropic_chat(anthropic_spec, sample_messages)
            finally:
                del os.environ["ANTHROPIC_API_KEY"]


# ─────────────────────────────────────────────────────────────────────────────
# Response parsing tests
# ─────────────────────────────────────────────────────────────────────────────


class TestResponseParsing:
    """Verify response parsing returns correct dict shape."""

    def test_successful_response(self, anthropic_spec, sample_messages):
        """Standard success → returns content, model, usage."""
        from autodream.providers_anthropic import anthropic_chat

        with patch("autodream.providers_anthropic.httpx.Client") as mock_client:
            mock_instance = mock_client.return_value.__enter__.return_value
            mock_instance.post.return_value.status_code = 200
            mock_instance.post.return_value.json.return_value = {
                "id": "msg_abc",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": "Hello! How can I help?"}],
                "model": "claude-sonnet-4-6",
                "stop_reason": "end_turn",
                "usage": {
                    "input_tokens": 15,
                    "output_tokens": 5,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
            }

            os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test-key"
            try:
                result = anthropic_chat(anthropic_spec, sample_messages)
                assert result["content"] == "Hello! How can I help?"
                assert result["model"] == "claude-sonnet-4-6"
                assert result["role"] == "assistant"
                assert result["stop_reason"] == "end_turn"
                assert result["usage"]["input_tokens"] == 15
                assert result["usage"]["output_tokens"] == 5
            finally:
                del os.environ["ANTHROPIC_API_KEY"]
