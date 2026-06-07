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


def check_hard_gate(gate: dict, text: str, dimension_scores: dict = None) -> bool:
    """Evaluate a single hard gate. Returns True if passed.

    Supports two formats:
    1. Pattern-based: {name, pattern} → regex match in text
       Negative gates (name starts with "no_"): pass when pattern NOT found
       Positive gates: pass when pattern IS found
    2. Metric-based: {name, metric, threshold, op} → compare dimension score
    """
    pattern = gate.get("pattern")
    if pattern:
        matched = bool(re.search(pattern, text, re.IGNORECASE | re.MULTILINE))
        gate_name = gate.get("name", "")
        # Negative gates (no_*): pass when pattern is NOT found
        if gate_name.startswith("no_"):
            return not matched
        return matched

    # Metric-based gate: compare dimension score against threshold
    metric_name = gate.get("metric")
    threshold = gate.get("threshold")
    op = gate.get("op", ">=")
    if metric_name is not None and threshold is not None and dimension_scores is not None:
        score = dimension_scores.get(metric_name, 0.0)
        if op == ">=":
            return score >= threshold
        elif op == ">":
            return score > threshold
        elif op == "<=":
            return score <= threshold
        elif op == "<":
            return score < threshold
        elif op == "==":
            return abs(score - threshold) < 0.001
        elif op == "!=":
            return abs(score - threshold) >= 0.001

    # Unknown gate format — fail closed (return False)
    return False


def score_dimension(dim_name: str, dim_spec: list, text: str) -> float:
    """Score a single dimension based on its spec rules."""
    text = _normalize_heading_numbering(_strip_markdown_fence(text))
    text_lower = text.lower()

    for rule in dim_spec:
        when = rule.get("when", "").lower()
        # Evaluate the 'when' condition heuristically
        if "present" in when or "has" in when:
            # Check if described content exists
            if _matches_condition(when, text_lower, dim_name):
                return float(rule.get("score", 0.0))
        elif when.startswith("no "):
            # Negative condition — only matches if nothing found
            if not _matches_condition(when.replace("no ", ""), text_lower, dim_name):
                return float(rule.get("score", 0.0))
        else:
            # Fallback: try to match positively
            if _matches_condition(when, text_lower, dim_name):
                return float(rule.get("score", 0.0))

    return 0.0


def _strip_markdown_fence(text: str) -> str:
    """LLMs often wrap output in ```markdown … ``` fences. Strip if present so
    frontmatter and other section heuristics see the real content."""
    s = text.lstrip()
    for prefix in ("```markdown\n", "```md\n", "```\n"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            # Drop trailing fence if present
            s = re.sub(r"\n```\s*\Z", "", s)
            return s
    return text


def _normalize_heading_numbering(text: str) -> str:
    """Strip numbered prefixes from markdown headings so '## 1. Foo' matches '## Foo'.

    Handles "## 1. Foo", "### 1.2 Foo", "## Section 1: Foo", "## 1) Foo".
    Idempotent — safe to call multiple times. Applied alongside fence-strip so
    rubric patterns written against canonical headings still match LLM output that
    chose to number sections.
    """
    return re.sub(
        r"^(#+\s+)(?:(?:section\s+)?\d+(?:\.\d+)*[\.\):]?\s+)",
        r"\1",
        text,
        flags=re.MULTILINE | re.IGNORECASE,
    )


def _section_body(text: str, *heading_patterns: str) -> str | None:
    """Return the body of the first matching ## / ### section (lowercased).

    Tolerates numbered prefixes ("## 1. Foo", "## 1.2 Foo", "## Section 1: Foo")
    so the rubric matches regardless of whether the LLM numbered its sections.
    """
    # Optional numbering: "1.", "1.2", "1.2.3", "section 1:", etc.
    numbering = r"(?:(?:section\s+)?\d+(?:\.\d+)*[\.\):]?\s+)?"
    for pat in heading_patterns:
        m = re.search(
            rf"##+\s+{numbering}{pat}\s*\n(.+?)(?=\n##\s|\Z)",
            text,
            re.IGNORECASE | re.DOTALL,
        )
        if m:
            return m.group(1)
    return None


def _match_by_dim(dim_name: str, condition: str, text: str) -> bool | None:
    """Per-dimension high-precision matchers. Returns None to fall through."""
    if not dim_name:
        return None

    if dim_name == "executive_summary":
        body = _section_body(text, r"executive\s+summary")
        if not body or not body.strip():
            return False
        # text is lowercased by caller, so don't anchor on [A-Z]
        sentences = len(re.findall(r"[.!?]+\s", body))
        if "≥3" in condition or "3 sentences" in condition or "covering what" in condition:
            return sentences >= 3
        if "1-2" in condition:
            return 1 <= sentences <= 2
        return sentences >= 1

    if dim_name == "product_goals":
        has_section = bool(_section_body(text, r"(product\s+)?goals?", r"objectives?\s+and\s+goals"))
        has_business = bool(re.search(r"###?\s+business\s+objectives?\b|\bbusiness\s+objectives?\s*[:\-]", text, re.IGNORECASE))
        has_user = bool(re.search(r"###?\s+user\s+goals?\b|\buser\s+goals?\s*[:\-]", text, re.IGNORECASE))
        if "explicitly listed" in condition or ("business" in condition and "user" in condition):
            return has_section and has_business and has_user
        if "mentioned inline" in condition:
            return has_section or has_business or has_user
        return has_section

    if dim_name == "non_functional_requirements":
        nfr_cats = sum([
            bool(re.search(r"\bperformance\b", text, re.IGNORECASE)),
            bool(re.search(r"\bsecurity\b", text, re.IGNORECASE)),
            bool(re.search(r"\breliab\w*\b", text, re.IGNORECASE)),
            bool(re.search(r"\bscalab\w*\b", text, re.IGNORECASE)),
        ])
        if "performance" in condition and "security" in condition and "reliability" in condition:
            return nfr_cats >= 4
        if "1-2 nfr" in condition or ("1-2" in condition and "category" in condition):
            return 1 <= nfr_cats <= 2
        return nfr_cats >= 1

    if dim_name == "success_metrics":
        # Count distinct metric items within the Success Metrics section
        body = _section_body(text, r"success\s+metrics?", r"metrics?")
        if not body:
            return False
        # Each bullet, numbered item, or table row counts once
        items = len(re.findall(r"^\s*(?:[-*]|\d+\.)\s+\S", body, re.MULTILINE))
        items += len(re.findall(r"^\s*\|\s*\S+\s*\|", body, re.MULTILINE))
        if "≥3" in condition or "3 measurable" in condition:
            return items >= 3
        if "1-2" in condition:
            return 1 <= items <= 2
        return items >= 1

    if dim_name == "problem_statement":
        body = _section_body(text, r"problem\s+statement", r"problem")
        if not body:
            return False
        has_problem = bool(body.strip())
        has_why_now = bool(re.search(r"why\s+now|timing|urgency|opportunity", body, re.IGNORECASE))
        has_impact = bool(re.search(r"impact|cost|risk|consequence|if\s+unsolved", body, re.IGNORECASE))
        if "why now" in condition and "impact" in condition:
            return has_problem and has_why_now and has_impact
        if "missing why_now" in condition or "missing impact" in condition:
            return has_problem and (has_why_now or has_impact) and not (has_why_now and has_impact)
        if "vague" in condition:
            return has_problem and not (has_why_now or has_impact)
        return has_problem

    if dim_name == "target_audience":
        has_section = bool(_section_body(text, r"target\s+audience", r"target\s+users", r"users?\s+and\s+personas?", r"audience"))
        primary_secondary = bool(re.search(r"primary\s+user|secondary\s+user|primary\s+audience|secondary\s+audience", text, re.IGNORECASE))
        personas = bool(re.search(r"persona|role\s*:|user\s+role", text, re.IGNORECASE))
        if "primary + secondary" in condition or "personas or roles" in condition:
            return has_section and (primary_secondary or personas)
        if "not segmented" in condition:
            return has_section and not primary_secondary
        return has_section

    if dim_name == "proposed_solution":
        has_section = bool(_section_body(text, r"(proposed\s+)?solution(\s+overview)?", r"solution"))
        if not has_section:
            return False
        body = _section_body(text, r"(proposed\s+)?solution(\s+overview)?", r"solution") or ""
        has_features = bool(re.search(r"feature|capabilit|component", body, re.IGNORECASE))
        has_differentiation = bool(re.search(r"differen|unique|advantage|vs\.|compared\s+to", body, re.IGNORECASE))
        if "key features" in condition or "differentiation" in condition:
            return has_features and has_differentiation
        return True

    if dim_name == "competitive_landscape":
        body = _section_body(text, r"competitive\s+landscape", r"competitors?", r"market\s+analysis")
        if not body:
            return False
        # Count distinct competitor mentions (bulleted/numbered)
        # text is lowercased by caller; match any letter, not just [A-Z]
        competitors = len(re.findall(r"^\s*(?:[-*]|\d+\.)\s+\*?\*?[a-z]", body, re.MULTILINE))
        differentiation = bool(re.search(r"differen|advantage|vs\.|compared", body, re.IGNORECASE))
        if "≥2 competitors" in condition and "differentiation" in condition:
            return competitors >= 2 and differentiation
        if "not analyzed" in condition:
            return competitors >= 1 and not differentiation
        return competitors >= 1

    if dim_name == "epic_decomposition":
        # Count distinct epic mentions
        epics = len(re.findall(r"##+\s+epic\b|^\s*[-*]\s+epic\b", text, re.IGNORECASE | re.MULTILINE))
        if "≥3 epics" in condition or "≥3" in condition:
            return epics >= 3
        if "1-2" in condition:
            return 1 <= epics <= 2
        return epics >= 1

    if dim_name == "user_stories":
        stories = len(re.findall(r"as\s+a\s+\S+.{0,200}i\s+want|^\s*(?:[-*]|\d+\.)\s+\*?\*?story\b", text, re.IGNORECASE))
        if "≥5 stories" in condition or "≥5" in condition:
            return stories >= 5
        if "1-2" in condition:
            return 1 <= stories <= 2
        return stories >= 1

    if dim_name == "acceptance_criteria":
        criteria = len(re.findall(r"given\b.*?when\b.*?then\b", text, re.IGNORECASE | re.DOTALL))
        if "≥3" in condition or "3 acceptance" in condition:
            return criteria >= 3
        if "1-2" in condition:
            return 1 <= criteria <= 2
        return criteria >= 1

    if dim_name == "architectural_drivers":
        body = _section_body(
            text,
            r"(?:\d+\.\s+)?architectural\s+drivers?",
            r"(?:\d+\.\s+)?drivers?\s+and\s+constraints?",
        )
        if not body:
            return False
        # Each "labeled bullet with description" counts as a driver-with-rationale:
        #   - **Label**: explanation
        #   - **Label** — explanation
        #   ### Heading\n description
        labeled_bullets = re.findall(
            r"^\s*(?:[-*]|\d+\.)\s+\*\*[^*]+\*\*\s*[:\-—]\s*\S+",
            body,
            re.MULTILINE,
        )
        plain_bullets = re.findall(r"^\s*(?:[-*]|\d+\.)\s+\S", body, re.MULTILINE)
        headed_subsections = re.findall(r"^\s*###?\s+\S+", body, re.MULTILINE)
        # Rationale-bearing drivers: bullet has body content (≥40 chars) OR literal rationale keyword
        rationale_keywords = bool(re.search(
            r"rationale|because|drives?|why|reason|enables?|allows?|ensures?|because\s+of",
            body, re.IGNORECASE,
        ))
        # If labeled bullets exist, they're self-evidently rationale-bearing
        drivers = max(len(labeled_bullets), len(plain_bullets), len(headed_subsections))
        if "≥3" in condition and "rationale" in condition:
            # Labeled bullets count as rationale-bearing; plain bullets need keyword
            if len(labeled_bullets) >= 3:
                return True
            return drivers >= 3 and rationale_keywords
        if "1-2" in condition:
            return 1 <= drivers <= 2
        return drivers >= 1

    if dim_name == "data_model":
        body = _section_body(text, r"(?:\d+\.\s+)?data\s+model", r"(?:\d+\.\s+)?domain\s+model")
        if not body:
            return False
        has_entities = bool(re.search(r"entit|table|schema|object|class\b", body, re.IGNORECASE))
        has_relationships = bool(re.search(r"relationship|reference|foreign\s+key|fk|joins?|associat", body, re.IGNORECASE))
        has_storage = bool(re.search(r"storage|database|persist|postgres|mysql|kafka|s3|sqlite|cassandra|redis|neo4j|mongo|object\s+store", body, re.IGNORECASE))
        if "entities" in condition and "relationships" in condition and "storage" in condition:
            return has_entities and has_relationships and has_storage
        if "no relationships" in condition:
            return has_entities and not has_relationships
        return has_entities

    if dim_name == "system_overview":
        body = _section_body(text, r"(?:\d+\.\s+)?system\s+overview", r"(?:\d+\.\s+)?overview")
        if not body:
            return False
        has_diagram = bool(re.search(r"```mermaid|```diagram|```\s*\n.+?[─│┌┐└┘┤├┬┴┼]|!\[.*?\]\(", body, re.DOTALL))
        has_structured = bool(re.search(r"^\s*[-*]\s+\*\*\w+|^\s*\d+\.\s+\*\*\w+|^\s*###?\s+\w+", body, re.MULTILINE))
        if "diagram" in condition or "structured components" in condition:
            return bool(body.strip()) and (has_diagram or has_structured)
        if "brief overview" in condition:
            return bool(body.strip())
        return bool(body.strip())

    if dim_name == "component_model":
        body = _section_body(text, r"(?:\d+\.\s+)?component\s+model", r"(?:\d+\.\s+)?components?")
        if not body:
            return False
        # Count components — typically heading-level or bold-labeled items
        components = len(re.findall(r"^\s*###?\s+\w|^\s*\*\*[\w/\-]+\*\*\s*[:\-]|^\s*[-*]\s+\*\*[\w/\-]+", body, re.MULTILINE))
        # Also count table rows
        components += len(re.findall(r"^\s*\|\s*[A-Za-z]", body, re.MULTILINE))
        has_responsibilities = bool(re.search(r"responsibilit|owns|handles|provides|exposes", body, re.IGNORECASE))
        if "≥3" in condition or "3 components" in condition:
            return components >= 3 and has_responsibilities
        if "2 components" in condition:
            return components == 2
        if "1 component" in condition:
            return components == 1
        return components >= 1

    if dim_name == "deployment_architecture":
        body = _section_body(text, r"(?:\d+\.\s+)?deployment(\s+architecture)?", r"(?:\d+\.\s+)?deploy")
        if not body:
            return False
        has_topology = bool(re.search(r"topolog|environment|cluster|kubernetes|k8s|docker|aws|gcp|azure|cloud", body, re.IGNORECASE))
        has_envs = bool(re.search(r"\b(dev|staging|prod|production|test)\b", body, re.IGNORECASE))
        has_scaling = bool(re.search(r"scal|replica|autoscal|horizontal|vertical|failover|load\s+balanc", body, re.IGNORECASE))
        if "topology" in condition and "environments" in condition and "scaling" in condition:
            return has_topology and has_envs and has_scaling
        if "brief deployment" in condition:
            return bool(body.strip())
        return bool(body.strip())

    return None


def _matches_condition(condition: str, text: str, dim_name: str = "") -> bool:
    """Heuristic condition matcher.

    dim_name (when supplied) routes through dimension-specific matchers that
    inspect the named section directly — much more precise than condition-text
    keyword matching.
    """
    condition = condition.strip().lower()

    # Dimension-specific matchers (high precision — section-anchored counts)
    dim_result = _match_by_dim(dim_name, condition, text)
    if dim_result is not None:
        return dim_result

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

        # Count acceptance criteria (Given/When/Then) — multi-line with re.DOTALL
        if "criteria" in condition:
            criteria = len(re.findall(r"given\b.*?when\b.*?then\b", text, re.IGNORECASE | re.DOTALL))
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

        # Count frontmatter fields (key: value inside --- ... --- block at START of doc)
        if "fields" in condition:
            # Only count fields if document starts with frontmatter
            if not text.lstrip().startswith('---'):
                return False
            lines = text.split('\n')
            in_frontmatter = False
            field_count = 0
            for line in lines:
                stripped = line.strip()
                if stripped == '---':
                    if not in_frontmatter:
                        in_frontmatter = True
                        continue
                    else:
                        break  # End of frontmatter
                if in_frontmatter and ':' in stripped:
                    # Must be key: value format, not a URL
                    key = stripped.split(':')[0]
                    if key and re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', key):
                        field_count += 1
            if field_count >= min_count:
                return True

    # Section presence heuristics
    if "frontmatter" in condition:
        # Frontmatter is --- at the very start of the document, not a horizontal rule
        return text.lstrip().startswith("---")
    if "overview" in condition:
        return "overview" in text
    if "findings" in condition or "results" in condition:
        return "finding" in text or "result" in text
    if "methodology" in condition:
        return "methodolog" in text or "source" in text
    if "citations" in condition or "sources" in condition:
        # Word-bounded check: "source" / "sources" but NOT "outsources" / "preferences"
        return bool(re.search(r"https?://|\[.*?\]|\bsources?\b|\breferences?\b|\bcitations?\b", text, re.IGNORECASE))
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
    text = _normalize_heading_numbering(_strip_markdown_fence(text))
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

    # Score dimensions FIRST (metric-based gates need these)
    for dim_name, weight in weights.items():
        dim_spec = scoring.get(dim_name, [])
        dim_score = score_dimension(dim_name, dim_spec, text)
        results["dimension_scores"][dim_name] = dim_score
        results["dimensions"][dim_name] = {
            "score": dim_score,
            "weight": weight,
            "weighted": dim_score * weight,
        }

    # Check hard gates (after dimensions are scored)
    for gate in hard_gates:
        passed = check_hard_gate(gate, text, results["dimension_scores"])
        gate_info = {
            "name": gate.get("name"),
            "passed": passed,
        }
        if gate.get("pattern"):
            gate_info["pattern"] = gate["pattern"]
        if gate.get("metric") is not None:
            gate_info["metric"] = gate["metric"]
            gate_info["threshold"] = gate.get("threshold")
            gate_info["op"] = gate.get("op", "")
        results["hard_gates"].append(gate_info)
        if passed:
            results["hard_gates_passed"] += 1
        else:
            results["hard_gates_all_pass"] = False

    # Composite: if no scoring block, skip (composite stays 0.0 for external metrics)
    if scoring:
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

    # Step 3: Schema compatibility guard — reject legacy metric/threshold/op metrics
    LEGACY_SCHEMA_MARKERS = {"metric", "threshold", "op"}
    hard_gates = metric.get("hard_gates", [])
    if hard_gates and isinstance(hard_gates[0], dict):
        first_gate_keys = set(hard_gates[0].keys())
        if LEGACY_SCHEMA_MARKERS & first_gate_keys and not hard_gates[0].get("pattern"):
            print(
                f"ERROR: {metric_name} uses legacy metric/threshold/op schema. "
                f"Route through metrics/{metric_name}.py (Epic 13 per-metric scorer).",
                file=sys.stderr,
            )
            return 3

    if "scoring" not in metric and "weights" in metric:
        print(
            f"ERROR: {metric_name} has weights but no scoring block. "
            f"Legacy metric — use the .py scorer.",
            file=sys.stderr,
        )
        return 3

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

    return 0 if results["hard_gates_all_pass"] else 1  # Exit 1 if gates fail


if __name__ == "__main__":
    sys.exit(main())
