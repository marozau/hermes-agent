---
name: bmad:research
description: |
  Analysis-phase research skill — market research, competitive analysis, domain
  research, technical feasibility. Trigger on: research, market research,
  competitive analysis, domain research, technical research.
  Phase 1 Analysis. Requires web search.
version: 6.6.0
author: BMAD Community (Hermes port by im)
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [bmad, bmm, research]
    category: bmad
---

# /bmad:research — Research Workflow

**Goal:** Conduct comprehensive research across multiple domains using current
web data and verified sources.

**Phase:** 1 - Analysis | **Agent:** Analyst

---

## Execution

### Step 1: Parse intent

Extract from the user's message:
- **Research type:** market | competitive | domain | technical
- **Topic:** what to research
- **Goals:** what decisions will this research inform?

If unclear, ask clarifying questions before proceeding.

### Step 2: Load template

Read the research template at `skills/bmad/templates/research.template.md`.
**Follow this template exactly** for output structure, substituting placeholder
values with actual research content.

### Step 3: Execute research

Use web search to gather current data. For each source:
- Record URL and access date
- Verify source credibility (official docs > blogs > forums)
- Cross-check key claims against 2+ sources

### Step 4: Synthesize and output

Populate the template with findings. Output must include:
- Frontmatter with research_type, research_topic, research_goals, date
- Research Overview (scope + methodology)
- Findings (structured per subtopic)
- Citations (≥3 distinct sources)
- Conclusions / recommendations

Save to: `planning-artifacts/research/{type}-{topic}-research-{date}.md`

---

## Template Reference

```markdown
---
stepsCompleted: []
inputDocuments: []
workflowType: 'research'
lastStep: 1
research_type: '{{research_type}}'
research_topic: '{{research_topic}}'
research_goals: '{{research_goals}}'
user_name: '{{user_name}}'
date: '{{date}}'
web_research_enabled: true
source_verification: true
---

# Research Report: {{research_type}}

**Date:** {{date}}
**Author:** {{user_name}}**
**Research Type:** {{research_type}}

---

## Research Overview

[Scope, methodology, and key questions]

## Findings

[Structured findings per subtopic]

## Sources

[URL + date accessed for each source]

## Conclusions

[Key takeaways and recommendations]
```

---

## Anti-patterns

- DO NOT proceed without clarifying the research type and goals
- DO NOT cite sources without URLs or access dates
- DO NOT present opinions as facts — distinguish verified claims from inference
- DO NOT skip the methodology section — future readers need to know how you searched
