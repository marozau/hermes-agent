"""Phase overrides — explicit markers for intentionally-skipped phases (Story 9.7).

Allows BMAD projects to declare "we deliberately skipped phase X" in
bmad/config.yaml.  Doctor honors these markers and never flags them as drift.

Schema in config.yaml:

    phase_overrides:
      analysis: skipped        # project started at planning
      solutioning: not_needed  # level-0 project, no architecture doc needed

Valid values: skipped, not_needed, deferred.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

VALID_PHASES = {"analysis", "planning", "solutioning", "implementation"}
VALID_STATES = {"skipped", "not_needed", "deferred"}


def load_phase_overrides(project_dir: Path) -> dict[str, str]:
    """Load phase_overrides from bmad/config.yaml.

    Returns dict of phase_name -> override_state.
    Empty dict if no overrides or not a BMAD project.
    """
    config_path = project_dir / "bmad" / "config.yaml"
    if not config_path.exists():
        return {}

    try:
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except (yaml.YAMLError, OSError) as e:
        logger.warning("[doctor] failed to read config.yaml: %s", e)
        return {}

    if not isinstance(config, dict):
        return {}

    raw = config.get("phase_overrides", {})
    if not isinstance(raw, dict):
        return {}

    result = {}
    for phase, state in raw.items():
        phase_str = str(phase).lower()
        state_str = str(state).lower()
        if phase_str not in VALID_PHASES:
            logger.warning("[doctor] invalid phase in phase_overrides: %s", phase)
            continue
        if state_str not in VALID_STATES:
            logger.warning("[doctor] invalid state in phase_overrides: %s=%s", phase, state)
            continue
        result[phase_str] = state_str

    return result


def is_phase_overridden(overrides: dict[str, str], phase: str) -> bool:
    """Check if a phase is explicitly overridden (skipped/not_needed/deferred)."""
    return phase.lower() in overrides
