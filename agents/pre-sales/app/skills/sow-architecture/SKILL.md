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

**Scope of this skill.** Only the architecture artifacts. Five sub-steps
(1a–1e) executed in order. No user-facing review here; the orchestrator
presents the result after this skill finishes.

## Reference authority and depth rules

The loaded reference files are the **binding quality contract** for the
architecture artifacts — not optional examples, loose inspiration, or style
suggestions.

Priority order for generated/patched content quality:

1. `sow-shared/references/style-guide.md` — binding cross-cutting writing
   rules, Self-sufficiency contract, paragraph-break rule, language hygiene.
2. `sow-shared/references/id-stability-rules.md` — binding ID preservation
   rules; when revising an existing architecture, every untouched node, edge,
   tech-stack row, and integration row stays byte-for-byte identical.
3. `references/reasoning-rules.md` — binding reasoning sequence (Steps 1-5)
   that must complete BEFORE any artifact is emitted.
4. `references/diagram-spec.md` — binding diagram construction rules
   (clusters, node granularity, edges, labels, layout, anti-patterns).
5. `references/tech-stack-table-rules.md` — binding table consistency rules
   (three-way invariant with description and diagram).
6. `references/audit-rules.md` — binding tool-audit behavior (retry budget,
   silent revision protocol, what the audit checks).
7. This `SKILL.md` — workflow orchestration only (sub-step ordering, when to
   call which reference, how to invoke the diagram tool).

If this skill says to do X and a reference defines how X must be
written/patched, the reference controls the content. Do not simplify,
shorten, or reinterpret reference requirements unless the reference
explicitly allows it.

**Brevity scope rule:** instructions such as "brief", "concise", "direct",
or "short" apply only to conversational orchestration messages,
confirmations, and error handling. They do NOT apply to SOW architecture
content. For the architecture description, technology stack rows, and
diagram spec, follow the depth, structure, minimums, required wording, and
quality rules from the loaded references.

---

## Workflow — sub-steps 1a through 1e (mandatory order)

Each sub-step has a completion gate — do not begin the next until the
current one is done.

**Pre-step — Load and apply references (mandatory gate before any drafting):**

- `load_skill_resource(skill_name="sow-shared", file_path="references/style-guide.md")`
  — **Binding quality contract.** Every cross-cutting writing rule and the
  Self-sufficiency contract are mandatory.
- `load_skill_resource(skill_name="sow-shared", file_path="references/scope-examples.md")`
  — **Quality floor.** The architecture description must match or exceed the
  depth, specificity, and professionalism demonstrated for the architecture
  example.
- `load_skill_resource(skill_name="sow-architecture", file_path="references/reasoning-rules.md")`
  — **Binding reasoning sequence.**
- `load_skill_resource(skill_name="sow-architecture", file_path="references/diagram-spec.md")`
  — **Binding diagram rules.**
- `load_skill_resource(skill_name="sow-architecture", file_path="references/tech-stack-table-rules.md")`
  — **Binding table rules.**
- `load_skill_resource(skill_name="sow-architecture", file_path="references/audit-rules.md")`
  — **Binding tool-audit behavior.**

If you are patching an existing architecture (not generating from scratch),
also load
`load_skill_resource(skill_name="sow-shared", file_path="references/id-stability-rules.md")`
and treat the Patch contract there as overriding any sub-step instinct to
regenerate the artifact.

Use as input for sub-step 1a:

- The Extraction Manifest (`manifest.extracted_items` — especially `Briefing`
  and `Integrations` — plus resolved `manifest.gaps`).
- The current `sow_data` snapshot with the requirements (FRs + NFRs),
  delivery plan (activities, deliverables, timeline), and scope boundaries
  (assumptions, OOS) already populated by previous Phase Steps.

If the Manifest captured a system, data source, or GCP service that does
not appear in the FRs, it must still be evaluated for inclusion in the
architecture.

### (1a) Think (silent)

Execute Steps 1–5 of `references/reasoning-rules.md` using the Manifest plus
the FRs/NFRs/deliverables snapshot as input. Produce an internal draft of:
layers, components, cluster assignments, primary data flow chain,
cross-cutting concerns. Do **not** emit this draft anywhere — it is
internal reasoning only.

### (1b) Write the textual description

150+ words, data-flow narrative per `references/diagram-spec.md` → Part E —
Architecture Description Rules. Apply paragraph breaks per
`sow-shared/references/style-guide.md` → "Paragraph breaks in long-form
narrative". This text is the **single source of truth** for the technology
stack table and the diagram spec: every GCP service mentioned here must
later appear in the table and in the diagram; every data-flow sentence
here must later become an edge in the diagram.

Apply the Architecture Description Self-Test in
`references/diagram-spec.md` → Part E before closing this sub-step.

### (1c) Write the Technology Stack table

One row per GCP service mentioned in (1b) — no more, no less. Apply
`references/tech-stack-table-rules.md` for row construction, description
rules, and the three-way invariant. Apply
`references/diagram-spec.md` → Part B (Node Granularity Rules) for what to
exclude from this table (IAM, built-in encryption, non-GCP systems).

### (1d) Derive the diagram spec from (1b) — do not use a mental model

Re-read the description you wrote in (1b) literally. Build the spec by
extracting from that text:

- **Nodes.** One node per proper noun in (1b) that is a system, GCP service,
  or entry point. For each node, apply `references/diagram-spec.md` →
  Part D (Node Labeling Rules) for `service` and `label`, and
  `references/diagram-spec.md` → Part A (Cluster Model) for `parent_cluster`
  and `sub_cluster`.
- **Edges.** Apply `references/diagram-spec.md` → Part C (Edge Rules) —
  both Edge Derivation and Edge Hygiene. Key constraints: one edge per
  data-flow sentence; honor the hops (1b) names AND the hops (1b) omits;
  labels match protocols named in (1b).
- **Direction.** Per `references/diagram-spec.md` → Part F (Direction &
  Layout).

Also populate the `architecture_integrations` list (one row per external
system named in (1b)) and the `architecture_components` list (functional
roles, not products) directly from the same source. Keep them consistent
with the nodes you derived for the diagram.

### (1e) Render the diagram

Invoke `generate_architecture_diagram` with the spec from (1d) plus the
description from (1b) and the technology stack from (1c) as arguments. The
tool runs the structural audit internally before rendering. Apply
`references/audit-rules.md` for the silent revision protocol on
`ToolError`, the 3-retry budget, and the post-budget fallback (continue
with textual sections only; never silently skip).

### Step 1.5 — Reference Compliance (silent, mandatory before returning)

Verify the four emitted artifacts (description, table, components,
integrations) against the loaded references before handing control back to
the orchestrator. Rewrite any non-compliant artifact in place.

Self-test checklist (all items mandatory):

1. Does the description's paragraph structure follow
   `sow-shared/references/style-guide.md` → "Paragraph breaks in long-form
   narrative"?
2. Does every GCP service named in the description appear in the table AND
   as a diagram node? (Three-way invariant —
   `references/tech-stack-table-rules.md`.)
3. Does every edge in the spec correspond to a data-flow sentence in the
   description? (`references/diagram-spec.md` → Part C — Edge Derivation.)
4. Is IAM absent from the diagram nodes and the table?
   (`references/diagram-spec.md` → Part B;
   `references/tech-stack-table-rules.md` → What does NOT go in the table.)
5. Does the minimum component checklist
   (`references/diagram-spec.md` → Part G) pass for this project class
   (always-required + the conditionally-required sets that apply)?
6. Are node labels functional and project-specific?
   (`references/diagram-spec.md` → Part D — Node Labeling Rules.)

If patching an existing architecture, add:

7. Are untouched nodes, edges, table rows, and integration rows byte-for-byte
   identical to the previous snapshot?
   (`sow-shared/references/id-stability-rules.md` → Patch contract.)

---

## What this skill does NOT do

- It does not produce Partner Overview, Customer Overview, or Executive
  Summary. Those fields belong to `sow-narrative`, which the orchestrator
  loads in a later Phase Step.
- It does not present the architecture review to the user. The orchestrator
  reads the produced fields and renders the review in the conversation
  language after this skill returns.
- It does not call `confirm_phase_completion`. The orchestrator owns phase
  gating.
- It does not call `stage_sow`. The orchestrator commits the artifacts to
  state after this skill returns.
