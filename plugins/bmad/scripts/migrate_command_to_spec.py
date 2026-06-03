"""Bulk migration script for Epic 12 — Story 12.9.

Adds spec: frontmatter to all command .md files.
"""

from pathlib import Path
import yaml

COMMANDS_DIR = Path("/Users/im/usr-local/hermes/worktree/hermes-agent/plugins/bmad/commands")

# Command → (persona, phase, imperative_preamble, verification_items)
COMMAND_SPECS = {
    # Analysis
    "product-brief": ("Analyst", "analysis", True, [
        "Product brief written to planning-artifacts/",
        "Problem statement is clear and specific",
        "Success metrics are measurable",
    ]),
    "document-project": ("Analyst", "analysis", True, [
        "Project documentation complete",
        "Architecture patterns identified",
        "Tech stack catalogued",
    ]),
    "brainstorm": ("Analyst", "analysis", True, [
        "Ideas documented in planning-artifacts/",
        "At least 3 approaches considered",
        "Trade-offs documented",
    ]),
    "research": ("Analyst", "analysis", True, [
        "Research findings written to planning-artifacts/",
        "Sources cited and verified",
        "Recommendations provided",
    ]),

    # Planning
    "create-prd": ("PM", "planning", True, [
        "PRD written to planning-artifacts/",
        "Functional requirements complete",
        "Non-functional requirements defined",
        "Epics and stories decomposed",
    ]),
    "edit-prd": ("PM", "planning", True, [
        "PRD updated in planning-artifacts/",
        "Changes are traceable",
        "No conflicting requirements introduced",
    ]),
    "validate-prd": ("PM", "planning", True, [
        "PRD validation report generated",
        "All sections present and complete",
        "No contradictions found",
    ]),
    "create-ux-design": ("UX Designer", "planning", True, [
        "UX design written to planning-artifacts/",
        "User flows documented",
        "Accessibility considerations addressed",
    ]),

    # Solutioning
    "create-architecture": ("Architect", "solutioning", True, [
        "Architecture document written",
        "Component model defined",
        "API contracts specified",
        "Data model documented",
    ]),
    "epics-stories": ("SM", "solutioning", True, [
        "Epics decomposed into stories",
        "Each story has acceptance criteria",
        "Dependencies identified",
    ]),
    "solutioning-gate-check": ("Architect", "solutioning", True, [
        "PRD to Architecture alignment verified",
        "Architecture to Epics alignment verified",
        "No blocking gaps identified",
    ]),
    "sprint-planning": ("SM", "implementation", True, [
        "Sprint backlog defined",
        "Story points estimated",
        "Capacity plan created",
        "Dependencies mapped",
    ]),

    # Implementation
    "create-story": ("SM", "implementation", True, [
        "Story spec written",
        "Acceptance criteria defined",
        "Implementation notes provided",
    ]),
    "code-review": ("QA", "implementation", True, [
        "Code review completed",
        "Findings categorized by severity",
        "Actionable recommendations provided",
    ]),
    "correct-course": ("Dev", "implementation", True, [
        "Issues identified and prioritized",
        "Fixes implemented",
        "Tests updated",
    ]),
    "quick-dev": ("Dev", "implementation", True, [
        "Feature implemented",
        "Tests pass",
        "Code follows conventions",
    ]),
    "quick-spec": ("Dev", "implementation", True, [
        "Spec written",
        "Requirements clear",
        "Implementation approach defined",
    ]),

    # TEA (Test Engineering & Automation)
    "test-design": ("QA", "implementation", True, [
        "Test architecture defined",
        "Risk areas identified",
        "Test cases specified",
    ]),
    "test-framework": ("QA", "implementation", True, [
        "Framework selected and configured",
        "Test scaffolding created",
        "Documentation written",
    ]),
    "test-review": ("QA", "implementation", True, [
        "Test quality assessed",
        "Coverage gaps identified",
        "Recommendations provided",
    ]),
    "automate": ("QA", "implementation", True, [
        "Test automation scripts created",
        "CI integration configured",
        "Tests run successfully",
    ]),
    "atdd": ("QA", "implementation", True, [
        "Acceptance tests defined",
        "Gherkin scenarios written",
        "Step definitions implemented",
    ]),
    "nfr": ("QA", "implementation", True, [
        "NFR assessment complete",
        "Security review done",
        "Performance benchmarks defined",
    ]),
    "ci": ("QA", "implementation", True, [
        "CI pipeline configured",
        "Test stages defined",
        "Reporting set up",
    ]),
    "trace": ("QA", "implementation", True, [
        "Traceability matrix created",
        "Coverage gaps identified",
        "Gate decisions documented",
    ]),

    # CIS (Creative & Innovation)
    "brainstorming": ("Carson", "analysis", True, [
        "Ideas generated",
        "Possibilities explored",
        "Creative directions documented",
    ]),
    "design-thinking": ("Maya", "analysis", True, [
        "User research synthesized",
        "Empathy map created",
        "Prototypes defined",
    ]),
    "innovation-strategy": ("Victor", "analysis", True, [
        "Innovation opportunities mapped",
        "Market disruption vectors identified",
        "Strategic recommendations provided",
    ]),
    "presentation": ("Caravaggio", "analysis", True, [
        "Presentation structure defined",
        "Key messages clear",
        "Visual design planned",
    ]),
    "problem-solving": ("Dr. Quinn", "analysis", True, [
        "Root cause identified",
        "Solution options evaluated",
        "Recommendation provided",
    ]),
    "storytelling": ("Sophia", "analysis", True, [
        "Narrative crafted",
        "Story arc defined",
        "Audience engagement planned",
    ]),

    # Builder
    "agent-builder": ("Builder", "solutioning", True, [
        "Agent configuration created",
        "Skills composed",
        "Quality checks pass",
    ]),
    "module-builder": ("Builder", "solutioning", True, [
        "Module scaffolded",
        "Setup skill generated",
        "Validation passes",
    ]),
    "workflow-builder": ("Builder", "solutioning", True, [
        "Workflow designed",
        "Steps defined",
        "Integrity checks pass",
    ]),

    # Init
    "init": ("Analyst", "analysis", True, [
        "BMAD project initialized",
        "Config created",
        "Directory structure set up",
    ]),
}


def migrate_command(name: str, persona: str, phase: str, preamble: bool, verification: list[str]):
    """Add spec: frontmatter to a command .md file."""
    md_path = COMMANDS_DIR / f"{name}.md"
    if not md_path.exists():
        print(f"  ⚠️  {name}.md not found")
        return False

    content = md_path.read_text()

    # Skip if already has spec:
    if content.startswith("---") and "spec:" in content:
        print(f"  ✅ {name}.md already has spec")
        return True

    # Build verification YAML
    ver_yaml = "\n".join(f'    - "{v}"' for v in verification)

    # Build frontmatter
    fm = f"""---
spec:
  persona: {persona}
  phase: {phase}
  imperative_preamble: {"true" if preamble else "false"}
  verification:
{ver_yaml}
---

"""
    new_content = fm + content
    md_path.write_text(new_content)
    print(f"  ✅ {name}.md migrated")
    return True


def migrate_informational(name: str):
    """Migrate informational commands with imperative_preamble: false."""
    return migrate_command(
        name,
        persona="System",
        phase="informational",
        preamble=False,
        verification=[f"{name} output displayed correctly"],
    )


def main():
    print("=== Bulk Migration — Story 12.9 ===\n")

    success = 0
    failed = 0

    # Migrate standard commands
    for name, (persona, phase, preamble, verification) in COMMAND_SPECS.items():
        if migrate_command(name, persona, phase, preamble, verification):
            success += 1
        else:
            failed += 1

    # Migrate informational commands
    informational = ["help", "status", "dashboard", "party-mode"]
    for name in informational:
        if name not in COMMAND_SPECS:
            if migrate_informational(name):
                success += 1
            else:
                failed += 1

    print(f"\n=== Results: {success} migrated, {failed} failed ===")


if __name__ == "__main__":
    main()
