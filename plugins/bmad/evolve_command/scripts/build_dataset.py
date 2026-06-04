"""Build training dataset for the offline tuner (Epic 13 stub).

Scans past orchestration runs and extracts (context, outcome) pairs
for reward model training.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def main() -> None:
    """Build dataset from historical orchestration data."""
    logger.info("[build_dataset] Stub — not yet implemented (Epic 13)")
    print("⚠️  build_dataset.py is a stub — implement in a future story.")


if __name__ == "__main__":
    main()
