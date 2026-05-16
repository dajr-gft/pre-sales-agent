---
name: sow-architecture
description: >
  Produces the architecture-related fields of `sow_data`
  (`architecture_description`, `technology_stack`, `architecture_components`,
  `architecture_integrations`, `customer_primary_domain` — domain only when
  set by a sibling skill, never by this one) and renders the architecture
  PNG via `generate_architecture_diagram`. Loaded by `sow-orchestrator`
  during Phase 2 Step D, AFTER requirements (Step A), delivery plan (Step B)
  and scope boundaries (Step C) snapshots are already in `sow_data`. NOT
  responsible for Partner Overview, Customer Overview, or Executive
  Summary — those belong to `sow-narrative`. NOT responsible for the user
  review presentation — the orchestrator owns that.
metadata:
  pattern: reasoning-chain + tool-call
  produces: architecture_description, technology_stack, architecture_components, architecture_integrations, diagram_png
  inputs: extraction_manifest, sow_data snapshot (requirements + delivery + scope)
  upstream-skill: sow-orchestrator
  references-skill: sow-shared
---

# SOW Architecture

Architecture artifacts only — five sub-steps (1a–1e) executed in order,
each with a completion gate. No user review here; the orchestrator presents
after this skill returns.

References below are binding — they override any paraphrase here. Depth
and structure follow the references; "brief" and "concise" apply to
orchestration messages only.

## Load before drafting (mandatory)

via `load_skill_resource`:

- `sow-shared` / `references/style-guide.md` — quality contract + paragraph-break rule.
- `sow-shared` / `references/scope-examples/architecture.md` — architecture quality floor (tech stack rows + description shape).
- `sow-architecture` / `references/reasoning-rules.md` — reasoning Steps 1-5 (must complete before any output).
- `sow-architecture` / `references/diagram-spec.md` — clusters, nodes, edges, labels, layout, checklist, anti-patterns.
- `sow-architecture` / `references/tech-stack-table-rules.md` — table + three-way invariant.
- `sow-architecture` / `references/audit-rules.md` — tool-audit retry budget + silent revision protocol.

When patching: also `sow-shared` / `references/id-stability-rules.md`. Untouched nodes, edges, table rows, and integrations stay byte-for-byte identical.

## Inputs (for sub-step 1a)

- Manifest `extracted_items` for `[Briefing, Integrations]` + resolved `manifest.gaps`.
- Current `sow_data` snapshot with FRs, NFRs, delivery plan, and scope boundaries already populated.

If the Manifest captured a system, data source, or GCP service that does not appear in the FRs, it must still be evaluated for inclusion in the architecture.

## Sub-steps (mandatory order)

### (1a) Think — silent

Execute `reasoning-rules.md` Steps 1-5 using Manifest + FRs/NFRs/deliverables as input. Produce an internal draft of layers, components, cluster assignments, primary data-flow chain, and cross-cutting concerns. Do NOT emit this draft.

### (1b) Write the textual description

150+ words, data-flow narrative per `diagram-spec.md` → Part E. Apply paragraph breaks per `style-guide.md`. This text is the **single source of truth** — every GCP service named here must later appear in the table AND as a diagram node; every data-flow sentence must later become a diagram edge. Run the Architecture Description Self-Test (`diagram-spec.md` → Part E) before closing.

### (1c) Write the Technology Stack table

One row per GCP service mentioned in (1b) — no more, no less. Apply `tech-stack-table-rules.md` and `diagram-spec.md` → Part B (what to exclude: IAM, built-in encryption, non-GCP systems).

### (1d) Derive the diagram spec from (1b) literally

Re-read (1b) and extract directly — do not use a mental model.

- **Nodes**: one per proper noun in (1b) that is a system, GCP service, or entry point. Apply `diagram-spec.md` → Part D (`service` + `label`) and Part A (`parent_cluster` + `sub_cluster`).
- **Edges**: apply `diagram-spec.md` → Part C (Edge Derivation + Edge Hygiene). One edge per data-flow sentence; honor the hops (1b) names AND the hops (1b) omits; labels match protocols named in (1b).
- **Direction & layout**: `diagram-spec.md` → Part F.

Populate `architecture_integrations` (one row per external system in (1b)) and `architecture_components` (functional roles, not products) from the same source.

### (1e) Render the diagram

Call `generate_architecture_diagram` with the spec from (1d) + description from (1b) + tech stack from (1c) as arguments. The tool runs the structural audit internally. Apply `audit-rules.md` for the silent revision protocol on `ToolError` (3-retry budget, post-budget fallback continues with textual sections only — never silently skip).

## Before returning (workflow gate)

Verify against loaded references and rewrite any non-compliant artifact in place:

- Paragraph structure follows `style-guide.md` → "Paragraph breaks".
- Three-way invariant: every GCP service in the description appears in the table AND as a diagram node.
- Every edge corresponds to a data-flow sentence in the description.
- IAM is absent from diagram nodes and from the table.
- Minimum component checklist (`diagram-spec.md` → Part G) passes for this project class.
- Node labels are functional and project-specific (`diagram-spec.md` → Part D).
- When patching: untouched nodes/edges/rows/integrations are byte-for-byte identical per `id-stability-rules.md`.

## Out of scope

- Does not produce Partner Overview, Customer Overview, or Executive Summary (→ `sow-narrative`).
- Does not present the architecture review.
- Does not call `stage_sow` or `confirm_phase_completion`.
