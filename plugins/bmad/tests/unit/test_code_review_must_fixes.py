"""Regression tests for MUST-FIX items from the 2026-05-21 code review.

Each test class names the M-N finding it pins. If any of these regress,
the relevant production codepath has broken in a way the previous test
suite didn't catch.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest import mock

import pytest
import yaml

from plugins.bmad.commands import code_review
from plugins.bmad.commands.code_review import (
    handler,
    _parse_args,
    _capture_diff,
)


# ── Helpers (mirrors existing fixtures) ─────────────────────────────────────


def _mock_ctx(project_dir: Path, captured_calls: list, *, profile_config: dict | None = None):
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
    (tmp_path / "bmad").mkdir()
    yaml.safe_dump({
        "project_name": "must-fix-test",
        "project_type": "api",
        "project_level": 2,
        "user_name": "tester",
    }, open(tmp_path / "bmad" / "config.yaml", "w"), sort_keys=False)
    (tmp_path / "planning-artifacts").mkdir()
    yaml.safe_dump({
        "project": "must-fix-test", "level": 2,
        "created": "2026-05-21", "last_updated": "2026-05-21",
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


# ───────────────────────────────────────────────────────────────────────────
# M-3 — shlex.split must not crash on unbalanced quotes
# ───────────────────────────────────────────────────────────────────────────


class TestM3_ShlexNeverCrashes:
    def test_unbalanced_quote_returns_error(self):
        result = _parse_args('--spec "docs/spec.md')
        # Should NOT raise; should populate _error
        assert result["_error"] is not None
        assert "unbalanced" in result["_error"].lower() or "quot" in result["_error"].lower()

    def test_empty_args_returns_clean(self):
        result = _parse_args("")
        assert result["_error"] is None
        assert result["no_fanout"] is False

    def test_handler_surfaces_parse_error(self, tmp_path: Path):
        project = _scaffold(tmp_path)
        calls: list = []
        ctx = _mock_ctx(project, calls)
        out = handler(ctx, '--spec "broken')
        # No reviewers should fire when parsing failed
        assert len(calls) == 0
        assert "unbalanced" in out.lower() or "quot" in out.lower() or "could not parse" in out.lower()


# ───────────────────────────────────────────────────────────────────────────
# M-4 — git diff failure surfaced, not silently shown as "No diff"
# ───────────────────────────────────────────────────────────────────────────


class TestM4_GitDiffErrorSurfaced:
    def _stub_git_failure(self, returncode: int = 128, stderr: str = "fatal: bad revision 'nope'"):
        def fake_run(cmd, *args, **kwargs):
            if cmd and cmd[0] == "git" and "diff" in cmd:
                class R:
                    pass
                R.returncode = returncode
                R.stdout = ""
                R.stderr = stderr
                return R()
            raise FileNotFoundError(cmd)
        return mock.patch("plugins.bmad.commands.code_review.subprocess.run", side_effect=fake_run)

    def test_capture_diff_returns_error_on_nonzero_returncode(self, tmp_path: Path):
        with self._stub_git_failure():
            text, meta = _capture_diff(tmp_path, "bad..rev")
        assert text == ""
        assert meta["error"]
        assert "bad revision" in meta["error"]

    def test_handler_reports_git_failure_distinctly(self, tmp_path: Path):
        project = _scaffold(tmp_path)
        calls: list = []
        ctx = _mock_ctx(project, calls)
        with self._stub_git_failure():
            out = handler(ctx, "--diff bad..rev")
        assert len(calls) == 0  # no fan-out happened
        assert "git diff" in out.lower() and "failed" in out.lower()
        assert "bad revision" in out  # actual git stderr surfaced
        # Must NOT say "no diff" (that was the pre-fix behavior)
        assert "no diff found" not in out.lower()

    def test_truly_empty_diff_still_says_no_diff(self, tmp_path: Path):
        """Genuine empty (returncode==0, stdout=='') still gets the clean message."""
        project = _scaffold(tmp_path)
        calls: list = []
        ctx = _mock_ctx(project, calls)

        def fake_run(cmd, *args, **kwargs):
            if cmd and cmd[0] == "git" and "diff" in cmd:
                class R:
                    returncode = 0
                    stdout = ""
                    stderr = ""
                return R()
            raise FileNotFoundError(cmd)
        with mock.patch(
            "plugins.bmad.commands.code_review.subprocess.run", side_effect=fake_run,
        ):
            out = handler(ctx, "")
        assert "no diff found" in out.lower()
        assert len(calls) == 0


# ───────────────────────────────────────────────────────────────────────────
# M-5 — spec path traversal must be rejected
# ───────────────────────────────────────────────────────────────────────────


class TestM5_SpecPathTraversal:
    def _stub_diff(self, diff_text="diff --git a/x b/x\n+pass\n"):
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

    def test_relative_traversal_rejected(self, tmp_path: Path):
        project = _scaffold(tmp_path)
        # Create a "secret" file OUTSIDE the project
        outside = tmp_path.parent / "outside-secrets.md"
        outside.write_text("SECRET")
        try:
            calls: list = []
            ctx = _mock_ctx(project, calls)
            with self._stub_diff():
                out = handler(ctx, "--spec ../outside-secrets.md")
            assert len(calls) == 0  # no fan-out on rejected path
            assert "escape" in out.lower() or "outside" in out.lower()
            assert "SECRET" not in out  # leak check
        finally:
            if outside.exists():
                outside.unlink()

    def test_absolute_outside_rejected(self, tmp_path: Path):
        project = _scaffold(tmp_path)
        # Use /tmp (definitely outside project)
        outside_abs = Path("/tmp") / f"bmad-traversal-test-{os.getpid()}.md"
        outside_abs.write_text("SECRET")
        try:
            calls: list = []
            ctx = _mock_ctx(project, calls)
            with self._stub_diff():
                out = handler(ctx, f"--spec {outside_abs}")
            assert len(calls) == 0
            assert "escape" in out.lower() or "outside" in out.lower()
        finally:
            if outside_abs.exists():
                outside_abs.unlink()

    def test_inside_project_accepted(self, tmp_path: Path):
        project = _scaffold(tmp_path)
        spec = project / "spec.md"
        spec.write_text("# Spec\nAC-1: x")
        calls: list = []
        ctx = _mock_ctx(project, calls)
        with self._stub_diff():
            out = handler(ctx, f"--spec {spec.name}")
        # Auditor fires (3 reviewers total)
        assert len(calls) == 3

    def test_spec_is_directory_rejected(self, tmp_path: Path):
        project = _scaffold(tmp_path)
        (project / "specs-dir").mkdir()
        calls: list = []
        ctx = _mock_ctx(project, calls)
        with self._stub_diff():
            out = handler(ctx, "--spec specs-dir")
        assert "directory" in out.lower()
        assert len(calls) == 0


# ───────────────────────────────────────────────────────────────────────────
# M-6 — non-UTF-8 git stdout must not crash subprocess.run
# ───────────────────────────────────────────────────────────────────────────


class TestM6_NonUtf8DiffHandling:
    def test_capture_diff_uses_errors_replace(self, tmp_path: Path):
        """subprocess.run is invoked with errors='replace' so non-UTF-8 bytes
        decode lossily rather than raising UnicodeDecodeError."""
        captured_kwargs: dict = {}

        def fake_run(cmd, *args, **kwargs):
            captured_kwargs.update(kwargs)
            class R:
                returncode = 0
                stdout = "diff --git a/x b/x\n+\xfe\xfdcontent\n"  # invalid bytes if read raw
                stderr = ""
            return R()

        with mock.patch(
            "plugins.bmad.commands.code_review.subprocess.run", side_effect=fake_run,
        ):
            text, meta = _capture_diff(tmp_path, "HEAD~1..HEAD")

        assert captured_kwargs.get("errors") == "replace", (
            "M-6: subprocess.run must be called with errors='replace' "
            "to survive non-UTF-8 binary diff hunks"
        )
        assert text != ""  # got the diff back

    def test_capture_diff_handles_permission_error(self, tmp_path: Path):
        """OSError/PermissionError from subprocess no longer escapes."""
        def fake_run(cmd, *args, **kwargs):
            raise PermissionError("denied")
        with mock.patch(
            "plugins.bmad.commands.code_review.subprocess.run", side_effect=fake_run,
        ):
            text, meta = _capture_diff(tmp_path, "HEAD~1..HEAD")
        assert text == ""
        assert "OS error" in meta["error"] or "denied" in meta["error"]


# ───────────────────────────────────────────────────────────────────────────
# M-1 — verify the 2 review-hunter skills are in the repo source tree
# ───────────────────────────────────────────────────────────────────────────


class TestM1_SkillsInRepoSourceTree:
    """Pin that the review-hunter skills are version-controlled, not just
    deployed locally. Without this, a clean redeploy silently drops them.

    Path resolved from this test file's location so the test runs both in
    the live install and in the dev fork — no ``Path.home()`` coupling.

    File path layout:
        <repo-root>/plugins/bmad/tests/unit/test_code_review_must_fixes.py
        parents[0] = unit/
        parents[1] = tests/
        parents[2] = bmad/
        parents[3] = plugins/
        parents[4] = <repo-root>
    """

    REPO_SKILLS = Path(__file__).resolve().parents[4] / "skills" / "bmad" / "bmm"

    def test_review_adversarial_general_in_repo(self):
        skill = self.REPO_SKILLS / "review-adversarial-general" / "SKILL.md"
        assert skill.exists(), (
            f"M-1: review-adversarial-general/SKILL.md missing from repo source tree "
            f"at {skill}. Without this, a clean deploy will not install the skill "
            f"and /bmad:code-review fan-out will silently degrade."
        )
        text = skill.read_text(encoding="utf-8")
        assert "name: review-adversarial-general" in text
        assert "Blind Hunter" in text or "Adversarial Review" in text

    def test_review_edge_case_hunter_in_repo(self):
        skill = self.REPO_SKILLS / "review-edge-case-hunter" / "SKILL.md"
        assert skill.exists(), (
            f"M-1: review-edge-case-hunter/SKILL.md missing from repo source tree "
            f"at {skill}. Without this, a clean deploy will not install the skill."
        )
        text = skill.read_text(encoding="utf-8")
        assert "name: review-edge-case-hunter" in text
        assert "Edge Case Hunter" in text or "path tracer" in text


# ───────────────────────────────────────────────────────────────────────────
# M-2 — --non-interactive must NOT call input()
# ───────────────────────────────────────────────────────────────────────────


class TestM2_NonInteractiveFlag:
    """The CLI fixes in __init__.py are tested by exercising the handler logic.

    Direct call requires constructing an argparse Namespace and stubbing
    sys.exit. The fix is verified by reading the file: the input() call
    is now gated behind ``if not non_interactive``.
    """

    def test_non_interactive_path_skips_input(self):
        """Smoke-test: read __init__.py source and verify the guard order.

        Path resolved from this test file's location — portable across live
        and dev-fork checkouts. ``parents[2]`` is the bmad/ plugin root:

            <repo>/plugins/bmad/tests/unit/test_code_review_must_fixes.py
            parents[0] = unit/
            parents[1] = tests/
            parents[2] = bmad/        ← we want __init__.py here
            parents[3] = plugins/
        """
        init_file = Path(__file__).resolve().parents[2] / "__init__.py"
        src = init_file.read_text(encoding="utf-8")
        # The non_interactive check should appear BEFORE any input() call.
        non_int_idx = src.find("non_interactive = bool")
        input_idx = src.find('input("Project name:')
        assert non_int_idx != -1, "non_interactive check missing"
        assert input_idx != -1, "input() call missing"
        assert non_int_idx < input_idx, (
            "M-2: non_interactive must be resolved BEFORE input() can fire. "
            "Reorder so the check fires first."
        )
        # And there must be a sys.exit(3) inside the non_interactive branch
        # before the input() call (lexical check).
        slice_before_input = src[non_int_idx:input_idx]
        assert "sys.exit(3)" in slice_before_input, (
            "M-2: non_interactive branch must sys.exit(3) before input() can fire"
        )
