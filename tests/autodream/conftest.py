"""Pytest conftest for tests/autodream/ -- substrate helper + provider adapter tests.

Exposes HERMES_ROOT for tests that need to locate runtime state (e.g.,
dreams/providers.yaml lives at ~/.hermes/dreams/providers.yaml, not in the dev tree).
"""

import os
import sys
from pathlib import Path

import pytest

# Dev-tree on sys.path so `import autodream` resolves to the dev-tree
# source-of-truth before any installed version.
_DEV_ROOT = Path(__file__).resolve().parents[2]                    # ~/usr-local/hermes/
if str(_DEV_ROOT) not in sys.path:
    sys.path.insert(0, str(_DEV_ROOT))

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

    Returns a dict that matches the ProviderSpec shape from autodream.llm.
    """
    from autodream.llm import ProviderSpec

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


# Pin `autodream.*` to the dev-tree package BEFORE test modules attempt their own
# imports. Pytest's collection loads conftests first, but if any prior import
# (editable-finder hook, pytest plugin, etc.) put autodream into sys.modules
# pointing at the installed version, the test module's import returns the cached
# (wrong) module.
import importlib as _importlib
for _mod in list(sys.modules.keys()):
    if _mod.startswith("autodream."):
        del sys.modules[_mod]
_importlib.import_module("autodream.preflight")
del _importlib
