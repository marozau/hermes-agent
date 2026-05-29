"""
artifact_reader.py — Artifact discovery and validation for BMAD judge.

Discovers, reads, summarizes, and validates project artifacts in a BMAD
artifacts directory. Designed to feed structured artifact data into the
judge pipeline.

Exports:
    SUPPORTED_EXTENSIONS    File extensions the reader handles
    discover_artifacts      Walk an artifacts dir, return {rel_path: content}
    read_artifact           Read a single artifact file
    summarize_artifacts     Extract headings, tables, code blocks from artifacts
    validate_required_artifacts  Check that required artifacts exist (stem match)
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml


# ═══════════════════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════════════════

SUPPORTED_EXTENSIONS: tuple[str, ...] = (
    ".md", ".yaml", ".yml", ".json", ".txt", ".py",
)

# Regex patterns (re.MULTILINE for multiline matching)
_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
_TABLE_ROW_PATTERN = re.compile(r"^\|.+\|$", re.MULTILINE)
_CODE_BLOCK_PATTERN = re.compile(r"```[\s\S]*?```", re.MULTILINE)
_INLINE_CODE_PATTERN = re.compile(r"`[^`]+`")


# ═══════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════

def discover_artifacts(
    artifacts_dir: str | Path,
    recursive: bool = True,
) -> dict[str, str]:
    """Walk *artifacts_dir* and read all supported files.

    Args:
        artifacts_dir: Path to the artifacts directory.
        recursive: If True (default), descend into subdirectories.

    Returns:
        ``{relative_path: content}`` mapping. Returns empty dict if
        the directory does not exist or is unreadable.
    """
    artifacts_dir = Path(artifacts_dir)
    if not artifacts_dir.is_dir():
        return {}

    result: dict[str, str] = {}
    glob_method = artifacts_dir.rglob if recursive else artifacts_dir.glob

    for file_path in glob_method("*"):
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        try:
            content = file_path.read_text(encoding="utf-8")
            rel_path = str(file_path.relative_to(artifacts_dir))
            result[rel_path] = content
        except (OSError, UnicodeDecodeError):
            # Graceful: skip unreadable files
            continue

    return result


def read_artifact(
    path: str | Path,
) -> dict[str, Any]:
    """Read a single artifact file.

    Args:
        path: Path to the artifact file.

    Returns:
        ``{path, content, metadata: {size, extension, line_count, ...}}``.
        On error, returns ``{path, content: "", metadata: {error: ...}}``.
    """
    path = Path(path)
    result: dict[str, Any] = {
        "path": str(path),
        "content": "",
        "metadata": {},
    }

    if not path.is_file():
        result["metadata"]["error"] = f"File not found: {path}"
        return result

    ext = path.suffix.lower()
    result["metadata"]["extension"] = ext
    result["metadata"]["size"] = path.stat().st_size

    try:
        content = path.read_text(encoding="utf-8")
        result["content"] = content
        result["metadata"]["line_count"] = len(content.splitlines())
        result["metadata"]["char_count"] = len(content)
    except UnicodeDecodeError:
        result["metadata"]["error"] = f"Binary or non-UTF-8 file: {path}"
    except OSError as exc:
        result["metadata"]["error"] = str(exc)

    return result


def summarize_artifacts(
    artifacts: dict[str, str],
) -> dict[str, Any]:
    """Summarize a collection of artifacts.

    For each artifact, extracts:
    - Headings (markdown)
    - Table counts
    - Code block counts
    - Inline code counts
    - Word count
    - Key-value pairs (YAML/JSON files)

    Also infers the BMAD phase from the shared parent directory name.

    Args:
        artifacts: ``{path: content}`` mapping from :func:`discover_artifacts`.

    Returns:
        ``{summaries: {path: summary_dict}, phase_guess: str | None,
           total_artifacts: int, total_size_chars: int}``.
    """
    summaries: dict[str, dict] = {}
    total_size = 0

    for path, content in artifacts.items():
        ext = Path(path).suffix.lower()

        summary: dict[str, Any] = {
            "word_count": len(content.split()),
            "char_count": len(content),
        }

        if ext == ".md":
            summary["headings"] = [
                {"level": len(m.group(1)), "text": m.group(2).strip()}
                for m in _HEADING_PATTERN.finditer(content)
            ]
            summary["heading_count"] = len(summary["headings"])
            summary["table_rows"] = len(_TABLE_ROW_PATTERN.findall(content))
            summary["code_blocks"] = len(_CODE_BLOCK_PATTERN.findall(content))
            summary["inline_code"] = len(_INLINE_CODE_PATTERN.findall(content))

        elif ext in (".yaml", ".yml"):
            try:
                parsed = yaml.safe_load(content)
                if isinstance(parsed, dict):
                    summary["top_level_keys"] = list(parsed.keys())
                elif isinstance(parsed, list):
                    summary["top_level_keys"] = [f"list[{len(parsed)} items]"]
                else:
                    summary["top_level_keys"] = [str(type(parsed).__name__)]
            except yaml.YAMLError:
                summary["parse_error"] = "Invalid YAML"

        elif ext == ".json":
            try:
                parsed = json.loads(content)
                if isinstance(parsed, dict):
                    summary["top_level_keys"] = list(parsed.keys())
                elif isinstance(parsed, list):
                    summary["top_level_keys"] = [f"list[{len(parsed)} items]"]
                else:
                    summary["top_level_keys"] = [str(type(parsed).__name__)]
            except json.JSONDecodeError:
                summary["parse_error"] = "Invalid JSON"

        elif ext == ".py":
            # Count classes and functions
            class_count = len(re.findall(r"^\s*class\s+\w+", content, re.MULTILINE))
            func_count = len(re.findall(r"^\s*def\s+\w+", content, re.MULTILINE))
            summary["classes"] = class_count
            summary["functions"] = func_count
            summary["lines"] = len(content.splitlines())

        summaries[path] = summary
        total_size += len(content)

    # Guess phase from shared parent directory
    phase_guess: str | None = None
    if artifacts:
        paths = [Path(p) for p in artifacts]
        # Check if all artifacts share a common parent dir
        try:
            common_parent = Path(paths[0]).parent
            for p in paths[1:]:
                while common_parent not in p.parents and common_parent != Path("."):
                    common_parent = common_parent.parent
            if common_parent != Path("."):
                phase_name = common_parent.name.lower()
                known_phases = {"analysis", "planning", "solutioning", "implementation"}
                if phase_name in known_phases:
                    phase_guess = phase_name
        except (ValueError, IndexError):
            pass

    return {
        "summaries": summaries,
        "phase_guess": phase_guess,
        "total_artifacts": len(artifacts),
        "total_size_chars": total_size,
    }


def validate_required_artifacts(
    required: list[str],
    discovered: dict[str, str],
) -> dict[str, Any]:
    """Check that all required artifacts exist in the discovered set.

    Uses stem matching: ``"product_brief.md"`` matches ``"product_brief"``
    and vice versa, so you don't need to know exact extensions.

    Args:
        required: List of required artifact names (with or without extension).
        discovered: ``{path: content}`` mapping from :func:`discover_artifacts`.

    Returns:
        ``{found: [str], missing: [str], all_present: bool}``.
    """
    found: list[str] = []
    missing: list[str] = []

    # Build set of discovered stems for flexible matching
    discovered_stems: dict[str, str] = {}
    for path in discovered:
        stem = Path(path).stem.lower()
        discovered_stems[stem] = path
        # Also register the full name (with extension) as a stem
        full_name = Path(path).name.lower()
        discovered_stems[full_name] = path

    for req in required:
        req_stem = Path(req).stem.lower()
        req_full = req.lower()

        # Try stem match first, then full name match
        if req_stem in discovered_stems:
            found.append(req)
        elif req_full in discovered_stems:
            found.append(req)
        elif any(req_stem in ds for ds in discovered_stems):
            # Partial match — e.g., "code/" matches "code/main.py"
            found.append(req)
        else:
            missing.append(req)

    return {
        "found": found,
        "missing": missing,
        "all_present": len(missing) == 0,
    }
