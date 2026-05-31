# Python Code Review Rules

## Security

### PY001: os.environ mutation
**Severity:** HIGH
**Pattern:** Direct `os.environ[key] = value` without cleanup in finally block
**Fix:** Use context manager or ensure cleanup in finally block
**Source:** Epic 7 N-2 (os.environ poisoning broke cross-epic chaining)

### PY002: shell=True in subprocess
**Severity:** HIGH
**Pattern:** `subprocess.run(..., shell=True)` or `subprocess.Popen(..., shell=True)`
**Fix:** Use list args without shell=True; use shlex.quote() if needed
**Source:** Epic 7 B-7 (shell injection via predicate commands)

### PY003: Unsanitized input to shell commands
**Severity:** HIGH
**Pattern:** `f"command {user_input}"` passed to subprocess with shell=True
**Fix:** Use list args or validate against allowlist
**Source:** Epic 7 B-7

### PY004: Hardcoded credentials/secrets
**Severity:** HIGH
**Pattern:** `password = "..."`, `api_key = "..."`, `token = "..."` (literal strings)
**Fix:** Use environment variables or secret manager

## Architecture

### PY005: Hook raises exception
**Severity:** HIGH
**Pattern:** Hook function (on_session_start, pre_tool_call, etc.) without try/except
**Fix:** Wrap hook body in try/except; log error, never raise
**Source:** Epic 6/7 hook patterns — hooks that raise bypass the entire plugin

### PY006: Direct LLM import in plugin code
**Severity:** MEDIUM
**Pattern:** `import openai`, `import anthropic`, `from litellm import` in plugin/ directory
**Fix:** Use plugin context API; LLM calls go through delegation layer

### PY007: Workload-keyed routing bypass
**Severity:** MEDIUM
**Pattern:** Hardcoded model names in routing logic instead of config-driven
**Fix:** Use profile config for model selection
**Source:** CLAUDE.md hard invariant #2

### PY008: Missing response_model on LLM calls
**Severity:** MEDIUM
**Pattern:** `delegate_one(...)` without `response_model=` parameter
**Fix:** Always specify response_model for structured output
**Source:** Epic 8-10 code review finding

## Quality

### PY009: Bare except
**Severity:** MEDIUM
**Pattern:** `except:` without specifying exception type
**Fix:** Use `except Exception:` at minimum

### PY010: Mutable default argument
**Severity:** MEDIUM
**Pattern:** `def f(x=[])` or `def f(x={})`
**Fix:** Use `None` default with initialization in body

### PY011: Type annotation missing on public function
**Severity:** LOW
**Pattern:** `def public_func(x, y):` without type hints
**Fix:** Add type annotations

### PY012: f-string in logging
**Severity:** LOW
**Pattern:** `logger.info(f"...")` instead of `logger.info("...", arg)`
**Fix:** Use %-style formatting for lazy evaluation

### PY013: Unused import
**Severity:** LOW
**Pattern:** Imported name not used in file
**Fix:** Remove unused import

### PY014: Magic number
**Severity:** LOW
**Pattern:** Bare numeric literal in condition/computation (not 0, 1, -1)
**Fix:** Extract to named constant

### PY015: Docstring missing on public class/function
**Severity:** LOW
**Pattern:** `class Foo:` or `def bar():` without docstring
**Fix:** Add docstring
