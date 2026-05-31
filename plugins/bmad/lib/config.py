"""Workspace-mode config schema for BMAD plugin (Story 6.2).

Provides Pydantic models for the ``workspace_mode`` and ``worktrees:``
fields in ``bmad/config.yaml``.

Hard invariants enforced:
- WI-1: Workspace-mode is opt-in. Missing field = old behavior.
- WI-2: Planning lives at workspace root, never inside a worktree.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, ConfigDict, field_validator


class WorktreeSpec(BaseModel):
    """Specification for a single git worktree in a BMAD workspace."""

    model_config = ConfigDict(frozen=True)

    name: str
    upstream: str
    branch: str
    path: str
    runtime_mirror: Optional[str] = None

    @field_validator("name")
    @classmethod
    def name_must_be_safe(cls, v: str) -> str:
        if not re.match(r"^[a-zA-Z0-9_-]+$", v):
            raise ValueError(
                f"Worktree name must be alphanumeric with hyphens/underscores: {v!r}"
            )
        return v

    @field_validator("path")
    @classmethod
    def path_must_be_relative_no_escape(cls, v: str) -> str:
        p = Path(v)
        if p.is_absolute():
            raise ValueError(f"Worktree path must be relative: {v!r}")
        parts = p.parts
        if ".." in parts:
            raise ValueError(f"Worktree path must not contain '..': {v!r}")
        if len(parts) < 2 or parts[0] != "worktree":
            raise ValueError(
                f"Worktree path must start with 'worktree/<name>': {v!r}"
            )
        return v


class WorkspaceConfig(BaseModel):
    """Top-level workspace configuration embedded in ``bmad/config.yaml``."""

    model_config = ConfigDict(frozen=True)

    workspace_mode: bool = False
    worktrees: list[WorktreeSpec] = []


def load_workspace_config(project_dir: Path) -> WorkspaceConfig:
    """Load workspace config from ``bmad/config.yaml`` in *project_dir*.

    Returns a WorkspaceConfig with safe defaults when the file
    is missing or lacks workspace fields (WI-1).
    """
    config_path = project_dir / "bmad" / "config.yaml"
    if not config_path.exists():
        return WorkspaceConfig()

    raw: dict[str, Any] = yaml.safe_load(config_path.read_text()) or {}

    ws_mode = raw.get("workspace_mode", False)
    ws_raw = raw.get("worktrees", [])

    if not ws_mode and not ws_raw:
        return WorkspaceConfig()

    return WorkspaceConfig(workspace_mode=ws_mode, worktrees=ws_raw)


def serialize_workspace_config(cfg: WorkspaceConfig) -> dict[str, Any]:
    """Serialize a WorkspaceConfig to a dict suitable for YAML output."""
    result: dict[str, Any] = {}
    result["workspace_mode"] = cfg.workspace_mode
    if cfg.worktrees:
        result["worktrees"] = [
            {
                "name": wt.name,
                "upstream": wt.upstream,
                "branch": wt.branch,
                "path": wt.path,
                **({"runtime_mirror": wt.runtime_mirror} if wt.runtime_mirror else {}),
            }
            for wt in cfg.worktrees
        ]
    return result
