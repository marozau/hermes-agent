"""SkillOpt adapter — STUB for Epic 14.

Epic 15 wires the actual SkillOpt Phase-1 optimization pass.
This stub exists to prove the pip-dep wiring works (FI-3)
and to leave the seam for Epic 15 to fill.

See: planning-artifacts/research/technical-skillopt-bmad-integration-2026-06-04.md §4
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional


def run_skillopt_phase1(
    seed_skill_path: Path,
    target_model: str = "openai/gpt-4.1-mini",
    optimizer_model: str = "openai/gpt-4.1-mini",
    benchmark_path: Optional[Path] = None,
) -> None:
    """Run SkillOpt Phase-1 optimization on a BMAD skill.

    STUB: raises NotImplementedError. Epic 15 wires the real pass.

    Args:
        seed_skill_path: Path to the seed SKILL.md to optimize.
        target_model: Model to evaluate the skill against.
        optimizer_model: Model to generate skill variants.
        benchmark_path: Path to benchmark dataset (JSONL).
    """
    raise NotImplementedError(
        "Epic 15 wires the SkillOpt Phase-1 pass; "
        "see planning-artifacts/research/technical-skillopt-bmad-integration-2026-06-04.md §4"
    )
