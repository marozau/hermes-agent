# Code Review OCR Integration (Epic 8)

## Overview

The `/bmad:code-review` command supports an optional 4th independent review
source: **OCR (Open Code Review)** by Alibaba. OCR runs in PARALLEL with the
existing 3 LLM reviewers (Blind Hunter, Edge Case Hunter, Acceptance Auditor)
and produces a **consensus signal** when multiple independent sources agree.

## Configuration

Add to `bmad/config.yaml`:

```yaml
code_review:
  ocr:
    enabled: true           # OI-9: opt-in, default false
    rule_path: .opencodereview/rule.json  # optional custom rules
    timeout_seconds: 120    # per-file timeout
    languages: [python, typescript, rust]  # OI-15: only these languages
```

### Profile Overrides (Story 8.6)

Different Hermes profiles can override OCR settings:

```yaml
# In profile config
delegation:
  skill_overrides:
    bmad-code-review:
      ocr:
        enabled: true
        timeout_seconds: 60
```

## How It Works

1. **Fan-out**: 4 reviewers run in parallel via `lib/delegation.fan_out()`
   - 3 LLM subagents (Blind Hunter, Edge Case Hunter, Acceptance Auditor)
   - 1 subprocess (OCR via `ocr review --format json`)

2. **Triage**: All 4 sources are normalized and merged
   - OCR findings: `HIGH→MAJOR`, `MED→MINOR`, `LOW→NIT` (OD-8)
   - Consensus classification based on independent agreement

3. **Consensus Signal**:

   | Sources agreeing | Classification | Meaning |
   |---|---|---|
   | ocr only | `patch` | Rule-driven; lower judgment confidence |
   | blind only | `decision_needed` | High false-positive risk |
   | blind + ocr | `patch` | High confidence; 2 independent sources |
   | blind + edge + ocr | `patch_strong` | Strong; mechanical fix |
   | blind + edge + auditor + ocr | `must_fix` | Unanimous; treat as BLOCKER |

## Custom Rule Files

Create `bmad/.opencodereview/` in your project:

```
bmad/.opencodereview/
├── rule.json          # File routing rules
├── python.md          # Python-specific rules
├── typescript.md      # TypeScript-specific rules
└── rust.md            # Rust-specific rules
```

### rule.json format

```json
{
  "version": "1.0",
  "rules": [
    {"pattern": "**/*.py", "rule": "python.md"},
    {"pattern": "**/*.ts", "rule": "typescript.md"}
  ],
  "disabled_builtin": ["java", "kotlin"]
}
```

### Rule file format

```markdown
### RULE_ID: Rule name
**Severity:** HIGH|MEDIUM|LOW
**Pattern:** What to look for
**Fix:** How to fix it
**Source:** Where this rule came from
```

## Hard Invariants

| # | Invariant | Enforcement |
|---|---|---|
| OI-9 | OCR is OPT-IN | `enabled: false` default in config |
| OI-10 | OCR not installed = WARN | `check_ocr_installed()` returns False → empty findings |
| OI-11 | OCR runs in PARALLEL | `fan_out()` with `kind: subprocess`; never injects into LLM prompts |
| OI-12 | OCR is independent source | Consensus classification, not authority |
| OI-13 | Per-project rules | `bmad/.opencodereview/` committed to repo |
| OI-14 | JSON schema contract | `parse_ocr_json()` raises on missing fields |
| OI-15 | Java rules disabled | `disabled_builtin: ["java"]` in rule.json |

## Installation

```bash
# Install OCR CLI
pip install open-code-review

# Verify
ocr --version

# Test on a diff
git diff | ocr review --format json
```

If OCR is not installed, the other 3 reviewers run normally with a warning (OI-10).
