# BMAD Plugin — Hermes Integration for BMAD Method v6.6.0

Registers hooks, CLI commands, and slash commands for the BMAD structured
product-development methodology inside Hermes Agent.

## Architecture

```
plugins/bmad/
├── __init__.py          # Plugin entry: register() called by Hermes loader
├── plugin.yaml          # Plugin metadata, version, dependencies
├── commands/            # /bmad:* slash command handlers
│   ├── init.py          # bmad:init  — scaffold BMAD project
│   ├── status.py        # bmad:status — show project phase & progress
│   ├── help.py          # bmad:help   — list available BMAD commands
│   ├── dashboard.py     # bmad:dashboard — live HTML dashboard
│   ├── party_mode.py    # bmad:party-mode — multi-persona round table
│   ├── party-mode.md    # Inline prompt body for party-mode
│   └── ...              # One handler per BMAD skill (40+ total)
├── hooks/               # Event hook implementations
│   ├── on_session_start.py
│   ├── pre_tool_call.py
│   ├── post_tool_call.py
│   ├── transform_terminal_output.py
│   └── subagent_stop.py
├── lib/                 # Shared utility modules
│   ├── phases.py        # Phase progression logic
│   ├── status.py        # Status tracking
│   └── templates.py     # Skill-instance templates
└── tests/
    └── unit/            # Unit tests for plugin components
```

## Registered Hooks

| Hook | Purpose |
|------|---------|
| `on_session_start` | Activate BMAD context; load project config if in a BMAD project |
| `pre_tool_call` | Inject BMAD workflow gates before tool execution |
| `post_tool_call` | Update phase status after tool execution |
| `transform_terminal_output` | Annotate terminal output with BMAD context |
| `subagent_stop` | Capture sub-agent completion state |

All hooks are wrapped with `_catch_all()` so a broken hook never breaks the user's
session (architecture §4 enforcement).

## CLI Commands

Available via `hermes <command>`:

### `hermes bmad-init`

Scaffold a new BMAD project in the current directory.

```
hermes bmad-init [--project-name NAME] [--project-type TYPE]
                 [--project-level LEVEL] [--user-name NAME]
                 [--force] [--non-interactive]
```

Creates `bmad/config.yaml`, `planning-artifacts/`, `implementation-artifacts/stories/`,
and a `workflow-status.yaml` with level-appropriate slots.

### `hermes bmad-check-port`

Verify BMAD port completeness against the v6.6.0 specification.

```
hermes bmad-check-port [--scope SCOPE] [--bmad-source PATH]
```

Scope: `analysis`, `planning`, `solutioning`, `implementation`, or `all` (default).

## Slash Commands

All available via `/bmad:<name>` from any Hermes chat interface.

### Meta
| Command | Description |
|---------|-------------|
| `/bmad:init` | Scaffold new BMAD project |
| `/bmad:status` | Show current project phase, phase gates, and workflow progress |
| `/bmad:help` | List available BMAD commands and this session's gates |
| `/bmad:dashboard` | Open live HTML dashboard |

### Analysis Phase
`product-brief`, `research`, `brainstorm`, `document-project`, `quick-spec`

### Planning Phase
`create-prd`, `validate-prd`, `edit-prd`, `create-ux-design`

### Solutioning Phase
`create-architecture`, `epics-stories`, `solutioning-gate-check`

### Implementation Phase
`sprint-planning`, `create-story`, `dev-story`, `code-review`,
`correct-course`, `quick-dev`

### TEA Phase (ungated)
`test-framework`, `atdd`, `test-design`, `test-review`, `trace`, `nfr`,
`ci`, `automate`

### CIS Phase (ungated)
`brainstorming`, `design-thinking`, `problem-solving`, `innovation-strategy`,
`storytelling`, `presentation`

### BMB Phase (ungated)
`agent-builder`, `module-builder`, `workflow-builder`

### Meta
`party-mode` — multi-persona round table discussion using BMAD personas.

## Party Mode

`/bmad:party-mode <topic>` or `/bmad:party-mode --fan-out <topic>`

Two modes:
- **Inline (default)**: The LLM reads the agent-manifest and produces all persona
  voices in-context. Fast, one turn.
- **Fan-out** (`--fan-out`): Spawns one sub-agent per selected persona, then
  aggregates results. More expensive but genuinely multi-agent.

The manifest is read from `~/.hermes/skills/bmad/_shared/agent-manifest.yaml`.
See `commands/party-mode.md` for the full inline prompt template with protocol
details, selection criteria, anti-patterns, and output format.

## Configuration

The plugin auto-activates when the current working directory contains a
`bmad/config.yaml`. Outside a BMAD project, all hooks are silent no-ops.

```yaml
# bmad/config.yaml (created by hermes bmad-init)
project_name: ""
project_type: "other"      # web-app, mobile-app, api, library, game, other
project_level: 1           # 0–4 BMAD rigor level
user_name: ""
phases:
  analysis: {}
  planning: {}
  solutioning: {}
  implementation: {}
  tea: {}
```

## Development

### Adding a new command

1. Create `commands/<name>.py` with a `handler(ctx, args: str) -> str` function
2. Create `commands/<name>.md` with the inline prompt template (if applicable)
3. Import and register in `plugins/bmad/__init__.py` → `register()` function
4. Add unit tests in `tests/unit/`
5. Update this README's command table

### Adding a new hook

1. Create `hooks/<hook_name>.py` with the handler function
2. Register in `plugins/bmad/__init__.py` using `ctx.register_hook()`
3. Wrap with `_catch_all("hook_name")` to prevent session breakage
