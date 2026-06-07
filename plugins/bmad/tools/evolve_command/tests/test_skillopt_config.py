"""Tests for SkillOpt config YAML loading."""

from __future__ import annotations

import pathlib
from typing import Any

import yaml
import pytest

CONFIGS_DIR = (
    pathlib.Path(__file__).resolve().parent.parent / "configs" / "dev_story"
)

REQUIRED_KEYS = {"model", "max_steps", "batch_size", "learning_rate", "reward_threshold"}


def _load_config(name: str) -> dict[str, Any]:
    path = CONFIGS_DIR / name
    with path.open() as fh:
        return yaml.safe_load(fh)


class TestSkillOptConfig:
    """Verify SkillOpt YAML configs are well-formed."""

    def test_default_config_has_all_keys(self) -> None:
        cfg = _load_config("default.yaml")
        assert REQUIRED_KEYS.issubset(cfg.keys())

    def test_smoke_config_has_all_keys(self) -> None:
        cfg = _load_config("smoke.yaml")
        assert REQUIRED_KEYS.issubset(cfg.keys())

    def test_smoke_values_are_smaller_or_equal(self) -> None:
        """Smoke config should use smaller/faster values than default."""
        default = _load_config("default.yaml")
        smoke = _load_config("smoke.yaml")
        assert smoke["max_steps"] <= default["max_steps"]
        assert smoke["batch_size"] <= default["batch_size"]

    def test_config_types(self) -> None:
        for name in ("default.yaml", "smoke.yaml"):
            cfg = _load_config(name)
            assert isinstance(cfg["model"], str)
            assert isinstance(cfg["max_steps"], int)
            assert isinstance(cfg["batch_size"], int)
            assert isinstance(cfg["learning_rate"], float)
            assert isinstance(cfg["reward_threshold"], float)
