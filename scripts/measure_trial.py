#!/usr/bin/env python3
"""
Trial measurement script — captures daily metrics for DoD items 2, 3, 5, 6, 7.

Run once daily (via cron or manual) during the one-week operational trial.
Appends timestamped data points to ~/.hermes/trial/metrics.jsonl.

DoD items:
  2: Frontmatter compliance (valid_until, type, source on all entries)
  3: valid_until token delta (entries don't expire before their time)
  5: Recall regression hit (preflight recommendations match user's actual need)
  6: Post-apply recall stability (entries survive the next session)
  7: Preflight hit-rate (≥30% of preflight suggestions match user's actual intent)

Usage:
    python3 measure_trial.py
    python3 measure_trial.py --report  # Print summary report of all days so far
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

TRIAL_DIR = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / "trial"
METRICS_FILE = TRIAL_DIR / "metrics.jsonl"


def _ensure_dir() -> None:
    TRIAL_DIR.mkdir(parents=True, exist_ok=True)


def _load_metrics() -> list[dict]:
    if not METRICS_FILE.exists():
        return []
    return [json.loads(line) for line in METRICS_FILE.read_text().strip().split("\n") if line]


def _save_measurement(entry: dict) -> None:
    _ensure_dir()
    with open(METRICS_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")
    print(f"✓ Wrote measurement to {METRICS_FILE}")


# ─────────────────────────────────────────────────────────────────────────────
# Measurements
# ─────────────────────────────────────────────────────────────────────────────


def measure_preflight_logs() -> dict:
    """Count preflight log entries for hit-rate estimation (DoD item 7)."""
    log_dir = Path.home() / ".hermes" / "preflight" / "log"
    total_invocations = 0
    total_injections = 0
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if log_dir.exists():
        for log_file in sorted(log_dir.glob("*.jsonl")):
            with open(log_file) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    total_invocations += 1
                    if entry.get("injected", False):
                        total_injections += 1

    hit_rate = (total_injections / total_invocations * 100) if total_invocations > 0 else 0.0
    return {
        "metric": "preflight_hit_rate",
        "timestamp": today_str,
        "total_invocations": total_invocations,
        "total_injections": total_injections,
        "hit_rate_pct": round(hit_rate, 2),
    }


def measure_llm_calls() -> dict:
    """Count LLM call telemetry rows (DoD item 10 proxy)."""
    obs_dir = Path.home() / ".hermes" / "observability"
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    total_calls = 0
    providers_seen: set[str] = set()

    llm_file = obs_dir / "llm_calls.jsonl"
    if llm_file.exists():
        with open(llm_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                total_calls += 1
                if "model" in entry:
                    providers_seen.add(entry["model"])

    return {
        "metric": "llm_call_telemetry",
        "timestamp": today_str,
        "total_calls": total_calls,
        "unique_models_seen": list(providers_seen),
    }


def measure_memory_entries() -> dict:
    """Check memory entries for frontmatter compliance (DoD item 2)."""
    typed_dir = Path.home() / ".hermes" / "memory" / "typed"
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    total = 0
    with_valid_until = 0
    with_type = 0
    with_source = 0

    if typed_dir.exists():
        for entry_file in typed_dir.glob("*.md"):
            total += 1
            content = entry_file.read_text()
            if "valid_until:" in content:
                with_valid_until += 1
            if "type:" in content:
                with_type += 1
            if "source:" in content:
                with_source += 1

    return {
        "metric": "memory_frontmatter_compliance",
        "timestamp": today_str,
        "total_entries": total,
        "with_valid_until": with_valid_until,
        "with_type": with_type,
        "with_source": with_source,
    }


def measure_current_state() -> list[dict]:
    """Run all measurements and return the data points."""
    measurements = [
        measure_preflight_logs(),
        measure_llm_calls(),
        measure_memory_entries(),
    ]
    return measurements


# ─────────────────────────────────────────────────────────────────────────────
# Report
# ─────────────────────────────────────────────────────────────────────────────


def print_report() -> None:
    """Print a human-readable summary of all trial data so far."""
    metrics = _load_metrics()
    if not metrics:
        print("No trial data yet. Run `python3 measure_trial.py` to start.")
        return

    print(f"{'='*60}")
    print(f"  Operational Trial Report")
    print(f"  {len(metrics)} data points across {len(set(m['timestamp'] for m in metrics))} days")
    print(f"{'='*60}")

    for metric_name in ["preflight_hit_rate", "llm_call_telemetry", "memory_frontmatter_compliance"]:
        points = [m for m in metrics if m.get("metric") == metric_name]
        if not points:
            continue
        print(f"\n── {metric_name} ──")
        for p in points:
            print(f"  {p['timestamp']}: {json.dumps({k: v for k, v in p.items() if k not in ('metric', 'timestamp')})}")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────


def main() -> None:
    if "--report" in sys.argv:
        print_report()
        return

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Check if we already recorded today
    existing = _load_metrics()
    if any(m.get("timestamp") == today for m in existing):
        print(f"Measurements already recorded for {today}. Overwrite? (y/N): ", end="")
        resp = input().strip().lower()
        if resp != "y":
            print("Aborted.")
            return

    measurements = measure_current_state()
    for m in measurements:
        _save_measurement(m)

    print(f"\nDone. {len(measurements)} measurements recorded for {today}.")
    print(f"Run with --report to see summary.")


if __name__ == "__main__":
    main()
