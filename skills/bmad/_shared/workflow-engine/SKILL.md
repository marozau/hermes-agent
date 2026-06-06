---
name: bmad:workflow-engine
description: 'Workflow engine: BMAD mandates (M1, M3, M4, M5, M7, M9, R1, R2, M8, M10, R3) — phase enforcement rules for BMAD projects'
version: '6.6.0'
tags:
  - bmad
  - shared
  - workflow
  - mandates
---

# Workflow Engine — BMAD Mandates & Rules

**Source:** `_shared/tasks/workflow.xml` (BMAD Core v6.2.2.0)

---

## CRITICAL LLM INSTRUCTIONS

- **MANDATORY:** Execute ALL steps in the flow section IN EXACT ORDER
- DO NOT skip steps or change the sequence
- HALT immediately when halt-conditions are met
- Each action within a step is a REQUIRED action to complete that step
- Sections outside flow (validation, output, critical-context) provide essential context — review and apply throughout execution

---

## LLM Mandates (Critical)

| ID | Mandate |
|----|---------|
| M1 | Always read COMPLETE files — NEVER use offset/limit when reading any workflow related files |
| M3 | Instructions are MANDATORY — either as file path, steps or embedded list in YAML, XML or markdown |
| M4 | Execute ALL steps in instructions IN EXACT ORDER |
| M5 | Save to template output file after EVERY "template-output" tag |
| M7 | NEVER skip a step — YOU are responsible for every steps execution without fail or excuse |
| M9 | Load config_source (REQUIRED for all modules) |
| R1 | Steps execute in exact numerical order (1, 2, 3...) |
| R2 | Optional steps: Ask user unless #yolo mode active |
| M8 | Template-output tags: Save content, discuss with the user the section completed, and NEVER proceed until the user indicates to proceed (unless YOLO mode has been activated) |
| M10 | Generate content for this section on template-output |
| R3 | When template-output tag found, display content and offer: [a] Advanced Elicitation, [c] Continue, [p] Party-Mode, [y] YOLO the rest of this document only. WAIT for response |

---

## WORKFLOW RULES

1. **Steps execute in exact numerical order** (1, 2, 3...)
2. **Optional steps:** Ask user unless #yolo mode active
3. **Template-output tags:** Save content, discuss with the user the section completed, and NEVER proceed until the user indicates to proceed (unless YOLO mode has been activated)

---

## Workflow Flow

### Step 1: Load and Initialize Workflow

#### 1a — Load Configuration and Resolve Variables
1. Read workflow.yaml from provided path
2. Load config_source (REQUIRED for all modules)
3. Resolve all {config_source}: references with values from config
4. Resolve system variables (date:system-generated) and paths ({project-root}, {installed_path})
5. Ask user for input of any variables that are still unknown

#### 1b — Load Required Components
- Instructions: Read COMPLETE file from path OR embedded list (REQUIRED)
- If template path → Read COMPLETE template file
- If validation path → Note path for later loading when needed
- If template: false → Mark as action-workflow (else template-workflow)
- Data files (csv, json) → Store paths only, load on-demand when instructions reference them

#### 1c — Initialize Output (if template-workflow)
1. Resolve default_output_file path with all variables and {{date}}
2. Create output directory if doesn't exist
3. If template-workflow → Write template to output file with placeholders
4. If action-workflow → Skip file creation

---

### Step 2: Process Each Instruction Step in Order

For each step in instructions:

#### 2a — Handle Step Attributes
- If optional="true" and NOT #yolo → Ask user to include
- If if="condition" → Evaluate condition
- If for-each="item" → Repeat step for each item
- If repeat="n" → Repeat step n times

#### 2b — Execute Step Content
- Process step instructions (markdown or XML tags)
- Replace {{variables}} with values (ask user if unknown)
- Execute tags:
  - `action` xml tag → Perform the action
  - `check if="condition"` ... `</check>` → Conditional block wrapping actions
  - `ask` xml tag → Prompt user and WAIT for response
  - `invoke-workflow` xml tag → Execute another workflow with given inputs
  - `invoke-task` xml tag → Execute specified task
  - `invoke-protocol name="protocol_name"` → Execute reusable protocol
  - `goto step="x"` → Jump to specified step

#### 2c — Handle template-output Tags
When template-output tag found:
1. Generate content for this section
2. Save to file (Write first time, Edit subsequent)
3. Display generated content
4. Offer: [a] Advanced Elicitation, [c] Continue, [p] Party-Mode, [y] YOLO
5. WAIT for response:
   - `a`: Start advanced elicitation workflow
   - `c`: Continue to next step
   - `p`: Start party-mode workflow
   - `y`: Enter #yolo mode for rest of workflow

#### 2d — Step Completion
- If no special tags and NOT #yolo: Ask "Continue to next step? (y/n/edit)"

---

### Step 3: Completion
1. Confirm document saved to output path
2. Report workflow completion

---

## Execution Modes

| Mode | Behavior |
|------|----------|
| **normal** | Full user interaction and confirmation of EVERY step at EVERY template output — NO EXCEPTIONS except yolo mode |
| **yolo** | Skip all confirmations and elicitation, minimize prompts and try to produce all of the workflow automatically by simulating the remaining discussions with a simulated expert user |

---

## Supported XML Tags

### Structural
- `<step n="X" goal="...">` — Define step with number and goal
- `optional="true"` — Step can be skipped
- `if="condition"` — Conditional execution
- `for-each="collection"` — Iterate over items
- `repeat="n"` — Repeat n times

### Execution
- `<action>` — Required action to perform
- `<action if="condition">` — Single conditional action (inline, no closing tag)
- `<check if="condition">...</check>` — Conditional block wrapping multiple items
- `<ask>` — Get user input (ALWAYS wait for response before continuing)
- `<goto>` — Jump to another step
- `<invoke-workflow>` — Call another workflow
- `<invoke-task>` — Call a task
- `<invoke-protocol>` — Execute a reusable protocol

### Output
- `<template-output>` — Save content checkpoint
- `<critical>` — Cannot be skipped
- `<example>` — Show example output

---

## Protocols

### discover_inputs — Smart File Discovery

Intelligently load project files (whole or sharded) based on workflow's `input_file_patterns` configuration.

**Critical:** Only execute if workflow.yaml contains `input_file_patterns` section.

**Flow:**
1. Parse input file patterns from workflow.yaml
2. For each pattern, try sharded documents first (FULL_LOAD, SELECTIVE_LOAD, or INDEX_GUIDED strategies)
3. If no sharded found, try whole document
4. Handle not-found gracefully
5. Report discovery results

**Load Strategies:**
- **FULL_LOAD:** Load ALL files in sharded directory (glob all .md files)
- **SELECTIVE_LOAD:** Load specific shard using template variable (e.g., {{epic_num}})
- **INDEX_GUIDED:** Load index.md, analyze structure, then intelligently load relevant docs

---

## Final Critical Rules

- This is the complete workflow execution engine
- You MUST follow instructions exactly as written
- The workflow execution engine is governed by: `_shared/tasks/workflow.xml`
- You MUST have already loaded and processed: `{installed_path}/workflow.yaml`
- This workflow uses INTENT-DRIVEN PLANNING — adapt organically to product type and context
- YOU ARE FACILITATING A CONVERSATION with a user to produce a final document step by step. The whole process is meant to be collaborative, helping the user flesh out their ideas. Do not rush or optimize and skip any section.
