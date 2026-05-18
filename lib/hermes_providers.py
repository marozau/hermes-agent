"""
hermes_providers — Central registration for all provider adapters.

Story 3.8: Importing this module registers all provider dispatchers.
Call hermes_providers.register_all() at dream-orchestrator startup.

Usage:
    from hermes_providers import register_all
    register_all()  # registers anthropic, deepseek, openai dispatchers

Smoke tests verify that all dispatchers are reachable and return the
correct NotImplementedError context when called without API keys.
"""

import logging

logger = logging.getLogger(__name__)


def register_all() -> None:
    """Register every available provider adapter.

    Idempotent — re-registering replaces the previous dispatch entry
    with the same function (no side effects).
    """
    import hermes_providers_anthropic
    import hermes_providers_chat

    hermes_providers_anthropic.register()
    hermes_providers_chat.register()

    logger.info("all provider adapters registered: anthropic, deepseek, openai")
