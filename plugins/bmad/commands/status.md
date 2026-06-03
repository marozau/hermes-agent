---
spec:
  persona: System
  phase: informational
  imperative_preamble: false
  verification:
    - "status output displayed correctly"
---

# /bmad:status

Show the current BMAD workflow phase state.

Displays:
- Current project name and level
- All phases (Analysis, Planning, Solutioning, Implementation)
- Status of each slot (✅ complete, 🔄 in-progress, ⬜ not started, 📌 required)
- Next recommended action

## Usage

```
/bmad:status
```
