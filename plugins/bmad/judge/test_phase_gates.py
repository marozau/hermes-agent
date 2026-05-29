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
    load_criteria,
)


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
