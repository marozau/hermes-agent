"""Template rendering pipeline for the BMAD plugin (A-8).

Provides deterministic-variable substitution via Jinja2 with a
PreservingUndefined strategy: unknown {{vars}} render as literal
'{{var_name}}' so the LLM can fill them in context.
"""

from pathlib import Path
from typing import Any

import yaml
from jinja2 import BaseLoader, Environment, Undefined

from ._datetime import _now_iso, _today_iso


# ---------------------------------------------------------------------------
# Preserving undefined — render unknowns as literal mustache tags
# ---------------------------------------------------------------------------

from plugins.bmad.lib.render import PreservingUndefined

# ---------------------------------------------------------------------------
# Jinja2 environment (singleton)
# ---------------------------------------------------------------------------

_env = Environment(
    loader=BaseLoader(),
    undefined=PreservingUndefined,
    keep_trailing_newline=True,
    autoescape=False,
)

# ---------------------------------------------------------------------------
# Deterministic-variable allow-list (15 vars)
# ---------------------------------------------------------------------------

DETERMINISTIC_VARS: frozenset[str] = frozenset({
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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render(template_text: str, vars: dict[str, Any]) -> str:
    """Pre-substitute deterministic vars; preserve content vars literally.

    Filters *vars* to the :data:`DETERMINISTIC_VARS` allow-list as
    defense-in-depth — even if a content-var name collides with a
    deterministic-var name, the LLM still owns the content.

    Parameters
    ----------
    template_text:
        Raw template string (e.g. the text of a BMAD slash-command prompt).
    vars:
        Dictionary of variable name → value. Only keys appearing in
        *DETERMINISTIC_VARS* are used; everything else is silently ignored.

    Returns
    -------
    str
        Rendered template with deterministic vars substituted and
        unknown ``{{vars}}`` preserved as literal ``{{var_name}}``.
    """
    # Per D-2 falsification: we support {{var}} substitution only.
    # Reject jinja2 control flow ({% ... %}) explicitly — templates are
    # data, not code.
    if "{%" in template_text:
        raise ValueError(
            "Jinja2 control flow ({% ... %}) is not supported in BMAD templates. "
            "Use {{var}} substitution only."
        )
    filtered = {k: v for k, v in vars.items() if k in DETERMINISTIC_VARS}
    return _env.from_string(template_text).render(**filtered)


def deterministic_vars(project_dir: Path) -> dict[str, Any]:
    """Resolve all deterministic template variables for *project_dir*.

    Reads ``bmad/config.yaml`` from the project and combines those
    values with system state (today's date, current timestamp, etc).

    Parameters
    ----------
    project_dir:
        Root of the BMAD project (contains ``bmad/config.yaml``).

    Returns
    -------
    dict[str, Any]
        All 15 deterministic variables filled with sensible defaults.
    """
    cfg_path = project_dir / "bmad" / "config.yaml"
    cfg: dict[str, Any] = yaml.safe_load(cfg_path.read_text()) if cfg_path.exists() else {}

    project_name: str = cfg.get("project_name", "")
    user_name: str = cfg.get("user_name", "")
    project_type: str = cfg.get("project_type", "other")
    project_level: int | str = cfg.get("project_level", 1)
    planning_artifacts: str = cfg.get("planning_artifacts", "planning-artifacts")

    return {
        # --- strings differing only by case ---
        "project_name": project_name,
        "PROJECT_NAME": project_name,
        "user_name": user_name,
        "project_type": project_type,
        "project_level": str(project_level),
        # --- temporal ---
        "date": _today_iso(),
        "TIMESTAMP": _now_iso(),
        "START_DATE": _today_iso(),
        # --- content metadata (often blank; filled later by LLM) ---
        "SPRINT_GOAL": cfg.get("sprint_goal", ""),
        "target_launch": cfg.get("target_launch", ""),
        "target_completion": cfg.get("target_completion", ""),
        "tech_stack": cfg.get("tech_stack", ""),
        # --- paths ---
        "product_brief_path": str(project_dir / planning_artifacts / "product-brief.md"),
        "output_folder": planning_artifacts,
        "project_root": str(project_dir),
    }
