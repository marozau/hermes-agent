"""End-to-end smoke tests for autodream.providers registration (Story 3.8)."""

import pytest


class TestRegistration:
    """Verify register_all wires all three dispatchers."""

    def test_register_all_wires_everything(self):
        """register_all() → anthropic, deepseek, openai dispatchers."""
        import autodream.llm
        from autodream.providers import register_all

        # Clean slate
        autodream.llm._PROVIDER_DISPATCH.clear()

        register_all()

        assert "anthropic" in autodream.llm._PROVIDER_DISPATCH
        assert "deepseek" in autodream.llm._PROVIDER_DISPATCH
        assert "openai" in autodream.llm._PROVIDER_DISPATCH

        # Verify each entry is callable
        for name in ("anthropic", "deepseek", "openai"):
            assert callable(autodream.llm._PROVIDER_DISPATCH[name]), (
                f"dispatch '{name}' is not callable"
            )

    def test_register_all_idempotent(self):
        """Calling register_all() twice → no error, same result."""
        import autodream.llm
        from autodream.providers import register_all

        autodream.llm._PROVIDER_DISPATCH.clear()
        register_all()
        snapshot = dict(autodream.llm._PROVIDER_DISPATCH)

        register_all()

        assert autodream.llm._PROVIDER_DISPATCH == snapshot


class TestDispatchEndpoint:
    """End-to-end smoke: _call_provider_api routes to the right adapter by name.

    These tests verify the full chain:
    register_all() → dispatch → adapter function (with mocked HTTP).
    """

    def test_dispatch_to_anthropic(self, sample_messages):
        """_call_provider_api for 'anthropic' → uses anthropic adapter."""
        import os
        from unittest.mock import patch

        import autodream.llm
        from autodream.providers import register_all

        autodream.llm._PROVIDER_DISPATCH.clear()
        register_all()

        provider = autodream.llm.ProviderSpec(
            provider="anthropic",
            model="claude-sonnet-4-6",
            max_tokens=100,
            timeout=5,
        )

        with patch("autodream.providers_anthropic.httpx.Client") as mock_client:
            mock_instance = mock_client.return_value.__enter__.return_value
            mock_instance.post.return_value.status_code = 200
            mock_instance.post.return_value.json.return_value = {
                "id": "msg_01",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": "Mocked response"}],
                "model": "claude-sonnet-4-6",
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 10, "output_tokens": 3},
            }

            os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test-key"
            try:
                result = autodream.llm._call_provider_api(provider, sample_messages)
                assert result["content"] == "Mocked response"
                assert result["model"] == "claude-sonnet-4-6"
            finally:
                del os.environ["ANTHROPIC_API_KEY"]

    def test_dispatch_to_deepseek(self, sample_messages):
        """_call_provider_api for 'deepseek' → uses chat adapter."""
        import os
        from unittest.mock import patch

        import autodream.llm
        from autodream.providers import register_all

        autodream.llm._PROVIDER_DISPATCH.clear()
        register_all()

        provider = autodream.llm.ProviderSpec(
            provider="deepseek",
            model="deepseek-v4-flash",
            max_tokens=100,
            timeout=5,
        )

        with patch("autodream.providers_chat.httpx.Client") as mock_client:
            mock_instance = mock_client.return_value.__enter__.return_value
            mock_instance.post.return_value.status_code = 200
            mock_instance.post.return_value.json.return_value = {
                "id": "chatcmpl-xyz",
                "object": "chat.completion",
                "created": 1700000000,
                "model": "deepseek-v4-flash",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "Mocked deepseek",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }

            os.environ["DEEPSEEK_API_KEY"] = "sk-ds-test-key"
            try:
                result = autodream.llm._call_provider_api(provider, sample_messages)
                assert result["content"] == "Mocked deepseek"
            finally:
                del os.environ["DEEPSEEK_API_KEY"]

    def test_dispatch_to_openai(self, sample_messages):
        """_call_provider_api for 'openai' → uses chat adapter."""
        import os
        from unittest.mock import patch

        import autodream.llm
        from autodream.providers import register_all

        autodream.llm._PROVIDER_DISPATCH.clear()
        register_all()

        provider = autodream.llm.ProviderSpec(
            provider="openai",
            model="gpt-4o",
            max_tokens=100,
            timeout=5,
        )

        with patch("autodream.providers_chat.httpx.Client") as mock_client:
            mock_instance = mock_client.return_value.__enter__.return_value
            mock_instance.post.return_value.status_code = 200
            mock_instance.post.return_value.json.return_value = {
                "id": "chatcmpl-abc",
                "object": "chat.completion",
                "created": 1700000000,
                "model": "gpt-4o",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "Mocked openai",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }

            os.environ["OPENAI_API_KEY"] = "sk-oa-test-key"
            try:
                result = autodream.llm._call_provider_api(provider, sample_messages)
                assert result["content"] == "Mocked openai"
            finally:
                del os.environ["OPENAI_API_KEY"]

    def test_dispatch_unregistered(self, sample_messages):
        """_call_provider_api for unregistered provider → NotImplementedError."""
        import autodream.llm
        from autodream.providers import register_all

        autodream.llm._PROVIDER_DISPATCH.clear()
        register_all()

        provider = autodream.llm.ProviderSpec(
            provider="nonexistent",
            model="fake-model",
            max_tokens=100,
            timeout=5,
        )

        with pytest.raises(NotImplementedError, match="no dispatcher.*nonexistent"):
            autodream.llm._call_provider_api(provider, sample_messages)


# ─────────────────────────────────────────────────────────────────────────────
# Integration lite: llm_call skips unregistered providers cleanly
# ─────────────────────────────────────────────────────────────────────────────


class TestLLMCallIntegration:
    """Verify llm_call handles the dispatch chain end-to-end.

    These test that llm_call's fallback logic works with adapters.
    """

    def test_llm_call_fatal_on_no_dispatcher(self):
        """NotImplementedError (no dispatcher) is fatal — no fallback attempted."""
        import autodream.llm
        from autodream.providers import register_all

        autodream.llm._PROVIDER_DISPATCH.clear()
        register_all()

        primary = autodream.llm.ProviderSpec(
            provider="nonexistent",
            model="fake",
            max_tokens=100,
            timeout=5,
        )

        workload = autodream.llm.WorkloadSpec(
            primary=primary,
            fallback=[],
            cache="none",
        )
        config = {"test_workload": workload}
        spec = autodream.llm.LLMSpec(
            workload="test_workload",
            messages=[{"role": "user", "content": "hi"}],
        )

        with pytest.raises(NotImplementedError):
            autodream.llm.llm_call(spec, providers_config=config)
