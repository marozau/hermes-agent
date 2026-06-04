"""Run the offline optimizer (Epic 13 stub).

Trains a reward model on the built dataset and optimizes
orchestrator hyperparameters (retry strategy, wave ordering, etc.).
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def main() -> None:
    """Run optimizer on the training dataset."""
    logger.info("[run_optimizer] Stub — not yet implemented (Epic 13)")
    print("⚠️  run_optimizer.py is a stub — implement in a future story.")


if __name__ == "__main__":
    main()
