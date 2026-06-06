#!/usr/bin/env python3
"""smoke_test_skill.py — Run smoke tests for BMAD skills against structural metrics.

Usage (from repo root, requires TUI to be running):
  hermes bmad-smoke-test --skill bmad:research --topic "AI agents" --runs 3

Or manually:
  1. Start TUI: hermes -p bmad --tui
  2. Run skill: /bmad:research AI agents
  3. Save output to planning-artifacts/smoke-research-1.md
  4. Score: python plugins/bmad/tools/evolve_command/scripts/score_output.py \\
       research_structural_v1 planning-artifacts/smoke-research-1.md

Environment:
  SMOKE_TEST_OUTPUT_DIR — where to write output files (default: planning-artifacts/smoke-tests/)
  SMOKE_TEST_MODEL — model to use (default: deepseek-v4-pro)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def find_repo_root() -> Path:
    return Path(__file__).resolve().parents[5]


def run_skill_via_cli(skill_name: str, topic: str, model: str) -> str:
    """Run a BMAD skill via CLI and capture output.

    NOTE: This requires the TUI or gateway to be running. If not,
    falls back to reading the skill file directly for testing.
    """
    repo_root = find_repo_root()
    # Try to invoke via hermes CLI
    result = subprocess.run(
        ["hermes", "-p", "bmad", "--skill", skill_name, topic],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode == 0:
        return result.stdout

    # Fallback: return skill body for inspection
    # Skills are organized in subdirs (bmm/, core/, tea/, cis/, bmb/, _shared/)
    skill_name_clean = skill_name.replace("bmad:", "").replace("-", "_")
    skill_path = repo_root / "skills" / "bmad" / skill_name_clean / "SKILL.md"
    if skill_path.exists():
        return f"[FALLBACK: skill body from {skill_path}]\n\n" + skill_path.read_text()

    # Search all subdirectories
    skills_base = repo_root / "skills" / "bmad"
    for subdir in skills_base.iterdir():
        if subdir.is_dir():
            p = subdir / skill_name_clean / "SKILL.md"
            if p.exists():
                return f"[FALLBACK: skill body from {p}]\n\n" + p.read_text()

    return f"[ERROR: Could not find skill {skill_name}]"


def score_output(metric_name: str, text: str) -> dict:
    score_script = find_repo_root() / "plugins" / "bmad" / "tools" / "evolve_command" / "scripts" / "score_output.py"
    result = subprocess.run(
        [sys.executable, str(score_script), metric_name, "-"],
        input=text,
        capture_output=True,
        text=True,
        cwd=find_repo_root(),
    )
    if result.returncode != 0:
        return {"error": result.stderr, "composite_score": 0.0}
    return json.loads(result.stdout)


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test BMAD skills")
    parser.add_argument("--skill", required=True, help="Skill name (e.g. bmad:research)")
    parser.add_argument("--topic", default="test topic", help="Test topic/prompt")
    parser.add_argument("--runs", type=int, default=3, help="Number of generations")
    parser.add_argument("--metric", help="Metric name (default: auto-detect from skill)")
    parser.add_argument("--output-dir", help="Output directory for results")
    args = parser.parse_args()

    repo_root = find_repo_root()
    output_dir = Path(args.output_dir or os.environ.get("SMOKE_TEST_OUTPUT_DIR", "planning-artifacts/smoke-tests"))
    output_dir = repo_root / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    metric_name = args.metric or args.skill.replace("bmad:", "") + "_structural_v1"
    model = os.environ.get("SMOKE_TEST_MODEL", "deepseek-v4-pro")

    scores = []
    results = []

    print(f"Smoke testing {args.skill} with metric {metric_name}")
    print(f"Model: {model}, Runs: {args.runs}, Output: {output_dir}")
    print("─" * 50)

    for i in range(1, args.runs + 1):
        print(f"\nRun {i}/{args.runs}...")
        text = run_skill_via_cli(args.skill, args.topic, model)

        # Save output
        out_file = output_dir / f"{args.skill.replace(':', '-')}-{i}.md"
        out_file.write_text(text, encoding="utf-8")
        print(f"  Saved: {out_file}")

        # Score
        score_result = score_output(metric_name, text)
        score = score_result.get("composite_score", 0.0)
        scores.append(score)
        results.append({
            "run": i,
            "file": str(out_file),
            "score": score,
            "hard_gates_passed": score_result.get("hard_gates_passed", 0),
            "hard_gates_total": score_result.get("hard_gates_total", 0),
        })
        print(f"  Score: {score:.3f}")
        print(f"  Hard gates: {score_result.get('hard_gates_passed', 0)}/{score_result.get('hard_gates_total', 0)}")

    avg_score = sum(scores) / len(scores) if scores else 0.0
    min_score = min(scores) if scores else 0.0
    max_score = max(scores) if scores else 0.0

    # Summary
    summary = {
        "skill": args.skill,
        "metric": metric_name,
        "model": model,
        "runs": args.runs,
        "topic": args.topic,
        "timestamp": datetime.now().isoformat(),
        "scores": {
            "avg": round(avg_score, 3),
            "min": round(min_score, 3),
            "max": round(max_score, 3),
        },
        "g1_gate": "PASS" if avg_score >= 0.7 else ("MARGINAL" if avg_score >= 0.5 else "FAIL"),
        "results": results,
    }

    summary_file = output_dir / f"{args.skill.replace(':', '-')}-summary.json"
    summary_file.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"\n─" * 50)
    print(f"SUMMARY: {args.skill}")
    print(f"  Avg score: {avg_score:.3f}")
    print(f"  Min score: {min_score:.3f}")
    print(f"  Max score: {max_score:.3f}")
    print(f"  G1 Gate:   {summary['g1_gate']}")
    print(f"  Details:   {summary_file}")

    return 0 if avg_score >= 0.5 else 1


if __name__ == "__main__":
    sys.exit(main())
