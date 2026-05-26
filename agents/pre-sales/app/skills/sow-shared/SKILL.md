---
name: sow-shared
description: >
  **Reference library — do NOT activate as a workflow skill.** This skill exists
  only to host cross-cutting references (writing style, language rules, ID stability
  rules, scope calibration examples) consumed by the SOW section workers and the
  surgical patcher: `sow-requirements`, `sow-delivery-plan`, `sow-scope-boundaries`,
  `sow-architecture`, `sow-narrative`, and `sow-revision`. Those consumers load the
  files here via `load_skill_resource(skill_name="sow-shared", file_path="references/<file>.md")`
  (section workers) or `load_sow_reference(target_skill="sow-shared", reference_path="references/<file>.md")`
  (revision agent). If you are about to call `load_skill("sow-shared")` expecting a
  workflow, STOP — this skill has no workflow. Load the specific reference instead.
metadata:
  pattern: reference-library
  consumers: sow-requirements, sow-delivery-plan, sow-scope-boundaries, sow-architecture, sow-narrative, sow-revision
---

# SOW Shared References

Cross-cutting reference library. **No workflow.** If `load_skill` ever
routes here, that is a misuse — load the specific reference instead via:

```
load_skill_resource(skill_name="sow-shared", file_path="references/<file>.md")
```

## Inventory

- `references/style-guide.md` — cross-cutting writing rules + Self-sufficiency contract. Binding for every SOW section.
- `references/language-rules.md` — conversation language vs. final English document; what to localize, what to keep verbatim in English.
- `references/id-stability-rules.md` — ID preservation rules; never renumber, never reorder, never swap; binding Patch contract for `sow-revision`.
- `references/scope-examples-*.md` — quality floor and calibration examples, one file per section:
  - `references/scope-examples-executive-summary.md` — Template-compliant Executive Summary (loaded by `sow-narrative`).
  - `references/scope-examples-fr-nfr.md` — FR + NFR patterns including binding Reliability Bad/Good pair (loaded by `sow-requirements`).
  - `references/scope-examples-scope-contractual.md` — OOS + Assumptions + CR Policy (loaded by `sow-scope-boundaries`).
  - `references/scope-examples-risks.md` — Risks patterns (loaded by `sow-scope-boundaries`).
  - `references/scope-examples-delivery.md` — Activities + Deliverables (loaded by `sow-delivery-plan`).
  - `references/scope-examples-architecture.md` — Tech Stack + Architecture Description (loaded by `sow-architecture`).

## What is NOT here

Section-specific rules (FR patterns, NFR WAF pillars, OOS categories, assumption patterns, architecture rules, workstream structure, exec summary template) live in the matching workflow skill — not here.

## Out of scope (critical boundary)

- **MUST NOT be activated via `load_skill`.** This skill has no workflow and there will not be one. Calling `load_skill("sow-shared")` is a routing defect.
