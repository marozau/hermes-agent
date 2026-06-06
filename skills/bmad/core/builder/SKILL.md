---
name: builder
description: "Creates custom BMAD agents, skills, workflows, and templates — extend the BMAD Method for specific domains. Builder persona for meta-tooling. Trigger on: create custom agent, create skill, custom workflow, extend BMAD, customize, build template, new BMAD component."
version: 6.6.0
author: BMAD Community (Hermes port by im)
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [bmad, core, persona, builder, meta, skill-creation, templates, customization]
    category: bmad
    related_skills: [analyst, pm, architect, dev, sm, ux-designer, bmad-master]
---

# Builder

**Purpose:** Core orchestrator for the BMAD Method (Breakthrough Method for Agile AI-Driven Development), managing workflows, tracking status, and routing users through structured development phases.

## When to Use This Skill

Use this skill when the user:
- Asks to "initialize BMAD" or "set up BMAD"
- Checks "project status" or "workflow status"
- Wants to know "what's next" or "next steps"
- Mentions "BMAD phases" or "BMAD workflow"
- Starts a new project and needs structure

## Core Responsibilities

1. **Project Initialization** — Set up BMAD directory structure and configuration
2. **Status Tracking** — Monitor progress across 4 development phases
3. **Workflow Routing** — Direct users to appropriate next steps based on project state
4. **Progress Management** — Maintain workflow status and completion tracking

## BMAD Method Overview

### 4 Development Phases

1. **Analysis** (Optional) — Research, brainstorming, product brief
2. **Planning** (Required) — PRD or Tech Spec based on project complexity
3. **Solutioning** (Conditional) — Architecture design for medium+ projects
4. **Implementation** (Required) — Sprint planning, stories, development

### Project Levels

### Templates

BMAD templates provide structured output formats for each phase. Templates
live in `skills/bmad/templates/`:

| Template | Phase | Purpose |
|----------|-------|---------|
| `product-brief.md` | Analysis | Discovery output |
| `prd.md` | Planning | Requirements document |
| `architecture.md` | Solutioning | System design |
| `tech-spec.md` | Planning | Technical specification |
| `bmm-workflow-status.template.yaml` | All | Machine-readable state |
| `sprint-status.template.yaml` | Implementation | Sprint tracking |

### Trigger Strategy

Each skill has trigger keywords in its description. Hermes auto-loads skills
when conversation matches triggers:
- "BMAD", "workflow-init", "project status" → `bmad-orchestrator`
- "PRD", "product requirements", "tech spec" → `product-manager`
- "architecture", "system design", "tech stack" → `system-architect`
- "sprint planning", "user stories", "velocity" → `scrum-master`
- "implement story", "build feature", "code review" → `developer`

Use these templates when creating phase artifacts. Copy the relevant template
and fill it in — don't invent your own format unless the template is missing
a needed section.

### Project Levels

| Level | Scope | Typical Stories | Required Docs |
|-------|-------|-----------------|---------------|
| 0 | Single atomic change | 1 | Tech Spec only |
| 1 | Small feature | 1-10 | Tech Spec |
| 2 | Medium feature set | 5-15 | PRD + Architecture |
| 3 | Complex integration | 12-40 | PRD + Architecture |
| 4 | Enterprise expansion | 40+ | PRD + Architecture |

## Workflow Commands

### workflow-init

Initialize BMAD structure in the current project.

**Steps:**
1. Create directory structure:

```
bmad/
├── config.yaml
└── agent-overrides/

docs/
├── bmm-workflow-status.yaml
└── stories/
```

2. Collect project information:
   - Project name
   - Project type (web-app, mobile-app, api, game, library, other)
   - Project level (0-4)
   - Communication language (default: English)

3. Create `bmad/config.yaml` with project metadata:

```yaml
project_name: "MyApp"
project_type: "web-app"
project_level: 2
output_folder: "docs"
communication_language: "English"
```

4. Create `docs/bmm-workflow-status.yaml` with conditional requirements:
   - PRD: required if level >= 2, else recommended
   - Tech-spec: required if level <= 1, else optional
   - Architecture: required if level >= 2, else optional
   - Product Brief: recommended for levels 2+

5. Display initialization summary and offer to start recommended workflow.

**Status indicators:**
- ✓ = Completed (shows file path)
- ⚠ = Required but not started
- → = Current phase
- - = Optional/not required

### workflow-status

Check project status and recommend next steps.

**Steps:**
1. Load `bmad/config.yaml` with `read_file`
2. Load `docs/bmm-workflow-status.yaml`
3. Scan `docs/` for completed artifacts with `search_files`
4. Determine current phase and next recommendation
5. Display visual status report

**Recommendation logic:**
1. No product-brief + new project → recommend product-brief (triggers `business-analyst`)
2. Product-brief complete, no PRD/tech-spec → recommend PRD (level 2+) or tech-spec (level 0-1)
3. Planning complete, no architecture, level 2+ → recommend architecture
4. Planning complete → recommend sprint-planning
5. Sprint active → recommend create-story or dev-story

**If project not initialized:** Inform user and offer workflow-init.

## Workflow Routing

Route users to specialized BMAD skills:

| Phase | Skill | Triggers |
|-------|-------|----------|
| Analysis | business-analyst | product-brief, brainstorm, research |
| Analysis | creative-intelligence | SCAMPER, SWOT, Six Thinking Hats |
| Planning | product-manager | prd, tech-spec, requirements |
| Planning | ux-designer | create-ux-design, wireframes |
| Solutioning | system-architect | architecture, tech stack, API design |
| Implementation | scrum-master | sprint-planning, create-story |
| Implementation | developer | dev-story, implement, build feature |

## Configuration Files

### Project Config (`bmad/config.yaml`)

```yaml
project_name: "MyApp"
project_type: "web-app"  # web-app, mobile-app, api, game, library, other
project_level: 2         # 0-4
output_folder: "docs"
communication_language: "English"
```

### Workflow Status (`docs/bmm-workflow-status.yaml`)

Tracks completion of each workflow with status values:
- `"optional"` — Can be skipped
- `"recommended"` — Strongly suggested
- `"required"` — Must be completed
- `"{file-path}"` — Completed (shows output file)
- `"skipped"` — Explicitly skipped

## Error Handling

- **Config missing** → Suggest workflow-init, explain BMAD not initialized
- **Invalid YAML** → Show error location, offer to fix or reinitialize
- **Template missing** → Use inline fallback, log warning, continue
- **Status file inconsistent** → Validate against project level, offer to regenerate

## Hermes Integration Notes

This skill uses Hermes tools:
- `read_file` → Read config, status, and artifact files
- `search_files` → Scan docs/ for completed artifacts
- `write_file` → Create config and status files
- `terminal` → Run validation scripts
- `todo` → Track multi-step initialization

**Subagent strategy:** For project initialization, use `delegate_task` with 3 parallel agents:
1. Agent 1: Create directory structure
2. Agent 2: Generate project config from template
3. Agent 3: Generate workflow status file with level-based requirements

After all complete, validate outputs and display summary.

## Quick Reference

- This skill is the entry point for all BMAD Method workflows
- Always check project state before recommending workflows
- Maintain phase-based progression — don't skip required phases
- Hand off to specialized BMAD skills for detailed workflows

## Current Limitations — Autonomous Execution

⚠️ **This skill describes an ideal workflow but does NOT execute autonomously.**
The BMAD implementation is currently **skills-only** — SKILL.md files with
instructions for the LLM to follow when the user explicitly invokes a phase.

What is NOT automated:
- **Sub-agent spawning** — No profile-to-profile delegation exists. The
  orchestrator can't autonomously spawn a business-analyst sub-agent.
  `delegate_task` can spawn ephemeral sub-agents within the same session,
  but there's no cross-profile persistence.
- **YAML workflow tracking** — `bmm-workflow-status.yaml` templates exist but
  are not auto-read/written between sessions. Status persists only as long
  as the current conversation.
- **Phase gates** — Described as "check before moving" but not enforced.
  No gate validation runs automatically.
- **Cross-session persistence** — No cron job or Prefect flow monitors BMAD
  workflow state between chat sessions.

**Tracking:** Kanban task `t_f016e9f3` covers the automation gap with 4
proposed approaches (enhanced skill, cron, Prefect flow, ACP delegation).

**Reference:** `references/bmad-automation-gaps.md` has the detailed gap
analysis, architecture notes, and Prefect integration plan.
