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
import http.client
import json as _json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path


def find_repo_root() -> Path:
    return Path(__file__).resolve().parents[5]


def _find_skill_md(skill_name: str, repo_root: Path) -> Path | None:
    clean = skill_name.replace("bmad:", "").replace("-", "_")
    skills_base = repo_root / "skills" / "bmad"
    for path in [
        skills_base / clean / "SKILL.md",
        *(subdir / clean / "SKILL.md" for subdir in skills_base.iterdir() if subdir.is_dir()),
        *(subdir / clean.replace("_", "-") / "SKILL.md" for subdir in skills_base.iterdir() if subdir.is_dir()),
    ]:
        if path.exists():
            return path
    return None


# LiteLLM proxy — long timeout for reasoning models with thinking mode (per-model
# generation can take 30-120s; chunked transfer means urllib's socket-read timeout
# must exceed total response time, not just per-chunk). Reasoning-model failures
# manifest CLIENT-side as http.client.IncompleteRead when timeout is too low.
_PROXY_TIMEOUT_SEC = 1800  # 30 min — accommodates v4-pro with max reasoning budget

# Transient errors worth retrying. Excludes 4xx (auth, bad request) which won't
# recover, and excludes IncompleteRead (that's a timeout config issue, not transient).
_RETRYABLE_HTTP_CODES = (429, 502, 503, 504)
_RETRY_BACKOFF_SEC = (10, 30, 60, 120)


def _load_proxy_creds() -> tuple[str, str]:
    """Read LiteLLM proxy creds (base_url, api_key) from the bmad profile config.

    Raises RuntimeError if config missing/incomplete — proxy is mandatory for Mode A.
    """
    cfg_path = Path.home() / ".hermes" / "profiles" / "bmad" / "config.yaml"
    if not cfg_path.exists():
        raise RuntimeError(f"LiteLLM proxy config not found at {cfg_path}")
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError as e:
        raise RuntimeError("pyyaml required to read proxy config") from e
    cfg = yaml.safe_load(cfg_path.read_text())
    model_cfg = cfg.get("model", {})
    base = (model_cfg.get("base_url") or "").rstrip("/")
    key = model_cfg.get("api_key") or ""
    if not (base and key):
        raise RuntimeError(f"Incomplete proxy config in {cfg_path}: base_url/api_key missing")
    return base, key


def _extract_content(response: dict) -> str | None:
    """Pull the assistant message content from an OpenAI chat-completions response."""
    choices = response.get("choices") or []
    if not choices:
        return None
    msg = choices[0].get("message") or {}
    content = msg.get("content") or ""
    return content if content.strip() else None


def _is_proxy_error_body(response: dict) -> bool:
    """LiteLLM passes upstream-provider errors through as 200 with error in body."""
    return isinstance(response, dict) and "error" in response and "choices" not in response


def _call_proxy(url: str, headers: dict, payload: dict, timeout: int) -> tuple[dict | None, str | None]:
    """One proxy call. Returns (response, error_str). Exactly one is non-None."""
    data = _json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return _json.loads(resp.read().decode("utf-8")), None
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:400]
        return None, f"HTTP {e.code}: {body}"
    except http.client.IncompleteRead as e:
        return None, f"IncompleteRead (client-side timeout — increase _PROXY_TIMEOUT_SEC): {e}"
    except (urllib.error.URLError, ConnectionResetError, OSError) as e:
        return None, f"{type(e).__name__}: {e}"
    except _json.JSONDecodeError as e:
        return None, f"JSONDecodeError: {e}"


def run_skill_via_litellm(skill_name: str, topic: str, model: str) -> str:
    """Mode A — send SKILL.md to LiteLLM proxy (OpenAI-compat /v1/chat/completions).

    Proxy is at the URL/key configured in ~/.hermes/profiles/bmad/config.yaml.
    Reasoning models (v4-pro with thinking) need a long client timeout — see
    _PROXY_TIMEOUT_SEC. Retries on 429/5xx and proxy-200-with-error-body.
    """
    repo_root = find_repo_root()
    skill_path = _find_skill_md(skill_name, repo_root)
    if skill_path is None:
        raise FileNotFoundError(f"SKILL.md for {skill_name} not found under {repo_root / 'skills' / 'bmad'}")

    body = skill_path.read_text(encoding="utf-8")
    parts = re.split(r"^---\s*$", body, maxsplit=2, flags=re.MULTILINE)
    instructions = parts[-1].strip()

    user_prompt = (
        f"You are executing the following BMAD skill for the topic: {topic!r}.\n\n"
        f"=== SKILL INSTRUCTIONS START ===\n{instructions}\n=== SKILL INSTRUCTIONS END ===\n\n"
        f"Produce the requested output document for the topic now. "
        f"Output ONLY the document body — no preamble, no meta-commentary."
    )

    base_url, api_key = _load_proxy_creds()
    url = f"{base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": user_prompt}],
        "max_tokens": int(os.environ.get("SMOKE_TEST_MAX_TOKENS", "32000")),
    }
    timeout = int(os.environ.get("SMOKE_TEST_TIMEOUT_SEC", str(_PROXY_TIMEOUT_SEC)))

    last_err: str | None = None
    for attempt, wait_s in enumerate((0,) + _RETRY_BACKOFF_SEC):
        if wait_s:
            time.sleep(wait_s)
        response, err = _call_proxy(url, headers, payload, timeout)
        if response is None:
            last_err = err
            # Only retry on 429/5xx HTTP errors; everything else is terminal.
            retryable = any(f"HTTP {code}" in (err or "") for code in _RETRYABLE_HTTP_CODES)
            if retryable and attempt < len(_RETRY_BACKOFF_SEC):
                print(f"  [retry {attempt+1}/{len(_RETRY_BACKOFF_SEC)} after {_RETRY_BACKOFF_SEC[attempt]}s — {err}]", file=sys.stderr)
                continue
            return f"[LITELLM ERROR {err}]"
        if _is_proxy_error_body(response):
            last_err = f"proxy-error-200: {str(response.get('error', {}))[:300]}"
            if attempt < len(_RETRY_BACKOFF_SEC):
                print(f"  [retry {attempt+1}/{len(_RETRY_BACKOFF_SEC)} after {_RETRY_BACKOFF_SEC[attempt]}s — {last_err}]", file=sys.stderr)
                continue
            return f"[LITELLM ERROR exhausted retries — {last_err}]"
        content = _extract_content(response)
        if content:
            return content
        last_err = f"empty content (usage={response.get('usage', {})}; raw={str(response)[:200]})"
        if attempt < len(_RETRY_BACKOFF_SEC):
            print(f"  [retry {attempt+1}/{len(_RETRY_BACKOFF_SEC)} after {_RETRY_BACKOFF_SEC[attempt]}s — empty content]", file=sys.stderr)
            continue
        return f"[LITELLM ERROR exhausted retries — {last_err}]"

    return f"[LITELLM ERROR no response after {len(_RETRY_BACKOFF_SEC)} retries — {last_err}]"


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
    """Score text against a metric. score_output.py exits non-zero when hard
    gates fail (Step 4e — intentional), but JSON is still emitted with the
    real composite/dimensions. Trust valid JSON regardless of returncode;
    only treat as error if stdout is empty or unparseable.
    """
    score_script = find_repo_root() / "plugins" / "bmad" / "tools" / "evolve_command" / "scripts" / "score_output.py"
    result = subprocess.run(
        [sys.executable, str(score_script), metric_name, "-"],
        input=text,
        capture_output=True,
        text=True,
        cwd=find_repo_root(),
        timeout=60,  # S3: prevent regex catastrophic backtracking hangs
    )
    if not result.stdout.strip():
        return {"error": result.stderr or "empty stdout", "composite_score": 0.0}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        return {"error": f"JSONDecode: {e}; stderr={result.stderr[:200]}", "composite_score": 0.0}


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test BMAD skills")
    parser.add_argument("--skill", required=True, help="Skill name (e.g. bmad:research)")
    parser.add_argument("--topic", default="test topic", help="Test topic/prompt")
    parser.add_argument("--runs", type=int, default=3, help="Number of generations")
    parser.add_argument("--metric", help="Metric name (default: auto-detect from skill)")
    parser.add_argument("--output-dir", help="Output directory for results")
    parser.add_argument(
        "--mode",
        choices=["cli", "litellm"],
        default="cli",
        help="cli=hermes TUI subprocess (requires gateway); litellm=direct LLM call via DEEPSEEK_API_KEY (recommended for G1)",
    )
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
        print(f"\nRun {i}/{args.runs}... (mode={args.mode})")
        if args.mode == "litellm":
            text = run_skill_via_litellm(args.skill, args.topic, model)
        else:
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

    print("\n" + "─" * 50)
    print(f"SUMMARY: {args.skill}")
    print(f"  Avg score: {avg_score:.3f}")
    print(f"  Min score: {min_score:.3f}")
    print(f"  Max score: {max_score:.3f}")
    print(f"  G1 Gate:   {summary['g1_gate']}")
    print(f"  Details:   {summary_file}")

    return 0 if avg_score >= 0.5 else 1


if __name__ == "__main__":
    sys.exit(main())
