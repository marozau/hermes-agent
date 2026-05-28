# Hermes Agent - Development Guide

Instructions for AI coding assistants and developers working on the hermes-agent codebase.

## Development Environment

```bash
# Prefer .venv; fall back to venv if that's what your checkout has.
source .venv/bin/activate   # or: source venv/bin/activate
```

`scripts/run_tests.sh` probes `.venv` first, then `venv`, then
`/Users/im/usr-local/hermes/.venv` (dev repo venv).

## Project Structure

File counts shift constantly — don't treat the tree below as exhaustive.
The canonical source is the filesystem. The notes call out the load-bearing
entry points you'll actually edit.

```
hermes-agent/
├── run_agent.py          # AIAgent class — core conversation loop (~12k LOC)
├── model_tools.py        # Tool orchestration, discover_builtin_tools(), handle_function_call()
├── toolsets.py           # Toolset definitions, _HERMES_CORE_TOOLS list
├── cli.py                # HermesCLI class — interactive CLI orchestrator (~11k LOC)
├── hermes_state.py       # SessionDB — SQLite session store (FTS5 search)
├── hermes_constants.py   # get_hermes_home(), display_hermes_home() — profile-aware paths
├── hermes_logging.py     # setup_logging() — agent.log / errors.log / gateway.log (profile-aware)
├── batch_runner.py       # Parallel batch processing
├── agent/                # Agent internals (provider adapters, memory, caching, compression, etc.)
├── hermes_cli/           # CLI subcommands, setup wizard, plugins loader, skin engine
├── tools/                # Tool implementations — auto-discovered via tools/registry.py
│   └── environments/     # Terminal backends (local, docker, ssh, modal, daytona, singularity)
├── gateway/              # Messaging gateway — run.py + session.py + platforms/
│   ├── platforms/        # Adapter per platform (telegram, discord, slack, whatsapp,
│   │                     #   homeassistant, signal, matrix, mattermost, email, sms,
│   │                     #   dingtalk, wecom, weixin, feishu, qqbot, bluebubbles,
│   │                     #   yuanbao, webhook, api_server, ...). See ADDING_A_PLATFORM.md.
│   └── builtin_hooks/    # Extension point for always-registered gateway hooks (none shipped)
├── plugins/              # Plugin system (see "Plugins" section below)
│   ├── memory/           # Memory-provider plugins (honcho, mem0, supermemory, ...)
│   ├── context_engine/   # Context-engine plugins
│   ├── model-providers/  # Inference backend plugins (openrouter, anthropic, gmi, ...)
│   ├── kanban/           # Multi-agent board dispatcher + worker plugin
│   ├── hermes-achievements/  # Gamified achievement tracking
│   ├── observability/    # Metrics / traces / logs plugin
│   ├── image_gen/        # Image-generation providers
│   ├── preflight/        # FAMA Tier-2 #3 / Epic 7 — pre_llm_call shim that calls lib/hermes_preflight
│   └── <others>/         # disk-cleanup, example-dashboard, google_meet, platforms,
│                         #   spotify, strike-freedom-cockpit, ...
├── optional-skills/      # Heavier/niche skills shipped but NOT active by default
├── skills/               # Built-in skills bundled with the repo
├── ui-tui/               # Ink (React) terminal UI — `hermes --tui`
│   └── src/              # entry.tsx, app.tsx, gatewayClient.ts + app/components/hooks/lib
├── tui_gateway/          # Python JSON-RPC backend for the TUI
├── acp_adapter/          # ACP server (VS Code / Zed / JetBrains integration)
├── cron/                 # Scheduler — jobs.py, scheduler.py
├── scripts/              # run_tests.sh, release.py, auxiliary scripts
├── website/              # Docusaurus docs site
└── tests/                # Pytest suite (~17k tests across ~900 files as of May 2026)
```

**User config:** `~/.hermes/config.yaml` (settings), `~/.hermes/.env` (API keys only).
**Logs:** `~/.hermes/logs/` — `agent.log` (INFO+), `errors.log` (WARNING+),
`gateway.log` when running the gateway. Profile-aware via `get_hermes_home()`.
Browse with `hermes logs [--follow] [--level ...] [--session ...]`.

## File Dependency Chain

```
tools/registry.py  (no deps — imported by all tool files)
       ↑
tools/*.py  (each calls registry.register() at import time)
       ↑
model_tools.py  (imports tools/registry + triggers tool discovery)
       ↑
run_agent.py, cli.py, batch_runner.py, environments/
```

---

## AIAgent Class (run_agent.py)

The real `AIAgent.__init__` takes ~60 parameters (credentials, routing, callbacks,
session context, budget, credential pool, etc.). The signature below is the
minimum subset you'll usually touch — read `run_agent.py` for the full list.

```python
class AIAgent:
    def __init__(self,
        base_url: str = None,
        api_key: str = None,
        provider: str = None,
        api_mode: str = None,              # "chat_completions" | "codex_responses" | ...
        model: str = "",                   # empty → resolved from config/provider later
        max_iterations: int = 90,          # tool-calling iterations (shared with subagents)
        enabled_toolsets: list = None,
        disabled_toolsets: list = None,
        quiet_mode: bool = False,
        save_trajectories: bool = False,
        platform: str = None,              # "cli", "telegram", etc.
        session_id: str = None,
        skip_context_files: bool = False,
        skip_memory: bool = False,
        credential_pool=None,
        # ... plus callbacks, thread/user/chat IDs, iteration_budget, fallback_model,
        # checkpoints config, prefill_messages, service_tier, reasoning_config, etc.
    ): ...

    def chat(self, message: str) -> str:
        """Simple interface — returns final response string."""

    def run_conversation(self, user_message: str, system_message: str = None,
                         conversation_history: list = None, task_id: str = None) -> dict:
        """Full interface — returns dict with final_response + messages."""
```

### Agent Loop

The core loop is inside `run_conversation()` — entirely synchronous, with
interrupt checks, budget tracking, and a one-turn grace call:

```python
while (api_call_count < self.max_iterations and self.iteration_budget.remaining > 0) \
        or self._budget_grace_call:
    if self._interrupt_requested: break
    response = client.chat.completions.create(model=model, messages=messages, tools=tool_schemas)
    if response.tool_calls:
        for tool_call in response.tool_calls:
            result = handle_function_call(tool_call.name, tool_call.args, task_id)
            messages.append(tool_result_message(result))
        api_call_count += 1
    else:
        return response.content
```

Messages follow OpenAI format: `{"role": "system/user/assistant/tool", ...}`.
Reasoning content is stored in `assistant_msg["reasoning"]`.

---

## CLI Architecture (cli.py)

- **Rich** for banner/panels, **prompt_toolkit** for input with autocomplete
- **KawaiiSpinner** (`agent/display.py`) — animated faces during API calls, `┊` activity feed for tool results
- `load_cli_config()` in cli.py merges hardcoded defaults + user config YAML
- **Skin engine** (`hermes_cli/skin_engine.py`) — data-driven CLI theming; initialized from `display.skin` config key at startup; skins customize banner colors, spinner faces/verbs/wings, tool prefix, response box, branding text
- `process_command()` is a method on `HermesCLI` — dispatches on canonical command name resolved via `resolve_command()` from the central registry
- Skill slash commands: `agent/skill_commands.py` scans `~/.hermes/skills/`, injects as **user message** (not system prompt) to preserve prompt caching

### Slash Command Registry (`hermes_cli/commands.py`)

All slash commands are defined in a central `COMMAND_REGISTRY` list of `CommandDef` objects. Every downstream consumer derives from this registry automatically:

- **CLI** — `process_command()` resolves aliases via `resolve_command()`, dispatches on canonical name
- **Gateway** — `GATEWAY_KNOWN_COMMANDS` frozenset for hook emission, `resolve_command()` for dispatch
- **Gateway help** — `gateway_help_lines()` generates `/help` output
- **Telegram** — `telegram_bot_commands()` generates the BotCommand menu
- **Slack** — `slack_subcommand_map()` generates `/hermes` subcommand routing
- **Autocomplete** — `COMMANDS` flat dict feeds `SlashCommandCompleter`
- **CLI help** — `COMMANDS_BY_CATEGORY` dict feeds `show_help()`

### Adding a Slash Command

1. Add a `CommandDef` entry to `COMMAND_REGISTRY` in `hermes_cli/commands.py`:
```python
CommandDef("mycommand", "Description of what it does", "Session",
           aliases=("mc",), args_hint="[arg]"),
```
2. Add handler in `HermesCLI.process_command()` in `cli.py`:
```python
elif canonical == "mycommand":
    self._handle_mycommand(cmd_original)
```
3. If the command is available in the gateway, add a handler in `gateway/run.py`:
```python
if canonical == "mycommand":
    return await self._handle_mycommand(event)
```
4. For persistent settings, use `save_config_value()` in `cli.py`

**CommandDef fields:**
- `name` — canonical name without slash (e.g. `"background"`)
- `description` — human-readable description
- `category` — one of `"Session"`, `"Configuration"`, `"Tools & Skills"`, `"Info"`, `"Exit"`
- `aliases` — tuple of alternative names (e.g. `("bg",)`)
- `args_hint` — argument placeholder shown in help (e.g. `"<prompt>"`, `"[name]"`)
- `cli_only` — only available in the interactive CLI
- `gateway_only` — only available in messaging platforms
- `gateway_config_gate` — config dotpath (e.g. `"display.tool_progress_command"`); when set on a `cli_only` command, the command becomes available in the gateway if the config value is truthy. `GATEWAY_KNOWN_COMMANDS` always includes config-gated commands so the gateway can dispatch them; help/menus only show them when the gate is open.

**Adding an alias** requires only adding it to the `aliases` tuple on the existing `CommandDef`. No other file changes needed — dispatch, help text, Telegram menu, Slack mapping, and autocomplete all update automatically.

---

## TUI Architecture (ui-tui + tui_gateway)

The TUI is a full replacement for the classic (prompt_toolkit) CLI, activated via `hermes --tui` or `HERMES_TUI=1`.

### Process Model

```
hermes --tui
  └─ Node (Ink)  ──stdio JSON-RPC──  Python (tui_gateway)
       │                                  └─ AIAgent + tools + sessions
       └─ renders transcript, composer, prompts, activity
```

TypeScript owns the screen. Python owns sessions, tools, model calls, and slash command logic.

### Transport

Newline-delimited JSON-RPC over stdio. Requests from Ink, events from Python. See `tui_gateway/server.py` for the full method/event catalog.

### Key Surfaces

| Surface | Ink component | Gateway method |
|---------|---------------|----------------|
| Chat streaming | `app.tsx` + `messageLine.tsx` | `prompt.submit` → `message.delta/complete` |
| Tool activity | `thinking.tsx` | `tool.start/progress/complete` |
| Approvals | `prompts.tsx` | `approval.respond` ← `approval.request` |
| Clarify/sudo/secret | `prompts.tsx`, `maskedPrompt.tsx` | `clarify/sudo/secret.respond` |
| Session picker | `sessionPicker.tsx` | `session.list/resume` |
| Slash commands | Local handler + fallthrough | `slash.exec` → `_SlashWorker`, `command.dispatch` |
| Completions | `useCompletion` hook | `complete.slash`, `complete.path` |
| Theming | `theme.ts` + `branding.tsx` | `gateway.ready` with skin data |

### Slash Command Flow

1. Built-in client commands (`/help`, `/quit`, `/clear`, `/resume`, `/copy`, `/paste`, etc.) handled locally in `app.tsx`
2. Everything else → `slash.exec` (runs in persistent `_SlashWorker` subprocess) → `command.dispatch` fallback

### Dev Commands

```bash
cd ui-tui
npm install       # first time
npm run dev       # watch mode (rebuilds hermes-ink + tsx --watch)
npm start         # production
npm run build     # full build (hermes-ink + tsc)
npm run type-check # typecheck only (tsc --noEmit)
npm run lint      # eslint
npm run fmt       # prettier
npm test          # vitest
```

### TUI in the Dashboard (`hermes dashboard` → `/chat`)

The dashboard embeds the real `hermes --tui` — **not** a rewrite.  See `hermes_cli/pty_bridge.py` + the `@app.websocket("/api/pty")` endpoint in `hermes_cli/web_server.py`.

- Browser loads `web/src/pages/ChatPage.tsx`, which mounts xterm.js's `Terminal` with the WebGL renderer, `@xterm/addon-fit` for container-driven resize, and `@xterm/addon-unicode11` for modern wide-character widths.
- `/api/pty?token=…` upgrades to a WebSocket; auth uses the same ephemeral `_SESSION_TOKEN` as REST, via query param (browsers can't set `Authorization` on WS upgrade).
- The server spawns whatever `hermes --tui` would spawn, through `ptyprocess` (POSIX PTY — WSL works, native Windows does not).
- Frames: raw PTY bytes each direction; resize via `\x1b[RESIZE:<cols>;<rows>]` intercepted on the server and applied with `TIOCSWINSZ`.

**Do not re-implement the primary chat experience in React.** The main transcript, composer/input flow (including slash-command behavior), and PTY-backed terminal belong to the embedded `hermes --tui` — anything new you add to Ink shows up in the dashboard automatically. If you find yourself rebuilding the transcript or composer for the dashboard, stop and extend Ink instead.

**Structured React UI around the TUI is allowed when it is not a second chat surface.** Sidebar widgets, inspectors, summaries, status panels, and similar supporting views (e.g. `ChatSidebar`, `ModelPickerDialog`, `ToolCall`) are fine when they complement the embedded TUI rather than replacing the transcript / composer / terminal. Keep their state independent of the PTY child's session and surface their failures non-destructively so the terminal pane keeps working unimpaired.

---

## Adding New Tools

For most custom or local-only tools, do **not** edit Hermes core. Use the plugin
route instead: create `~/.hermes/plugins/<name>/plugin.yaml` and
`~/.hermes/plugins/<name>/__init__.py`, then register tools with
`ctx.register_tool(...)`. Plugin toolsets are discovered automatically and can be
enabled or disabled without touching `tools/` or `toolsets.py`.

Use the built-in route below only when the user is explicitly contributing a new
core Hermes tool that should ship in the base system.

Built-in/core tools require changes in **2 files**:

**1. Create `tools/your_tool.py`:**
```python
import json, os
from tools.registry import registry

def check_requirements() -> bool:
    return bool(os.getenv("EXAMPLE_API_KEY"))

def example_tool(param: str, task_id: str = None) -> str:
    return json.dumps({"success": True, "data": "..."})

registry.register(
    name="example_tool",
    toolset="example",
    schema={"name": "example_tool", "description": "...", "parameters": {...}},
    handler=lambda args, **kw: example_tool(param=args.get("param", ""), task_id=kw.get("task_id")),
    check_fn=check_requirements,
    requires_env=["EXAMPLE_API_KEY"],
)
```

**2. Add to `toolsets.py`** — either `_HERMES_CORE_TOOLS` (all platforms) or a new toolset. **This step is required:** auto-discovery imports the tool and registers its schema, but the tool is only *exposed to an agent* if its name appears in a toolset. `_HERMES_CORE_TOOLS` is not dead code — it's the default bundle every platform's base toolset inherits from.

Auto-discovery: any `tools/*.py` file with a top-level `registry.register()` call is imported automatically — no manual import list to maintain. Wiring into a toolset is still a deliberate, manual step.

The registry handles schema collection, dispatch, availability checking, and error wrapping. All handlers MUST return a JSON string.

**Path references in tool schemas**: If the schema description mentions file paths (e.g. default output directories), use `display_hermes_home()` to make them profile-aware. The schema is generated at import time, which is after `_apply_profile_override()` sets `HERMES_HOME`.

**State files**: If a tool stores persistent state (caches, logs, checkpoints), use `get_hermes_home()` for the base directory — never `Path.home() / ".hermes"`. This ensures each profile gets its own state.

**Agent-level tools** (todo, memory): intercepted by `run_agent.py` before `handle_function_call()`. See `tools/todo_tool.py` for the pattern.

---

## Dependency Pinning Policy

All dependencies must have upper bounds to limit supply-chain attack surface.
This policy was established after the litellm compromise (PR #2796, #2810) and
reinforced after the Mini Shai-Hulud worm campaign (May 2026).

| Source type | Treatment | Example |
|---|---|---|
| PyPI package | `>=floor,<next_major` | `"httpx>=0.28.1,<1"` |
| Git URL | Commit SHA | `git+https://...@<40-char-sha>` |
| GitHub Actions | Commit SHA + comment | `uses: actions/checkout@<sha>  # v4` |
| CI-only pip | `==exact` | `pyyaml==6.0.2` |

**When adding a new dependency to `pyproject.toml`:**
1. Pin to `>=current_version,<next_major` for post-1.0 (e.g. `>=1.5.0,<2`).
2. For pre-1.0 packages, use `<0.(current_minor + 2)` (e.g. `>=0.29,<0.32`).
3. Never commit a bare `>=X.Y.Z` without a ceiling — CI and reviewers will reject it.
4. Run `uv lock` to regenerate `uv.lock` with hashes.

Reference: #2810 (bounds pass), #9801 (SHA pinning + audit CI).

---

## Adding Configuration

### config.yaml options:
1. Add to `DEFAULT_CONFIG` in `hermes_cli/config.py`
2. Bump `_config_version` (check the current value at the top of `DEFAULT_CONFIG`)
   ONLY if you need to actively migrate/transform existing user config
   (renaming keys, changing structure). Adding a new key to an existing
   section is handled automatically by the deep-merge and does NOT require
   a version bump.

### Top-level `config.yaml` sections (non-exhaustive):

`model`, `agent`, `terminal`, `compression`, `display`, `stt`, `tts`,
`memory`, `security`, `delegation`, `smart_model_routing`, `checkpoints`,
`auxiliary`, `curator`, `skills`, `gateway`, `logging`, `cron`, `profiles`,
`plugins`, `honcho`.

`auxiliary` holds per-task overrides for side-LLM work (curator, vision,
embedding, title generation, session_search, etc.) — each task can pin
its own provider/model/base_url/max_tokens/reasoning_effort. See
`agent/auxiliary_client.py::_resolve_auto` for resolution order.

`curator` holds the background skill-maintenance config —
`enabled`, `interval_hours`, `min_idle_hours`, `stale_after_days`,
`archive_after_days`, `backup` (nested).

### .env variables (SECRETS ONLY — API keys, tokens, passwords):
1. Add to `OPTIONAL_ENV_VARS` in `hermes_cli/config.py` with metadata:
```python
"NEW_API_KEY": {
    "description": "What it's for",
    "prompt": "Display name",
    "url": "https://...",
    "password": True,
    "category": "tool",  # provider, tool, messaging, setting
},
```

Non-secret settings (timeouts, thresholds, feature flags, paths, display
preferences) belong in `config.yaml`, not `.env`. If internal code needs an
env var mirror for backward compatibility, bridge it from `config.yaml` to
the env var in code (see `gateway_timeout`, `terminal.cwd` → `TERMINAL_CWD`).

### Config loaders (three paths — know which one you're in):

| Loader | Used by | Location |
|--------|---------|----------|
| `load_cli_config()` | CLI mode | `cli.py` — merges CLI-specific defaults + user YAML |
| `load_config()` | `hermes tools`, `hermes setup`, most CLI subcommands | `hermes_cli/config.py` — merges `DEFAULT_CONFIG` + user YAML |
| Direct YAML load | Gateway runtime | `gateway/run.py` + `gateway/config.py` — reads user YAML raw |

If you add a new key and the CLI sees it but the gateway doesn't (or vice
versa), you're on the wrong loader. Check `DEFAULT_CONFIG` coverage.

### Working directory:
- **CLI** — uses the process's current directory (`os.getcwd()`).
- **Messaging** — uses `terminal.cwd` from `config.yaml`. The gateway bridges this
  to the `TERMINAL_CWD` env var for child tools. **`MESSAGING_CWD` has been
  removed** — the config loader prints a deprecation warning if it's set in
  `.env`. Same for `TERMINAL_CWD` in `.env`; the canonical setting is
  `terminal.cwd` in `config.yaml`.

---

## Skin/Theme System

The skin engine (`hermes_cli/skin_engine.py`) provides data-driven CLI visual customization. Skins are **pure data** — no code changes needed to add a new skin.

### Architecture

```
hermes_cli/skin_engine.py    # SkinConfig dataclass, built-in skins, YAML loader
~/.hermes/skins/*.yaml       # User-installed custom skins (drop-in)
```

- `init_skin_from_config()` — called at CLI startup, reads `display.skin` from config
- `get_active_skin()` — returns cached `SkinConfig` for the current skin
- `set_active_skin(name)` — switches skin at runtime (used by `/skin` command)
- `load_skin(name)` — loads from user skins first, then built-ins, then falls back to default
- Missing skin values inherit from the `default` skin automatically

### What skins customize

| Element | Skin Key | Used By |
|---------|----------|---------|
| Banner panel border | `colors.banner_border` | `banner.py` |
| Banner panel title | `colors.banner_title` | `banner.py` |
| Banner section headers | `colors.banner_accent` | `banner.py` |
| Banner dim text | `colors.banner_dim` | `banner.py` |
| Banner body text | `colors.banner_text` | `banner.py` |
| Response box border | `colors.response_border` | `cli.py` |
| Spinner faces (waiting) | `spinner.waiting_faces` | `display.py` |
| Spinner faces (thinking) | `spinner.thinking_faces` | `display.py` |
| Spinner verbs | `spinner.thinking_verbs` | `display.py` |
| Spinner wings (optional) | `spinner.wings` | `display.py` |
| Tool output prefix | `tool_prefix` | `display.py` |
| Per-tool emojis | `tool_emojis` | `display.py` → `get_tool_emoji()` |
| Agent name | `branding.agent_name` | `banner.py`, `cli.py` |
| Welcome message | `branding.welcome` | `cli.py` |
| Response box label | `branding.response_label` | `cli.py` |
| Prompt symbol | `branding.prompt_symbol` | `cli.py` |

### Built-in skins

- `default` — Classic Hermes gold/kawaii (the current look)
- `ares` — Crimson/bronze war-god theme with custom spinner wings
- `mono` — Clean grayscale monochrome
- `slate` — Cool blue developer-focused theme

### Adding a built-in skin

Add to `_BUILTIN_SKINS` dict in `hermes_cli/skin_engine.py`:

```python
"mytheme": {
    "name": "mytheme",
    "description": "Short description",
    "colors": { ... },
    "spinner": { ... },
    "branding": { ... },
    "tool_prefix": "┊",
},
```

### User skins (YAML)

Users create `~/.hermes/skins/<name>.yaml`:

```yaml
name: cyberpunk
description: Neon-soaked terminal theme

colors:
  banner_border: "#FF00FF"
  banner_title: "#00FFFF"
  banner_accent: "#FF1493"

spinner:
  thinking_verbs: ["jacking in", "decrypting", "uploading"]
  wings:
    - ["⟨⚡", "⚡⟩"]

branding:
  agent_name: "Cyber Agent"
  response_label: " ⚡ Cyber "

tool_prefix: "▏"
```

Activate with `/skin cyberpunk` or `display.skin: cyberpunk` in config.yaml.

---

## Plugins

Hermes has two plugin surfaces. Both live under `plugins/` in the repo so
repo-shipped plugins can be discovered alongside user-installed ones in
`~/.hermes/plugins/` and pip-installed entry points.

### General plugins (`hermes_cli/plugins.py` + `plugins/<name>/`)

`PluginManager` discovers plugins from `~/.hermes/plugins/`, `./.hermes/plugins/`,
and pip entry points. Each plugin exposes a `register(ctx)` function that
can:

- Register Python-callback lifecycle hooks:
  `pre_tool_call`, `post_tool_call`, `pre_llm_call`, `post_llm_call`,
  `on_session_start`, `on_session_end`
- Register new tools via `ctx.register_tool(...)`
- Register CLI subcommands via `ctx.register_cli_command(...)` — the
  plugin's argparse tree is wired into `hermes` at startup so
  `hermes <pluginname> <subcmd>` works with no change to `main.py`

Hooks are invoked from `model_tools.py` (pre/post tool) and `run_agent.py`
(lifecycle). **Discovery timing pitfall:** `discover_plugins()` only runs
as a side effect of importing `model_tools.py`. Code paths that read plugin
state without importing `model_tools.py` first must call `discover_plugins()`
explicitly (it's idempotent).

### Preflight plugin (`plugins/preflight/` — FAMA Tier-2 #3 / Epic 7)

Thin `pre_llm_call` shim that wires `lib/hermes_preflight.should_run_preflight()`
into the model dispatch path. The plugin is intentionally minimal — all gate
state, classification, retrieval, ranking, dedupe, formatting, telemetry,
and citation persistence live in `lib/hermes_preflight.py`. The plugin is
just the registration glue.

**Files** (bundled, version-controlled here in the fork):

```
plugins/preflight/
├── __init__.py    # register(ctx) → ctx.register_hook("pre_llm_call", on_pre_llm_call)
└── plugin.yaml    # manifest (name, version, requires lib.hermes_preflight)
```

**Runtime state lives elsewhere** (per the dev tree vs runtime split in the
Auto-Dream Substrate section):

- `~/.hermes/preflight/config.yaml` — operator-edited (mode: shadow|live,
  skip_window, top_k, ranking weights)
- `~/.hermes/preflight/domain-vocab.txt` — domain keywords for classifier
  (per-profile overrides under `~/.hermes/profiles/<role>/preflight/`)
- `~/.hermes/preflight/last-cited.json` — current-session citations
  (consumed by `verify` skill via `scripts/preflight_verify_helper.py`)
- `~/.hermes/preflight/gates.json` — per-session gate state (turn_count,
  _fired_hashes)
- `~/.hermes/preflight/log/<YYYY-MM-DD>.jsonl` — per-invocation telemetry

**Mode flag.** `mode: shadow` runs the pipeline and writes telemetry but
does **not** inject the heads-up block — used for the 5-day rollout period
per CLAUDE.md "Implementation order". `mode: live` injects the heads-up
after the user message (preserves cache prefix; FR-31 / Hard Invariant #12).
Flip via `hermes-preflight mode live` (writes `config.yaml`) or the
`HERMES_PREFLIGHT_MODE` env override.

**Hook contract.** The plugin returns `{"context": "<preflight-heads-up>...</preflight-heads-up>"}`
when injecting; returns `None` when the gate skipped, the recall set was
empty, or `mode: shadow` is set. Telemetry is written for every invocation
(fire OR skip — FR-34).

**Companion CLI:** `~/.hermes/bin/hermes-preflight` — `check`, `force`,
`mode`, `tail` subcommands. Used for inspecting what preflight would emit
on a given message, force-firing past the gate (the `/preflight [task]`
slash-command equivalent — FR-35), and tailing today's telemetry.

### Memory-provider plugins (`plugins/memory/<name>/`)

Separate discovery system for pluggable memory backends. Current built-in
providers include **honcho, mem0, supermemory, byterover, hindsight,
holographic, openviking, retaindb**.

Each provider implements the `MemoryProvider` ABC (see `agent/memory_provider.py`)
and is orchestrated by `agent/memory_manager.py`. Lifecycle hooks include
`sync_turn(turn_messages)`, `prefetch(query)`, `shutdown()`, and optional
`post_setup(hermes_home, config)` for setup-wizard integration.

**CLI commands via `plugins/memory/<name>/cli.py`:** if a memory plugin
defines `register_cli(subparser)`, `discover_plugin_cli_commands()` finds
it at argparse setup time and wires it into `hermes <plugin>`. The
framework only exposes CLI commands for the **currently active** memory
provider (read from `memory.provider` in config.yaml), so disabled
providers don't clutter `hermes --help`.

**Rule (Teknium, May 2026):** plugins MUST NOT modify core files
(`run_agent.py`, `cli.py`, `gateway/run.py`, `hermes_cli/main.py`, etc.).
If a plugin needs a capability the framework doesn't expose, expand the
generic plugin surface (new hook, new ctx method) — never hardcode
plugin-specific logic into core. PR #5295 removed 95 lines of hardcoded
honcho argparse from `main.py` for exactly this reason.

**No new in-tree memory providers (policy, May 2026):** the set of
built-in memory providers under `plugins/memory/` is closed. New memory
backends must ship as **standalone plugin repos** that users install
into `~/.hermes/plugins/` (or via pip entry points) — they implement
the same `MemoryProvider` ABC, register through the same discovery
path, and integrate via `hermes memory setup` / `post_setup()` without
landing in this tree. PRs that add a new directory under
`plugins/memory/` will be closed with a pointer to publish the
provider as its own repo. Existing in-tree providers stay; bug fixes
to them are welcome.

### Model-provider plugins (`plugins/model-providers/<name>/`)

Every inference backend (openrouter, anthropic, gmi, deepseek, nvidia, …)
ships as a plugin here. Each plugin's `__init__.py` calls
`providers.register_provider(ProviderProfile(...))` at module load.
`providers/__init__.py._discover_providers()` is a **lazy, separate
discovery system** — scanned on first `get_provider_profile()` or
`list_providers()` call, NOT by the general PluginManager.

Scan order:
1. Bundled: `<repo>/plugins/model-providers/<name>/`
2. User: `$HERMES_HOME/plugins/model-providers/<name>/`
3. Legacy: `<repo>/providers/<name>.py` (back-compat)

User plugins of the same name override bundled ones — `register_provider()`
is last-writer-wins. This lets third parties swap out any built-in
profile without a repo patch.

The general PluginManager records `kind: model-provider` manifests but does
NOT import them (would double-instantiate `ProviderProfile`). Plugins
without an explicit `kind:` get auto-coerced via a source-text heuristic
(`register_provider` + `ProviderProfile` in `__init__.py`).

Full authoring guide: `website/docs/developer-guide/model-provider-plugin.md`.

### Dashboard / context-engine / image-gen plugin directories

`plugins/context_engine/`, `plugins/image_gen/`, etc. follow the same
pattern (ABC + orchestrator + per-plugin directory). Context engines
plug into `agent/context_engine.py`; image-gen providers into
`agent/image_gen_provider.py`. Reference / docs-companion plugins
(`example-dashboard`, `strike-freedom-cockpit`, `plugin-llm-example`,
`plugin-llm-async-example`) live in the
[`hermes-example-plugins`](https://github.com/NousResearch/hermes-example-plugins)
companion repo, not in this tree.

---

## Skills

Two parallel surfaces:

- **`skills/`** — built-in skills shipped and loadable by default.
  Organized by category directories (e.g. `skills/github/`, `skills/mlops/`).
- **`optional-skills/`** — heavier or niche skills shipped with the repo but
  NOT active by default. Installed explicitly via
  `hermes skills install official/<category>/<skill>`. Adapter lives in
  `tools/skills_hub.py` (`OptionalSkillSource`). Categories include
  `autonomous-ai-agents`, `blockchain`, `communication`, `creative`,
  `devops`, `email`, `health`, `mcp`, `migration`, `mlops`, `productivity`,
  `research`, `security`, `web-development`.

When reviewing skill PRs, check which directory they target — heavy-dep or
niche skills belong in `optional-skills/`.

### SKILL.md frontmatter

Standard fields: `name`, `description`, `version`, `author`, `license`,
`platforms` (OS-gating list: `[macos]`, `[linux, macos]`, ...),
`metadata.hermes.tags`, `metadata.hermes.category`,
`metadata.hermes.related_skills`, `metadata.hermes.config` (config.yaml
settings the skill needs — stored under `skills.config.<key>`, prompted
during setup, injected at load time).

Top-level `tags:` and `category:` are also accepted and mirrored from
`metadata.hermes.*` by the loader.

### Skill authoring standards (HARDLINE)

Every new or modernized skill — bundled, optional, or contributed —
must meet these standards before merge. Reviewers reject PRs that
violate them.

1. **`description` ≤ 60 characters, one sentence, ends with a period.**
   Long descriptions bloat skill listings and dilute the model's
   attention when many skills are loaded. State the capability, not
   the implementation. No marketing words ("powerful",
   "comprehensive", "seamless", "advanced"). Don't repeat the skill
   name. Verify with:
   ```python
   import re, pathlib
   m = re.search(r'^description: (.*)$',
                 pathlib.Path('skills/<cat>/<name>/SKILL.md').read_text(),
                 re.MULTILINE)
   assert len(m.group(1)) <= 60, len(m.group(1))
   ```

2. **Tools referenced in SKILL.md prose must be native Hermes tools or
   MCP servers the skill explicitly expects.** When the skill needs a
   capability, point at the proper tool by name in backticks
   (`` `terminal` ``, `` `web_extract` ``, `` `read_file` ``,
   `` `patch` ``, `` `search_files` ``, `` `vision_analyze` ``,
   `` `browser_navigate` ``, `` `delegate_task` ``, etc.). Do NOT
   name shell utilities the agent already has wrapped — `grep` →
   `search_files`, `cat`/`head`/`tail` → `read_file`, `sed`/`awk` →
   `patch`, `find`/`ls` → `search_files target='files'`. If the skill
   depends on an MCP server, name the MCP server and document the
   expected setup in `## Prerequisites`. Anything else (third-party
   CLIs, shell pipelines, etc.) is fair game inside script files but
   should not be the headline interaction surface in the prose.

3. **`platforms:` gating audited against actual script imports.**
   Skills that use POSIX-only primitives (`fcntl`, `termios`,
   `os.setsid`, `os.kill(pid, 0)` for liveness, `/proc`, `/tmp`
   hardcoded, `signal.SIGKILL`, bash heredocs, `osascript`, `apt`,
   `systemctl`) must declare their supported platforms. Default
   posture: try to fix it cross-platform first — `tempfile.gettempdir`,
   `pathlib.Path`, `psutil.pid_exists`, Python-level filtering instead
   of `grep`. Gate to a narrower set only when the dependency is
   genuinely platform-bound.

4. **`author` credits the human contributor first.** For external
   contributions, the contributor's real name + GitHub handle goes
   first; "Hermes Agent" is the secondary collaborator. If the
   contributor's commit shows "Hermes Agent" as author (because they
   used Hermes to draft the skill), replace it with their actual name
   — credit the human, not the tool.

5. **SKILL.md body uses the modern section order.** `# <Skill> Skill`
   title, 2-3 sentence intro stating what it does and doesn't do,
   `## When to Use`, `## Prerequisites`, `## How to Run`,
   `## Quick Reference`, `## Procedure`, `## Pitfalls`,
   `## Verification`. Target ~200 lines for a complex skill,
   ~100 lines for a simple one. Cut redundant intro fluff, marketing
   prose, and re-explanations of env vars already in
   `## Prerequisites`.

6. **Scripts go in `scripts/`, references in `references/`,
   templates in `templates/`.** Don't expect the model to inline-write
   parsers, XML walkers, or non-trivial logic every call — ship a
   helper script. Reference it from SKILL.md by path relative to the
   skill directory.

7. **Tests live at `tests/skills/test_<skill>_skill.py`** and use only
   stdlib + pytest + `unittest.mock`. No live network calls. Run via
   `scripts/run_tests.sh tests/skills/test_<skill>_skill.py -q`.

8. **`.env.example` additions are isolated to a clearly delimited
   block.** Don't touch the surrounding file — contributor-supplied
   `.env.example` versions are usually stale and edits outside the
   skill's own block must be dropped during salvage.

The full salvage / modernization checklist for external skill PRs
lives in the `hermes-agent-dev` skill at
`references/new-skill-pr-salvage.md` — load it before polishing
contributor skill PRs.

---

## Toolsets

All toolsets are defined in `toolsets.py` as a single `TOOLSETS` dict.
Each platform's adapter picks a base toolset (e.g. Telegram uses
`"messaging"`); `_HERMES_CORE_TOOLS` is the default bundle most
platforms inherit from.

Current toolset keys: `browser`, `clarify`, `code_execution`, `cronjob`,
`debugging`, `delegation`, `discord`, `discord_admin`, `feishu_doc`,
`feishu_drive`, `file`, `homeassistant`, `image_gen`, `kanban`, `memory`,
`messaging`, `moa`, `rl`, `safe`, `search`, `session_search`, `skills`,
`spotify`, `terminal`, `todo`, `tts`, `video`, `vision`, `web`, `yuanbao`.

Enable/disable per platform via `hermes tools` (the curses UI) or the
`tools.<platform>.enabled` / `tools.<platform>.disabled` lists in
`config.yaml`.

---

## Delegation (`delegate_task`)

`tools/delegate_tool.py` spawns a subagent with an isolated
context + terminal session. Synchronous: the parent waits for the
child's summary before continuing its own loop — if the parent is
interrupted, the child is cancelled.

Two shapes:

- **Single:** pass `goal` (+ optional `context`, `toolsets`).
- **Batch (parallel):** pass `tasks: [...]` — each gets its own subagent
  running concurrently. Concurrency is capped by
  `delegation.max_concurrent_children` (default 3).

Roles:

- `role="leaf"` (default) — focused worker. Cannot call `delegate_task`,
  `clarify`, `memory`, `send_message`, `execute_code`.
- `role="orchestrator"` — retains `delegate_task` so it can spawn its
  own workers. Gated by `delegation.orchestrator_enabled` (default true)
  and bounded by `delegation.max_spawn_depth` (default 2).

Key config knobs (under `delegation:` in `config.yaml`):
`max_concurrent_children`, `max_spawn_depth`, `child_timeout_seconds`,
`orchestrator_enabled`, `subagent_auto_approve`, `inherit_mcp_toolsets`,
`max_iterations`.

Synchronicity rule: delegate_task is **not** durable. For long-running
work that must outlive the current turn, use `cronjob` or
`terminal(background=True, notify_on_complete=True)` instead.

---

## Curator (skill lifecycle)

Background skill-maintenance system that tracks usage on agent-created
skills and auto-archives stale ones. Users never lose skills; archives
go to `~/.hermes/skills/.archive/` and are restorable.

- **Core:** `agent/curator.py` (review loop, auto-transitions, LLM review
  prompt) + `agent/curator_backup.py` (pre-run tar.gz snapshots).
- **CLI:** `hermes_cli/curator.py` wires `hermes curator <verb>` where
  verbs are: `status`, `run`, `pause`, `resume`, `pin`, `unpin`,
  `archive`, `restore`, `prune`, `backup`, `rollback`.
- **Telemetry:** `tools/skill_usage.py` owns the sidecar
  `~/.hermes/skills/.usage.json` — per-skill `use_count`, `view_count`,
  `patch_count`, `last_activity_at`, `state` (active / stale /
  archived), `pinned`.

Invariants:
- Curator only touches skills with `created_by: "agent"` provenance —
  bundled + hub-installed skills are off-limits.
- Never deletes; max destructive action is archive.
- Pinned skills are exempt from every auto-transition and from the
  LLM review pass.
- `skill_manage(action="delete")` refuses pinned skills; patch/edit/
  write_file/remove_file go through so the agent can keep improving
  pinned skills.

Config section (`curator:` in `config.yaml`):
`enabled`, `interval_hours`, `min_idle_hours`, `stale_after_days`,
`archive_after_days`, `backup.*`.

Full user-facing docs: `website/docs/user-guide/features/curator.md`.

---

## Auto-Dream Substrate (FAMA Tier-2 #3)

Copy-on-write memory consolidation pipeline + pre-task context injection layer
that closes the FAMA Tier-2 #3 gap (proactive context retrieval at task start
+ proposal-gated memory mutation). Vocabulary and CLI verbs match
`NousResearch/hermes-agent#10771` byte-for-byte; private fork extensions are
confined to `<dream_id>/.hermes-private/` inside each dream artifact.

### Dev tree vs live runtime — paths are not interchangeable

> **All code is edited here, in `~/usr-local/hermes/` — the BMAD/dev tree.
> Nothing in `~/.hermes/` is a source-of-truth file.**

| Concern | Location | Edited? |
|---|---|---|
| Source code (Python modules, plugins, skills) | `~/usr-local/hermes/{lib,plugins,skills,...}/` | **Yes — only here** |
| Version-controlled config (e.g. `providers.yaml`) | `~/usr-local/hermes/dreams/providers.yaml` (target — currently at `~/.hermes/dreams/providers.yaml`, mirror to dev tree when stable) | Yes |
| Tests | `~/usr-local/hermes/tests/lib/`, `~/usr-local/hermes/tests/tools/`, ... | Yes |
| Planning artifacts (PRD, architecture, epics, status, stories) | `~/usr-local/hermes/{planning,implementation}-artifacts/` | Yes |
| **Runtime state** — dream artifacts, audit log, raw entries, memory store, telemetry, logs, per-instance preflight config | `~/.hermes/{dreams,raw,observability,memory,memories,preflight,logs}/` | **No — written by code at runtime** |
| Per-instance / per-profile config | `~/.hermes/config.yaml`, `~/.hermes/preflight/config.yaml`, `~/.hermes/.env` | Operator-edited, not version-controlled |

**Two checkouts of the same private repo**, kept in sync with `git pull`:

- `~/usr-local/hermes/` — **dev checkout.** `origin = marozau/hermes-agent-private`. Edit code here; push to `origin/main`.
- `~/.hermes/hermes-agent/` — **runtime checkout.** Same private repo via remote `private = marozau/hermes-agent-private`. To pick up edits: `cd ~/.hermes/hermes-agent && git pull private main`. The live agent imports from this tree.
- `~/.hermes/` (top-level, no remote) — separate local git repo that tracks **runtime state only**: profiles, installed skills, runtime config, dreams, raw, observability, preflight logs. `hermes-agent/` is gitignored at this level (it's the separate checkout above).

No code-copying step is needed. The legacy `deploy.sh` in the dev tree is a historical relic from before the two-checkouts model and is unused — `git pull` in the runtime checkout is the canonical sync mechanism.

### Required reading (when touching substrate code)

All paths are relative to the dev tree root (`~/usr-local/hermes/`).

| # | File | What it answers |
|---|---|---|
| 1 | `planning-artifacts/product-brief.md` | Vision, scope, why |
| 2 | `planning-artifacts/prd-hermes-2026-05-12.md` | 47 FRs + 25 NFRs + risks + **DoD §18** — the contract |
| 3 | `planning-artifacts/architecture.md` | 12 ADRs + 7 pattern groups + file layout |
| 4 | `planning-artifacts/epics.md` | 7 epics, 34 stories, Given/When/Then ACs |
| 5 | `planning-artifacts/workflow-status.yaml` | Phase / sprint position |
| 6 | `planning-artifacts/research/technical-hermes-issue-10771-2026-05-12.md` | Upstream design consensus (typed entries, staged artifacts, dry-run gate) |
| 7 | `planning-artifacts/research/technical-auto-dream-full-2026-05-12.md` | Verbatim auto-dream article + Hermes adaptation; full dream prompt |
| 8 | `planning-artifacts/research/technical-pretask-context-injection-2026-05-12.md` | Preflight implementation spec |
| 9 | `planning-artifacts/research/technical-llm-prefect-reliable-2026-05-12.md` | Provider routing + Prefect patterns |
| 10 | `planning-artifacts/research/technical-fama-skill-improvement-2026-05-12.md` | FAMA gap analysis (the "why") |
| 11 | `planning-artifacts/research/technical-auto-dream-for-hermes-2026-05-12.md` | Claude Code source dive (lock pattern, gates) |
| 12 | `CLAUDE.md` | Implementation playbook + hard invariants index |
| 13 | `lib/README.md` | Provider adapter architecture (Stories 3.6–3.8) + deploy.sh + fixtures |
| 14 | `implementation-artifacts/stories/story-3.{6,7,8}-*.md` | In-flight story specs (Given/When/Then ACs) |

Read 1–4 always; 5 to know status; 6–11 when the specific subsystem is touched; 13–14 when touching providers/dispatcher.

### Status snapshot (2026-05-18)

7 epics + 34 stories marked complete in `workflow-status.yaml` (214 unit tests).
A standalone **V1 Definition-of-Done audit** against PRD §18 found:

- **5/13 PASS** — items 4, 10, 11, 12, 13 (REPORT.md artifact + cross-provider fallback + schema retry + transactional rollback + attestation drift abort, all with passing tests).
- **2/13 FAIL** —
  - Item 1: `hermes_memory.add_entry()` is NOT the only writer of MEMORY.md. `tools/memory_tool.py:283-303` still writes directly to the runtime `MEMORY.md` ("Story 1.5 follow-up"). **Hard invariant #1 violated.**
  - Item 7: Preflight hit-rate is **11.8%** (need ≥30%) and the plugin is in `mode: shadow` at 1% sampling — zero injections.
- **6/13 UNVERIFIABLE** — items 2, 3, 5, 6, 8, 9. The empty `_PROVIDER_DISPATCH` seam in `hermes_llm.py:344` (stub: "Production wiring lives in Epic 4") AND zero operational runs (no dream artifacts written to runtime, no `audit.jsonl`, no `observability/llm_calls.jsonl`).

**Audit re-run on the same day (after dispatcher visibility + boot-path patch):** item 8 flipped from PASS → FAIL because `register_all()` is built but never called at boot. Items 9, 10, 11 became CONTINGENT-FAIL on the same blocker. The boot-path patch at `hermes_cli/main.py:245-262` closes the wiring gap; verified with `python -c "import hermes_cli.main"` populating `_PROVIDER_DISPATCH` with all 3 keys at module load. **Operational verification still required** — only a real run with non-zero `cache_read_tokens` in `~/.hermes/observability/llm_calls.jsonl` makes item 8 a clean PASS.

**Do NOT declare V1 done.** A one-week operational trial flips items 2, 3, 5, 6 (frontmatter compliance, `valid_until` token delta, recall regression hit, post-apply recall stability) and gives a directional signal on item 7.

### Source tree layout (`~/usr-local/hermes/`)

```
~/usr-local/hermes/
├── lib/                                # Substrate helpers (fork-only; NOT upstream hermes-agent)
│   ├── hermes_memory.py                # FR-3 chokepoint — add_entry / update_entry / supersede_entry / expire_entry / read_entries
│   ├── hermes_llm.py                   # LLMSpec + llm_call (Prefect @task; cache_policy=INPUTS) — sole LLM call site
│   ├── hermes_dream.py                 # create_dream_artifact / apply_dream / discard_dream (lock + COW)
│   ├── hermes_recall.py                # run_regression_check — dry-run gate for proposals
│   ├── hermes_trust.py                 # attestation pre-flight + rebaseline
│   ├── hermes_preflight.py             # pre_task_start classification + retrieval (≤200ms p95)
│   ├── hermes_providers.py             # register_all() — Story 3.8 dispatcher entry point
│   ├── hermes_providers_anthropic.py   # Story 3.6: Anthropic Messages API + cache_control breakpoints
│   ├── hermes_providers_chat.py        # Story 3.7: shared /chat/completions adapter (DeepSeek + OpenAI)
│   └── README.md                       # Provider adapter architecture + deploy.sh + fixtures
├── tests/lib/                          # Substrate + provider adapter tests
│   ├── conftest.py                     # Dev-tree lib/ on sys.path + HERMES_ROOT
│   ├── test_hermes_memory.py
│   ├── test_hermes_llm.py
│   ├── test_hermes_dream.py
│   ├── test_hermes_recall.py
│   ├── test_hermes_trust.py
│   ├── test_hermes_preflight.py
│   ├── test_hermes_preflight_integration.py  # integration; needs deployed ~/.hermes/bin/hermes-preflight
│   ├── test_hermes_providers.py
│   ├── test_hermes_providers_anthropic.py
│   ├── test_hermes_providers_chat.py
│   └── test_dispatcher_e2e.py
├── planning-artifacts/                 # The plan
│   ├── product-brief.md
│   ├── prd-hermes-2026-05-12.md        # §18 = DoD
│   ├── architecture.md
│   ├── epics.md
│   ├── workflow-status.yaml
│   └── research/                       # 6 technical research docs
├── implementation-artifacts/
│   └── stories/                        # Story-3.6/3.7/3.8 specs (Given/When/Then ACs)
├── plugins/preflight/                  # FAMA Tier-2 #3 / Epic 7 — pre_llm_call hook shim
│   ├── __init__.py                     # register(ctx) → ctx.register_hook("pre_llm_call", on_pre_llm_call)
│   └── plugin.yaml                     # manifest
├── deploy.sh                           # ⚠ legacy / unused — see "Sync flow" below
├── CLAUDE.md                           # Implementation playbook
└── AGENTS.md                           # (this file)
```

### Live runtime layout (`~/.hermes/`)

```
~/.hermes/                              # Per-instance state (top-level local git repo; no remote)
├── hermes-agent/                       # Separate checkout of marozau/hermes-agent-private
│   │                                   # (gitignored at top level; updated via `git pull private main`)
│   ├── lib/                            # ← Substrate helpers + provider adapters land here on pull
│   ├── tests/                          # Tests come with the checkout (don't run in production)
│   ├── hermes_cli/                     # CLI entry point — what the live `hermes` command imports
│   └── ...                             # rest of the codebase mirror
├── dreams/
│   ├── providers.yaml                  # Workload-keyed routing config (5 workloads at V1)
│   ├── audit.jsonl                     # Hash-chained apply/discard/force-override audit
│   └── <dream_id>/
│       ├── manifest.json               # Scope, gates fired, model, cost, signal-density, recall verdict
│       ├── REPORT.md                   # Human-readable narrative
│       ├── memory.patch                # Proposed typed-memory mutations
│       ├── user.patch                  # Proposed USER.md mutations
│       ├── skills.proposed/<skill>.patch
│       ├── sources.jsonl               # Provenance (raw-layer + session references)
│       └── .hermes-private/            # Private fork extensions (off the upstream vocab)
├── raw/<project>/<role>/YYYY-MM-DD.jsonl  # Append-only immutable log; MEMORY.md is rebuildable from this
├── preflight/
│   ├── config.yaml                     # mode: shadow|live; sampling; top_k; skip_window (operator-edited)
│   ├── domain-vocab.txt
│   ├── gates.json
│   ├── last-cited.json
│   └── log/YYYY-MM-DD.jsonl            # Per-invocation telemetry
├── observability/
│   ├── llm_calls.jsonl                 # Per-call telemetry (workload, model, tokens, cache_read, latency, schema_status)
│   └── advisory.jsonl
├── memory/typed/                       # Frontmattered typed entries (written by lib/hermes_memory.add_entry)
├── memories/                           # LEGACY § -delimited memory (Story 1.5 follow-up will retire)
└── logs/                               # agent.log / errors.log / gateway.log
```

### Canonical helpers — chokepoints

Imports use the dev tree's `lib/` package; at runtime, deployed copies are picked up from `~/.hermes/lib/`.

**Hard Invariant #1 — `hermes_memory.add_entry()` is the only writer of typed memory entries** (FR-3):

```python
from lib.hermes_memory import add_entry
add_entry(
    type="preference|fact|procedure|episode|superseded|trajectory|unknown",
    body="...",
    source="user-correction|self-derived|dogfood-incident|session:<id>|trajectory|import:<origin>",
    evidence=None, valid_until=None, supersedes=None,
)  # Returns ULID. Emits frontmatter unconditionally. Pairs typed write with raw-layer append transactionally.
   # Runs secret scanner pre-check (NFR-16); aborts on hit.
```

Siblings: `update_entry`, `supersede_entry`, `expire_entry`. **No direct file writes from anywhere else to the runtime `memory/typed/` store or to legacy `memories/MEMORY.md`.** The remaining direct-write site at `tools/memory_tool.py:283-303` is the open Story 1.5 follow-up.

**Hard Invariant #2 — `hermes_llm.llm_call(LLMSpec)` is the only LLM call site** (FR-37, NFR-23):

```python
from lib.hermes_llm import LLMSpec, llm_call
result = llm_call(LLMSpec(
    workload="memory_dream_consolidate",   # key into runtime dreams/providers.yaml
    messages=[...],
    response_model=ProposalList,           # Pydantic gate (FR-40)
    cache_breakpoints=[0, 1, 2],           # system | skills bundle | trajectories (ADR-7)
    idempotency_key="memory-dream:agent:2026-05-12",
))
```

**No `import anthropic` / `import openai` / `import deepseek` in skills or plugins.** Cross-provider fallback is enforced inside (never DeepSeek→DeepSeek). One telemetry row per call to the runtime `observability/llm_calls.jsonl`.

### Hard invariants (full list at CLAUDE.md §"Hard invariants")

1. Only `add_entry()` writes typed memory.
2. Only `llm_call()` calls LLM providers.
3. LLMs NEVER write to `skills/`, `SOUL.md`, or `USER.md` — proposals only.
4. Dreams NEVER mutate live state (copy-on-write to runtime `dreams/<dream_id>/`; manual ack required).
5. soul-guardian's protected set MUST exclude runtime `agents/*/memory/` and `dreams/`.
6. MEMORY.md is rebuildable from runtime `raw/` + sessions; weekly CI enforces.
7. Kill ≠ crash on the dream lock — user kill records audit + does NOT rewind mtime.
8. `valid_until` filters at read; NEVER deletes from disk.
9. `apply` is idempotent by `dream_id` (content-hashed patches).
10. Fallback is always cross-provider.
11. Pydantic schemas gate every effectful LLM output.
12. Anthropic prompt caching uses three explicit breakpoints (system / skills bundle / trajectories).

### Cache-break trap

The dream bundle (`[system | skills bundle | trajectories | human turn]`) **must be byte-stable within a flow run.** Mutations on message dicts re-hash the block and invalidate `cache_control`. Adapter code MUST build new dicts; never mutate in place. Bundle is assembled **once per flow run** — building it mid-flow is the cache-break trap.

Cross-references: this is the doctrine CLAUDE.md cites and that the provider adapters in `lib/hermes_providers_anthropic.py` gate on. Story 3.8's end-to-end smoke test asserts the cache-warm second run pays zero provider cost.

### Provider routing (workload-keyed)

The runtime `dreams/providers.yaml` (target: version-controlled at `~/usr-local/hermes/dreams/providers.yaml`, picked up by `~/.hermes/hermes-agent/` via `git pull`) maps workload names → primary + fallback chain.

| Workload | Primary | Cross-provider fallback |
|---|---|---|
| `classify_intent` (hot path) | DeepSeek V4 Flash | none (degrade to rule-based) |
| `preflight_polish` | DeepSeek V4 Flash | Anthropic Haiku 4.5 |
| `skill_dream_reflect` | Anthropic Sonnet 4.6 | DeepSeek V4 Pro |
| `memory_dream_consolidate` | Anthropic Sonnet 4.6 | DeepSeek V4 Pro |
| `board_dream_synthesize` (V2) | Anthropic Opus 4.7 | Anthropic Sonnet 4.6 |

**Callers specify the workload name only — never inline a model.** Routing is policy (one file) rather than code.

### Telemetry expectations (runtime sinks)

Every effectful code path emits:
- **Per LLM call** → row to runtime `observability/llm_calls.jsonl` (workload, model, tokens, cache_read, latency, schema status, idempotency_key).
- **Per dream create** → `manifest.json` inside runtime `dreams/<dream_id>/`.
- **Per apply / discard / force-override** → hash-chained row to runtime `dreams/audit.jsonl`.
- **Per preflight invocation** → row to runtime `preflight/log/<YYYY-MM-DD>.jsonl`.

A code path that does effectful work without emitting telemetry is wrong.

### CLI verbs (upstream vocabulary)

```
hermes dream create [--scope memory|skill|board] [--since <duration>]
hermes dream status
hermes dream diff   <dream_id>
hermes dream apply  <dream_id> [--accept | --select <ids>]
hermes dream discard <dream_id>
```

In-session triggers: `/dream`, `/dream status`, `/dream diff`. Match these verb names byte-for-byte — diverging causes rebase pain against upstream #10771.

### Sync flow

```bash
# 1. Edit + commit + push in the dev checkout
cd ~/usr-local/hermes
git add -p && git commit -m "..." && git push origin main

# 2. Pull into the runtime checkout
cd ~/.hermes/hermes-agent
git pull private main
hermes gateway restart   # if the gateway is running
```

No file-copying step; the two checkouts are two working copies of the same git repo on different remotes (dev tree → `origin = hermes-agent-private`; runtime → `private = hermes-agent-private`). The legacy `deploy.sh` is unused — keep it on disk for now in case any external scripts reference it, but don't run it.

Tests run against the dev tree (source-of-truth), NOT against the runtime checkout — `pytest tests/lib/` from `~/usr-local/hermes/` exercises the version you're about to push. Don't `pytest` against `~/.hermes/hermes-agent/` either; its state lags the dev tree by however long since the last pull.

### Definition-of-Done (V1)

`planning-artifacts/prd-hermes-2026-05-12.md` §18 is the ship gate. Re-audit before declaring V1 complete; `workflow-status.yaml`'s "V1 COMPLETE" line reflects epic test-suite green, **not** the operational DoD.

### Out of scope for V1 (deferred to V2)

Per PRD §10.2: auto-scheduling activation, per-role dreams (CEO/CTO/CFO), reflection split, skill-dream (FR-45–47), board-dream, persona-proposals.

---

## Cron (scheduled jobs)

`cron/jobs.py` (job store) + `cron/scheduler.py` (tick loop). Agents
schedule jobs via the `cronjob` tool; users via `hermes cron <verb>`
(`list`, `add`, `edit`, `pause`, `resume`, `run`, `remove`) or the
`/cron` slash command.

Supported schedule formats:
- Duration: `"30m"`, `"2h"`, `"1d"`
- "every" phrase: `"every 2h"`, `"every monday 9am"`
- 5-field cron expression: `"0 9 * * *"`
- ISO timestamp (one-shot): `"2026-06-01T09:00:00Z"`

Per-job fields include `skills` (load specific skills), `model` /
`provider` overrides, `script` (pre-run data-collection script whose
stdout is injected into the prompt; `no_agent=True` turns the script
into the entire job), `context_from` (chain job A's last output into
job B's prompt), `workdir` (run in a specific directory with its
`AGENTS.md`/`CLAUDE.md` loaded), and multi-platform delivery.

Hardening invariants:
- **3-minute hard interrupt** on cron sessions — runaway agent loops
  cannot monopolize the scheduler.
- Catchup window: half the job's period, clamped to 120s–2h.
- Grace window: 120s for one-shot jobs whose fire time was missed.
- File lock at `~/.hermes/cron/.tick.lock` prevents duplicate ticks
  across processes.
- Cron sessions pass `skip_memory=True` by default; memory providers
  intentionally do not run during cron.

Cron deliveries are **not** mirrored into the target gateway session —
they land in their own cron session with a header/footer frame so the
main conversation's message-role alternation stays intact.

---

## Kanban (multi-agent work queue)

Durable SQLite-backed board that lets multiple profiles / workers
collaborate on shared tasks. Users drive it via `hermes kanban <verb>`;
workers spawned by the dispatcher drive it via a dedicated `kanban_*`
toolset so their schema footprint is zero when they're not inside a
kanban task.

- **CLI:** `hermes_cli/kanban.py` wires `hermes kanban` with verbs
  `init`, `create`, `list` (alias `ls`), `show`, `assign`, `link`,
  `unlink`, `comment`, `complete`, `block`, `unblock`, `archive`,
  `tail`, plus less-commonly-used `watch`, `stats`, `runs`, `log`,
  `assignees`, `heartbeat`, `notify-*`, `dispatch`, `daemon`, `gc`.
- **Worker toolset:** `tools/kanban_tools.py` exposes `kanban_show`,
  `kanban_complete`, `kanban_block`, `kanban_heartbeat`, `kanban_comment`,
  `kanban_create`, `kanban_link` — gated by `HERMES_KANBAN_TASK` so
  the schema only appears for processes actually running as a worker.
- **Dispatcher:** long-lived loop that (default every 60s) reclaims
  stale claims, promotes ready tasks, atomically claims, and spawns
  assigned profiles. Runs **inside the gateway** by default via
  `kanban.dispatch_in_gateway: true`.
- **Plugin assets:** `plugins/kanban/dashboard/` (web UI) +
  `plugins/kanban/systemd/` (`hermes-kanban-dispatcher.service` for
  standalone dispatcher deployment).

Isolation model:
- **Board** is the hard boundary — workers are spawned with
  `HERMES_KANBAN_BOARD` pinned in their env so they can't see other
  boards.
- **Tenant** is a soft namespace *within* a board — one specialist
  fleet can serve multiple businesses with workspace-path + memory-key
  isolation.
- After ~5 consecutive spawn failures on the same task the dispatcher
  auto-blocks it to prevent spin loops.

Full user-facing docs: `website/docs/user-guide/features/kanban.md`.

---

## Important Policies

### Prompt Caching Must Not Break

Hermes-Agent ensures caching remains valid throughout a conversation. **Do NOT implement changes that would:**
- Alter past context mid-conversation
- Change toolsets mid-conversation
- Reload memories or rebuild system prompts mid-conversation

Cache-breaking forces dramatically higher costs. The ONLY time we alter context is during context compression.

Slash commands that mutate system-prompt state (skills, tools, memory, etc.)
must be **cache-aware**: default to deferred invalidation (change takes
effect next session), with an opt-in `--now` flag for immediate
invalidation. See `/skills install --now` for the canonical pattern.

### Background Process Notifications (Gateway)

When `terminal(background=true, notify_on_complete=true)` is used, the gateway runs a watcher that
detects process completion and triggers a new agent turn. Control verbosity of background process
messages with `display.background_process_notifications`
in config.yaml (or `HERMES_BACKGROUND_NOTIFICATIONS` env var):

- `all` — running-output updates + final message (default)
- `result` — only the final completion message
- `error` — only the final message when exit code != 0
- `off` — no watcher messages at all

---

## Profiles: Multi-Instance Support

Hermes supports **profiles** — multiple fully isolated instances, each with its own
`HERMES_HOME` directory (config, API keys, memory, sessions, skills, gateway, etc.).

The core mechanism: `_apply_profile_override()` in `hermes_cli/main.py` sets
`HERMES_HOME` before any module imports. All `get_hermes_home()` references
automatically scope to the active profile.

### Rules for profile-safe code

1. **Use `get_hermes_home()` for all HERMES_HOME paths.** Import from `hermes_constants`.
   NEVER hardcode `~/.hermes` or `Path.home() / ".hermes"` in code that reads/writes state.
   ```python
   # GOOD
   from hermes_constants import get_hermes_home
   config_path = get_hermes_home() / "config.yaml"

   # BAD — breaks profiles
   config_path = Path.home() / ".hermes" / "config.yaml"
   ```

2. **Use `display_hermes_home()` for user-facing messages.** Import from `hermes_constants`.
   This returns `~/.hermes` for default or `~/.hermes/profiles/<name>` for profiles.
   ```python
   # GOOD
   from hermes_constants import display_hermes_home
   print(f"Config saved to {display_hermes_home()}/config.yaml")

   # BAD — shows wrong path for profiles
   print("Config saved to ~/.hermes/config.yaml")
   ```

3. **Module-level constants are fine** — they cache `get_hermes_home()` at import time,
   which is AFTER `_apply_profile_override()` sets the env var. Just use `get_hermes_home()`,
   not `Path.home() / ".hermes"`.

4. **Tests that mock `Path.home()` must also set `HERMES_HOME`** — since code now uses
   `get_hermes_home()` (reads env var), not `Path.home() / ".hermes"`:
   ```python
   with patch.object(Path, "home", return_value=tmp_path), \
        patch.dict(os.environ, {"HERMES_HOME": str(tmp_path / ".hermes")}):
       ...
   ```

5. **Gateway platform adapters should use token locks** — if the adapter connects with
   a unique credential (bot token, API key), call `acquire_scoped_lock()` from
   `gateway.status` in the `connect()`/`start()` method and `release_scoped_lock()` in
   `disconnect()`/`stop()`. This prevents two profiles from using the same credential.
   See `gateway/platforms/telegram.py` for the canonical pattern.

6. **Profile operations are HOME-anchored, not HERMES_HOME-anchored** — `_get_profiles_root()`
   returns `Path.home() / ".hermes" / "profiles"`, NOT `get_hermes_home() / "profiles"`.
   This is intentional — it lets `hermes -p coder profile list` see all profiles regardless
   of which one is active.

## Known Pitfalls

### DO NOT hardcode `~/.hermes` paths
Use `get_hermes_home()` from `hermes_constants` for code paths. Use `display_hermes_home()`
for user-facing print/log messages. Hardcoding `~/.hermes` breaks profiles — each profile
has its own `HERMES_HOME` directory. This was the source of 5 bugs fixed in PR #3575.

### DO NOT introduce new `simple_term_menu` usage
Existing call sites in `hermes_cli/main.py` remain for legacy fallback only;
the preferred UI is curses (stdlib) because `simple_term_menu` has
ghost-duplication rendering bugs in tmux/iTerm2 with arrow keys. New
interactive menus must use `hermes_cli/curses_ui.py` — see
`hermes_cli/tools_config.py` for the canonical pattern.

### DO NOT use `\033[K` (ANSI erase-to-EOL) in spinner/display code
Leaks as literal `?[K` text under `prompt_toolkit`'s `patch_stdout`. Use space-padding: `f"\r{line}{' ' * pad}"`.

### `_last_resolved_tool_names` is a process-global in `model_tools.py`
`_run_single_child()` in `delegate_tool.py` saves and restores this global around subagent execution. If you add new code that reads this global, be aware it may be temporarily stale during child agent runs.

### DO NOT hardcode cross-tool references in schema descriptions
Tool schema descriptions must not mention tools from other toolsets by name (e.g., `browser_navigate` saying "prefer web_search"). Those tools may be unavailable (missing API keys, disabled toolset), causing the model to hallucinate calls to non-existent tools. If a cross-reference is needed, add it dynamically in `get_tool_definitions()` in `model_tools.py` — see the `browser_navigate` / `execute_code` post-processing blocks for the pattern.

### The gateway has TWO message guards — both must bypass approval/control commands
When an agent is running, messages pass through two sequential guards:
(1) **base adapter** (`gateway/platforms/base.py`) queues messages in
`_pending_messages` when `session_key in self._active_sessions`, and
(2) **gateway runner** (`gateway/run.py`) intercepts `/stop`, `/new`,
`/queue`, `/status`, `/approve`, `/deny` before they reach
`running_agent.interrupt()`. Any new command that must reach the runner
while the agent is blocked (e.g. approval prompts) MUST bypass BOTH
guards and be dispatched inline, not via `_process_message_background()`
(which races session lifecycle).

### Squash merges from stale branches silently revert recent fixes
Before squash-merging a PR, ensure the branch is up to date with `main`
(`git fetch origin main && git reset --hard origin/main` in the worktree,
then re-apply the PR's commits). A stale branch's version of an unrelated
file will silently overwrite recent fixes on main when squashed. Verify
with `git diff HEAD~1..HEAD` after merging — unexpected deletions are a
red flag.

### Don't wire in dead code without E2E validation
Unused code that was never shipped was dead for a reason. Before wiring an
unused module into a live code path, E2E test the real resolution chain
with actual imports (not mocks) against a temp `HERMES_HOME`.

### Tests must not write to `~/.hermes/`
The `_isolate_hermes_home` autouse fixture in `tests/conftest.py` redirects `HERMES_HOME` to a temp dir. Never hardcode `~/.hermes/` paths in tests.

**Profile tests**: When testing profile features, also mock `Path.home()` so that
`_get_profiles_root()` and `_get_default_hermes_home()` resolve within the temp dir.
Use the pattern from `tests/hermes_cli/test_profiles.py`:
```python
@pytest.fixture
def profile_env(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home
```

---

## Testing

**ALWAYS use `scripts/run_tests.sh`** — do not call `pytest` directly. The script enforces
hermetic environment parity with CI (unset credential vars, TZ=UTC, LANG=C.UTF-8,
4 xdist workers matching GHA ubuntu-latest). Direct `pytest` on a 16+ core
developer machine with API keys set diverges from CI in ways that have caused
multiple "works locally, fails in CI" incidents (and the reverse).

```bash
scripts/run_tests.sh                                  # full suite, CI-parity
scripts/run_tests.sh tests/gateway/                   # one directory
scripts/run_tests.sh tests/agent/test_foo.py::test_x  # one test
scripts/run_tests.sh -v --tb=long                     # pass-through pytest flags
```

### Why the wrapper (and why the old "just call pytest" doesn't work)

Five real sources of local-vs-CI drift the script closes:

| | Without wrapper | With wrapper |
|---|---|---|
| Provider API keys | Whatever is in your env (auto-detects pool) | All `*_API_KEY`/`*_TOKEN`/etc. unset |
| HOME / `~/.hermes/` | Your real config+auth.json | Temp dir per test |
| Timezone | Local TZ (PDT etc.) | UTC |
| Locale | Whatever is set | C.UTF-8 |
| xdist workers | `-n auto` = all cores (20+ on a workstation) | `-n 4` matching CI |

`tests/conftest.py` also enforces points 1-4 as an autouse fixture so ANY pytest
invocation (including IDE integrations) gets hermetic behavior — but the wrapper
is belt-and-suspenders.

### Running without the wrapper (only if you must)

If you can't use the wrapper (e.g. on Windows or inside an IDE that shells
pytest directly), at minimum activate the venv and pass `-n 4`:

```bash
source .venv/bin/activate   # or: source venv/bin/activate
python -m pytest tests/ -q -n 4
```

Worker count above 4 will surface test-ordering flakes that CI never sees.

Always run the full suite before pushing changes.

### Don't write change-detector tests

A test is a **change-detector** if it fails whenever data that is **expected
to change** gets updated — model catalogs, config version numbers,
enumeration counts, hardcoded lists of provider models. These tests add no
behavioral coverage; they just guarantee that routine source updates break
CI and cost engineering time to "fix."

**Do not write:**

```python
# catalog snapshot — breaks every model release
assert "gemini-2.5-pro" in _PROVIDER_MODELS["gemini"]
assert "MiniMax-M2.7" in models

# config version literal — breaks every schema bump
assert DEFAULT_CONFIG["_config_version"] == 21

# enumeration count — breaks every time a skill/provider is added
assert len(_PROVIDER_MODELS["huggingface"]) == 8
```

**Do write:**

```python
# behavior: does the catalog plumbing work at all?
assert "gemini" in _PROVIDER_MODELS
assert len(_PROVIDER_MODELS["gemini"]) >= 1

# behavior: does migration bump the user's version to current latest?
assert raw["_config_version"] == DEFAULT_CONFIG["_config_version"]

# invariant: no plan-only model leaks into the legacy list
assert not (set(moonshot_models) & coding_plan_only_models)

# invariant: every model in the catalog has a context-length entry
for m in _PROVIDER_MODELS["huggingface"]:
    assert m.lower() in DEFAULT_CONTEXT_LENGTHS_LOWER
```

The rule: if the test reads like a snapshot of current data, delete it. If
it reads like a contract about how two pieces of data must relate, keep it.
When a PR adds a new provider/model and you want a test, make the test
assert the relationship (e.g. "catalog entries all have context lengths"),
not the specific names.

Reviewers should reject new change-detector tests; authors should convert
them into invariants before re-requesting review.

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **hermes-agent** (33996 symbols, 35770 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> If any GitNexus tool warns the index is stale, run `npx gitnexus analyze` in terminal first.

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `gitnexus_impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `gitnexus_detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `gitnexus_query({query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `gitnexus_context({name: "symbolName"})`.

## Never Do

- NEVER edit a function, class, or method without first running `gitnexus_impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `gitnexus_rename` which understands the call graph.
- NEVER commit changes without running `gitnexus_detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/hermes-agent/context` | Codebase overview, check index freshness |
| `gitnexus://repo/hermes-agent/clusters` | All functional areas |
| `gitnexus://repo/hermes-agent/processes` | All execution flows |
| `gitnexus://repo/hermes-agent/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
