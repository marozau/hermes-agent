"""Pytest conftest for tests/lib/ -- substrate helper + provider adapter tests.

Adds dev-tree lib/ to sys.path so `import hermes_memory`, `import hermes_providers`
etc. resolve to the dev-tree source-of-truth (FR-3, NFR-23). Exposes HERMES_ROOT
for tests that need to locate runtime state (e.g., dreams/providers.yaml lives
at ~/.hermes/dreams/providers.yaml, not in the dev tree).
"""

import os
import sys
from pathlib import Path

import pytest

# Dev-tree lib/ (source-of-truth) on sys.path first; ~/.hermes/lib/ (deployed
# runtime copy) only as a fallback for legacy callers.
_DEV_LIB = Path(__file__).resolve().parents[2] / "lib"  # ~/usr-local/hermes/lib/
_RUNTIME_LIB_CANDIDATES = [
    Path.home() / ".hermes" / "lib",                       # normal case
    Path.home().parent.parent / ".hermes" / "lib",          # profile case: ~/.hermes/profiles/*/home/
    Path("/Users/im/.hermes/lib"),                          # fallback
]
for _candidate in [_DEV_LIB, *_RUNTIME_LIB_CANDIDATES]:
    if _candidate.is_dir():
        lib_str = str(_candidate)
        if lib_str not in sys.path:
            sys.path.insert(0, lib_str)
        break

# HERMES_ROOT is the runtime workspace root — where dreams/, raw/, observability/,
# memory/, memories/, preflight/ live. Tests use this to locate runtime config
# (notably ~/.hermes/dreams/providers.yaml). Independent of the dev tree.
HERMES_ROOT = Path.home() / ".hermes"

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
