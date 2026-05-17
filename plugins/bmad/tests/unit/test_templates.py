"""Tests for the BMAD template rendering pipeline (A-8)."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest
import yaml

from plugins.bmad.lib.templates import (
    DETERMINISTIC_VARS,
    PreservingUndefined,
    deterministic_vars,
    render,
)


# ===========================================================================
# PreservingUndefined
# ===========================================================================


class TestPreservingUndefined:
    """Verify the custom Undefined subclass preserves unknown vars as literals."""

    def test_str_renders_as_mustache_literal(self) -> None:
        """__str__ returns ``{{var_name}}`` for an unknown variable."""
        undefined = PreservingUndefined(  # type: ignore[call-arg]
            name="my_var"
        )
        assert str(undefined) == "{{my_var}}"

    def test_html_same_as_str(self) -> None:
        """__html__ returns the same mustache-literal string."""
        undefined = PreservingUndefined(  # type: ignore[call-arg]
            name="html_content"
        )
        assert undefined.__html__() == "{{html_content}}"

    def test_multiple_underscores_in_name(self) -> None:
        """Variable names with underscores are preserved correctly."""
        undefined = PreservingUndefined(  # type: ignore[call-arg]
            name="deeply_nested_var"
        )
        assert str(undefined) == "{{deeply_nested_var}}"

    def test_short_name(self) -> None:
        """Single-character variable names work."""
        undefined = PreservingUndefined(name="x")  # type: ignore[call-arg]
        assert str(undefined) == "{{x}}"


# ===========================================================================
# DETERMINISTIC_VARS frozenset
# ===========================================================================


class TestDeterministicVarsSet:
    """Validate the allow-list frozenset."""

    def test_has_exactly_15_vars(self) -> None:
        """The allow-list contains exactly 15 deterministic variable names."""
        assert len(DETERMINISTIC_VARS) == 15

    def test_includes_expected_vars(self) -> None:
        """All expected variable names are present."""
        expected = frozenset({
            "date",
            "project_name",
            "user_name",
            "project_type",
            "project_level",
            "TIMESTAMP",
            "PROJECT_NAME",
            "START_DATE",
            "SPRINT_GOAL",
            "target_launch",
            "target_completion",
            "tech_stack",
            "product_brief_path",
            "output_folder",
            "project_root",
        })
        assert DETERMINISTIC_VARS == expected

    def test_is_frozenset(self) -> None:
        """The constant is an immutable frozenset."""
        assert isinstance(DETERMINISTIC_VARS, frozenset)


# ===========================================================================
# render() — basic substitution
# ===========================================================================


class TestRenderBasicSubstitution:
    """Core substitution behaviour of the render function."""

    def test_single_var(self) -> None:
        """A single deterministic var is substituted."""
        result = render("Hello {{project_name}}!", {"project_name": "bmad"})
        assert result == "Hello bmad!"

    def test_multiple_vars(self) -> None:
        """Multiple deterministic vars are substituted in one pass."""
        result = render(
            "{{project_name}} ({{project_type}}, level {{project_level}})",
            {"project_name": "my-app", "project_type": "api", "project_level": "2"},
        )
        assert result == "my-app (api, level 2)"

    def test_camelcase_vars(self) -> None:
        """CamelCase deterministic vars (e.g. PROJECT_NAME) are substituted."""
        result = render("{{PROJECT_NAME}}", {"PROJECT_NAME": "FooBar"})
        assert result == "FooBar"

    def test_var_appears_multiple_times(self) -> None:
        """A single deterministic var appearing multiple times is substituted every time."""
        result = render(
            "{{project_name}} / {{project_name}} / {{project_name}}",
            {"project_name": "repeat"},
        )
        assert result == "repeat / repeat / repeat"

    def test_empty_template(self) -> None:
        """An empty template string returns an empty string."""
        result = render("", {"project_name": "x"})
        assert result == ""

    def test_no_vars_in_template(self) -> None:
        """A template with no mustache tags is returned as-is."""
        result = render("plain text without variables", {"project_name": "x"})
        assert result == "plain text without variables"


# ===========================================================================
# render() — unknown var preservation
# ===========================================================================


class TestRenderUnknownVarPreservation:
    """Unknown {{vars}} should be preserved as literal mustache tags."""

    def test_single_unknown_var(self) -> None:
        """An unknown var renders as the literal '{{var_name}}'."""
        result = render("Hello {{user_prompt}}!", {})
        assert "{{user_prompt}}" in result

    def test_mixed_known_and_unknown(self) -> None:
        """Known deterministic vars are substituted; unknown vars are preserved."""
        result = render(
            "Project: {{project_name}}, Goal: {{SPRINT_GOAL}}, Detail: {{user_detail}}",
            {"project_name": "test", "SPRINT_GOAL": "ship it"},
        )
        assert result == "Project: test, Goal: ship it, Detail: {{user_detail}}"

    def test_multiple_unknowns(self) -> None:
        """Multiple unknown vars are all preserved literally."""
        result = render("{{a}} + {{b}} = {{c}}", {})
        assert result == "{{a}} + {{b}} = {{c}}"

    def test_unknown_var_not_in_allow_list(self) -> None:
        """A var whose name is not in DETERMINISTIC_VARS is not substituted
        even if passed in the vars dict."""
        result = render(
            "{{user_prompt}}",
            {"user_prompt": "should not appear", "project_name": "ok"},
        )
        # user_prompt is not in DETERMINISTIC_VARS, so it's preserved literally
        assert "{{user_prompt}}" in result
        # project_name is not in the template, so no impact
        assert "should not appear" not in result

    def test_unknown_var_partial_string(self) -> None:
        """Unknown vars embedded in text are preserved in place."""
        result = render("Prefix {{middle}} suffix", {})
        assert result == "Prefix {{middle}} suffix"


# ===========================================================================
# render() — allow-list filtering
# ===========================================================================


class TestRenderAllowListFiltering:
    """Verify that render() only substitutes vars on the DETERMINISTIC_VARS allow-list."""

    def test_non_deterministic_var_ignored(self) -> None:
        """A var passed that is not in DETERMINISTIC_VARS is silently ignored."""
        result = render(
            "{{secret_sauce}}",
            {"secret_sauce": "extra", "project_name": "main"},
        )
        # secret_sauce is not in allow-list, so preserved as literal
        assert result == "{{secret_sauce}}"

    def test_mixed_allow_and_deny_list(self) -> None:
        """Known vars substituted; unknown vars (even if passed) preserved."""
        result = render(
            "{{project_name}}: {{description}}",
            {"project_name": "app", "description": "a cool app"},
        )
        assert result == "app: {{description}}"

    def test_empty_vars_dict(self) -> None:
        """An empty vars dict means nothing is substituted."""
        result = render("{{project_name}}", {})
        assert result == "{{project_name}}"

    def test_var_name_case_sensitivity(self) -> None:
        """Substitution is case-sensitive — only exact match in DETERMINISTIC_VARS works."""
        # 'Project_Name' is not in the set; 'project_name' is
        result = render(
            "{{Project_Name}} vs {{project_name}}",
            {"Project_Name": "wrong", "project_name": "right"},
        )
        assert result == "{{Project_Name}} vs right"


# ===========================================================================
# render() — edge cases
# ===========================================================================


class TestRenderEdgeCases:
    """Edge cases and Jinja2 behaviour."""

    def test_keep_trailing_newline(self) -> None:
        """Trailing newlines are preserved (keep_trailing_newline=True)."""
        result = render("line1\nline2\n", {"project_name": "x"})
        assert result == "line1\nline2\n"

    def test_no_trailing_newline(self) -> None:
        """Text without a trailing newline stays as-is."""
        result = render("no newline at end", {"project_name": "x"})
        assert result == "no newline at end"

    def test_whitespace_only_template(self) -> None:
        """Whitespace-only template is returned unchanged."""
        result = render("   \n  \t  ", {"project_name": "x"})
        assert result == "   \n  \t  "

    def test_jinja2_comment_is_stripped(self) -> None:
        """Jinja2 {# comment #} blocks are removed in the output."""
        result = render("before{# comment #}after", {"project_name": "x"})
        assert result == "beforeafter"

    def test_jinja2_expression_raises(self) -> None:
        """Non-variable Jinja2 expressions ({% ... %}) are not supported and should raise."""
        with pytest.raises(Exception):
            render("before{% if True %}yes{% endif %}after", {})


# ===========================================================================
# deterministic_vars() — reads config
# ===========================================================================


class TestDeterministicVarsReadsConfig:
    """Verify deterministic_vars() reads bmad/config.yaml correctly."""

    def test_reads_project_name_from_config(self, tmp_project_dir: Path) -> None:
        """The project_name from config.yaml is returned."""
        result = deterministic_vars(tmp_project_dir)
        assert result["project_name"] == "test-project"

    def test_reads_project_type_from_config(self, tmp_project_dir: Path) -> None:
        """The project_type from config.yaml is returned."""
        result = deterministic_vars(tmp_project_dir)
        assert result["project_type"] == "api"

    def test_reads_user_name_from_config(self, tmp_project_dir: Path) -> None:
        """The user_name from config.yaml is returned."""
        result = deterministic_vars(tmp_project_dir)
        assert result["user_name"] == "tester"

    def test_project_level_is_string(self, tmp_project_dir: Path) -> None:
        """project_level is converted to a string regardless of config value."""
        result = deterministic_vars(tmp_project_dir)
        assert result["project_level"] == "1"
        assert isinstance(result["project_level"], str)

    def test_PROJECT_NAME_matches_project_name(self, tmp_project_dir: Path) -> None:
        """PROJECT_NAME (uppercase) has the same value as project_name."""
        result = deterministic_vars(tmp_project_dir)
        assert result["PROJECT_NAME"] == result["project_name"] == "test-project"

    def test_date_is_today_iso(self, tmp_project_dir: Path) -> None:
        """date is today in YYYY-MM-DD format."""
        result = deterministic_vars(tmp_project_dir)
        assert result["date"] == date.today().isoformat()

    def test_TIMESTAMP_is_iso_format(self, tmp_project_dir: Path) -> None:
        """TIMESTAMP matches the current datetime ISO format."""
        result = deterministic_vars(tmp_project_dir)
        expected_prefix = datetime.now().strftime("%Y-%m-%dT%H:%M")
        assert result["TIMESTAMP"].startswith(expected_prefix)

    def test_START_DATE_matches_date(self, tmp_project_dir: Path) -> None:
        """START_DATE equals date (today)."""
        result = deterministic_vars(tmp_project_dir)
        assert result["START_DATE"] == result["date"]

    def test_product_brief_path(self, tmp_project_dir: Path) -> None:
        """product_brief_path is project_dir / planning-artifacts / product-brief.md."""
        result = deterministic_vars(tmp_project_dir)
        expected = str(tmp_project_dir / "planning-artifacts" / "product-brief.md")
        assert result["product_brief_path"] == expected

    def test_output_folder_default(self, tmp_project_dir: Path) -> None:
        """output_folder matches the planning_artifacts config value."""
        result = deterministic_vars(tmp_project_dir)
        assert result["output_folder"] == "planning-artifacts"

    def test_project_root(self, tmp_project_dir: Path) -> None:
        """project_root equals str(project_dir)."""
        result = deterministic_vars(tmp_project_dir)
        assert result["project_root"] == str(tmp_project_dir)

    def test_SPRINT_GOAL_defaults_to_empty(self, tmp_project_dir: Path) -> None:
        """SPRINT_GOAL defaults to empty string when not in config."""
        result = deterministic_vars(tmp_project_dir)
        assert result["SPRINT_GOAL"] == ""

    def test_returns_all_15_keys(self, tmp_project_dir: Path) -> None:
        """The returned dict contains all 15 deterministic variables."""
        result = deterministic_vars(tmp_project_dir)
        assert set(result.keys()) == DETERMINISTIC_VARS

    def test_custom_planning_artifacts_path(self, tmp_project_dir: Path) -> None:
        """A custom planning_artifacts config value changes product_brief_path."""
        import yaml
        cfg_path = tmp_project_dir / "bmad" / "config.yaml"
        cfg = yaml.safe_load(cfg_path.read_text())
        cfg["planning_artifacts"] = "docs"
        cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False))

        result = deterministic_vars(tmp_project_dir)
        expected = str(tmp_project_dir / "docs" / "product-brief.md")
        assert result["product_brief_path"] == expected
        assert result["output_folder"] == "docs"


# ===========================================================================
# deterministic_vars() — missing config defaults
# ===========================================================================


class TestDeterministicVarsMissingConfig:
    """Behaviour when bmad/config.yaml does not exist."""

    def test_empty_project_name(self, tmp_path: Path) -> None:
        """project_name defaults to '' when no config exists."""
        result = deterministic_vars(tmp_path)
        assert result["project_name"] == ""

    def test_default_project_type(self, tmp_path: Path) -> None:
        """project_type defaults to 'other' when no config exists."""
        result = deterministic_vars(tmp_path)
        assert result["project_type"] == "other"

    def test_default_project_level(self, tmp_path: Path) -> None:
        """project_level defaults to '1' when no config exists."""
        result = deterministic_vars(tmp_path)
        assert result["project_level"] == "1"

    def test_empty_user_name(self, tmp_path: Path) -> None:
        """user_name defaults to '' when no config exists."""
        result = deterministic_vars(tmp_path)
        assert result["user_name"] == ""

    def test_empty_SPRINT_GOAL(self, tmp_path: Path) -> None:
        """SPRINT_GOAL defaults to '' when no config exists."""
        result = deterministic_vars(tmp_path)
        assert result["SPRINT_GOAL"] == ""

    def test_default_output_folder(self, tmp_path: Path) -> None:
        """output_folder defaults to 'planning-artifacts' when no config exists."""
        result = deterministic_vars(tmp_path)
        assert result["output_folder"] == "planning-artifacts"

    def test_dates_still_work(self, tmp_path: Path) -> None:
        """date, TIMESTAMP, START_DATE are populated even without config."""
        result = deterministic_vars(tmp_path)
        assert result["date"] == date.today().isoformat()
        assert result["START_DATE"] == result["date"]
        assert "T" in result["TIMESTAMP"]

    def test_all_15_keys_present(self, tmp_path: Path) -> None:
        """All 15 keys are present even when config is missing."""
        result = deterministic_vars(tmp_path)
        assert set(result.keys()) == DETERMINISTIC_VARS


# ===========================================================================
# Integration: deterministic_vars() → render()
# ===========================================================================


class TestIntegrationDeterministicVarsAndRender:
    """End-to-end usage: deterministic_vars() output feeds into render()."""

    def test_render_with_full_var_dict(self, tmp_project_dir: Path) -> None:
        """All deterministic vars are available for substitution in render()."""
        vars_dict = deterministic_vars(tmp_project_dir)
        template = "{{project_name}} ({{project_type}}, level {{project_level}}) — {{date}}"
        result = render(template, vars_dict)
        assert "test-project" in result
        assert "(api, level 1)" in result
        assert date.today().isoformat() in result

    def test_unknown_vars_survive_pipeline(self, tmp_project_dir: Path) -> None:
        """Non-deterministic vars in the template survive rendering with deterministic_vars()."""
        vars_dict = deterministic_vars(tmp_project_dir)
        template = "{{project_name}}: {{user_input}}"
        result = render(template, vars_dict)
        assert result == f"test-project: {{{{user_input}}}}"

    def test_missing_config_renders_sensible_blanks(self, tmp_path: Path) -> None:
        """With no config, render still works and preserves unknowns.

        When ``project_name`` resolves to "", the literal space before the "/"
        in the template is preserved (the renderer does not collapse
        surrounding whitespace).
        """
        vars_dict = deterministic_vars(tmp_path)
        template = "{{project_name}} / {{user_detail}}"
        result = render(template, vars_dict)
        assert result == " / {{user_detail}}"
