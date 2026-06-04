---
spec:
  persona: Analyst
  phase: analysis
  imperative_preamble: true
  verification:
    - "Research findings written to planning-artifacts/"
    - "Sources cited and verified"
    - "Recommendations provided"
---

Conduct structured research for the current project:

1. **Market Research** — Industry size, trends, growth rates, key players
2. **Competitive Analysis** — Feature matrix, strengths, weaknesses, positioning
3. **Technical Feasibility** — What's possible given constraints, technology assessment
4. **User Research** — Interviews, surveys, analytics data synthesis

Use `delegate_task` for parallel research across multiple sources:
- Agent 1: Market size and trends research
- Agent 2: Competitive landscape analysis
- Agent 3: Technical feasibility assessment

Write findings to `planning-artifacts/research-{project_name}-{date}.md`.
