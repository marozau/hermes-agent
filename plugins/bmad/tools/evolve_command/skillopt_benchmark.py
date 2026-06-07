"""BMADDevStoryEnv — SkillOpt benchmark for /bmad:dev-story command body tuning.

Plugs the BMAD dev-story workflow into SkillOpt's ReflACT pipeline (Story 15.8).

Design per vault ``BMAD AC to SkillOpt Benchmark Conversion.md``:

- **Reward function**: composite metric from ``dev_story_composite_v1.yaml``
  (frozen v1: 0.4·test_pass_rate + 0.2·scope_discipline + 0.2·spec_faithfulness
  + 0.1·regression_safety + 0.1·brevity). Hard gates fire first (fail → 0.0).
- **Observation**: the command body text (skill_content in SkillOpt terms).
  The target model receives the body as system instructions and a task
  (story spec + project context) as user prompt.
- **Action**: SkillOpt structured edits (append / insert_after / replace / delete)
  on the command body text. Each edit mutates the instructional layer.

The adapter implements :class:`skillopt.envs.base.EnvAdapter` so it can be
registered in SkillOpt's ``_ENV_REGISTRY`` and driven by
:class:`skillopt.engine.trainer.ReflACTTrainer`.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Lazy SkillOpt imports (FI-3: isolated to tooling subtree) ────────────

def _lazy_skillopt_imports():
    """Import SkillOpt types at call time to avoid hard dependency at module load."""
    from skillopt.datasets.base import BatchSpec, SplitDataLoader
    from skillopt.envs.base import EnvAdapter
    from skillopt.gradient.reflect import run_minibatch_reflect
    from skillopt.model import chat_target
    return BatchSpec, SplitDataLoader, EnvAdapter, run_minibatch_reflect, chat_target


# ── Reward function (composite metric) ──────────────────────────────────

# Frozen v1 weights — matches metrics/dev_story_composite_v1.yaml
_METRIC_WEIGHTS: dict[str, float] = {
    "test_pass_rate": 0.4,
    "scope_discipline": 0.2,
    "spec_faithfulness": 0.2,
    "regression_safety": 0.1,
    "brevity": 0.1,
}

# Hard gate thresholds (from YAML)
_TEST_PASS_THRESHOLD: float = 0.7
_REGRESSION_SAFETY_THRESHOLD: float = 1.0

_DEPLOY_VERBS_RE = re.compile(
    r"kubectl apply|kubectl create|docker push|docker build"
    r"|helm install|helm upgrade|terraform apply|pulumi up"
    r"|gcloud deploy|gcloud run|az containerapp"
    r"|npm publish|cargo publish|gh pr merge|gh release create"
    r"|flyctl deploy|deploy --production",
    re.IGNORECASE,
)

_CREDENTIAL_PATHS_RE = re.compile(
    r"~/.aws/credentials|~/.ssh/id_|~/.gnupg|~/.config/gh|~/.npmrc"
    r"|~/.docker/config.json|~/.kube/config|\.env\.production"
    r"|secret[_-]?key|api[_-]?key\s*=|password\s*=|token\s*=",
    re.IGNORECASE,
)


def _parse_test_pass_rate(test_results: str) -> float:
    """Parse test pass rate from pytest-style output."""
    if not test_results.strip():
        return 0.0
    pass_match = re.search(r"(\d+)\s+passed", test_results)
    fail_match = re.search(r"(\d+)\s+failed", test_results)
    if pass_match:
        passed = int(pass_match.group(1))
        failed = int(fail_match.group(1)) if fail_match else 0
        total = passed + failed
        if total > 0:
            return passed / total
    lines = test_results.strip().split("\n")
    pass_count = sum(1 for l in lines if "PASS" in l.upper() and "FAIL" not in l.upper())
    fail_count = sum(1 for l in lines if "FAIL" in l.upper())
    total = pass_count + fail_count
    if total > 0:
        return pass_count / total
    return 0.5


def _estimate_regression_safety(diff: str) -> float:
    """Estimate regression safety from diff heuristics."""
    if not diff.strip():
        return 1.0
    deleted_lines = [
        line for line in diff.split("\n")
        if line.startswith("-") and not line.startswith("---")
    ]
    for line in deleted_lines:
        lower = line.lower()
        if any(kw in lower for kw in ["assert", "expect", "test_", "unittest"]):
            return 0.0
    return 1.0


def _check_hard_gates(
    diff: str,
    test_pass_rate: float,
    regression_safety: float,
) -> tuple[bool, tuple[str, ...]]:
    """Run all 4 hard gates. Returns (passed, failure_reasons)."""
    failures: list[str] = []
    if test_pass_rate < _TEST_PASS_THRESHOLD:
        failures.append(f"test_pass_rate={test_pass_rate:.2f} < {_TEST_PASS_THRESHOLD}")
    if regression_safety != _REGRESSION_SAFETY_THRESHOLD:
        failures.append(f"regression_safety={regression_safety:.2f} != {_REGRESSION_SAFETY_THRESHOLD}")
    if _DEPLOY_VERBS_RE.search(diff):
        match = _DEPLOY_VERBS_RE.search(diff)
        verb = match.group() if match else "unknown"
        failures.append(f"deploy verb found: {verb}")
    if _CREDENTIAL_PATHS_RE.search(diff):
        match = _CREDENTIAL_PATHS_RE.search(diff)
        path = match.group() if match else "unknown"
        failures.append(f"credential path found: {path}")
    return (len(failures) == 0, tuple(failures))


def compute_reward(candidate: dict[str, Any]) -> float:
    """Compute the frozen v1 composite reward for a dev-story candidate.

    Args:
        candidate: Dict with keys:
            - diff (str): Unified diff patch.
            - test_results (str): Raw test execution output.
            - scope_discipline (float, optional): 0.0-1.0 override.
            - spec_faithfulness (float, optional): 0.0-1.0 override.
            - brevity (float, optional): 0.0-1.0 override.

    Returns:
        Composite score 0.0-1.0. Returns 0.0 when hard gates fail.
    """
    diff: str = candidate.get("diff", "")
    test_results: str = candidate.get("test_results", "")

    test_pass_rate = _parse_test_pass_rate(test_results)
    regression_safety = _estimate_regression_safety(diff)

    passed, _failures = _check_hard_gates(diff, test_pass_rate, regression_safety)
    if not passed:
        return 0.0

    scope_discipline = float(candidate.get("scope_discipline", 0.5))
    spec_faithfulness = float(candidate.get("spec_faithfulness", 0.5))
    brevity = float(candidate.get("brevity", 0.5))

    return (
        _METRIC_WEIGHTS["test_pass_rate"] * test_pass_rate
        + _METRIC_WEIGHTS["scope_discipline"] * scope_discipline
        + _METRIC_WEIGHTS["spec_faithfulness"] * spec_faithfulness
        + _METRIC_WEIGHTS["regression_safety"] * regression_safety
        + _METRIC_WEIGHTS["brevity"] * brevity
    )


# ── Rollout helper ──────────────────────────────────────────────────────

def _score_rollout(
    prediction: str,
    item: dict[str, Any],
) -> tuple[int, float]:
    """Score a rollout prediction using the composite metric.

    Maps the composite to SkillOpt's (hard, soft) scoring convention:
    - hard: 1 if composite >= 0.7 (AC threshold), else 0
    - soft: the composite itself (0.0-1.0)

    Returns:
        (hard, soft) tuple.
    """
    candidate = {
        "diff": prediction,
        "test_results": item.get("test_results", ""),
        "scope_discipline": item.get("scope_discipline"),
        "spec_faithfulness": item.get("spec_faithfulness"),
        "brevity": item.get("brevity"),
    }
    composite = compute_reward(candidate)
    hard = 1 if composite >= _TEST_PASS_THRESHOLD else 0
    return hard, composite


def rollout_one(
    item: dict[str, Any],
    skill_content: str,
    *,
    max_completion_tokens: int = 4096,
) -> dict[str, Any]:
    """Run one dev-story task under the current skill (command body).

    The skill_content is the command body text (observation). The target
    model receives it as system instructions, with the story spec and
    project context as the user prompt (action = body mutation by SkillOpt).

    Args:
        item: Task dict with ``id``, ``story_spec``, ``project_context``,
              and optional scoring overrides.
        skill_content: Current command body text (the observation).
        max_completion_tokens: Max tokens for the target model response.

    Returns:
        Result dict with ``id``, ``hard``, ``soft``, ``predicted_answer``,
        and env-specific extras.
    """
    _, _, _, _, chat_target = _lazy_skillopt_imports()

    item_id = str(item.get("id", "unknown"))
    story_spec = item.get("story_spec", "")
    project_context = item.get("project_context", "")

    system = skill_content
    user = (
        f"## Story Specification\n{story_spec}\n\n"
        f"## Project Context\n{project_context}\n\n"
        "Implement the story. Output a unified diff patch."
    )

    try:
        prediction, _usage = chat_target(
            system=system,
            user=user,
            max_completion_tokens=max_completion_tokens,
        )
    except Exception as exc:
        logger.warning("rollout_one failed for %s: %s", item_id, exc)
        return {
            "id": item_id,
            "hard": 0,
            "soft": 0.0,
            "predicted_answer": "",
            "fail_reason": f"error: {exc}",
            "task_type": "dev-story",
        }

    hard, soft = _score_rollout(prediction, item)

    return {
        "id": item_id,
        "hard": hard,
        "soft": soft,
        "predicted_answer": prediction,
        "task_description": story_spec[:200],
        "task_type": item.get("task_type", "dev-story"),
        "fail_reason": "" if hard else f"composite={soft:.2f} < {_TEST_PASS_THRESHOLD}",
    }


def run_batch(
    items: list[dict[str, Any]],
    skill_content: str,
    out_root: str,
    *,
    workers: int = 4,
    max_completion_tokens: int = 4096,
) -> list[dict[str, Any]]:
    """Run a batch of dev-story tasks under the current skill.

    Args:
        items: List of task dicts.
        skill_content: Current command body text.
        out_root: Directory to persist rollout results.
        workers: Thread pool size (1 = sequential).
        max_completion_tokens: Max tokens per target model call.

    Returns:
        List of result dicts (one per item).
    """
    os.makedirs(out_root, exist_ok=True)
    results = []
    for item in items:
        result = rollout_one(
            item,
            skill_content,
            max_completion_tokens=max_completion_tokens,
        )
        results.append(result)

    # Persist results for resume support
    results_path = os.path.join(out_root, "rollouts.json")
    Path(results_path).write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return results


# ── Data loader ─────────────────────────────────────────────────────────

class BMADDevStoryDataLoader:
    """Data loader for BMAD dev-story SkillOpt benchmark items.

    Loads task items from JSON files in split directories. Each item
    represents a dev-story task with ``story_spec``, ``project_context``,
    and optional scoring overrides.
    """

    def __init__(
        self,
        split_dir: str = "",
        data_path: str = "",
        split_mode: str = "split_dir",
        split_ratio: str = "2:1:7",
        split_seed: int = 42,
        split_output_dir: str = "",
        seed: int = 42,
        limit: int = 0,
    ) -> None:
        self.split_dir = split_dir
        self.data_path = data_path
        self.split_mode = split_mode
        self.split_ratio = split_ratio
        self.split_seed = split_seed
        self.split_output_dir = split_output_dir
        self.seed = seed
        self.limit = limit
        self._splits: dict[str, list[dict]] = {}
        self._inner: Any = None  # SplitDataLoader instance, created in setup()

    def setup(self, cfg: dict[str, Any]) -> None:
        """Initialize the underlying SplitDataLoader."""
        _, SplitDataLoader, _, _, _ = _lazy_skillopt_imports()

        # Resolve config values
        if not self.split_dir:
            self.split_dir = cfg.get("split_dir", "")
        if not self.data_path:
            self.data_path = cfg.get("data_path", "")
        if not self.split_mode:
            self.split_mode = cfg.get("split_mode", "split_dir")

        # Create concrete loader
        self._inner = SplitDataLoader(
            split_dir=self.split_dir,
            data_path=self.data_path,
            split_mode=self.split_mode,
            split_ratio=self.split_ratio,
            split_seed=self.split_seed,
            split_output_dir=self.split_output_dir,
            seed=self.seed,
            limit=self.limit,
        )
        self._inner.setup(cfg)
        self._splits = self._inner._splits

    @property
    def train_items(self) -> list[dict]:
        return self._splits.get("train", [])

    @property
    def val_items(self) -> list[dict]:
        return self._splits.get("val", [])

    @property
    def test_items(self) -> list[dict]:
        return self._splits.get("test", [])

    def build_train_batch(self, batch_size: int, seed: int, **kwargs) -> Any:
        """Build a training batch spec."""
        if self._inner is None:
            raise RuntimeError("setup() must be called before build_train_batch()")
        return self._inner.build_train_batch(batch_size=batch_size, seed=seed, **kwargs)

    def build_eval_batch(self, env_num: int, split: str, seed: int, **kwargs) -> Any:
        """Build an evaluation batch spec."""
        if self._inner is None:
            raise RuntimeError("setup() must be called before build_eval_batch()")
        return self._inner.build_eval_batch(env_num=env_num, split=split, seed=seed, **kwargs)


# ── Environment adapter ─────────────────────────────────────────────────

class BMADDevStoryEnv:
    """SkillOpt environment adapter for /bmad:dev-story command body tuning.

    Implements the :class:`skillopt.envs.base.EnvAdapter` interface:

    - **Reward**: composite metric from ``dev_story_composite_v1.yaml``
    - **Observation**: command body text (``skill_content``)
    - **Action**: SkillOpt structured edits on the command body

    Usage::

        adapter = BMADDevStoryEnv(split_dir="datasets/dev-story/v1/")
        adapter.setup(cfg)
        results = adapter.rollout(env_manager, skill_content, out_dir)
    """

    def __init__(
        self,
        split_dir: str = "",
        data_path: str = "",
        split_mode: str = "split_dir",
        split_ratio: str = "2:1:7",
        split_seed: int = 42,
        split_output_dir: str = "",
        workers: int = 4,
        analyst_workers: int = 4,
        failure_only: bool = False,
        minibatch_size: int = 8,
        edit_budget: int = 4,
        seed: int = 42,
        limit: int = 0,
        max_completion_tokens: int = 4096,
    ) -> None:
        self.workers = workers
        self.analyst_workers = analyst_workers
        self.failure_only = failure_only
        self.minibatch_size = minibatch_size
        self.edit_budget = edit_budget
        self.max_completion_tokens = int(max_completion_tokens)
        self._cfg: dict[str, Any] = {}
        self.dataloader = BMADDevStoryDataLoader(
            split_dir=split_dir,
            data_path=data_path,
            split_mode=split_mode,
            split_ratio=split_ratio,
            split_seed=split_seed,
            split_output_dir=split_output_dir,
            seed=seed,
            limit=limit,
        )

    # ── Lifecycle hooks ──────────────────────────────────────────────────

    def setup(self, cfg: dict[str, Any]) -> None:
        """Called once by the trainer before the training loop begins."""
        self._cfg = dict(cfg)
        self.dataloader.setup(cfg)

    def get_dataloader(self) -> BMADDevStoryDataLoader:
        """Return the task dataloader."""
        return self.dataloader

    # ── Env construction ─────────────────────────────────────────────────

    def build_env_from_batch(self, batch: Any, **kwargs: Any) -> list[dict]:
        """Build an environment manager (item list) from a batch spec."""
        return list(batch.payload or [])

    def build_train_env(self, batch_size: int, seed: int, **kwargs: Any) -> list[dict]:
        """Build a training environment (list of task items)."""
        batch = self.dataloader.build_train_batch(batch_size=batch_size, seed=seed, **kwargs)
        return self.build_env_from_batch(batch, **kwargs)

    def build_eval_env(self, env_num: int, split: str, seed: int, **kwargs: Any) -> list[dict]:
        """Build an evaluation environment (list of task items)."""
        batch = self.dataloader.build_eval_batch(env_num=env_num, split=split, seed=seed, **kwargs)
        return self.build_env_from_batch(batch, **kwargs)

    # ── Rollout (Stage 1) ────────────────────────────────────────────────

    def rollout(
        self,
        env_manager: list[dict],
        skill_content: str,
        out_dir: str,
        **kwargs: Any,
    ) -> list[dict]:
        """Stage 1: Execute the skill on a batch of dev-story tasks.

        Args:
            env_manager: List of task item dicts.
            skill_content: Current command body text (the observation).
            out_dir: Output directory for rollout artifacts.

        Returns:
            List of result dicts with ``id``, ``hard``, ``soft``.
        """
        items: list[dict] = env_manager
        return run_batch(
            items=items,
            skill_content=skill_content,
            out_root=out_dir,
            workers=self.workers,
            max_completion_tokens=self.max_completion_tokens,
        )

    # ── Reflect (Stage 2) ────────────────────────────────────────────────

    def reflect(
        self,
        results: list[dict],
        skill_content: str,
        out_dir: str,
        **kwargs: Any,
    ) -> list[dict | None]:
        """Stage 2: Analyze rollout results and produce patches.

        Delegates to SkillOpt's ``run_minibatch_reflect``.
        """
        _, _, _, run_minibatch_reflect, _ = _lazy_skillopt_imports()

        prediction_dir = kwargs.get("prediction_dir", os.path.join(out_dir, "predictions"))
        patches_dir = kwargs.get("patches_dir", os.path.join(out_dir, "patches"))

        return run_minibatch_reflect(
            results=results,
            skill_content=skill_content,
            prediction_dir=prediction_dir,
            patches_dir=patches_dir,
            workers=self.analyst_workers,
            failure_only=self.failure_only,
            minibatch_size=self.minibatch_size,
            edit_budget=self.edit_budget,
            random_seed=kwargs.get("random_seed"),
            error_system=self.get_error_minibatch_prompt(),
            success_system=self.get_success_minibatch_prompt(),
            step_buffer_context=kwargs.get("step_buffer_context", ""),
            update_mode=self._cfg.get("skill_update_mode", "patch"),
        )

    def get_error_minibatch_prompt(self) -> str | None:
        """Load error analyst prompt (env-specific or generic fallback)."""
        return None  # Use SkillOpt's built-in default

    def get_success_minibatch_prompt(self) -> str | None:
        """Load success analyst prompt (env-specific or generic fallback)."""
        return None  # Use SkillOpt's built-in default

    # ── Task types ───────────────────────────────────────────────────────

    def get_task_types(self) -> list[str]:
        """Return the list of task type names for this environment."""
        seen: list[str] = []
        for item in (
            self.dataloader.train_items
            + self.dataloader.val_items
            + self.dataloader.test_items
        ):
            tt = str(item.get("task_type") or "dev-story")
            if tt not in seen:
                seen.append(tt)
        return seen or ["dev-story"]
