"""
Tests for judge/phase_gates.py — phase gate evaluation logic.

Run from repo root:
    source .venv/bin/activate && pytest plugins/bmad/judge/test_phase_gates.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure plugin is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Force yaml to be available
try:
    import yaml  # noqa: F401
except ImportError:
    pytest.skip("pyyaml not installed", allow_module_level=True)

from plugins.bmad.judge.phase_gates import (
    check_gate,
    evaluate_all_gates,
    get_phase_adjustments,
    inject_adjustments,
    load_criteria,
)


# ═══════════════════════════════════════════════════════════════════════════
# get_phase_adjustments — pre-phase recurring-pattern query
# ═══════════════════════════════════════════════════════════════════════════


class TestGetPhaseAdjustments:
    """Pre-phase adjustment queries against the reflection bank."""

    @pytest.fixture
    def project_root(self, tmp_path):
        """Create a minimal BMAD project tree with a reflection bank."""
        bmad_dir = tmp_path / "bmad"
        bmad_dir.mkdir(parents=True)
        (bmad_dir / "config.yaml").write_text("project_name: Test\nuser_name: tester\n")
        reflection_path = tmp_path / ".hermes"
        reflection_path.mkdir(parents=True)
        return tmp_path

    def _write_bank(self, project_root: Path, yaml_text: str):
        path = project_root / ".hermes" / "reflection-bank.yaml"
        path.write_text(yaml_text, encoding="utf-8")

    def test_empty_bank_returns_empty_string(self, project_root):
        """When the reflection bank has no entries, the result is empty."""
        result = get_phase_adjustments("analysis", project_root=project_root)
        assert result == ""

    def test_no_matching_phase_returns_empty_string(self, project_root):
        """Entries for a different phase should not match."""
        self._write_bank(
            project_root,
            """\
- id: 00000000-0000-0000-0000-000000000010
  phase: planning
  profile: default
  timestamp: "2026-05-29T00:00:00+00:00"
  summary: "PRD missing acceptance criteria"
  mistake_pattern: missing-acceptance-criteria
  severity: high
  phase_of_discovery: planning
  root_cause: "No AC template in PRD"
  first_principles_vs_heuristic: heuristic
  confidence: 0.9
  recommendation: "Add AC template"
  adjusted_instruction: "Always include acceptance criteria in PRDs"
  affected_skill: create-prd
  recurrence_count: 3
  fixed_in_phase: null
  requires_adjustment: true
""",
        )
        result = get_phase_adjustments("analysis", project_root=project_root)
        assert result == ""

    def test_recurrence_count_one_excluded(self, project_root):
        """Entries with recurrence_count == 1 should not match (> 1 required)."""
        self._write_bank(
            project_root,
            """\
- id: 00000000-0000-0000-0000-000000000011
  phase: analysis
  profile: default
  timestamp: "2026-05-29T00:00:00+00:00"
  summary: "Missing problem statement"
  mistake_pattern: missing-problem-statement
  severity: medium
  phase_of_discovery: analysis
  root_cause: "Template didn't emphasize problem statement"
  first_principles_vs_heuristic: heuristic
  confidence: 0.8
  recommendation: "Update template"
  adjusted_instruction: "Start with the problem statement before listing features"
  affected_skill: product-brief
  recurrence_count: 1
  fixed_in_phase: null
  requires_adjustment: true
""",
        )
        result = get_phase_adjustments("analysis", project_root=project_root)
        assert result == ""

    def test_empty_adjusted_instruction_excluded(self, project_root):
        """Entries with empty adjusted_instruction should not match."""
        self._write_bank(
            project_root,
            """\
- id: 00000000-0000-0000-0000-000000000012
  phase: analysis
  profile: default
  timestamp: "2026-05-29T00:00:00+00:00"
  summary: "Scope not defined"
  mistake_pattern: scope-undefined
  severity: low
  phase_of_discovery: analysis
  root_cause: "Skipped scope section"
  first_principles_vs_heuristic: heuristic
  confidence: 0.6
  recommendation: ""
  adjusted_instruction: ""
  affected_skill: null
  recurrence_count: 4
  fixed_in_phase: null
  requires_adjustment: false
""",
        )
        result = get_phase_adjustments("analysis", project_root=project_root)
        assert result == ""

    def test_matching_entry_returns_watch_out_section(self, project_root):
        """A matching entry (right phase, recurrence > 1, non-empty
        adjusted_instruction) should produce a formatted Watch-out section."""
        self._write_bank(
            project_root,
            """\
- id: 00000000-0000-0000-0000-000000000013
  phase: solutioning
  profile: default
  timestamp: "2026-05-29T00:00:00+00:00"
  summary: "Architecture docs consistently omit rate-limit considerations"
  mistake_pattern: rate-limit-omission
  severity: high
  phase_of_discovery: solutioning
  root_cause: "Template doesn't include rate-limit section"
  first_principles_vs_heuristic: heuristic
  confidence: 0.95
  recommendation: "Add rate-limit section to architecture template"
  adjusted_instruction: "The judge will specifically check that rate-limits are addressed in the architecture. Include per-endpoint rate-limit specifications."
  affected_skill: create-architecture
  recurrence_count: 3
  fixed_in_phase: null
  requires_adjustment: true
""",
        )
        result = get_phase_adjustments("solutioning", project_root=project_root)
        assert "Watch-out" in result
        assert "rate-limit-omission" in result
        assert "×3" in result
        assert "first seen in solutioning" in result
        assert "rate-limits are addressed" in result
        assert result.startswith("\n## ")

    def test_multiple_matching_entries(self, project_root):
        """Multiple matching entries should all appear in the Watch-out section."""
        self._write_bank(
            project_root,
            """\
- id: 00000000-0000-0000-0000-000000000014
  phase: analysis
  profile: default
  timestamp: "2026-05-29T00:00:00+00:00"
  summary: "Problem statement often missing"
  mistake_pattern: missing-problem-statement
  severity: high
  phase_of_discovery: analysis
  root_cause: "Template de-emphasizes problem"
  first_principles_vs_heuristic: heuristic
  confidence: 0.9
  recommendation: "Update product brief template"
  adjusted_instruction: "Ensure the problem statement section is filled before anything else"
  affected_skill: product-brief
  recurrence_count: 2
  fixed_in_phase: null
  requires_adjustment: true
- id: 00000000-0000-0000-0000-000000000015
  phase: analysis
  profile: default
  timestamp: "2026-05-29T00:00:00+00:00"
  summary: "Target users not identified"
  mistake_pattern: missing-target-users
  severity: medium
  phase_of_discovery: analysis
  root_cause: "Users section skipped"
  first_principles_vs_heuristic: heuristic
  confidence: 0.8
  recommendation: "Require user personas"
  adjusted_instruction: "Identify at least 2 target user personas before submitting"
  affected_skill: product-brief
  recurrence_count: 4
  fixed_in_phase: null
  requires_adjustment: true
""",
        )
        result = get_phase_adjustments("analysis", project_root=project_root)
        assert "missing-problem-statement" in result
        assert "missing-target-users" in result
        assert "×2" in result
        assert "×4" in result

    def test_profile_filter_respected(self, project_root):
        """Only entries matching the requested profile should appear."""
        self._write_bank(
            project_root,
            """\
- id: 00000000-0000-0000-0000-000000000016
  phase: solutioning
  profile: engineer
  timestamp: "2026-05-29T00:00:00+00:00"
  summary: "Architecture misses integration points"
  mistake_pattern: missing-integration-points
  severity: high
  phase_of_discovery: solutioning
  root_cause: "Template incomplete"
  first_principles_vs_heuristic: heuristic
  confidence: 0.9
  recommendation: "Template update"
  adjusted_instruction: "List every external integration point explicitly"
  affected_skill: create-architecture
  recurrence_count: 5
  fixed_in_phase: null
  requires_adjustment: true
""",
        )
        result = get_phase_adjustments(
            "solutioning", project_root=project_root, profile="cto"
        )
        assert result == ""


# ═══════════════════════════════════════════════════════════════════════════
# inject_adjustments
# ═══════════════════════════════════════════════════════════════════════════


class TestInjectAdjustments:
    """Tests for the inject_adjustments convenience wrapper."""

    @pytest.fixture
    def project_root(self, tmp_path):
        bmad_dir = tmp_path / "bmad"
        bmad_dir.mkdir(parents=True)
        (bmad_dir / "config.yaml").write_text("project_name: Test\nuser_name: tester\n")
        reflection_path = tmp_path / ".hermes"
        reflection_path.mkdir(parents=True)
        return tmp_path

    def _write_bank(self, project_root: Path, yaml_text: str):
        path = project_root / ".hermes" / "reflection-bank.yaml"
        path.write_text(yaml_text, encoding="utf-8")

    def test_no_adjustments_returns_body_unchanged(self, project_root):
        """When the reflection bank has no matching entries, the body is
        returned exactly as-is."""
        body = "# PRD Template\n\nFill in requirements here."
        result = inject_adjustments("planning", project_root, body)
        assert result == body

    def test_adjustments_appended_to_body(self, project_root):
        """When adjustments exist, they are appended to the body with proper
        formatting (no double newlines between body end and watch-out)."""
        self._write_bank(
            project_root,
            """\
- id: 00000000-0000-0000-0000-000000000020
  phase: planning
  profile: default
  timestamp: "2026-05-29T00:00:00+00:00"
  summary: "PRDs missing acceptance criteria"
  mistake_pattern: missing-ac
  severity: high
  phase_of_discovery: planning
  root_cause: "No AC enforcement"
  first_principles_vs_heuristic: heuristic
  confidence: 0.9
  recommendation: "Add AC enforcement"
  adjusted_instruction: "Verify acceptance criteria exist for every feature"
  affected_skill: create-prd
  recurrence_count: 3
  fixed_in_phase: null
  requires_adjustment: true
""",
        )
        body = "# PRD Template\n\nFill in requirements here."
        result = inject_adjustments("planning", project_root, body)
        assert result.startswith(body + "\n## ")
        assert "Watch-out" in result
        assert "missing-ac" in result
        assert "×3" in result
        # Body content preserved
        assert "Fill in requirements here" in result

    def test_body_with_trailing_newlines_handled(self, project_root):
        """Bodies already ending in newlines should not produce double gaps."""
        self._write_bank(
            project_root,
            """\
- id: 00000000-0000-0000-0000-000000000021
  phase: implementation
  profile: default
  timestamp: "2026-05-29T00:00:00+00:00"
  summary: "Sprint plans miss hardcoded-secret checks"
  mistake_pattern: secrets-in-code
  severity: critical
  phase_of_discovery: implementation
  root_cause: "No pre-commit hook"
  first_principles_vs_heuristic: heuristic
  confidence: 0.95
  recommendation: "Add pre-commit secret scan"
  adjusted_instruction: "Run a secret scan before marking any story complete"
  affected_skill: dev-story
  recurrence_count: 2
  fixed_in_phase: null
  requires_adjustment: true
""",
        )
        body = "# Sprint Plan\n\n1. Story one\n2. Story two\n\n"
        result = inject_adjustments("implementation", project_root, body)
        # Should not have a double like "two\\n\\n\\n##"
        assert "\n\n\n##" not in result
        assert "Watch-out" in result
        assert "secret scan" in result


# ═══════════════════════════════════════════════════════════════════════════
# load_criteria
# ═══════════════════════════════════════════════════════════════════════════


class TestLoadCriteria:
    def test_loads_analysis_phase(self):
        criteria = load_criteria("analysis")
        assert criteria["phase"] == "analysis"
        assert "product_brief.md" in criteria["required_artifacts"]
        assert len(criteria["gates"]) >= 4
        for gate in criteria["gates"]:
            assert "id" in gate
            assert "description" in gate
            assert "severity" in gate
            assert gate["severity"] in ("required", "recommended")

    def test_loads_planning_phase(self):
        criteria = load_criteria("planning")
        assert criteria["phase"] == "planning"
        assert "prd.md" in criteria["required_artifacts"]
        assert len(criteria["gates"]) >= 4

    def test_loads_solutioning_phase(self):
        criteria = load_criteria("solutioning")
        assert criteria["phase"] == "solutioning"
        assert "architecture.md" in criteria["required_artifacts"]
        assert len(criteria["gates"]) >= 4

    def test_loads_implementation_phase(self):
        criteria = load_criteria("implementation")
        assert criteria["phase"] == "implementation"
        assert len(criteria["gates"]) >= 4

    def test_unknown_phase_raises(self):
        with pytest.raises(ValueError, match="Unknown phase"):
            load_criteria("nonexistent")

    def test_custom_path(self, tmp_path):
        custom = tmp_path / "custom.yaml"
        custom.write_text("""
phases:
  analysis:
    required_artifacts: []
    gates:
      - id: custom_gate
        description: "Custom"
        severity: required
""")
        criteria = load_criteria("analysis", custom_path=str(custom))
        assert len(criteria["gates"]) == 1
        assert criteria["gates"][0]["id"] == "custom_gate"

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_criteria("analysis", custom_path="/nonexistent/criteria.yaml")


# ═══════════════════════════════════════════════════════════════════════════
# check_gate — analysis phase gates
# ═══════════════════════════════════════════════════════════════════════════


class TestAnalysisGates:
    def test_problem_statement_present_positive(self):
        result = check_gate(
            {"id": "problem_statement_present", "description": "desc", "severity": "required"},
            {"product_brief.md": "The problem statement is that users cannot easily ..."},
            {},
        )
        assert result["passed"] is True
        assert "problem statement" in result["evidence"].lower()

    def test_problem_statement_present_negative(self):
        result = check_gate(
            {"id": "problem_statement_present", "description": "desc", "severity": "required"},
            {"product_brief.md": "We are building a todo app. It will have features."},
            {},
        )
        assert result["passed"] is False

    def test_target_users_defined_positive(self):
        result = check_gate(
            {"id": "target_users_defined", "description": "desc", "severity": "required"},
            {"product_brief.md": "Target users: developers who need ... persona: Alice, a busy PM"},
            {},
        )
        assert result["passed"] is True

    def test_target_users_defined_negative(self):
        result = check_gate(
            {"id": "target_users_defined", "description": "desc", "severity": "required"},
            {"product_brief.md": "This is a system for managing tasks."},
            {},
        )
        assert result["passed"] is False

    def test_success_metrics_listed_positive(self):
        result = check_gate(
            {"id": "success_metrics_listed", "description": "desc", "severity": "required"},
            {"product_brief.md": "Success metrics: 50% reduction in time, KPI: user engagement"},
            {},
        )
        assert result["passed"] is True

    def test_success_metrics_listed_negative(self):
        result = check_gate(
            {"id": "success_metrics_listed", "description": "desc", "severity": "required"},
            {"product_brief.md": "We hope users will like it."},
            {},
        )
        assert result["passed"] is False

    def test_constraints_documented_positive(self):
        result = check_gate(
            {"id": "constraints_documented", "description": "desc", "severity": "recommended"},
            {"product_brief.md": "Constraints: budget of $10k, deadline Q3 2025"},
            {},
        )
        assert result["passed"] is True

    def test_scope_boundary_clear_positive(self):
        result = check_gate(
            {"id": "scope_boundary_clear", "description": "desc", "severity": "recommended"},
            {"product_brief.md": "In scope: user auth. Out of scope: payment integration."},
            {},
        )
        assert result["passed"] is True

    def test_artifacts_without_md_extension(self):
        """check_gate should find artifacts keyed without .md extension."""
        result = check_gate(
            {"id": "problem_statement_present", "description": "desc", "severity": "required"},
            {"product_brief": "The problem is latency in the dashboard, and this challenge affects all users."},
            {},
        )
        assert result["passed"] is True


# ═══════════════════════════════════════════════════════════════════════════
# check_gate — planning phase gates
# ═══════════════════════════════════════════════════════════════════════════


class TestPlanningGates:
    def test_requirements_traceable_to_brief_positive(self):
        result = check_gate(
            {"id": "requirements_traceable_to_brief", "description": "desc", "severity": "required"},
            {"prd.md": "FR1 traces to user need from product brief: 'developers need fast search'"},
            {},
        )
        assert result["passed"] is True

    def test_acceptance_criteria_per_feature_positive(self):
        result = check_gate(
            {"id": "acceptance_criteria_per_feature", "description": "desc", "severity": "required"},
            {"prd.md": "Given a user is logged in, When they click search, Then results appear. Acceptance criteria: must satisfy latency < 200ms."},
            {},
        )
        assert result["passed"] is True

    def test_priority_assigned_positive(self):
        result = check_gate(
            {"id": "priority_assigned", "description": "desc", "severity": "required"},
            {"prd.md": "P0: login, P1: dashboard, must have: auth"},
            {},
        )
        assert result["passed"] is True

    def test_priority_assigned_negative(self):
        result = check_gate(
            {"id": "priority_assigned", "description": "desc", "severity": "required"},
            {"prd.md": "Here are some features we could build."},
            {},
        )
        assert result["passed"] is False

    def test_dependencies_identified_positive(self):
        result = check_gate(
            {"id": "dependencies_identified", "description": "desc", "severity": "recommended"},
            {"prd.md": "Dependency: requires auth service to be deployed first"},
            {},
        )
        assert result["passed"] is True

    def test_out_of_scope_stated_positive(self):
        result = check_gate(
            {"id": "out_of_scope_stated", "description": "desc", "severity": "recommended"},
            {"prd.md": "Out of scope for v1: mobile app, AI features"},
            {},
        )
        assert result["passed"] is True


# ═══════════════════════════════════════════════════════════════════════════
# check_gate — solutioning phase gates
# ═══════════════════════════════════════════════════════════════════════════


class TestSolutioningGates:
    def test_components_mapped_to_requirements_positive(self):
        result = check_gate(
            {"id": "components_mapped_to_requirements", "description": "desc", "severity": "required"},
            {"architecture.md": "Auth service maps to requirement FR1"},
            {},
        )
        assert result["passed"] is True

    def test_data_flow_documented_positive(self):
        result = check_gate(
            {"id": "data_flow_documented", "description": "desc", "severity": "required"},
            {"architecture.md": "Data flow: user -> API gateway -> message queue -> database"},
            {},
        )
        assert result["passed"] is True

    def test_tech_stack_decisions_justified_positive(self):
        result = check_gate(
            {"id": "tech_stack_decisions_justified", "description": "desc", "severity": "required"},
            {"architecture.md": "PostgreSQL chosen because of ACID compliance and team familiarity"},
            {},
        )
        assert result["passed"] is True

    def test_integration_points_identified_positive(self):
        result = check_gate(
            {"id": "integration_points_identified", "description": "desc", "severity": "required"},
            {"architecture.md": "Integration: Stripe API for payments, OAuth for social login"},
            {},
        )
        assert result["passed"] is True

    def test_risk_mitigation_planned_positive(self):
        result = check_gate(
            {"id": "risk_mitigation_planned", "description": "desc", "severity": "recommended"},
            {"architecture.md": "Risk: DB outage. Mitigation: read replicas and circuit breaker"},
            {},
        )
        assert result["passed"] is True


# ═══════════════════════════════════════════════════════════════════════════
# check_gate — implementation phase gates
# ═══════════════════════════════════════════════════════════════════════════


class TestImplementationGates:
    def test_code_compiles_positive(self):
        result = check_gate(
            {"id": "code_compiles", "description": "desc", "severity": "required"},
            {"main.py": 'print("hello")'},
            {},
        )
        assert result["passed"] is True

    def test_code_compiles_negative(self):
        result = check_gate(
            {"id": "code_compiles", "description": "desc", "severity": "required"},
            {},
            {},
        )
        assert result["passed"] is False

    def test_tests_pass_with_results(self):
        result = check_gate(
            {"id": "tests_pass", "description": "desc", "severity": "required"},
            {},
            {"test_results": {"passed": 15, "failed": 0}},
        )
        assert result["passed"] is True

    def test_tests_fail_with_results(self):
        result = check_gate(
            {"id": "tests_pass", "description": "desc", "severity": "required"},
            {},
            {"test_results": {"passed": 14, "failed": 2}},
        )
        assert result["passed"] is False
        assert "2" in result["evidence"]

    def test_requirements_covered_no_tests(self):
        result = check_gate(
            {"id": "requirements_covered", "description": "desc", "severity": "required"},
            {"prd.md": "FR1: system shall authenticate users. FR2: system shall log events."},
            {},
        )
        assert result["passed"] is False

    def test_requirements_covered_with_tests(self):
        result = check_gate(
            {"id": "requirements_covered", "description": "desc", "severity": "required"},
            {"prd.md": "FR1: auth", "test_auth.py": "def test_login(): ..."},
            {},
        )
        assert result["passed"] is True

    def test_no_hardcoded_secrets_clean(self):
        result = check_gate(
            {"id": "no_hardcoded_secrets", "description": "desc", "severity": "required"},
            {"config.py": "API_KEY = os.environ['API_KEY']"},
            {},
        )
        assert result["passed"] is True

    def test_no_hardcoded_secrets_violation(self):
        result = check_gate(
            {"id": "no_hardcoded_secrets", "description": "desc", "severity": "required"},
            {"config.py": 'API_KEY = "sk-1234567890abcdef"'},
            {},
        )
        assert result["passed"] is False
        assert "secret" in result["evidence"].lower() or "hardcoded" in result["evidence"].lower()

    def test_documentation_updated_with_doc_changes(self):
        result = check_gate(
            {"id": "documentation_updated", "description": "desc", "severity": "recommended"},
            {"readme.md": "Updated API docs", "main.py": 'print("hello")'},
            {"changed_files": ["README.md"]},
        )
        assert result["passed"] is True

    def test_documentation_updated_no_changes(self):
        result = check_gate(
            {"id": "documentation_updated", "description": "desc", "severity": "recommended"},
            {"main.py": 'print("hello")'},
            {},
        )
        assert result["passed"] is True  # advisory only


# ═══════════════════════════════════════════════════════════════════════════
# evaluate_all_gates
# ═══════════════════════════════════════════════════════════════════════════


class TestEvaluateAllGates:
    def test_all_analysis_gates_pass_with_good_brief(self):
        """A well-written product brief should pass all required gates."""
        brief = """
Problem Statement: Developers waste 30% of their time searching across tools — this challenge costs enterprises millions in lost productivity.
Target Users: Software engineers at mid-size companies (50-500 devs).
Persona: Alice, a senior engineer managing 5 repos across 3 tools.
Success Metrics: 50% reduction in search time (from 90s to <45s), 90% user satisfaction.
Constraints: Must work within existing toolchains, budget of $500k, launch by Q4 2026.
In Scope: Cross-tool search, unified dashboard.
Out of Scope: Code generation, AI pair programming.
"""
        result = evaluate_all_gates(
            "analysis",
            {"product_brief.md": brief},
            {},
        )
        assert result["phase"] == "analysis"
        assert result["required_passed"] is True
        assert result["recommended_pass_rate"] >= 0.5

    def test_all_required_must_pass(self):
        """If a required gate fails, required_passed is False."""
        result = evaluate_all_gates(
            "analysis",
            {"product_brief.md": ""},  # empty content
            {},
        )
        assert result["required_passed"] is False
        assert result["recommended_pass_rate"] <= 0.5

    def test_structured_result_shape(self):
        """Every gate result has the expected shape."""
        result = evaluate_all_gates(
            "planning",
            {"prd.md": "FR1: login (traces to brief), P0. Acceptance criteria: Given valid creds, user logs in."},
            {},
        )
        for gate in result["gates"]:
            assert "gate_id" in gate
            assert "description" in gate
            assert "severity" in gate
            assert "passed" in gate
            assert isinstance(gate["passed"], bool)
            assert "evidence" in gate
            assert isinstance(gate["evidence"], str)
            assert "notes" in gate

    def test_unknown_gate_falls_back(self):
        """A gate id not in the registry uses the content-presence heuristic."""
        result = check_gate(
            {"id": "some_custom_gate", "description": "custom", "severity": "required"},
            {"some_file.md": "content"},
            {},
        )
        assert "passed" in result
        assert isinstance(result["passed"], bool)
        assert "heuristic" in result["evidence"].lower()


# ═══════════════════════════════════════════════════════════════════════════
# evaluate_all_gates — Reflection Bank Integration
# ═══════════════════════════════════════════════════════════════════════════


class TestReflectionBankIntegration:
    """Verify that evaluate_all_gates cross-references gate results against
    the reflection bank when a profile is supplied."""

    @pytest.fixture
    def project_root(self, tmp_path: Path) -> Path:
        """Scratch project directory with a pre-populated reflection bank."""
        bank_dir = tmp_path / ".hermes"
        bank_dir.mkdir()
        return tmp_path

    def _write_bank(self, project_root: Path, entries_yaml: str) -> None:
        """Write a reflection-bank.yaml into the project."""
        bank_file = project_root / ".hermes" / "reflection-bank.yaml"
        bank_file.write_text(entries_yaml, encoding="utf-8")

    def test_no_profile_gives_null_reflection(self):
        """Without a profile, reflection should be None (backward compat)."""
        result = evaluate_all_gates("analysis", {}, {})
        assert result["reflection"] is None

    def test_profile_with_empty_bank(self, project_root):
        """Profile supplied but bank is empty — all lists empty."""
        result = evaluate_all_gates(
            "analysis",
            {"product_brief.md": "Problem statement. Users defined."},
            {},
            profile="pm",
            project_root=project_root,
        )
        assert result["reflection"] is not None
        assert result["reflection"]["pattern_flags"] == []
        assert result["reflection"]["confidence_adjustments"] == []
        assert result["reflection"]["skill_recommendations"] == []

    def test_recurring_pattern_flag_for_failed_gate(self, project_root):
        """A failed gate with a matching reflection entry (recurrence >= 3)
        produces a pattern flag."""
        self._write_bank(
            project_root,
            """\
- id: 00000000-0000-0000-0000-000000000001
  phase: analysis
  profile: pm
  timestamp: "2026-05-01T00:00:00+00:00"
  summary: "Gate problem_statement_present has failed repeatedly across projects."
  mistake_pattern: problem_statement_present
  severity: high
  phase_of_discovery: analysis
  root_cause: "Briefs submitted without formal problem statements"
  first_principles_vs_heuristic: heuristic
  confidence: 0.9
  recommendation: "Enforce problem-statement template in PRD creation"
  affected_skill: create-prd
  recurrence_count: 4
  fixed_in_phase: null
  requires_adjustment: true
""",
        )
        result = evaluate_all_gates(
            "analysis",
            {"product_brief.md": ""},  # empty → gate fails
            {},
            profile="pm",
            project_root=project_root,
        )
        flags = result["reflection"]["pattern_flags"]
        assert len(flags) == 1
        assert flags[0]["gate_id"] == "problem_statement_present"
        assert flags[0]["mistake_pattern"] == "problem_statement_present"
        assert flags[0]["recurrence_count"] == 4
        assert "Recurring issue" in flags[0]["message"]

    def test_confidence_adjustment_for_passing_gate(self, project_root):
        """A gate that PASSES but has an unresolved recurring issue in the
        reflection bank gets a confidence adjustment."""
        self._write_bank(
            project_root,
            """\
- id: 00000000-0000-0000-0000-000000000002
  phase: solutioning
  profile: architect
  timestamp: "2026-05-15T00:00:00+00:00"
  summary: "Data flow documentation often missing in architecture"
  mistake_pattern: data_flow_documented
  severity: medium
  phase_of_discovery: solutioning
  root_cause: "Architects skip data flow diagrams"
  first_principles_vs_heuristic: heuristic
  confidence: 0.7
  recommendation: "Add data flow checklist to architecture template"
  affected_skill: null
  recurrence_count: 5
  fixed_in_phase: null
  requires_adjustment: false
""",
        )
        # A good architecture with data flow indicators passes the gate
        result = evaluate_all_gates(
            "solutioning",
            {
                "architecture.md": (
                    "Components map to requirements. "
                    "Data flows between services via REST API calls, "
                    "message queue for async events, and database for persistence. "
                    "Tech stack justified. Integration points documented."
                )
            },
            {},
            profile="architect",
            project_root=project_root,
        )
        adjustments = result["reflection"]["confidence_adjustments"]
        assert len(adjustments) == 1
        assert adjustments[0]["gate_id"] == "data_flow_documented"
        assert adjustments[0]["recurrence_count"] == 5
        assert "PASSED" in adjustments[0]["message"]
        assert "Confidence" in adjustments[0]["message"]

    def test_skill_recommendation_when_affected_skill_set(self, project_root):
        """When a reflection entry has affected_skill, a skill update
        recommendation appears in the verdict."""
        self._write_bank(
            project_root,
            """\
- id: 00000000-0000-0000-0000-000000000003
  phase: planning
  profile: pm
  timestamp: "2026-05-20T00:00:00+00:00"
  summary: "PRDs consistently miss acceptance criteria"
  mistake_pattern: acceptance_criteria_per_feature
  severity: high
  phase_of_discovery: planning
  root_cause: "Acceptance criteria not part of default PRD template"
  first_principles_vs_heuristic: heuristic
  confidence: 0.85
  recommendation: "Add GIVEN-WHEN-THEN template to PRD creation flow"
  adjusted_instruction: "Before marking PRD complete, verify each FR has at least one acceptance criterion"
  affected_skill: create-prd
  recurrence_count: 6
  fixed_in_phase: null
  requires_adjustment: true
""",
        )
        result = evaluate_all_gates(
            "planning",
            {"prd.md": ""},  # empty → acceptance criteria gate fails
            {},
            profile="pm",
            project_root=project_root,
        )
        recs = result["reflection"]["skill_recommendations"]
        assert len(recs) == 1
        assert recs[0]["affected_skill"] == "create-prd"
        assert "create-prd" in recs[0]["message"]
        assert recs[0]["adjusted_instruction"] != ""

    def test_multiple_profiles_only_match_current(self, project_root):
        """Entries from other profiles should be ignored."""
        self._write_bank(
            project_root,
            """\
- id: 00000000-0000-0000-0000-000000000004
  phase: analysis
  profile: dev
  timestamp: "2026-05-01T00:00:00+00:00"
  summary: "Dev profile issue"
  mistake_pattern: problem_statement_present
  severity: low
  phase_of_discovery: analysis
  root_cause: "N/A"
  first_principles_vs_heuristic: heuristic
  confidence: 0.5
  recommendation: ""
  affected_skill: null
  recurrence_count: 5
  fixed_in_phase: null
  requires_adjustment: false
""",
        )
        result = evaluate_all_gates(
            "analysis",
            {"product_brief.md": ""},
            {},
            profile="pm",  # different from entry's profile
            project_root=project_root,
        )
        # Entry is for 'dev', we queried for 'pm' — no match
        assert result["reflection"]["pattern_flags"] == []

    def test_fixed_issue_no_confidence_adjustment(self, project_root):
        """An issue that has been fixed (fixed_in_phase is set) should NOT
        trigger a confidence adjustment even if the gate passes."""
        self._write_bank(
            project_root,
            """\
- id: 00000000-0000-0000-0000-000000000005
  phase: analysis
  profile: pm
  timestamp: "2026-05-01T00:00:00+00:00"
  summary: "Previously missing constraints"
  mistake_pattern: constraints_documented
  severity: low
  phase_of_discovery: analysis
  root_cause: "Template didn't include constraints section"
  first_principles_vs_heuristic: heuristic
  confidence: 0.8
  recommendation: ""
  affected_skill: null
  recurrence_count: 3
  fixed_in_phase: analysis
  requires_adjustment: false
""",
        )
        result = evaluate_all_gates(
            "analysis",
            {
                "product_brief.md": (
                    "Problem: test. Users: devs. Metrics: 50%. "
                    "Constraints: must use OSS only."
                )
            },
            {},
            profile="pm",
            project_root=project_root,
        )
        # Gate passes, but reflection entry shows this issue IS fixed —
        # no confidence adjustment should appear
        assert result["reflection"]["confidence_adjustments"] == []
