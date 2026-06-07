"""Phase 1 SkillOpt smoke run — Story 15.10 (D-38 scaffolding).

Creates a minimal SkillOpt config (from configs/dev_story/smoke.yaml),
runs ``ReflACTTrainer(cfg).train()`` on a small command body loaded from
``tests/fixtures/mini_command.md``, and verifies the output produces
``best_skill.md`` OR a "no improvement found" outcome.

This is an integration smoke test — not a unit test.  The goal is to
prove that the Phase 1 SkillOpt pipeline (config → adapter → trainer)
composes correctly end-to-end.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import yaml

# Ensure the package is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

# ── Paths ──────────────────────────────────────────────────────────────

_CONFIGS_DIR = Path(__file__).resolve().parent.parent / "configs" / "dev_story"
_FIXTURES_DIR = Path(__file__).parent / "fixtures"
_MINI_COMMAND_MD = _FIXTURES_DIR / "mini_command.md"


# ── Helpers ────────────────────────────────────────────────────────────


def _load_smoke_config() -> dict[str, Any]:
    """Load the minimal smoke YAML config."""
    path = _CONFIGS_DIR / "smoke.yaml"
    with path.open() as fh:
        return yaml.safe_load(fh)


def _build_reflact_config(
    smoke_cfg: dict[str, Any],
    out_root: str,
    skill_init_path: str,
) -> dict[str, Any]:
    """Map BMAD smoke config keys → ReflACTTrainer config dict.

    The smoke.yaml uses simplified keys (model, max_steps, etc.) while
    the ReflACT trainer expects its own naming convention.  This helper
    bridges the two.
    """
    return {
        # ── Output / skill ─────────────────────────────────────────────
        "out_root": out_root,
        "skill_init": skill_init_path,
        # ── Model config ───────────────────────────────────────────────
        "model_backend": "openai_chat",
        "optimizer_backend": "openai_chat",
        "target_backend": "openai_chat",
        "optimizer_model": smoke_cfg["model"],
        "target_model": smoke_cfg["model"],
        # ── Training parameters ────────────────────────────────────────
        "num_epochs": 1,
        "batch_size": smoke_cfg["batch_size"],
        "accumulation": 1,
        "train_size": 1,
        "seed": 42,
        "edit_budget": 2,
        "merge_batch_size": 1,
        "lr_scheduler": "constant",
        "min_edit_budget": 2,
        # ── Evaluation ─────────────────────────────────────────────────
        "sel_env_num": 1,
        "test_env_num": 1,
        "gate_metric": "hard",
        # ── Misc ───────────────────────────────────────────────────────
        "use_gate": True,
        "skill_update_mode": "patch",
        "max_analyst_rounds": 1,
        "analyst_workers": 1,
        "eval_test": False,
    }


# ── Mock adapter ──────────────────────────────────────────────────────

_MOCK_ITEMS = [
    {
        "id": f"task-{i}",
        "story_spec": f"## Story {i}\nImplement feature {i}.",
        "project_context": "name: smoke-project\nlang: python",
        "task_type": "dev-story",
    }
    for i in range(2)
]


def _make_mock_rollout_results(items: list[dict]) -> list[dict]:
    """Build deterministic rollout results for mock items."""
    return [
        {
            "id": item["id"],
            "hard": 1,
            "soft": 0.85,
            "predicted_answer": "+def foo(): pass",
            "task_type": item.get("task_type", "dev-story"),
        }
        for item in items
    ]


class _MockAdapter:
    """Minimal adapter that satisfies the ReflACT EnvAdapter interface.

    Returns deterministic results — no real model calls are made.
    Reflect returns empty patches so the training loop exercises the
    full pipeline without mutating the skill document.
    """

    def __init__(self) -> None:
        self._cfg: dict[str, Any] = {}

    # ── Lifecycle ──────────────────────────────────────────────────────

    def setup(self, cfg: dict[str, Any]) -> None:
        self._cfg = dict(cfg)

    def get_dataloader(self) -> None:
        return None

    def requires_ray(self) -> bool:
        return False

    # ── Environment construction ───────────────────────────────────────

    def build_train_env(self, batch_size: int, seed: int, **kw: Any) -> list[dict]:
        return _MOCK_ITEMS[:batch_size]

    def build_eval_env(
        self, env_num: int, split: str, seed: int, **kw: Any,
    ) -> list[dict]:
        return _MOCK_ITEMS[:env_num]

    # ── Rollout (Stage 1) ─────────────────────────────────────────────

    def rollout(
        self,
        env_manager: list[dict],
        skill_content: str,
        out_dir: str,
        **kw: Any,
    ) -> list[dict]:
        return _make_mock_rollout_results(env_manager)

    # ── Reflect (Stage 2) ─────────────────────────────────────────────

    def reflect(
        self,
        results: list[dict],
        skill_content: str,
        out_dir: str,
        **kw: Any,
    ) -> list[dict | None]:
        # No patches → no edits → skill unchanged
        return []

    # ── Task types ─────────────────────────────────────────────────────

    def get_task_types(self) -> list[str]:
        return ["dev-story"]

    # ── Prompt hooks (optional) ────────────────────────────────────────

    def get_error_minibatch_prompt(self) -> str | None:
        return None

    def get_success_minibatch_prompt(self) -> str | None:
        return None


# ── Mocks for skillopt.model functions ────────────────────────────────

# These are called by ReflACTTrainer.train() to configure the LLM
# backends.  We patch them to avoid real API key requirements.

_MODEL_PATCH_TARGETS = [
    "skillopt.model.configure_azure_openai",
    "skillopt.model.set_optimizer_backend",
    "skillopt.model.set_target_backend",
    "skillopt.model.set_optimizer_deployment",
    "skillopt.model.set_target_deployment",
    "skillopt.model.configure_codex_exec",
    "skillopt.model.configure_claude_code_exec",
    "skillopt.model.configure_qwen_chat",
    "skillopt.model.configure_minimax_chat",
    "skillopt.model.set_reasoning_effort",
    "skillopt.model.reset_token_tracker",
]


# ── Smoke test ─────────────────────────────────────────────────────────


@pytest.mark.smoke
@pytest.mark.slow
class TestPhase1SkillOptSmokeRun:
    """D-38 smoke: run ReflACTTrainer and verify best_skill.md output."""

    def test_smoke_run_produces_best_skill_or_no_improvement(self) -> None:
        """ReflACTTrainer should complete the training loop and produce
        either a ``best_skill.md`` file or a summary indicating no
        improvement was found.

        The smoke run uses 1 epoch, batch_size=1, and a mock adapter
        that returns deterministic results so the test completes in <30s.
        """
        smoke_cfg = _load_smoke_config()
        out_dir = tempfile.mkdtemp(prefix="skillopt_smoke_")
        try:
            skill_init_path = str(_MINI_COMMAND_MD)
            cfg = _build_reflact_config(smoke_cfg, out_dir, skill_init_path)

            adapter = _MockAdapter()

            # Patch all model configuration functions to avoid API keys
            patches = [patch(target, MagicMock()) for target in _MODEL_PATCH_TARGETS]
            for p in patches:
                p.start()

            try:
                # Lazy-import inside the test to ensure patching is active
                from skillopt.engine.trainer import ReflACTTrainer

                trainer = ReflACTTrainer(cfg, adapter)
                summary = trainer.train()

                # ── Verify summary structure ────────────────────────────
                assert isinstance(summary, dict), (
                    "ReflACTTrainer.train() should return a dict summary"
                )

                # ── Verify best_skill.md was written ───────────────────
                best_skill_path = Path(out_dir) / "best_skill.md"
                if best_skill_path.exists():
                    best_skill_content = best_skill_path.read_text()
                    assert len(best_skill_content) > 0, (
                        "best_skill.md should not be empty"
                    )
                else:
                    # Acceptable alternative: no improvement found
                    # The summary should indicate this
                    assert summary.get("total_accepts", -1) == 0 or \
                        summary.get("best_selection_hard") is not None, (
                        "Expected best_skill.md or summary indicating "
                        "no improvement found"
                    )

                # ── Verify summary has expected keys ────────────────────
                assert "version" in summary, "summary should have 'version'"
                assert "total_wall_time_s" in summary, (
                    "summary should have 'total_wall_time_s'"
                )
                assert summary["total_wall_time_s"] >= 0.0

            finally:
                for p in patches:
                    p.stop()

        finally:
            shutil.rmtree(out_dir, ignore_errors=True)
