"""
port_completeness.py — BMAD port completeness check.

Verifies file equivalence between BMAD v6.6.0 source and the
``~/.hermes/skills/bmad/`` port, grouped by scope.

Usage::

    # CLI via hermes plugin
    hermes bmad-check-port --scope analysis+planning
    hermes bmad-check-port --scope all

    # Direct Python
    python -m plugins.bmad.scripts.port_completeness --bmad-source <path>

Exit codes:
    0 — no missing files in scope
    1 — missing files found
    2 — invalid BMAD source path
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# ── Scope-to-source mapping ──────────────────────────────────────────────
# Each entry: (source_glob, description, scope_tags)

_SCOPE_FILES: dict[str, list[tuple[str, str, set[str]]]] = {
    "core": [
        ("agent-builder.md", "BMB agent builder persona", {"all", "core"}),
        ("analyst.md", "Analyst persona", {"all", "core"}),
        ("architect.md", "Architect persona", {"all", "core"}),
        ("bmad-master.md", "BMAD Master persona", {"all", "core"}),
        ("bmad-tea.md", "TEA persona", {"all", "core"}),
        ("dev.md", "Developer persona", {"all", "core"}),
        ("module-builder.md", "BMB module builder persona", {"all", "core"}),
        ("pm.md", "Product Manager persona", {"all", "core"}),
        ("qa.md", "QA Engineer persona", {"all", "core"}),
        ("quick-flow-solo-dev.md", "Quick Flow Solo Dev persona", {"all", "core"}),
        ("sm.md", "Scrum Master persona", {"all", "core"}),
        ("tech-writer.md", "Tech Writer persona", {"all", "core"}),
        ("ux-designer.md", "UX Designer persona", {"all", "core"}),
        ("workflow-builder.md", "BMB workflow builder persona", {"all", "core"}),
    ],
    "shared": [
        ("agent-manifest.csv", "Agent manifest CSV", {"all", "shared"}),
        ("tea-index.csv", "TEA knowledge index CSV", {"all", "shared", "tea"}),
        ("tasks/workflow.xml", "Workflow engine", {"all", "shared"}),
        ("tasks/editorial-review-prose.xml", "Editorial review prose", {"all", "shared"}),
        ("tasks/editorial-review-structure.xml", "Editorial review structure", {"all", "shared"}),
        ("tasks/help.md", "Help task", {"all", "shared"}),
        ("tasks/index-docs.xml", "Index docs task", {"all", "shared"}),
        ("tasks/review-adversarial-general.xml", "Adversarial review", {"all", "shared"}),
        ("tasks/review-edge-case-hunter.xml", "Edge case hunter", {"all", "shared"}),
        ("tasks/shard-doc.xml", "Shard doc task", {"all", "shared"}),
    ],
    "bmm": [
        # Analysis skills
        ("skills/bmad-product-brief/SKILL.md", "Product brief analysis skill", {"all", "analysis", "bmm"}),
        ("skills/bmad-brainstorming/SKILL.md", "Brainstorming skill", {"all", "analysis", "bmm"}),
        ("skills/bmad-document-project/SKILL.md", "Document project skill", {"all", "analysis", "bmm"}),
        ("skills/research/SKILL.md", "Research skill", {"all", "analysis", "bmm"}),
        ("skills/bmad-quick-spec/SKILL.md", "Quick spec skill", {"all", "analysis", "quick", "bmm"}),
        # Planning skills
        ("skills/bmad-create-prd/SKILL.md", "Create PRD skill", {"all", "planning", "bmm"}),
        ("skills/bmad-validate-prd/SKILL.md", "Validate PRD skill", {"all", "planning", "bmm"}),
        ("skills/bmad-edit-prd/SKILL.md", "Edit PRD skill", {"all", "planning", "bmm"}),
        ("skills/bmad-create-ux-design/SKILL.md", "Create UX design skill", {"all", "planning", "bmm"}),
        # Solutioning skills
        ("skills/bmad-create-architecture/SKILL.md", "Create architecture skill", {"all", "solutioning", "bmm"}),
        ("skills/bmad-create-epics-and-stories/SKILL.md", "Create epics and stories skill", {"all", "solutioning", "bmm"}),
        ("skills/bmad-check-implementation-readiness/SKILL.md", "Solutioning gate check skill", {"all", "solutioning", "bmm"}),
        # Implementation skills
        ("skills/bmad-sprint-planning/SKILL.md", "Sprint planning skill", {"all", "implementation", "bmm"}),
        ("skills/bmad-create-story/SKILL.md", "Create story skill", {"all", "implementation", "bmm"}),
        ("skills/bmad-dev-story/SKILL.md", "Dev story skill", {"all", "implementation", "bmm"}),
        ("skills/bmad-code-review/SKILL.md", "Code review skill", {"all", "implementation", "bmm"}),
        ("skills/bmad-correct-course/SKILL.md", "Correct course skill", {"all", "implementation", "bmm"}),
        ("skills/bmad-quick-dev/SKILL.md", "Quick dev skill", {"all", "implementation", "quick", "bmm"}),
    ],
    "cis": [
        ("skills/bmad-brainstorming/SKILL.md", "CIS brainstorming (Carson)", {"all", "cis"}),
        ("skills/bmad-cis-design-thinking/SKILL.md", "CIS design thinking (Maya)", {"all", "cis"}),
        ("skills/bmad-cis-problem-solving/SKILL.md", "CIS creative problem solving (Dr. Quinn)", {"all", "cis"}),
        ("skills/bmad-cis-innovation-strategy/SKILL.md", "CIS innovation strategy (Victor)", {"all", "cis"}),
        ("skills/bmad-cis-storytelling/SKILL.md", "CIS storytelling (Sophia)", {"all", "cis"}),
        ("skills/bmad-cis-agent-brainstorming-coach/SKILL.md", "CIS agent: brainstorming coach", {"all", "cis"}),
        ("skills/bmad-cis-agent-creative-problem-solver/SKILL.md", "CIS agent: creative problem solver", {"all", "cis"}),
        ("skills/bmad-cis-agent-design-thinking-coach/SKILL.md", "CIS agent: design thinking coach", {"all", "cis"}),
        ("skills/bmad-cis-agent-innovation-strategist/SKILL.md", "CIS agent: innovation strategist", {"all", "cis"}),
        ("skills/bmad-cis-agent-presentation-master/SKILL.md", "CIS agent: presentation master (Caravaggio)", {"all", "cis"}),
        ("skills/bmad-cis-agent-storyteller/SKILL.md", "CIS agent: storyteller (Sophia)", {"all", "cis"}),
    ],
    "tea": [
        ("skills/bmad-testarch-atdd/SKILL.md", "TEA: ATDD skill", {"all", "tea"}),
        ("skills/bmad-testarch-automate/SKILL.md", "TEA: Automate skill", {"all", "tea"}),
        ("skills/bmad-testarch-ci/SKILL.md", "TEA: CI skill", {"all", "tea"}),
        ("skills/bmad-testarch-framework/SKILL.md", "TEA: Framework skill", {"all", "tea"}),
        ("skills/bmad-testarch-nfr/SKILL.md", "TEA: NFR skill", {"all", "tea"}),
        ("skills/bmad-testarch-test-design/SKILL.md", "TEA: Test design skill", {"all", "tea"}),
        ("skills/bmad-testarch-test-review/SKILL.md", "TEA: Test review skill", {"all", "tea"}),
        ("skills/bmad-testarch-trace/SKILL.md", "TEA: Trace skill", {"all", "tea"}),
    ],
    "bmb": [
        ("skills/bmad-bmb-setup/SKILL.md", "BMB setup skill", {"all", "bmb"}),
        ("skills/bmad-module-builder/SKILL.md", "BMB module builder skill", {"all", "bmb"}),
        ("skills/bmad-workflow-builder/SKILL.md", "BMB workflow builder skill", {"all", "bmb"}),
        ("skills/bmad-distillator/SKILL.md", "BMB distillatory skill", {"all", "bmb"}),
    ],
    "templates": [
        ("templates/bmad-settings.template.md", "BMAD settings template", {"all", "templates"}),
        ("templates/product-brief.template.md", "Product brief template", {"all", "templates"}),
        ("templates/project-context-template.md", "Project context template", {"all", "templates"}),
    ],
}

# ── Port destination mapping ─────────────────────────────────────────────

_PORT_PATH = Path.home() / ".hermes" / "skills" / "bmad"
_BMAD_CACHE = Path.home() / ".claude" / "plugins" / "cache" / "bmad-method" / "bmad" / "6.2.2.0"


def _port_skill_path(name: str) -> Path:
    """Map a BMAD source skill name to its port destination.

    Default mapping: bmad-<name> → core/<name>, else bmm/<name>, etc.
    """
    # TODO: full mapping table — for now returns None meaning "look up manually"
    return None


def check_port(
    bmad_source: Path,
    scope: set[str] | None = None,
) -> dict:
    """Compare BMAD source files against port, return report."""
    if not bmad_source.exists():
        return {"error": f"BMAD source path not found: {bmad_source}"}

    report = {
        "both": [],
        "bmad_only": [],
        "port_only": [],
        "summary": {"bmad_only_count": 0, "port_only_count": 0, "both_count": 0},
    }

    source_files = {}
    # Walk the BMAD source directory
    for root, _dirs, files in os.walk(bmad_source):
        for fn in files:
            rel = os.path.relpath(os.path.join(root, fn), bmad_source)
            source_files[rel] = rel

    scm_dirs = {".git", "__pycache__", ".DS_Store"}

    # Get port files
    port_files = {}
    if _PORT_PATH.exists():
        for root, _dirs, files in os.walk(_PORT_PATH):
            for fn in files:
                rel = os.path.relpath(os.path.join(root, fn), _PORT_PATH)
                port_files[rel] = rel

    # If a scope is specified, filter source files to just those
    if scope:
        relevant_sources: set[str] = set()
        for _key, entries in _SCOPE_FILES.items():
            for rel_path, _desc, tags in entries:
                if scope & tags:
                    relevant_sources.add(rel_path)
        # Find these in source_files
        matched_sources = {k: v for k, v in source_files.items()
                          if any(k.endswith(rs) for rs in relevant_sources)}
    else:
        matched_sources = source_files

    # Check source → port
    for rel_path in matched_sources:
        # Check if any port file matches the basename
        basename = os.path.basename(rel_path)
        # Look for matching port file
        found = any(basename in p or rel_path.split("/")[-2:] == p.split("/")[-2:]
                    for p in port_files)
        if found:
            report["both"].append(rel_path)
        else:
            report["bmad_only"].append(rel_path)

    # Check port → source (orphans)
    for port_path in port_files:
        basename = os.path.basename(port_path)
        found = any(basename in s for s in matched_sources)
        if not found:
            # It might be a Hermes-specific file (frontmatter, etc.), that's OK
            hermes_specific = (
                port_path.endswith("SKILL.md")
                and "SKILL.md" not in [os.path.basename(s) for s in matched_sources]
            )
            if not hermes_specific:
                report["port_only"].append(port_path)

    report["summary"] = {
        "bmad_only_count": len(report["bmad_only"]),
        "port_only_count": len(report["port_only"]),
        "both_count": len(report["both"]),
    }
    return report


def print_report(report: dict, scope_name: str = "all") -> None:
    """Pretty-print the port completeness report."""
    if "error" in report:
        print(f"❌ {report['error']}")
        return

    s = report["summary"]
    print(f"\n{'='*60}")
    print(f"  BMAD Port Completeness Report — Scope: {scope_name}")
    print(f"{'='*60}")
    print(f"  ✅ In both source and port:  {s['both_count']}")
    print(f"  ❌ BMAD source only:         {s['bmad_only_count']}")
    print(f"  ⚠️  Port only (orphans):      {s['port_only_count']}")
    print(f"{'='*60}")

    if report["bmad_only"]:
        print("\n📋 BMAD source files NOT in port:")
        for f in sorted(report["bmad_only"])[:30]:
            print(f"   ❌ {f}")
        if len(report["bmad_only"]) > 30:
            print(f"   ... and {len(report['bmad_only']) - 30} more")

    if report["port_only"]:
        print("\n📋 Port-only files (possible orphans):")
        for f in sorted(report["port_only"])[:20]:
            print(f"   ⚠️  {f}")
        if len(report["port_only"]) > 20:
            print(f"   ... and {len(report['port_only']) - 20} more")

    print("")


def cli_main() -> None:
    """CLI entry point for ``hermes bmad-check-port``."""
    parser = argparse.ArgumentParser(
        description="Check BMAD port completeness against v6.6.0 source",
    )
    parser.add_argument(
        "--bmad-source",
        default=str(_BMAD_CACHE),
        help=f"Path to BMAD v6.6.0 source (default: {_BMAD_CACHE})",
    )
    parser.add_argument(
        "--scope",
        default="analysis+planning",
        help="Scope: all, analysis, planning, analysis+planning, core, shared, bmm, cis, tea, bmb",
    )

    args = parser.parse_args()
    source = Path(args.bmad_source)

    if not source.exists():
        print(f"❌ BMAD source path not found: {source}")
        sys.exit(2)

    # Parse scope
    scope_parts = set(args.scope.replace("+", " ").split())
    scope = scope_parts if scope_parts else {"all"}

    report = check_port(source, scope)
    print_report(report, args.scope)

    if report["bmad_only"]:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    cli_main()
