# scope_discipline_v1 — LLM Judge Prompt (LOCKED)

> **FROZEN**: 2026-06-04 | Metric: dev_story_composite_v1 | Weight: 0.2

## Prompt

Score 0.0–1.0: Does the diff stay within the story's scope?

**1.0** = Only changes directly required by the story's acceptance criteria.
**0.5** = Minor tangential changes (e.g., formatting fixes in touched files).
**0.0** = Significant changes unrelated to the story (feature creep, unrelated refactors).

### Scoring rubric

- Files touched should map 1:1 to the story's "Touches:" section
- New files are acceptable only if the AC requires them
- Refactoring within touched files is acceptable if it serves the AC
- Refactoring outside touched files is scope creep unless the AC explicitly requires it
