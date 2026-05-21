"""Tests for /bmad:party-mode handler — inline + fan-out modes."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from plugins.bmad.commands.party_mode import (
    handler,
    _inline,
    _select_personas,
    _build_persona_goal,
    _format_results,
    _MANIFEST_PATH,
)


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def fake_manifest() -> list[dict]:
    return [
        {"name": "analyst", "displayName": "Mary", "title": "Business Analyst",
         "icon": "📊", "role": "BA", "communicationStyle": "treasure-hunter",
         "principles": "evidence-based", "module": "core"},
        {"name": "architect", "displayName": "Winston", "title": "Architect",
         "icon": "🏗️", "role": "system architect", "communicationStyle": "calm pragmatic",
         "principles": "boring tech", "module": "core"},
        {"name": "brainstorming-coach", "displayName": "Carson", "title": "Brainstormer",
         "icon": "🧠", "role": "facilitator", "communicationStyle": "improv coach",
         "principles": "yes-and", "module": "cis"},
        {"name": "agent-builder", "displayName": "Bond", "title": "Agent Builder",
         "icon": "🤖", "role": "agent architect", "communicationStyle": "technical",
         "principles": "BMAD compliance", "module": "bmb"},
        {"name": "qa", "displayName": "Quinn", "title": "QA",
         "icon": "🧪", "role": "test architect", "communicationStyle": "skeptical",
         "principles": "shift-left", "module": "tea"},
        {"name": "pm", "displayName": "John", "title": "PM",
         "icon": "📋", "role": "product manager", "communicationStyle": "stakeholder-driven",
         "principles": "user value", "module": "core"},
    ]


@pytest.fixture
def mock_ctx():
    class Ctx:
        pass
    return Ctx()


# ── _select_personas ──────────────────────────────────────────────────────


class TestSelectPersonas:
    def test_returns_up_to_cap(self, fake_manifest):
        sel = _select_personas(fake_manifest, cap=3)
        assert len(sel) == 3

    def test_returns_all_when_cap_exceeds_manifest(self, fake_manifest):
        sel = _select_personas(fake_manifest, cap=10)
        assert len(sel) == 6

    def test_prefers_cross_module_diversity(self, fake_manifest):
        """Round-robin must cover multiple modules before doubling up."""
        sel = _select_personas(fake_manifest, cap=4)
        modules = [p["module"] for p in sel]
        assert len(set(modules)) >= 3, f"Expected >=3 modules in {modules}"

    def test_empty_manifest_returns_empty(self):
        assert _select_personas([], cap=5) == []

    def test_non_list_manifest_returns_empty(self):
        assert _select_personas({}, cap=5) == []

    def test_skips_non_dict_entries(self):
        manifest = [
            "not-a-dict",
            {"name": "real", "module": "core"},
            None,
        ]
        sel = _select_personas(manifest, cap=5)
        assert len(sel) == 1
        assert sel[0]["name"] == "real"


# ── _build_persona_goal ───────────────────────────────────────────────────


class TestBuildPersonaGoal:
    def test_includes_persona_voice_attributes(self):
        persona = {
            "displayName": "Sophia",
            "title": "Storyteller",
            "communicationStyle": "bardic",
            "principles": "narrative truth",
            "role": "creative narrative",
        }
        goal = _build_persona_goal(persona, "the migration plan")
        assert "Sophia" in goal
        assert "Storyteller" in goal
        assert "bardic" in goal
        assert "narrative truth" in goal
        assert "creative narrative" in goal
        assert "the migration plan" in goal

    def test_falls_back_to_name_if_no_displayName(self):
        persona = {"name": "fallback-name"}
        goal = _build_persona_goal(persona, "x")
        assert "fallback-name" in goal


# ── _format_results ───────────────────────────────────────────────────────


class TestFormatResults:
    def test_renders_round_table_format(self, fake_manifest):
        personas = fake_manifest[:2]
        results = [
            {"summary": "Mary's take.", "status": "success"},
            {"summary": "Winston's take.", "status": "success"},
        ]
        out = _format_results("topic-x", personas, results)
        assert "PARTY MODE" in out
        assert "topic-x" in out
        assert "Mary" in out and "Mary's take." in out
        assert "Winston" in out and "Winston's take." in out

    def test_renders_failure_marker_on_child_error(self, fake_manifest):
        personas = fake_manifest[:1]
        results = [{"summary": "boom", "status": "failure", "error": True}]
        out = _format_results("t", personas, results)
        assert "_(sub-agent failed" in out


# ── Inline mode ───────────────────────────────────────────────────────────


class TestInline:
    def test_substitutes_topic_into_body(self):
        out = _inline("how should we test the rocket?")
        assert "how should we test the rocket?" in out
        assert "{args}" not in out

    def test_returns_body_text(self):
        out = _inline("anything")
        # The body explains the protocol
        assert "PARTY MODE" in out.upper() or "round-table" in out.lower()


class TestHandler:
    def test_default_mode_is_inline(self, mock_ctx):
        out = handler(mock_ctx, "discuss the migration plan")
        assert "discuss the migration plan" in out
        # No fan-out preamble in inline mode
        assert "fan-out" not in out.lower()

    def test_handles_empty_args(self, mock_ctx):
        out = handler(mock_ctx, "")
        # Either prompts for topic or includes the placeholder
        assert "topic" in out.lower()

    def test_fan_out_flag_routes_to_fan_out(self, mock_ctx, fake_manifest, tmp_path):
        """When --fan-out is passed and manifest loads, fan-out branch runs."""
        # Stub dispatch_tool
        calls = []

        class Ctx:
            def dispatch_tool(self, name, **kw):
                calls.append((name, kw))
                return {
                    "task_id": f"t-{len(calls)}",
                    "status": "success",
                    "summary": f"Response #{len(calls)}",
                }
        ctx = Ctx()

        # Patch the manifest path and yaml
        import yaml
        manifest_text = yaml.safe_dump(fake_manifest, sort_keys=False)
        fake_path = tmp_path / "agent-manifest.yaml"
        fake_path.write_text(manifest_text)

        with mock.patch(
            "plugins.bmad.commands.party_mode._MANIFEST_PATH", fake_path,
        ):
            out = handler(ctx, "--fan-out the architecture decision")

        # Should have invoked delegate_task at least once
        assert len(calls) >= 1
        assert all(name == "delegate_task" for name, _ in calls)
        # Output renders in fan-out format
        assert "fan-out" in out.lower() or "PARTY MODE" in out

    def test_fan_out_falls_back_inline_when_manifest_missing(self, mock_ctx, tmp_path):
        """If the manifest file doesn't exist, fall back gracefully."""
        missing = tmp_path / "no-such-manifest.yaml"
        with mock.patch(
            "plugins.bmad.commands.party_mode._MANIFEST_PATH", missing,
        ):
            out = handler(mock_ctx, "--fan-out some topic")
        assert "some topic" in out
        # Should fall back to inline (which doesn't say "(fan-out)")
        assert "manifest not found" in out.lower() or "Falling back" in out

    def test_fan_out_falls_back_inline_when_yaml_parse_fails(
        self, mock_ctx, tmp_path,
    ):
        bad = tmp_path / "bad.yaml"
        bad.write_text(":\n: [invalid: yaml")

        class Ctx:
            def dispatch_tool(self, name, **kw):
                return {"summary": "x", "status": "success"}

        ctx = Ctx()
        with mock.patch(
            "plugins.bmad.commands.party_mode._MANIFEST_PATH", bad,
        ):
            out = handler(ctx, "--fan-out anything")
        # Output produced (some form of body), no exception raised
        assert isinstance(out, str)
        assert len(out) > 50
