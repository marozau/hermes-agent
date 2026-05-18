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

# Dev-tree on sys.path first so the `lib` namespace package resolves to
# ~/usr-local/hermes/lib/ (source-of-truth), not ~/.hermes/lib/ (deployed
# runtime copy). Both lib/ dirs may co-exist; sys.path order decides which
# `lib.hermes_X` resolves to. The dev-tree root must come BEFORE any
# ~/.hermes/* entry so `from lib.hermes_X import ...` hits the dev tree.
_DEV_ROOT = Path(__file__).resolve().parents[2]                    # ~/usr-local/hermes/
_DEV_LIB = _DEV_ROOT / "lib"                                       # ~/usr-local/hermes/lib/
for _p in (str(_DEV_LIB), str(_DEV_ROOT)):                         # insert in reverse-priority order
    if _p not in sys.path:                                          # so _DEV_ROOT ends up first.
        sys.path.insert(0, _p)

# Runtime lib/ as a fallback for top-level imports (e.g. `import hermes_X`),
# kept AFTER the dev-tree entries so dev-tree wins on namespace-package
# resolution.
_RUNTIME_LIB_CANDIDATES = [
    Path.home() / ".hermes" / "lib",                                # normal case
    Path.home().parent.parent / ".hermes" / "lib",                  # profile case: ~/.hermes/profiles/*/home/
    Path("/Users/im/.hermes/lib"),                                  # fallback
]
for _candidate in _RUNTIME_LIB_CANDIDATES:
    if _candidate.is_dir():
        lib_str = str(_candidate)
        if lib_str not in sys.path:
            sys.path.append(lib_str)                                # append, not insert: dev tree wins
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


# Pin `lib.hermes_*` to the dev-tree namespace-package portion BEFORE test
# modules attempt their own `from lib.X import ...`. Pytest's collection
# loads conftests first, but if any prior import (editable-finder hook,
# pytest plugin, etc.) put lib.X into sys.modules pointing at ~/.hermes/lib/
# the test module's import returns the cached (wrong) module.
#
# Forcing a fresh import here — after the sys.path manipulations above —
# guarantees `lib.hermes_*` resolves to ~/usr-local/hermes/lib/.
import importlib as _importlib
for _mod in (
    "lib", "lib.hermes_preflight", "lib.hermes_memory", "lib.hermes_llm",
    "lib.hermes_dream", "lib.hermes_recall", "lib.hermes_trust",
):
    sys.modules.pop(_mod, None)
_importlib.import_module("lib.hermes_preflight")
del _importlib
