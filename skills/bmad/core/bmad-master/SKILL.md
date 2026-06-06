---
name: bmad:bmad-master
description: "Master BMAD orchestrator — project initialization, workflow routing, phase progression, and multi-agent coordination. Trigger on: BMAD, workflow-init, project status, initialization, orchestrate, bmad master, guide me."
version: 6.6.0
author: BMAD Community (Hermes port by im)
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [bmad, core, persona, bmad-master, orchestrator, workflow, methodology, project-management, agile]
    category: bmad
    related_skills: [analyst, pm, architect, dev, sm, ux-designer, builder]
---

# BMAD Master

**Role:** Meta-skill for extending the BMAD Method — creating custom agent personas, specialized workflows, document templates, and domain-specific skills.

**Core Purpose:** Enable users to customize BMAD for their specific domain by creating new agents, workflows, and templates that follow BMAD conventions.

## When to Use

- User wants a specialized agent for a domain not covered by existing skills
- User needs a custom workflow for their specific development process
- User wants to create reusable document templates
- User asks to "extend BMAD" or "customize for our organization"

## Creating a BMAD Skill

### SKILL.md Structure

Follow Hermes skill format:

```yaml
---
name: my-custom-skill
description: "What it does and trigger keywords."
version: 1.0.0
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [bmad, custom]
    category: bmad
    related_skills: [bmad-orchestrator]
---

# Skill Name

**Role:** One-line description.

**Core Purpose:** What value this skill provides.

## When to Use

[Trigger conditions and keywords]

## Core Workflow

[Step-by-step process the agent follows]

## Hermes Tool Usage

[Which tools and how to use them]

## Subagent Strategy

[Parallel execution patterns if applicable]
```

### Skill Creation Checklist

- [ ] Skills directory: `skills/bmad/{skill-name}/`
- [ ] SKILL.md with proper YAML frontmatter
- [ ] Clear trigger keywords in description
- [ ] Core workflow with numbered steps
- [ ] Hermes tool usage mapped to specific actions
- [ ] Related skills linked in metadata
- [ ] Version follows BMAD v6 numbering

## Creating Custom Workflows

Custom workflows extend BMAD's phase structure:

1. **Identify the gap** — What existing BMAD workflow doesn't cover?
2. **Define inputs/outputs** — What docs go in, what comes out?
3. **Design the process** — Sequential or parallel steps
4. **Specify tools** — Which Hermes tools are needed
5. **Add quality gates** — What validates completion?

**Example:** A `compliance-review` workflow:
- Input: Architecture document
- Process: Subagent check against GDPR/HIPAA/SOC2 checklists
- Output: Compliance report to `docs/compliance-review-*.md`

## Creating Document Templates

Templates use Markdown with `{{variable}}` placeholders:

```markdown
# {{document_title}}

**Author:** {{author}}
**Date:** {{date}}
**Project:** {{project_name}}

## Overview

{{overview_content}}

## {{section_name}}

{{section_content}}

## Decision Log

| Decision | Rationale | Date |
|----------|-----------|------|
{{#each decisions}}
| {{decision}} | {{rationale}} | {{date}} |
{{/each}}
```

## Integration with BMAD Orchestrator

New skills should be registered so the orchestrator can route to them:

1. Add skill name to orchestrator's `related_skills` metadata
2. Add routing rule to orchestrator's workflow routing table
3. Add trigger documentation to the project's `AGENTS.md` or `CLAUDE.md`

## Hermes Tool Usage

- `skill_manage` — Create new skills with `action='create'`
- `write_file` — Create template and reference files
- `read_file` — Read existing BMAD skills for patterns
- `skill_view` — Inspect existing skill formats

## Best Practices

1. **Follow existing patterns** — New skills should feel native to BMAD
2. **Keep SKILL.md focused** — Under 5K words, reference external docs for details
3. **Use 4 phases** — Map custom workflows to Analysis/Planning/Solutioning/Implementation
4. **Enable subagents** — Design for parallel execution where possible
5. **Include examples** — Show concrete session examples
6. **Test with real use cases** — Verify with actual project scenarios
