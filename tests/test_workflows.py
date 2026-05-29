#!/usr/bin/env python3
"""
Tests for hermes_cli/workflows.py — YAML-based workflow persistence layer.

Tests cover:
  - YAML serialization roundtrip
  - Personal workflow save/load/delete
  - Project workflow override behavior
  - Script generation (pipeline, fan-out, adversarial patterns)
  - Resume-aware script generation
  - Slash command scanning (via agent/skill_commands.py)

Run with:  python -m pytest tests/test_workflows.py -v
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml


class TestWorkflowYamlSerialization(unittest.TestCase):
    """Test YAML serialization / deserialization of WorkflowDefinition."""

    def setUp(self):
        from hermes_cli.workflows import (
            PhaseSpec,
            WorkflowDefinition,
        )
        self.PhaseSpec = PhaseSpec
        self.WorkflowDefinition = WorkflowDefinition

    def test_roundtrip_pipeline(self):
        """Pipeline workflow survives YAML roundtrip."""
        from hermes_cli.workflows import workflow_to_yaml, workflow_from_yaml

        wf = self.WorkflowDefinition(
            name="test-pipeline",
            description="A test pipeline",
            pattern="pipeline",
            phases=[
                self.PhaseSpec(name="research", goal="Research {goal}", toolsets=["web"]),
                self.PhaseSpec(name="draft", goal="Write draft", context_from="research", toolsets=["file"]),
                self.PhaseSpec(name="review", goal="Review draft", context_from="draft", toolsets=["file", "web"], review_agents=2),
            ],
            settings={"timeout_minutes": 5},
        )

        yaml_str = workflow_to_yaml(wf)
        parsed = workflow_from_yaml(yaml_str)

        self.assertEqual(parsed.name, "test-pipeline")
        self.assertEqual(parsed.pattern, "pipeline")
        self.assertEqual(len(parsed.phases), 3)
        self.assertEqual(parsed.phases[0].name, "research")
        self.assertEqual(parsed.phases[0].goal, "Research {goal}")
        self.assertEqual(parsed.phases[2].review_agents, 2)
        self.assertEqual(parsed.settings.get("timeout_minutes"), 5)

    def test_roundtrip_fan_out(self):
        """Fan-out-fan-in workflow with parallel tasks survives roundtrip."""
        from hermes_cli.workflows import workflow_to_yaml, workflow_from_yaml

        wf = self.WorkflowDefinition(
            name="fan-out-test",
            description="Parallel research",
            pattern="fan-out-fan-in",
            phases=[
                self.PhaseSpec(
                    name="research",
                    goal="Research topics",
                    is_parallel=True,
                    parallel_tasks=[
                        {"goal": "Research topic A", "toolsets": ["web"]},
                        {"goal": "Research topic B", "toolsets": ["web"]},
                    ],
                ),
                self.PhaseSpec(name="synthesize", goal="Synthesize findings", toolsets=["file"]),
            ],
        )

        yaml_str = workflow_to_yaml(wf)
        parsed = workflow_from_yaml(yaml_str)

        self.assertEqual(parsed.pattern, "fan-out-fan-in")
        self.assertTrue(parsed.phases[0].is_parallel)
        self.assertEqual(len(parsed.phases[0].parallel_tasks), 2)
        self.assertEqual(parsed.phases[0].parallel_tasks[0]["goal"], "Research topic A")

    def test_minimal_workflow(self):
        """Minimal workflow with only required fields."""
        from hermes_cli.workflows import workflow_to_yaml, workflow_from_yaml

        yaml_str = """name: minimal
description: Bare minimum
pattern: pipeline
phases:
  - name: step1
"""

        parsed = workflow_from_yaml(yaml_str)
        self.assertEqual(parsed.name, "minimal")
        self.assertEqual(len(parsed.phases), 1)
        self.assertEqual(parsed.phases[0].toolsets, ["web", "file"])  # default


class TestWorkflowPersistence(unittest.TestCase):
    """Test file-based save/load/delete with dual-directory resolution."""

    def setUp(self):
        from hermes_cli.workflows import (
            PhaseSpec,
            WorkflowDefinition,
        )
        self.PhaseSpec = PhaseSpec
        self.WorkflowDefinition = WorkflowDefinition
        self._tmp_personal = tempfile.TemporaryDirectory()
        self._personal_dir = Path(self._tmp_personal.name)

    def tearDown(self):
        self._tmp_personal.cleanup()

    def _make_wf(self, name="test-wf"):
        return self.WorkflowDefinition(
            name=name,
            description="Test workflow",
            pattern="pipeline",
            phases=[
                self.PhaseSpec(name="research", goal="Research {goal}", toolsets=["web"]),
                self.PhaseSpec(name="draft", goal="Write draft", context_from="research", toolsets=["file"]),
            ],
        )

    def test_save_and_load_personal(self):
        """Save to personal dir and load back."""
        from hermes_cli.workflows import workflow_to_yaml, workflow_from_yaml

        # Save directly to personal dir
        wf = self._make_wf()
        yaml_str = workflow_to_yaml(wf)
        file_path = self._personal_dir / "test-wf.yaml"
        file_path.write_text(yaml_str)

        # Load from file
        loaded = workflow_from_yaml(
            file_path.read_text(),
            source_path=file_path,
            source_tier="personal",
        )
        self.assertEqual(loaded.name, "test-wf")
        self.assertEqual(loaded.source_tier, "personal")
        self.assertEqual(len(loaded.phases), 2)

    def test_list_workflows_with_override(self):
        """Project workflow overrides personal workflow with same name."""
        from hermes_cli.workflows import (
            workflow_to_yaml,
            workflow_from_yaml,
            _personal_workflows_dir,
            _project_workflows_dir,
        )

        # Create personal workflow
        wf_personal = self._make_wf("my-wf")
        wf_personal.description = "Personal version — 2 phases"
        personal_yaml = workflow_to_yaml(wf_personal)
        (self._personal_dir / "my-wf.yaml").write_text(personal_yaml)

        # Create project directory with override
        with tempfile.TemporaryDirectory() as tmp_proj:
            proj_root = Path(tmp_proj)
            proj_hermes = proj_root / ".hermes" / "workflows"
            proj_hermes.mkdir(parents=True)

            wf_project = self._make_wf("my-wf")
            wf_project.description = "Project override — 1 phase"
            wf_project.phases = [wf_project.phases[0]]  # Only 1 phase
            project_yaml = workflow_to_yaml(wf_project)
            (proj_hermes / "my-wf.yaml").write_text(project_yaml)

            # Now test with cwd inside project — should get project version
            with patch("hermes_cli.workflows._personal_workflows_dir",
                       return_value=self._personal_dir):
                with patch("hermes_cli.workflows._project_workflows_dir",
                           return_value=proj_hermes):
                    from hermes_cli.workflows import list_workflows
                    wfs = list_workflows(cwd=proj_root)
                    self.assertIn("my-wf", wfs)
                    wf = wfs["my-wf"]
                    self.assertEqual(wf.source_tier, "project")
                    self.assertEqual(wf.description, "Project override — 1 phase")
                    self.assertEqual(len(wf.phases), 1)

    def test_delete_workflow(self):
        """Delete removes the file."""
        from hermes_cli.workflows import delete_workflow

        file_path = self._personal_dir / "to-delete.yaml"
        file_path.write_text("name: to-delete\ndescription: temp\npattern: pipeline\nphases: []\n")

        self.assertTrue(file_path.exists())

        with patch("hermes_cli.workflows._personal_workflows_dir",
                   return_value=self._personal_dir):
            with patch("hermes_cli.workflows._project_workflows_dir",
                       return_value=None):
                result = delete_workflow("to-delete")
                self.assertTrue(result)
                self.assertFalse(file_path.exists())

    def test_delete_nonexistent(self):
        """Delete non-existent workflow returns False."""
        from hermes_cli.workflows import delete_workflow

        with patch("hermes_cli.workflows._personal_workflows_dir",
                   return_value=self._personal_dir):
            with patch("hermes_cli.workflows._project_workflows_dir",
                       return_value=None):
                result = delete_workflow("no-such-workflow")
                self.assertFalse(result)


class TestScriptGeneration(unittest.TestCase):
    """Test orchestration script generation from workflow definitions."""

    def setUp(self):
        from hermes_cli.workflows import PhaseSpec, WorkflowDefinition
        self.PhaseSpec = PhaseSpec
        self.WorkflowDefinition = WorkflowDefinition

    def _make_pipeline_wf(self):
        return self.WorkflowDefinition(
            name="test-pipeline",
            description="Test pipeline",
            pattern="pipeline",
            phases=[
                self.PhaseSpec(name="research", goal="Research: {goal}", toolsets=["web"]),
                self.PhaseSpec(name="draft", goal="Write draft", context_from="research", toolsets=["file"]),
                self.PhaseSpec(name="review", goal="Review", context_from="draft", toolsets=["file", "web"], review_agents=2),
            ],
        )

    def test_generate_pipeline_script(self):
        """Generated pipeline script contains expected structure."""
        from hermes_cli.workflows import generate_orchestration_script

        wf = self._make_pipeline_wf()
        script = generate_orchestration_script(wf, user_goal="test goal", workflow_id="wf-001")

        # Should be valid Python
        compile(script, "<script>", "exec")

        # Should contain key elements
        self.assertIn("delegate_task", script)
        self.assertIn("checkpoint_save", script)
        self.assertIn("checkpoint_load", script)
        self.assertIn('"status": "complete"', script)
        self.assertIn('"research"', script)
        self.assertIn('"draft"', script)
        self.assertIn('"review"', script)
        self.assertIn("safe_delegate", script)

    def test_generate_resume_script(self):
        """Resume-aware script checks for completed phases."""
        from hermes_cli.workflows import generate_orchestration_script

        wf = self._make_pipeline_wf()
        script = generate_orchestration_script(
            wf, user_goal="test", workflow_id="wf-001", resume=True
        )

        # Resume scripts check for completed phases
        self.assertIn("checkpoint_load", script)
        self.assertIn('"completed"', script)
        self.assertIn("not in completed", script)

    def test_generate_fan_out_script(self):
        """Fan-out script has parallel delegate_task calls."""
        from hermes_cli.workflows import generate_orchestration_script

        wf = self.WorkflowDefinition(
            name="fan-out",
            description="Parallel",
            pattern="fan-out-fan-in",
            phases=[
                self.PhaseSpec(
                    name="research",
                    goal="Research",
                    is_parallel=True,
                    parallel_tasks=[
                        {"goal": "Task A", "toolsets": ["web"]},
                        {"goal": "Task B", "toolsets": ["web"]},
                    ],
                ),
                self.PhaseSpec(name="synthesize", goal="Synthesize", toolsets=["file"]),
            ],
        )

        script = generate_orchestration_script(wf, user_goal="test", workflow_id="wf-002")
        compile(script, "<script>", "exec")

        # Fan-out uses delegate_task(tasks=[...]) with goal values
        self.assertIn("delegate_task(tasks=[", script)
        self.assertIn("Task A", script)
        self.assertIn("Task B", script)

    def test_script_handles_user_goal_template(self):
        """{goal} placeholder is replaced with user_goal."""
        from hermes_cli.workflows import generate_orchestration_script

        wf = self._make_pipeline_wf()
        script = generate_orchestration_script(wf, user_goal="analyze Rust", workflow_id="wf-003")

        self.assertIn("analyze Rust", script)
        self.assertNotIn("{goal}", script)

    def test_extract_workflow_from_prompt(self):
        """Extract workflow from explicit phase descriptions."""
        from hermes_cli.workflows import extract_workflow_from_prompt

        wf = extract_workflow_from_prompt(
            name="extracted",
            description="Extracted from a run",
            pattern="pipeline",
            phases=[
                {"name": "step1", "goal": "Do step 1", "toolsets": ["web"]},
                {"name": "step2", "goal": "Do step 2", "toolsets": ["file"], "review_agents": 1},
            ],
            settings={"timeout_minutes": 10},
        )

        self.assertEqual(wf.name, "extracted")
        self.assertEqual(len(wf.phases), 2)
        self.assertEqual(wf.phases[1].review_agents, 1)
        self.assertEqual(wf.settings["timeout_minutes"], 10)

    def test_script_handles_empty_user_goal(self):
        """Script generation works without a user_goal."""
        from hermes_cli.workflows import generate_orchestration_script

        wf = self._make_pipeline_wf()
        script = generate_orchestration_script(wf, user_goal="", workflow_id="wf-004")
        compile(script, "<script>", "exec")
        self.assertIn("USER_GOAL = \"\"", script)


class TestSlashCommandIntegration(unittest.TestCase):
    """Test workflow slash command scanning via skill_commands."""

    def setUp(self):
        self._tmp_personal = tempfile.TemporaryDirectory()
        self._personal_dir = Path(self._tmp_personal.name)

    def tearDown(self):
        self._tmp_personal.cleanup()
        # Clear the skill_commands cache
        import agent.skill_commands as sc
        sc._skill_commands = {}

    def test_workflow_appears_as_slash_command(self):
        """Workflow YAML in personal dir appears as a slash command."""
        import agent.skill_commands as sc

        # Create a workflow YAML
        (self._personal_dir / "test-cmd.yaml").write_text(
            "name: test-cmd\ndescription: A test workflow command\npattern: pipeline\nphases:\n  - name: step1\n"
        )

        with patch("hermes_cli.workflows._personal_workflows_dir",
                   return_value=self._personal_dir):
            with patch("hermes_cli.workflows._project_workflows_dir",
                       return_value=None):
                cmds = sc.scan_skill_commands()
                self.assertIn("/test-cmd", cmds)
                info = cmds["/test-cmd"]
                self.assertEqual(info["kind"], "workflow")
                self.assertEqual(info["name"], "test-cmd")
                self.assertEqual(info["description"], "A test workflow command")

    def test_build_workflow_invocation_message(self):
        """build_workflow_invocation_message generates a prompt with script."""
        import agent.skill_commands as sc

        # Create workflow YAML
        (self._personal_dir / "invoke-test.yaml").write_text(
            "name: invoke-test\ndescription: Invocation test\npattern: pipeline\nphases:\n  - name: step1\n    toolsets: [web]\n"
        )

        with patch("hermes_cli.workflows._personal_workflows_dir",
                   return_value=self._personal_dir):
            with patch("hermes_cli.workflows._project_workflows_dir",
                       return_value=None):
                # Force rescan
                sc._skill_commands = {}
                cmds = sc.scan_skill_commands()
                self.assertIn("/invoke-test", cmds)

                msg = sc.build_workflow_invocation_message(
                    "/invoke-test",
                    user_instruction="test goal",
                    task_id="task-123",
                )
                assert msg is not None, "build_workflow_invocation_message returned None"
                self.assertIn("invoke-test", msg)
                self.assertIn("test goal", msg)
                self.assertIn("execute_code", msg.lower())
                self.assertIn("```python", msg)

    def test_is_workflow_command(self):
        """is_workflow_command correctly identifies workflow vs skill."""
        import agent.skill_commands as sc

        # Seed a workflow entry directly
        sc._skill_commands["/test-wf"] = {
            "kind": "workflow",
            "name": "test-wf",
            "description": "A workflow",
        }
        sc._skill_commands["/test-skill"] = {
            "kind": "skill",
            "name": "test-skill",
            "description": "A skill",
        }

        self.assertTrue(sc.is_workflow_command("/test-wf"))
        self.assertFalse(sc.is_workflow_command("/test-skill"))
        self.assertFalse(sc.is_workflow_command("/no-such-command"))


if __name__ == "__main__":
    unittest.main()
