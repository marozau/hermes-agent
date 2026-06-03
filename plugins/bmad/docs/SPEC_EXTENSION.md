# BMAD Command Spec Extension

> **Version:** 1.0.0  
> **License:** CC-BY-4.0  
> **Compatibility:** BMAD Method v6.6.0+, Hermes Agent v0.14.0+

## Overview

Every BMAD command `.md` file can include a YAML `spec:` frontmatter block
that declares the command's persona, phase, verification predicates, and
rendering hints.  The runtime renderer wraps the body with an imperative
preamble, verification checklist, and stop condition — eliminating the
need for per-command imperative-voice authoring.

## Frontmatter Format

```yaml
---
spec:
  persona: Dev
  phase: implementation
  predicate_module: plugins.bmad.predicates.dev_story
  imperative_preamble: true
  output_artifacts: []
  verification:
    - description: All tests pass
      predicate: predicates.dev_story.tests_pass
    - description: No regressions
      predicate: predicates.dev_story.no_regressions
    - description: Manual review complete
  metadata:
    level: 2
---

## Instructions

Your command body content here...
```

## Fields

### Required

| Field | Type | Description |
|---|---|---|
| `persona` | string | Role the LLM adopts (e.g. "Dev", "QA", "SM", "Analyst") |
| `phase` | string | BMAD phase name (e.g. "implementation", "planning", "analysis") |
| `verification` | list | Non-empty list of verification items |

### Optional

| Field | Type | Default | Description |
|---|---|---|---|
| `imperative_preamble` | bool | `true` | If false, renderer omits "EXECUTE NOW" preamble |
| `predicate_module` | string | `null` | Dotted path to predicate module |
| `output_artifacts` | list[string] | `[]` | Expected output files for stop condition |
| `metadata` | dict | `{}` | Freeform key-value pairs for extensions |

### Verification Items

Each item in `verification` is either:

- **String** (manual check): `"- description: Review complete"`
- **Dict** (with predicate): `{"description": "Tests pass", "predicate": "predicates.dev_story.tests_pass"}`

## Rendering

The renderer (`plugins.bmad/lib/render.py`) composes:

1. **Preamble** — `"EXECUTE NOW. You are {persona}."` (skipped if `imperative_preamble: false`)
2. **Body** — The markdown content after the frontmatter
3. **Verification Checklist** — `- [ ] {description}` for each item
4. **Stop Condition** — Lists `output_artifacts` or "Complete all verification checklist items"

## Predicates

Predicate functions follow the signature:

```python
def check_name(project_dir: Path, **kwargs) -> tuple[bool | None, str]:
    """Returns (passed, reason).
    - True: check passed
    - False: check failed
    - None: check deferred (manual or needs LLM judge)
    """
```

Dotted paths resolve as: `predicates.{command_name}.{function_name}` or
`plugins.bmad.predicates.{command_name}.{function_name}`.

## Ecosystem Compatibility

The `spec:` frontmatter is:

- **SKILL.md compatible** — Hermes skill loader ignores YAML frontmatter
- **Cursor MDC compatible** — MDC format uses YAML frontmatter natively
- **Goose recipe compatible** — Recipe format supports YAML headers

## Informational Commands

Commands like `help`, `status`, `dashboard`, `list`, `version`, `debug`
should set `imperative_preamble: false` to avoid the "EXECUTE NOW" prefix.
