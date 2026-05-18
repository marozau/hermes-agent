"""Pytest conftest for tests/lib/ -- provider adapter tests.

Ensures ~/.hermes/lib/ is importable and credentials are isolated.
"""

import os
import sys
from pathlib import Path

import pytest

# Add ~/.hermes/lib/ to sys.path so tests can import lib modules.
# NOTE: HOME may point to a profile directory (~/.hermes/profiles/*/home/)
# in this environment. Resolve the REAL .hermes/lib by detecting the
# profile-relative case.
_LIB_CANDIDATES = [
    Path.home() / ".hermes" / "lib",       # normal case
    Path.home().parent.parent / ".hermes" / "lib",  # profile case: ~/.hermes/profiles/*/home/
    Path("/Users/im/.hermes/lib"),          # fallback
]
for _candidate in _LIB_CANDIDATES:
    if _candidate.is_dir():
        lib_str = str(_candidate)
        if lib_str not in sys.path:
            sys.path.insert(0, lib_str)
        break

# Provider API keys that MUST NOT be set during tests
_CHAT_API_KEY_VARS = ["OPENAI_API_KEY", "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY"]


@pytest.fixture(autouse=True)
def _isolate_credentials() -> None:
    """Unset all provider API keys so tests never hit real endpoints."""
    saved = {}
    for var in _CHAT_API_KEY_VARS:
        saved[var] = os.environ.pop(var, None)
    yield
    for var, val in saved.items():
        if val is not None:
            os.environ[var] = val


@pytest.fixture
def mock_provider_spec():
    """Create a minimal ProviderSpec-like dict for adapter tests.

    Returns a dict that matches the ProviderSpec shape from hermes_llm.
    """
    from hermes_llm import ProviderSpec

    return ProviderSpec(
        provider="test",
        model="test-model",
        max_tokens=1000,
        timeout=5,
    )


@pytest.fixture
def sample_messages():
    """Standard messages fixture used across adapter tests."""
    return [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello, world!"},
    ]
