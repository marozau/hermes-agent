"""Slash command handler for /bmad:help — list available BMAD commands.

Displays all registered BMAD slash commands grouped by phase.
"""

from __future__ import annotations


def handler(ctx, args: str) -> str:
    """Show BMAD help: list all available slash commands."""
    return """**BMAD Slash Commands**

**Analysis:**
  `/bmad:init`          — Scaffold a new BMAD project
  `/bmad:status`        — Show current workflow status
  `/bmad:dashboard`     — Rich project dashboard
  `/bmad:product-brief` — Create product brief
  `/bmad:research`      — Conduct research
  `/bmad:brainstorm`    — Structured brainstorming
  `/bmad:document-project` — Generate project documentation
  `/bmad:quick-spec`    — Quick spec (single-turn analysis)

**Planning:**
  `/bmad:create-prd`    — Create product requirements document
  `/bmad:validate-prd`  — Validate PRD against checklist
  `/bmad:edit-prd`      — Edit existing PRD
  `/bmad:create-ux-design` — Create UX design spec

**Solutioning:**
  `/bmad:create-architecture` — Create architecture document
  `/bmad:epics-stories`       — Create epics and stories
  `/bmad:solutioning-gate-check` — Run solutioning gate check

**Implementation:**
  `/bmad:sprint-planning` — Create sprint plan
  `/bmad:create-story`    — Generate rich story spec
  `/bmad:dev-story`       — Implement a story
  `/bmad:code-review`     — Review implementation
  `/bmad:correct-course`  — Fix implementation issues
  `/bmad:quick-dev`       — Quick dev (single-turn implementation)

**TEA (Test-Architect) — Ungated:**
  `/bmad:test-framework` — Evaluate and scaffold test framework
  `/bmad:atdd`           — Acceptance Test-Driven Development
  `/bmad:test-design`    — Test design workflow
  `/bmad:test-review`    — Test quality review
  `/bmad:trace`          — Requirements traceability
  `/bmad:nfr`            — Non-Functional Requirements testing
  `/bmad:ci`             — CI/CD pipeline configuration
  `/bmad:automate`       — Test automation workflow

**CIS (Creative Intelligence) — Ungated:**
  `/bmad:brainstorming`         — Creative brainstorming (Carson)
  `/bmad:design-thinking`       — Design thinking (Maya)
  `/bmad:problem-solving`       — Creative problem-solving (Dr. Quinn)
  `/bmad:innovation-strategy`   — Innovation strategy (Victor)
  `/bmad:storytelling`          — Narrative design (Sophia)
  `/bmad:presentation`          — Presentation design (Caravaggio)

**BMB (Builder) — Ungated:**
  `/bmad:agent-builder`    — Build and refine BMAD agents
  `/bmad:module-builder`   — Create and scaffold BMAD modules
  `/bmad:workflow-builder` — Design and build BMAD workflows

**General:**
  `/bmad:help`            — Show this help
"""
