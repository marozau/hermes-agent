#!/usr/bin/env python3
"""score_output.py — Score a text output against a FROZEN structural metric.

Usage:
  python score_output.py <metric_name> <output_file>
  python score_output.py <metric_name> -       # read from stdin

Exit codes:
  0 — scored successfully, prints JSON with score + details
  1 — metric not found or invalid
  2 — output file not found
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: pyyaml required")
    sys.exit(1)


def load_metric(metric_name: str) -> dict:
    """Load metric YAML by name (with or without .yaml suffix)."""
    repo_root = Path(__file__).resolve().parents[5]
    metrics_dir = repo_root / "plugins" / "bmad" / "tools" / "evolve_command" / "metrics"

    # Try exact name, then with .yaml suffix
    for suffix in ("", ".yaml"):
        path = metrics_dir / f"{metric_name}{suffix}"
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)

    # Try partial match
    for path in metrics_dir.glob("*.yaml"):
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if data.get("name") == metric_name:
            return data

    raise FileNotFoundError(f"Metric '{metric_name}' not found in {metrics_dir}")


def check_hard_gate(gate: dict, text: str) -> bool:
    """Evaluate a single hard gate. Returns True if passed."""
    pattern = gate.get("pattern", "")
    if not pattern:
        return True
    return bool(re.search(pattern, text, re.IGNORECASE | re.MULTILINE))


def score_dimension(dim_name: str, dim_spec: list, text: str) -> float:
    """Score a single dimension based on its spec rules."""
    text_lower = text.lower()

    for rule in dim_spec:
        when = rule.get("when", "").lower()
        # Evaluate the 'when' condition heuristically
        if "present" in when or "has" in when:
            # Check if described content exists
            if _matches_condition(when, text_lower):
                return float(rule.get("score", 0.0))
        elif "no " in when and ("no frontmatter" in when or "no overview" in when or "no " in when):
            # Negative condition — only matches if nothing found
            if not _matches_condition(when.replace("no ", ""), text_lower):
                return float(rule.get("score", 0.0))
        else:
            # Fallback: try to match positively
            if _matches_condition(when, text_lower):
                return float(rule.get("score", 0.0))

    return 0.0


def _matches_condition(condition: str, text: str) -> bool:
    """Heuristic condition matcher."""
    condition = condition.strip().lower()

    # Count-based conditions — only for explicit countable nouns,
    # with type-specific counting logic
    count_match = re.search(r"≥?(\d+)", condition)
    if count_match and any(word in condition for word in [
        "requirements", "stories", "epics", "sources", "components", "metrics",
        "criteria", "findings", "drivers", "fields"
    ]):
        min_count = int(count_match.group(1))

        # Count URLs/references for sources
        if "sources" in condition or "citations" in condition:
            urls = len(re.findall(r"https?://", text))
            refs = len(re.findall(r"\[.*?\]", text))
            if urls + refs >= min_count:
                return True

        # Count requirements (FR-NN or table rows with "requirement")
        if "requirements" in condition:
            reqs = len(re.findall(r"fr-\d+", text, re.IGNORECASE))
            if reqs >= min_count:
                return True

        # Count stories ("As a" blocks)
        if "stories" in condition:
            stories = len(re.findall(r"as a\b", text))
            if stories >= min_count:
                return True

        # Count epics ("## Epic" headers)
        if "epics" in condition:
            epics = len(re.findall(r"##\s*epic", text))
            if epics >= min_count:
                return True

        # Count components (table rows in component model)
        if "components" in condition:
            comps = len(re.findall(r"^\s*\|\s*\w+", text, re.MULTILINE))
            if comps >= min_count:
                return True

        # Count metrics
        if "metrics" in condition:
            metrics_found = len(re.findall(r"\b(kpi|metric|nps|mau|dau|conversion)\b", text))
            if metrics_found >= min_count:
                return True

        # Count acceptance criteria (Given/When/Then)
        if "criteria" in condition:
            criteria = len(re.findall(r"given\b.*when\b.*then\b", text))
            if criteria >= min_count:
                return True

        # Count findings (subsections under findings)
        if "findings" in condition:
            findings = len(re.findall(r"###\s+\w+", text))
            if findings >= min_count:
                return True

        # Count drivers
        if "drivers" in condition:
            drivers = len(re.findall(r"^\s*\d+\.", text, re.MULTILINE))
            if drivers >= min_count:
                return True

        # Count frontmatter fields
        if "fields" in condition:
            fields = len(re.findall(r"^[\w_]+:", text, re.MULTILINE))
            if fields >= min_count:
                return True

    # Section presence heuristics
    if "frontmatter" in condition:
        return "---" in text
    if "overview" in condition:
        return "overview" in text
    if "findings" in condition or "results" in condition:
        return "finding" in text or "result" in text
    if "methodology" in condition:
        return "methodolog" in text or "source" in text
    if "citations" in condition or "sources" in condition:
        return "http" in text or "source" in text or "[" in text
    if "conclusions" in condition:
        return "conclusion" in text or "recommendation" in text
    if "requirements" in condition:
        return "requirement" in text or "fr-" in text
    if "success metrics" in condition:
        return "metric" in text and ("success" in text or "kpi" in text)
    if "epics" in condition:
        return "epic" in text
    if "user stories" in condition or "stories" in condition:
        return "as a" in text and "i want" in text
    if "acceptance criteria" in condition:
        return "given" in text and "when" in text and "then" in text
    if "prioritization" in condition:
        return "p0" in text or "priority" in text
    if "component" in condition:
        return "component" in text
    if "data model" in condition:
        return "data" in text and "model" in text
    if "deployment" in condition:
        return "deploy" in text
    if "problem statement" in condition:
        return "problem" in text
    if "solution" in condition:
        return "solution" in text
    if "target audience" in condition:
        return "user" in text or "audience" in text
    if "competitive" in condition:
        return "competitor" in text or "competitive" in text

    return False


def compute_score(metric: dict, text: str) -> dict:
    """Compute full score for a text against a metric."""
    weights = metric.get("weights", {})
    scoring = metric.get("scoring", {})
    hard_gates = metric.get("hard_gates", [])

    results = {
        "metric_name": metric.get("name"),
        "metric_version": metric.get("version"),
        "hard_gates_passed": 0,
        "hard_gates_total": len(hard_gates),
        "hard_gates": [],
        "dimensions": {},
        "dimension_scores": {},
        "composite_score": 0.0,
        "hard_gates_all_pass": True,
    }

    # Check hard gates
    for gate in hard_gates:
        passed = check_hard_gate(gate, text)
        results["hard_gates"].append({
            "name": gate.get("name"),
            "pattern": gate.get("pattern"),
            "passed": passed,
        })
        if passed:
            results["hard_gates_passed"] += 1
        else:
            results["hard_gates_all_pass"] = False

    # Score dimensions
    for dim_name, weight in weights.items():
        dim_spec = scoring.get(dim_name, [])
        dim_score = score_dimension(dim_name, dim_spec, text)
        results["dimension_scores"][dim_name] = dim_score
        results["dimensions"][dim_name] = {
            "score": dim_score,
            "weight": weight,
            "weighted": dim_score * weight,
        }

    # Composite
    composite = sum(d["weighted"] for d in results["dimensions"].values())
    results["composite_score"] = round(composite, 3)

    return results


def main() -> int:
    if len(sys.argv) < 3:
        print("Usage: score_output.py <metric_name> <output_file>|-")
        return 1

    metric_name = sys.argv[1]
    output_path = sys.argv[2]

    try:
        metric = load_metric(metric_name)
    except FileNotFoundError as e:
        print(f"ERROR: {e}")
        return 1

    if output_path == "-":
        text = sys.stdin.read()
    else:
        path = Path(output_path)
        if not path.exists():
            print(f"ERROR: Output file not found: {path}")
            return 2
        text = path.read_text(encoding="utf-8")

    results = compute_score(metric, text)
    print(json.dumps(results, indent=2))

    return 0 if results["hard_gates_all_pass"] else 0  # Still exit 0, score in JSON


if __name__ == "__main__":
    sys.exit(main())
