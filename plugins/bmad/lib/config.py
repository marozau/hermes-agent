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


class OCRConfig(BaseModel):
    """Configuration for OCR (Open Code Review) integration — Epic 8.

    Hard invariants:
    - OI-9: OCR is OPT-IN per project (enabled defaults to False).
    - OI-15: Built-in Java rules are DISABLED for non-Java projects
      (handled at rule-routing level, not config).
    """

    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    rule_path: Optional[str] = None
    timeout_seconds: int = 120
    languages: list[str] = []

    @field_validator("timeout_seconds")
    @classmethod
    def timeout_must_be_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError(f"OCR timeout must be positive: {v}")
        return v

    @field_validator("rule_path")
    @classmethod
    def rule_path_must_be_relative_or_none(cls, v: str | None) -> str | None:
        if v is None:
            return v
        p = Path(v)
        if p.is_absolute():
            raise ValueError(
                f"OCR rule_path must be relative to project root: {v!r}"
            )
        return v


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

    @field_validator("runtime_mirror")
    @classmethod
    def runtime_mirror_must_not_point_into_workspace(cls, v: str | None) -> str | None:
        """B-4: Validate that runtime_mirror target is not under workspace paths.

        The mirror writes outside pre_tool_call's boundary. If the target were
        inside a worktree or planning-artifacts, it would silently corrupt
        workspace state.  We can only validate that the expanded path is
        absolute and does NOT contain 'worktree/' or 'planning-artifacts' as
        path segments — a full validation against the actual workspace root
        requires the WorkspaceConfig context (done at load time).
        """
        if v is None:
            return v
        expanded = Path(v).expanduser().resolve()
        parts = set(expanded.parts)
        # Must not point into a worktree or planning-artifacts
        if "worktree" in parts:
            raise ValueError(
                f"runtime_mirror must not target a path containing 'worktree/': {v!r}"
            )
        if "planning-artifacts" in parts:
            raise ValueError(
                f"runtime_mirror must not target 'planning-artifacts/': {v!r}"
            )
        return v


class WorkspaceConfig(BaseModel):
    """Top-level workspace configuration embedded in ``bmad/config.yaml``."""

    model_config = ConfigDict(frozen=True)

    workspace_mode: bool = False
    worktrees: list[WorktreeSpec] = []
    code_review_ocr: OCRConfig = OCRConfig()


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

    # OCR config: nested under code_review.ocr in bmad/config.yaml
    code_review_raw = raw.get("code_review", {})
    ocr_raw = (code_review_raw.get("ocr", {}) if isinstance(code_review_raw, dict) else {})
    ocr_cfg = OCRConfig(**ocr_raw) if ocr_raw else OCRConfig()

    if not ws_mode and not ws_raw and not ocr_raw:
        return WorkspaceConfig()

    return WorkspaceConfig(workspace_mode=ws_mode, worktrees=ws_raw, code_review_ocr=ocr_cfg)


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
    # Serialize OCR config if it differs from defaults (OI-9: opt-in)
    ocr = cfg.code_review_ocr
    if ocr.enabled or ocr.rule_path or ocr.languages or ocr.timeout_seconds != 120:
        ocr_dict: dict[str, Any] = {"enabled": ocr.enabled}
        if ocr.rule_path:
            ocr_dict["rule_path"] = ocr.rule_path
        if ocr.timeout_seconds != 120:
            ocr_dict["timeout_seconds"] = ocr.timeout_seconds
        if ocr.languages:
            ocr_dict["languages"] = ocr.languages
        result["code_review"] = {"ocr": ocr_dict}
    return result
