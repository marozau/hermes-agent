# spec_faithfulness_v1 — LLM Judge Prompt (LOCKED)

> **FROZEN**: 2026-06-04 | Metric: dev_story_composite_v1 | Weight: 0.2

## Prompt

Score 0.0–1.0: Does the implementation match the spec requirements?

**1.0** = All acceptance criteria satisfied; implementation matches spec exactly.
**0.5** = Most criteria met; minor deviations (e.g., naming differences, partial coverage).
**0.0** = Critical criteria unmet; implementation diverges from spec intent.

### Scoring rubric

- Each acceptance criterion in the story spec must be addressed
- "Given/When/Then" scenarios must be verifiable in the diff
- Edge cases mentioned in the spec must be handled
- Deviations from spec must be documented in the commit message
- Anti-rationalization: "close enough" is not faithful — spec is the contract
