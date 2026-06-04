---
spec:
  persona: Dev
  phase: implementation
  imperative_preamble: true
  verification:
    - "Issues identified and prioritized"
    - "Fixes implemented"
    - "Tests updated"
---

Fix implementation issues found during code review:

1. **Analyze Findings** — Review the code review report and prioritize fixes
2. **Fix Critical Issues** — Address 🔴 Critical items first
3. **Fix Warnings** — Address 🟡 Warning items
4. **Verify Fixes** — Re-run tests to confirm nothing broke
5. **Document Changes** — What was fixed, why, and how

For each fix:
- Understand the root cause (not just the symptom)
- Implement the minimal fix
- Add or update tests to prevent regression
- Verify with the reviewer if needed

Write fix log to `implementation-artifacts/correct-course-{date}.md`.
