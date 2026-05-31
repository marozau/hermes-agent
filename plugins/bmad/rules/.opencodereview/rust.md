# Rust Code Review Rules

## Safety

### RS001: unsafe block without comment
**Severity:** HIGH
**Pattern:** `unsafe {` without `// SAFETY:` comment
**Fix:** Add SAFETY comment explaining invariants

### RS002: unwrap() in non-test code
**Severity:** HIGH
**Pattern:** `.unwrap()` in src/ (not tests/)
**Fix:** Use `?` operator, `.expect("reason")`, or match

### RS003: panic! in library code
**Severity:** HIGH
**Pattern:** `panic!(`, `todo!(`, `unimplemented!(` in src/
**Fix:** Return Result or use proper error handling

### RS004: Transmute usage
**Severity:** HIGH
**Pattern:** `std::mem::transmute(`
**Fix:** Use safe alternatives; document if unavoidable

## Correctness

### RS005: Dead code suppression
**Severity:** MEDIUM
**Pattern:** `#[allow(dead_code)]` without justification
**Fix:** Remove dead code or document why it's kept

### RS006: Clone on Copy type
**Severity:** MEDIUM
**Pattern:** `.clone()` on types implementing Copy
**Fix:** Remove unnecessary .clone()

### RS007: Missing error propagation
**Severity:** MEDIUM
**Pattern:** `match result { Ok(v) => v, Err(e) => return Err(e) }`
**Fix:** Use `?` operator

### RS008: Blocking call in async context
**Severity:** HIGH
**Pattern:** `std::thread::sleep`, `std::fs::read`, blocking I/O in async fn
**Fix:** Use tokio::fs, tokio::time::sleep, or spawn_blocking

## Quality

### RS009: Too many function parameters
**Severity:** MEDIUM
**Pattern:** Function with > 6 parameters
**Fix:** Use builder pattern or config struct

### RS110: Nested match complexity
**Severity:** MEDIUM
**Pattern:** match inside match inside match (> 3 levels)
**Fix:** Extract helper functions

### RS011: Unnecessary allocation
**Severity:** LOW
**Pattern:** `.to_string()` where `&str` suffices, `.to_vec()` where `&[u8]` works
**Fix:** Use references where possible

### RS012: Missing must_use
**Severity:** LOW
**Pattern:** Function returning Result without `#[must_use]`
**Fix:** Add `#[must_use]` attribute

### RS013: Magic number
**Severity:** LOW
**Pattern:** Bare numeric literal in condition/computation
**Fix:** Extract to named constant

### RS014: Doc comment missing on pub item
**Severity:** LOW
**Pattern:** `pub fn`, `pub struct`, `pub enum` without `///` doc comment
**Fix:** Add documentation comment

### RS015: Inconsistent error type
**Severity:** LOW
**Pattern:** Mixing `Box<dyn Error>` with custom error types
**Fix:** Use consistent error type (thiserror/anyhow)
