"""
Fixture loader for provider adapter tests.

Usage:
    from fixtures import load_fixture

    response = load_fixture("anthropic", "messages_api")
    # Returns the JSON-parsed dict from fixtures/anthropic/messages_api.json

For httpx-based tests:
    mock_instance.post.return_value.json.return_value = load_fixture("anthropic", "messages_api")
    mock_instance.post.return_value.status_code = 200
"""

import json
from pathlib import Path

_FIXTURE_DIR = Path(__file__).parent


def load_fixture(provider: str, name: str) -> dict:
    """Load a fixture JSON file from fixtures/<provider>/<name>.json.

    Args:
        provider: Provider directory name (e.g. 'anthropic', 'deepseek', 'openai').
        name: Fixture name without .json extension.

    Returns:
        Parsed JSON dict.
    """
    path = _FIXTURE_DIR / provider / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"Fixture not found: {path}. "
            f"Available: {list((_FIXTURE_DIR / provider).iterdir())}"
        )
    return json.loads(path.read_text())


def list_fixtures(provider: str) -> list[str]:
    """List available fixture names for a provider."""
    return [p.stem for p in (_FIXTURE_DIR / provider).glob("*.json")]
