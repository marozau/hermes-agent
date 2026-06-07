"""
autodream.providers — Central registration for all provider adapters.

Story 3.8: Importing this module registers all provider dispatchers.
Call autodream.providers.register_all() at dream-orchestrator startup.

Usage:
    from autodream.providers import register_all
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
    import autodream.providers_anthropic
    import autodream.providers_chat

    autodream.providers_anthropic.register()
    autodream.providers_chat.register()

    logger.info("all provider adapters registered: anthropic, deepseek, openai")
