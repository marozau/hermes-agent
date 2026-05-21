"""Integration tests for the code-review reviewer-model resolution.

Verifies the resolution chain:
  1. ``--model <id>`` CLI flag
  2. ``ctx.profile_config["delegation"]["skill_overrides"]["bmad-code-review"]``
     (per-profile override)
  3. Default constant ``_DEFAULT_REVIEWER_MODEL`` (``"claude-opus-4-7"``)
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest
import yaml

from plugins.bmad.commands.code_review import (
    handler,
    _resolve_reviewer_model,
    _read_profile_override,
    _DEFAULT_REVIEWER_MODEL,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _mock_ctx(
    project_dir: Path,
    captured_calls: list,
    *,
    profile_config: dict | None = None,
):
    class MockCtx:
        pass
    ctx = MockCtx()
    ctx.project_dir = str(project_dir)
    ctx.working_directory = str(project_dir)
    ctx.profile_config = profile_config or {}

    def dispatch_tool(name, **kwargs):
        captured_calls.append((name, kwargs))
        return {"task_id": f"t-{len(captured_calls)}", "status": "success", "summary": "ok"}
    ctx.dispatch_tool = dispatch_tool
    return ctx


def _scaffold(tmp_path: Path) -> Path:
    """Minimal BMAD project at level 2 with implementation phase open."""
    (tmp_path / "bmad").mkdir()
    yaml.safe_dump({
        "project_name": "model-override-test",
        "project_type": "api",
        "project_level": 2,
        "user_name": "tester",
    }, open(tmp_path / "bmad" / "config.yaml", "w"), sort_keys=False)
    (tmp_path / "planning-artifacts").mkdir()
    yaml.safe_dump({
        "project": "model-override-test",
        "level": 2,
        "created": "2026-05-21",
        "last_updated": "2026-05-21",
        "phases": {
            "analysis": {"product-brief": "planning-artifacts/brief.md"},
            "planning": {"prd": "planning-artifacts/prd.md"},
            "solutioning": {
                "architecture": "planning-artifacts/arch.md",
                "solutioning-gate-check": "planning-artifacts/sgc.md",
            },
            "implementation": {"sprint-planning": "planning-artifacts/sp.md"},
        },
    }, open(tmp_path / "planning-artifacts" / "workflow-status.yaml", "w"), sort_keys=False)
    return tmp_path


def _stub_diff(diff_text: str = "diff --git a/x.py b/x.py\n+def foo(): return 1\n"):
    def fake_run(cmd, *args, **kwargs):
        if cmd and cmd[0] == "git" and "diff" in cmd:
            class R:
                returncode = 0
                stdout = diff_text
                stderr = ""
            return R()
        raise FileNotFoundError(cmd)
    return mock.patch(
        "plugins.bmad.commands.code_review.subprocess.run", side_effect=fake_run,
    )


def _profile_with_override(reviewer: dict) -> dict:
    """Build a plausible Hermes profile config with code-review override."""
    return {
        "delegation": {
            "model": "deepseek-v4-pro",
            "provider": "custom",
            "base_url": "http://localhost:4000/v1",
            "skill_overrides": {
                "bmad-code-review": reviewer,
            },
        },
    }


# ── _read_profile_override unit tests ────────────────────────────────────────


class TestReadProfileOverride:
    def test_returns_override_when_present(self):
        class C:
            profile_config = _profile_with_override({"model": "claude-opus-4-7"})
        assert _read_profile_override(C()) == {"model": "claude-opus-4-7"}

    def test_returns_none_when_no_profile_config(self):
        class C:
            pass
        assert _read_profile_override(C()) is None

    def test_returns_none_when_no_delegation_block(self):
        class C:
            profile_config = {}
        assert _read_profile_override(C()) is None

    def test_returns_none_when_no_skill_overrides(self):
        class C:
            profile_config = {"delegation": {"model": "deepseek-v4-pro"}}
        assert _read_profile_override(C()) is None

    def test_returns_none_when_skill_not_listed(self):
        class C:
            profile_config = {"delegation": {"skill_overrides": {"other-skill": {"model": "x"}}}}
        assert _read_profile_override(C()) is None

    def test_handles_non_dict_skill_overrides(self):
        class C:
            profile_config = {"delegation": {"skill_overrides": "garbage"}}
        assert _read_profile_override(C()) is None

    def test_handles_non_dict_override(self):
        class C:
            profile_config = {"delegation": {"skill_overrides": {"bmad-code-review": "garbage"}}}
        assert _read_profile_override(C()) is None


# ── _resolve_reviewer_model unit tests ──────────────────────────────────────


class TestResolveReviewerModel:
    def test_cli_override_wins(self):
        class C:
            profile_config = _profile_with_override({"model": "gemini-3.1-pro"})
        out = _resolve_reviewer_model(C(), cli_model="some-cli-model")
        assert out == {"model": "some-cli-model"}

    def test_profile_used_when_no_cli(self):
        class C:
            profile_config = _profile_with_override({"model": "gemini-3.1-pro"})
        out = _resolve_reviewer_model(C(), cli_model=None)
        assert out["model"] == "gemini-3.1-pro"

    def test_default_when_no_cli_and_no_profile(self):
        class C:
            profile_config = {}
        out = _resolve_reviewer_model(C(), cli_model=None)
        assert out["model"] == _DEFAULT_REVIEWER_MODEL
        assert out["model"] == "claude-opus-4-7"

    def test_provider_credentials_propagate(self):
        class C:
            profile_config = _profile_with_override({
                "model": "claude-opus-4-7",
                "provider": "anthropic",
                "base_url": "https://api.anthropic.com",
                "api_key": "sk-ant-xyz",
                "api_mode": "messages",
            })
        out = _resolve_reviewer_model(C(), cli_model=None)
        assert out["model"] == "claude-opus-4-7"
        assert out["provider"] == "anthropic"
        assert out["base_url"] == "https://api.anthropic.com"
        assert out["api_key"] == "sk-ant-xyz"
        assert out["api_mode"] == "messages"

    def test_override_without_model_key_uses_default(self):
        """Override dict exists but has no 'model' key → fall back to default."""
        class C:
            profile_config = _profile_with_override({"provider": "anthropic"})
        out = _resolve_reviewer_model(C(), cli_model=None)
        assert out["model"] == _DEFAULT_REVIEWER_MODEL


# ── Handler-level integration ───────────────────────────────────────────────


class TestHandlerPassesModelDownstream:
    def test_default_model_reaches_dispatch_tool(self, tmp_path: Path):
        project = _scaffold(tmp_path)
        calls: list = []
        ctx = _mock_ctx(project, calls)  # no profile override
        with _stub_diff():
            out = handler(ctx, "")
        # Two reviewers fired (no spec → auditor skipped)
        assert len(calls) == 2
        for _, kw in calls:
            assert kw["model"] == "claude-opus-4-7"
        assert "claude-opus-4-7" in out

    def test_profile_override_reaches_dispatch_tool(self, tmp_path: Path):
        project = _scaffold(tmp_path)
        calls: list = []
        ctx = _mock_ctx(
            project, calls,
            profile_config=_profile_with_override({"model": "gemini-3.1-pro"}),
        )
        with _stub_diff():
            out = handler(ctx, "")
        for _, kw in calls:
            assert kw["model"] == "gemini-3.1-pro"
        assert "gemini-3.1-pro" in out

    def test_cli_model_overrides_profile(self, tmp_path: Path):
        project = _scaffold(tmp_path)
        calls: list = []
        ctx = _mock_ctx(
            project, calls,
            profile_config=_profile_with_override({"model": "gemini-3.1-pro"}),
        )
        with _stub_diff():
            out = handler(ctx, "--model claude-opus-4-7")
        for _, kw in calls:
            assert kw["model"] == "claude-opus-4-7"
        assert "claude-opus-4-7" in out

    def test_provider_override_reaches_dispatch_tool(self, tmp_path: Path):
        project = _scaffold(tmp_path)
        calls: list = []
        ctx = _mock_ctx(
            project, calls,
            profile_config=_profile_with_override({
                "model": "claude-opus-4-7",
                "provider": "anthropic",
                "base_url": "https://api.anthropic.com",
                "api_key": "sk-ant-xyz",
            }),
        )
        with _stub_diff():
            handler(ctx, "")
        for _, kw in calls:
            assert kw["model"] == "claude-opus-4-7"
            assert kw["provider"] == "anthropic"
            assert kw["base_url"] == "https://api.anthropic.com"
            assert kw["api_key"] == "sk-ant-xyz"

    def test_different_profiles_produce_different_models(self, tmp_path: Path):
        """Same project, two different profiles → two different reviewer models."""
        project = _scaffold(tmp_path)
        # bmad profile: deepseek for default, opus for code review
        bmad_calls: list = []
        bmad_ctx = _mock_ctx(
            project, bmad_calls,
            profile_config=_profile_with_override({"model": "claude-opus-4-7"}),
        )
        with _stub_diff():
            handler(bmad_ctx, "")

        # security-audit profile: opus default, gemini for code review (heterogeneous)
        audit_calls: list = []
        audit_ctx = _mock_ctx(
            project, audit_calls,
            profile_config=_profile_with_override({
                "model": "gemini-3.1-pro",
                "provider": "google",
            }),
        )
        with _stub_diff():
            handler(audit_ctx, "")

        # Verify each profile produced a different reviewer model
        assert all(kw["model"] == "claude-opus-4-7" for _, kw in bmad_calls)
        assert all(kw["model"] == "gemini-3.1-pro" for _, kw in audit_calls)


# ── --model arg parsing (unchanged from before) ─────────────────────────────


class TestArgsParsesModel:
    def test_model_arg_in_args(self):
        from plugins.bmad.commands.code_review import _parse_args
        p = _parse_args("--model claude-opus-4-7")
        assert p["model"] == "claude-opus-4-7"

    def test_model_combined_with_other_flags(self):
        from plugins.bmad.commands.code_review import _parse_args
        p = _parse_args("--diff main..HEAD --spec spec.md --model claude-opus-4-7")
        assert p["model"] == "claude-opus-4-7"
        assert p["diff_rev"] == "main..HEAD"
        assert p["spec_path"] == "spec.md"
