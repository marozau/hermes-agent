# Provider Adapter Dispatcher

This directory implements the provider adapter architecture (Stories 3.6–3.8):
cross-provider LLM dispatch for Anthropic, DeepSeek, and OpenAI through a
shared registration interface.

## Architecture

```
hermes_providers.register_all()          ← Story 3.8
  ├── hermes_providers_anthropic.register()  ← Story 3.6
  │     → _PROVIDER_DISPATCH["anthropic"]
  │     → Anthropic Messages API with cache_control breakpoints
  │
  └── hermes_providers_chat.register()       ← Story 3.7
        → _PROVIDER_DISPATCH["deepseek"]
        → _PROVIDER_DISPATCH["openai"]
        → Shared /chat/completions transport
```

### Files

| File | Role |
|------|------|
| `lib/hermes_providers.py` | Central `register_all()` entry point. Idempotent. |
| `lib/hermes_providers_anthropic.py` | Anthropic adapter: cache_control breakpoints, Messages API format conversion |
| `lib/hermes_providers_chat.py` | Shared adapter for DeepSeek/OpenAI (and any /chat/completions provider) |

### Deployment

```bash
# From dev repo, copy lib modules to live runtime path:
./deploy.sh                    # copies to ~/.hermes/lib/
./deploy.sh --live-dir /custom/path  # alternative target

# Or manually:
cp lib/hermes_providers*.py ~/.hermes/lib/
```

## Usage

### Basic dispatch

```python
from hermes_providers import register_all
from hermes_llm import ProviderSpec

register_all()

spec = ProviderSpec(
    provider="anthropic",
    model="claude-sonnet-4-6",
    max_tokens=500,
    timeout=10,
)
result = _PROVIDER_DISPATCH["anthropic"](spec, messages)
# Returns: {"content": "...", "model": "...", "usage": {...}}
```

### With cache_control breakpoints (Anthropic only)

```python
spec = ProviderSpec(provider="anthropic", model="claude-sonnet-4-6", ...)
result = _PROVIDER_DISPATCH["anthropic"](
    spec,
    messages,
    cache_breakpoints=[0, 1],   # system[0], skills bundle[1]
    cache_mode="5m",             # "none" (default), "5m", "1h"
)
```

### Fallback pattern (cross-provider)

```python
# The hard invariant: fallback is always cross-provider (never same provider)
providers = ["anthropic", "deepseek", "openai"]
for provider_name in providers:
    try:
        spec = ProviderSpec(provider=provider_name, model=model, ...)
        return _PROVIDER_DISPATCH[provider_name](spec, messages)
    except (ValueError, TimeoutError):
        continue
raise RuntimeError("All providers exhausted")
```

## Testing with Fixtures

The `fixtures/` directory contains replayable API responses for deterministic
tests without live API calls:

```
fixtures/
├── __init__.py          # load_fixture(provider, name) helper
├── anthropic/
│   └── messages_api.json        # Anthropic Messages API response
├── deepseek/
│   └── chat_completion.json     # DeepSeek chat completion response
└── openai/
    └── chat_completion.json     # OpenAI chat completion response
```

### Using fixtures in tests

```python
from fixtures import load_fixture

def test_anthropic_chat():
    response = load_fixture("anthropic", "messages_api")
    with patch("hermes_providers_anthropic.httpx.Client") as mock_client:
        mock_instance = mock_client.return_value.__enter__.return_value
        mock_instance.post.return_value.status_code = 200
        mock_instance.post.return_value.json.return_value = response
        # ... call dispatcher ...

# Or use the replay helpers:
from tests.lib.test_dispatcher_e2e import _replay_anthropic_response
_replay_anthropic_response(mock_instance)
```

### Adding new fixtures

1. Create a JSON file in `fixtures/<provider>/<name>.json`
2. Use the actual API response format for that provider
3. Load with `load_fixture(provider, name)`

## DoD Status Interpretation

| Item | Status | Meaning |
|------|--------|---------|
| DoD item 4 (cross-provider fallback) | ✅ Tested (e2e) | Unit tests + e2e smoke tests verify fallback chain |
| DoD item 8 (operational run) | ⏳ Trial pending | Requires preflight `shadow` → `live` flip + 7-day trial |
| DoD item 9 (audit log) | ⏳ Trial pending | Dream audit depends on live runs |
| DoD item 10 (telemetry) | ⏳ Trial pending | Observability pipeline needs live data |

## Test Suite

```bash
# All provider adapter tests (25 unit + 13 e2e):
pytest tests/lib/ -v

# Memory tool tests (33 unit + 2 regression):
pytest tests/tools/test_memory_tool.py -v

# Everything:
pytest tests/lib/ tests/tools/test_memory_tool.py -v
```
