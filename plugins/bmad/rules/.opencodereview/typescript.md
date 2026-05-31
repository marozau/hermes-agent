# TypeScript Code Review Rules

## Security

### TS001: eval() usage
**Severity:** HIGH
**Pattern:** `eval(`, `new Function(`, `setTimeout(string`, `setInterval(string`
**Fix:** Use structured alternatives; parse JSON safely

### TS002: innerHTML without sanitization
**Severity:** HIGH
**Pattern:** `.innerHTML =` without DOMPurify or equivalent
**Fix:** Use textContent or sanitize with DOMPurify

### TS003: Hardcoded secrets
**Severity:** HIGH
**Pattern:** `apiKey = "..."`, `token = "..."`, `secret = "..."` (literal strings)
**Fix:** Use environment variables or config

### TS004: SQL string concatenation
**Severity:** HIGH
**Pattern:** `` `SELECT ... ${userInput}` `` or `"SELECT " + input`
**Fix:** Use parameterized queries

## Type Safety

### TS005: any type annotation
**Severity:** MEDIUM
**Pattern:** `: any`, `as any`, `@ts-ignore`
**Fix:** Use proper types or `unknown` with type guard

### TS006: Non-null assertion on optional
**Severity:** MEDIUM
**Pattern:** `value!.property` where value could be null
**Fix:** Use optional chaining `value?.property` or null check

### TS007: Type assertion without guard
**Severity:** MEDIUM
**Pattern:** `as SomeType` without runtime validation
**Fix:** Add type guard or use zod/io-ts validation

### TS008: Missing return type on async function
**Severity:** MEDIUM
**Pattern:** `async function foo() {` without explicit return type
**Fix:** Add `Promise<ReturnType>` annotation

## Quality

### TS009: console.log in production
**Severity:** MEDIUM
**Pattern:** `console.log(`, `console.error(` in non-test files
**Fix:** Use structured logger

### TS110: Promise without error handling
**Severity:** MEDIUM
**Pattern:** `somePromise.then(` without `.catch(`
**Fix:** Add .catch() or use try/await

### TS011: Unused variable
**Severity:** LOW
**Pattern:** Variable declared but never referenced
**Fix:** Remove or prefix with underscore

### TS012: Magic number
**Severity:** LOW
**Pattern:** Bare numeric literal in condition/computation
**Fix:** Extract to named constant

### TS013: Function too long
**Severity:** LOW
**Pattern:** Function > 50 lines
**Fix:** Extract sub-functions

### TS014: Deep nesting
**Severity:** LOW
**Pattern:** > 4 levels of nesting
**Fix:** Extract early returns or helper functions

### TS015: Non-null assertion operator
**Severity:** LOW
**Pattern:** `!` after expression in non-test code
**Fix:** Use optional chaining or explicit null check
