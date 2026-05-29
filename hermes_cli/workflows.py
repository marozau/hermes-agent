#!/usr/bin/env python3
"""
Workflow persistence layer — YAML definition save/load for orchestration workflows.

Stores workflow definitions as YAML files in two tiers:
  - Personal:  ~/.hermes/workflows/
  - Project:   .hermes/workflows/ (relative to cwd or explicit project root)

Project definitions override personal ones (same filename wins in project dir).
Auto-discovery registers workflows as slash commands via the skill command system.

Resumption: loading a saved workflow can resume from the last checkpoint by
generating a resume-aware orchestration script that skips completed phases.

Schema version 1 — each YAML file describes phases, subagent specs, verification
settings, and the orchestration pattern (pipeline, fan-out-fan-in, adversarial).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1

# Valid orchestration patterns
VALID_PATTERNS = frozenset({"pipeline", "fan-out-fan-in", "adversarial"})

# Allowed toolsets for sandbox subagents
ALLOWED_TOOLSETS = frozenset({
    "web", "terminal", "file", "browser", "search",
})


@dataclass
class PhaseSpec:
    """Specification for a single workflow phase."""
    name: str
    description: str = ""
    goal: str = ""                      # Goal template for delegate_task
    toolsets: List[str] = field(default_factory=lambda: ["web", "file"])
    context_from: Optional[str] = None  # Phase name to pull context from (pipeline only)
    review_agents: int = 0              # 0 = skip adversarial review
    max_retries: int = 2
    is_parallel: bool = False           # For fan-out: run this phase with parallel subagents
    parallel_tasks: List[Dict[str, Any]] = field(default_factory=list)  # [{goal, context, toolsets}]


@dataclass
class WorkflowDefinition:
    """A complete workflow definition loaded from YAML."""
    name: str
    description: str = ""
    version: int = SCHEMA_VERSION
    pattern: str = "pipeline"           # pipeline | fan-out-fan-in | adversarial
    phases: List[PhaseSpec] = field(default_factory=list)
    settings: Dict[str, Any] = field(default_factory=dict)
    source_path: Optional[Path] = None  # Path to the YAML file this was loaded from
    source_tier: str = "personal"       # "personal" or "project"

    # Global settings defaults
    @property
    def timeout_minutes(self) -> int:
        return self.settings.get("timeout_minutes", 5)

    @property
    def max_concurrent(self) -> int:
        return self.settings.get("max_concurrent", 3)

    @property
    def default_review_agents(self) -> int:
        return self.settings.get("default_review_agents", 0)


# ---------------------------------------------------------------------------
# YAML serialization
# ---------------------------------------------------------------------------

def _phase_to_dict(phase: PhaseSpec) -> Dict[str, Any]:
    """Serialize a PhaseSpec to a YAML-safe dict."""
    d: Dict[str, Any] = {"name": phase.name}
    if phase.description:
        d["description"] = phase.description
    if phase.goal:
        d["goal"] = phase.goal
    if phase.toolsets and phase.toolsets != ["web", "file"]:
        d["toolsets"] = phase.toolsets
    if phase.context_from:
        d["context_from"] = phase.context_from
    if phase.review_agents:
        d["review_agents"] = phase.review_agents
    if phase.max_retries != 2:
        d["max_retries"] = phase.max_retries
    if phase.is_parallel:
        d["is_parallel"] = phase.is_parallel
    if phase.parallel_tasks:
        d["parallel_tasks"] = phase.parallel_tasks
    return d


def _phase_from_dict(d: Dict[str, Any]) -> PhaseSpec:
    """Deserialize a PhaseSpec from a YAML dict."""
    return PhaseSpec(
        name=d["name"],
        description=d.get("description", ""),
        goal=d.get("goal", d.get("description", "")),
        toolsets=d.get("toolsets", ["web", "file"]),
        context_from=d.get("context_from"),
        review_agents=d.get("review_agents", 0),
        max_retries=d.get("max_retries", 2),
        is_parallel=d.get("is_parallel", False),
        parallel_tasks=d.get("parallel_tasks", []),
    )


def workflow_to_yaml(wf: WorkflowDefinition) -> str:
    """Serialize a WorkflowDefinition to YAML string."""
    doc: Dict[str, Any] = {
        "name": wf.name,
        "description": wf.description,
        "version": wf.version,
        "pattern": wf.pattern,
        "phases": [_phase_to_dict(p) for p in wf.phases],
    }
    if wf.settings:
        doc["settings"] = wf.settings
    return yaml.dump(doc, default_flow_style=False, sort_keys=False, allow_unicode=True)


def workflow_from_yaml(content: str, source_path: Optional[Path] = None,
                       source_tier: str = "personal") -> WorkflowDefinition:
    """Parse a WorkflowDefinition from YAML string."""
    doc = yaml.safe_load(content) or {}
    phases = [_phase_from_dict(p) for p in doc.get("phases", [])]
    return WorkflowDefinition(
        name=doc.get("name", source_path.stem if source_path else "unnamed"),
        description=doc.get("description", ""),
        version=doc.get("version", SCHEMA_VERSION),
        pattern=doc.get("pattern", "pipeline"),
        phases=phases,
        settings=doc.get("settings", {}),
        source_path=source_path,
        source_tier=source_tier,
    )


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

def _personal_workflows_dir() -> Path:
    """Return ~/.hermes/workflows/, creating it if needed."""
    d = get_hermes_home() / "workflows"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _project_workflows_dir(cwd: Optional[Path] = None) -> Optional[Path]:
    """Return .hermes/workflows/ relative to cwd, or None if not found.

    Walks up from cwd looking for .hermes/workflows/. Returns the first
    match, or None if no project root has a workflows directory.

    Excludes the Hermes home directory (~/.hermes/) — that's the personal
    workflows location, not a project root.
    """
    cwd = Path(cwd) if cwd else Path.cwd()
    personal_dir = _personal_workflows_dir()
    for parent in [cwd] + list(cwd.parents):
        candidate = parent / ".hermes" / "workflows"
        if candidate.is_dir():
            # Skip if this is the personal workflows directory
            try:
                if candidate.resolve() == personal_dir.resolve():
                    continue
            except Exception:
                pass
            return candidate
    return None


def _project_root(cwd: Optional[Path] = None) -> Optional[Path]:
    """Find the project root (closest ancestor with .hermes/ workdir).

    Excludes the Hermes home directory (~/.hermes/).
    """
    cwd = Path(cwd) if cwd else Path.cwd()
    personal_dir = _personal_workflows_dir()
    for parent in [cwd] + list(cwd.parents):
        candidate = parent / ".hermes"
        if candidate.is_dir():
            # Skip if this is the Hermes home directory
            try:
                if candidate.resolve() == get_hermes_home().resolve():
                    continue
            except Exception:
                pass
            return parent
    return None


# ---------------------------------------------------------------------------
# CRUD operations
# ---------------------------------------------------------------------------

def list_workflows(cwd: Optional[Path] = None) -> Dict[str, WorkflowDefinition]:
    """List all available workflows (project overrides personal).

    Returns dict of workflow_name -> WorkflowDefinition.
    """
    result: Dict[str, WorkflowDefinition] = {}

    # Tier 1: Personal workflows (loaded first, may be overridden)
    personal_dir = _personal_workflows_dir()
    for yaml_file in sorted(personal_dir.glob("*.yaml")):
        try:
            name = yaml_file.stem
            wf = workflow_from_yaml(
                yaml_file.read_text(encoding="utf-8"),
                source_path=yaml_file,
                source_tier="personal",
            )
            result[name] = wf
        except Exception:
            logger.warning(f"Failed to load personal workflow: {yaml_file}", exc_info=True)

    # Tier 2: Project workflows (override personal)
    project_dir = _project_workflows_dir(cwd)
    if project_dir:
        for yaml_file in sorted(project_dir.glob("*.yaml")):
            try:
                name = yaml_file.stem
                wf = workflow_from_yaml(
                    yaml_file.read_text(encoding="utf-8"),
                    source_path=yaml_file,
                    source_tier="project",
                )
                result[name] = wf  # Override personal if same name
            except Exception:
                logger.warning(f"Failed to load project workflow: {yaml_file}", exc_info=True)

    return result


def load_workflow(name: str, cwd: Optional[Path] = None) -> Optional[WorkflowDefinition]:
    """Load a single workflow by name (project overrides personal)."""
    workflows = list_workflows(cwd)
    return workflows.get(name)


def save_workflow(wf: WorkflowDefinition, tier: str = "personal",
                  cwd: Optional[Path] = None) -> Path:
    """Save a workflow definition to disk.

    Args:
        wf: The workflow definition to save.
        tier: "personal" (default) or "project".
        cwd: Working directory for project-tier resolution.

    Returns:
        Path to the saved file.
    """
    if tier == "project":
        root = _project_root(cwd)
        if root is None:
            raise FileNotFoundError(
                "No .hermes/ directory found in project tree. "
                "Run `hermes init` or create .hermes/workflows/ manually."
            )
        target_dir = root / ".hermes" / "workflows"
    else:
        target_dir = _personal_workflows_dir()

    target_dir.mkdir(parents=True, exist_ok=True)
    yaml_content = workflow_to_yaml(wf)
    file_path = target_dir / f"{wf.name}.yaml"
    file_path.write_text(yaml_content, encoding="utf-8")
    logger.info(f"Saved workflow '{wf.name}' to {file_path}")
    return file_path


def delete_workflow(name: str, cwd: Optional[Path] = None) -> bool:
    """Delete a workflow by name. Tries project first, then personal."""
    # Check project first
    project_dir = _project_workflows_dir(cwd)
    if project_dir:
        project_file = project_dir / f"{name}.yaml"
        if project_file.exists():
            project_file.unlink()
            logger.info(f"Deleted project workflow: {project_file}")
            return True

    # Then personal
    personal_file = _personal_workflows_dir() / f"{name}.yaml"
    if personal_file.exists():
        personal_file.unlink()
        logger.info(f"Deleted personal workflow: {personal_file}")
        return True

    return False


# ---------------------------------------------------------------------------
# Script generation
# ---------------------------------------------------------------------------

def generate_orchestration_script(
    wf: WorkflowDefinition,
    user_goal: str = "",
    workflow_id: Optional[str] = None,
    resume: bool = False,
    cwd: Optional[Path] = None,
) -> str:
    """Generate a Python orchestration script from a workflow definition.

    Args:
        wf: The workflow definition.
        user_goal: The user's goal text (replaces {goal} template in phase goals).
        workflow_id: Optional workflow ID for checkpointing. Auto-generated if None.
        resume: If True, generate a resume-aware script that skips completed phases.
        cwd: Working directory for the script.

    Returns:
        A Python script string ready for execute_code.
    """
    lines: List[str] = []
    indent = "    "

    # --- Header ---
    lines.append("#!/usr/bin/env python3")
    lines.append(f'"""Generated workflow: {wf.name} — {wf.description}"""')
    lines.append("import json, os, uuid as _uuid")
    lines.append("from hermes_tools import (")
    lines.append(f"{indent}delegate_task, checkpoint_save, checkpoint_load,")
    lines.append(f"{indent}terminal, read_file, write_file, search_files,")
    lines.append(f"{indent}web_search, web_extract, patch, json_parse,")
    lines.append(f"{indent}shell_quote, retry,")
    lines.append(")")
    lines.append("")

    # --- Workflow ID ---
    if workflow_id:
        wf_id = workflow_id
        lines.append(f'WORKFLOW_ID = {wf_id!r}')
    else:
        lines.append('WORKFLOW_ID = os.environ.get("HERMES_KANBAN_TASK", "hermes") + "-" + _uuid.uuid4().hex[:8]')
    lines.append("")

    # --- User goal ---
    if user_goal:
        lines.append(f'USER_GOAL = {user_goal!r}')
    else:
        lines.append('USER_GOAL = ""')
    lines.append("")

    # --- Safe delegate helper ---
    lines.append("def safe_delegate(goal, context=\"\", toolsets=None, max_retries=2):")
    lines.append(f"{indent}for attempt in range(max_retries + 1):")
    lines.append(f"{indent}{indent}try:")
    lines.append(f"{indent}{indent}{indent}result = delegate_task(goal=goal, context=context, toolsets=toolsets)")
    lines.append(f"{indent}{indent}{indent}if isinstance(result, dict) and \"summary\" in result:")
    lines.append(f"{indent}{indent}{indent}{indent}return result")
    lines.append(f"{indent}{indent}{indent}if isinstance(result, list) and result and \"summary\" in result[0]:")
    lines.append(f"{indent}{indent}{indent}{indent}return result")
    lines.append(f"{indent}{indent}{indent}if attempt < max_retries:")
    lines.append(f"{indent}{indent}{indent}{indent}continue")
    lines.append(f"{indent}{indent}{indent}return {{\"error\": f\"No summary after {{max_retries+1}} attempts\"}}")
    lines.append(f"{indent}{indent}except Exception as e:")
    lines.append(f"{indent}{indent}{indent}if attempt < max_retries:")
    lines.append(f"{indent}{indent}{indent}{indent}continue")
    lines.append(f"{indent}{indent}{indent}return {{\"error\": str(e)}}")
    lines.append(f"{indent}return {{\"error\": \"max_retries exhausted\"}}")
    lines.append("")

    # --- Resumption checkpoint check ---
    if resume:
        lines.append("# --- Resumption: check for completed phases ---")
        lines.append("cached = checkpoint_load(WORKFLOW_ID)")
        lines.append("completed = {p for p, v in cached.items() if v.get(\"status\") == \"completed\"}")
        lines.append("")

    # --- Phase execution ---
    results: Dict[str, str] = {}
    for i, phase in enumerate(wf.phases):
        phase_var = phase.name.replace("-", "_")
        lines.append(f"# --- Phase {i+1}: {phase.name} ---")

        if resume:
            lines.append(f'if "{phase.name}" not in completed:')
            prefix = indent
        else:
            prefix = ""

        goal_text = phase.goal or phase.description
        if "{goal}" in goal_text:
            goal_text = goal_text.replace("{goal}", user_goal)
        elif user_goal:
            goal_text = f"{goal_text}: {user_goal}"

        if phase.is_parallel and phase.parallel_tasks:
            # Fan-out phase — parallel subagents
            tasks_lines = []
            for j, task in enumerate(phase.parallel_tasks):
                t_goal = task.get("goal", goal_text)
                t_context = task.get("context", "")
                t_toolsets = task.get("toolsets", phase.toolsets)
                tasks_lines.append(f'{prefix}{indent}{{')
                tasks_lines.append(f'{prefix}{indent}{indent}"goal": {t_goal!r},')
                tasks_lines.append(f'{prefix}{indent}{indent}"context": {t_context!r},')
                if t_toolsets:
                    tasks_lines.append(f'{prefix}{indent}{indent}"toolsets": {t_toolsets!r},')
                tasks_lines.append(f'{prefix}{indent}}},')

            lines.append(f"{prefix}{phase_var}_results = delegate_task(tasks=[")
            lines.extend(tasks_lines)
            lines.append(f"{prefix}])")

            # Store result
            lines.append(f"{prefix}if isinstance({phase_var}_results, list):")
            lines.append(f"{prefix}{indent}{phase_var}_summaries = [")
            lines.append(f"{prefix}{indent}{indent}r.get(\"summary\", str(r)) if isinstance(r, dict) else str(r)")
            lines.append(f"{prefix}{indent}{indent}for r in {phase_var}_results")
            lines.append(f"{prefix}]")
            lines.append(f"{prefix}{indent}{phase_var}_result = {{\"summary\": json.dumps({phase_var}_summaries)}}")
            lines.append(f"{prefix}else:")
            lines.append(f"{prefix}{indent}{phase_var}_result = {phase_var}_results")
            results[phase.name] = f"{phase_var}_result"
        else:
            # Single subagent phase
            context_from = None
            if phase.context_from:
                prev_var = phase.context_from.replace("-", "_")
                context_from = f"{prev_var}_result.get(\"summary\", \"\") if isinstance({prev_var}_result, dict) else str({prev_var}_result)"

            toolset_list = phase.toolsets if phase.toolsets else ["web", "file"]

            if context_from:
                lines.append(f"{prefix}context_{phase_var} = {context_from}")
                lines.append(f"{prefix}{phase_var}_result = safe_delegate(")
                lines.append(f"{prefix}{indent}goal={goal_text!r},")
                lines.append(f"{prefix}{indent}context=context_{phase_var},")
                lines.append(f"{prefix}{indent}toolsets={toolset_list!r},")
                lines.append(f"{prefix}{indent}max_retries={phase.max_retries},")
                lines.append(f"{prefix})")
            else:
                lines.append(f"{prefix}{phase_var}_result = safe_delegate(")
                lines.append(f"{prefix}{indent}goal={goal_text!r},")
                if not phase.is_parallel:
                    lines.append(f"{prefix}{indent}toolsets={toolset_list!r},")
                lines.append(f"{prefix}{indent}max_retries={phase.max_retries},")
                lines.append(f"{prefix})")

            results[phase.name] = f"{phase_var}_result"

        # Error gate
        lines.append(f"{prefix}if \"error\" in {phase_var}_result:")
        lines.append(f"{prefix}{indent}print(json.dumps({{\"status\": \"failed\", \"phase\": {phase.name!r}, \"error\": {phase_var}_result[\"error\"]}}))")
        lines.append(f"{prefix}{indent}exit(1)")

        # Checkpoint save
        lines.append(f"{prefix}checkpoint_save(WORKFLOW_ID, {phase.name!r}, status=\"completed\",")
        lines.append(f"{prefix}{indent}result_cache=json.dumps({phase_var}_result.get(\"summary\", \"\") if isinstance({phase_var}_result, dict) else str({phase_var}_result)))")
        lines.append("")

        if resume and prefix:
            lines.append("")

    # --- Final synthesis ---
    lines.append(f"# --- Final result ---")
    summary_parts = []
    for phase in wf.phases:
        phase_var = phase.name.replace("-", "_")
        summary_parts.append(f'"{phase.name}": {phase_var}_result.get("summary", "") if isinstance({phase_var}_result, dict) else str({phase_var}_result)')
    lines.append("print(json.dumps({")
    lines.append(f'{indent}"status": "complete",')
    lines.append(f'{indent}"workflow": {wf.name!r},')
    lines.append(f'{indent}"workflow_id": WORKFLOW_ID,')
    lines.append(f'{indent}"results": {{{", ".join(summary_parts)}}},')
    lines.append("}))")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Save from a running workflow (extraction)
# ---------------------------------------------------------------------------

def extract_workflow_from_prompt(
    name: str,
    description: str,
    pattern: str,
    phases: List[Dict[str, Any]],
    settings: Optional[Dict[str, Any]] = None,
) -> WorkflowDefinition:
    """Create a WorkflowDefinition from explicit phase descriptions.

    This is used to save a workflow definition that describes what the
    agent's orchestration script does, so it can be reused.

    Args:
        name: Workflow name.
        description: Human-readable description.
        pattern: One of "pipeline", "fan-out-fan-in", "adversarial".
        phases: List of phase dicts with keys: name, description, goal,
                toolsets, context_from, review_agents, is_parallel, parallel_tasks.
        settings: Optional global settings dict.

    Returns:
        A WorkflowDefinition ready to save.
    """
    phase_specs = []
    for p in phases:
        spec = PhaseSpec(
            name=p["name"],
            description=p.get("description", ""),
            goal=p.get("goal", p.get("description", "")),
            toolsets=p.get("toolsets", ["web", "file"]),
            context_from=p.get("context_from"),
            review_agents=p.get("review_agents", 0),
            max_retries=p.get("max_retries", 2),
            is_parallel=p.get("is_parallel", False),
            parallel_tasks=p.get("parallel_tasks", []),
        )
        phase_specs.append(spec)

    return WorkflowDefinition(
        name=name,
        description=description,
        version=SCHEMA_VERSION,
        pattern=pattern,
        phases=phase_specs,
        settings=settings or {},
    )
