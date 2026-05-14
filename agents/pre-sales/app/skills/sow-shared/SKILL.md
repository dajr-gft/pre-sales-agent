---
name: sow-shared
description: >
  **Reference library — do NOT activate as a workflow skill.** This skill exists
  only to host cross-cutting references (writing style, language rules, ID stability
  rules, scope calibration examples) shared by every SOW workflow skill —
  `sow-orchestrator`, `sow-architecture`, `sow-scope-boundaries`, `sow-delivery-plan`,
  `sow-requirements`, `sow-narrative`, and `sow-revision`. Those skills consume the
  files here via `load_skill_resource(skill_name="sow-shared", file_path="references/<file>.md")`.
  If you are about to call `load_skill("sow-shared")` expecting a workflow, STOP —
  this skill has no workflow. Load the specific reference instead.
metadata:
  pattern: reference-library
  consumers: sow-orchestrator, sow-architecture, sow-scope-boundaries, sow-delivery-plan, sow-requirements, sow-narrative, sow-revision
---

# SOW Shared References

This skill hosts the cross-cutting reference files used by every SOW workflow
skill. It deliberately has **no workflow of its own** — if `load_skill` ever
routes here, that is a misuse.

## How other skills consume this

Each workflow skill loads only the references it needs, in the Pre-step of
the relevant step. The call shape is always:

```
load_skill_resource(skill_name="sow-shared", file_path="references/<file>.md")
```

## Inventory

- `references/style-guide.md` — cross-cutting writing rules and the
  Self-sufficiency contract. Binding quality contract for every SOW section
  generated or patched by any workflow skill.
- `references/language-rules.md` — conversation language vs. final English
  document; what to localize, what to keep verbatim in English.
- `references/id-stability-rules.md` — ID preservation rules that survive
  every revision; never renumber, never reorder, never swap.
- `references/scope-examples.md` — quality floor and calibration examples
  for the heavyweight sections (FRs, NFRs, OOS, Assumptions, narrative).

## What is NOT here

Section-specific rules (Functional Requirements patterns, NFR WAF pillars,
Out-of-Scope categories, Assumption patterns, architecture rules, deliverable
workstream structure, executive summary template) live in the matching
workflow skill, not here. Load the section skill for those.

## No workflow

There is no `## Workflow`, no `## Phase`, no `## Step` here, and there will
not be. Load the file you need from `references/` and follow the rules in
the calling skill's own Phase Step.
