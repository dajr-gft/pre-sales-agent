---
name: sow-delivery-plan
description: >
  Produces the delivery-planning fields of `sow_data` — `activity_phases`,
  `deliverables`, `success_criteria`, `timeline`, `partner_roles`,
  `customer_roles`, `objectives`, `project_start_date`, `project_end_date` —
  as a single coherent plan. The five sections are generated TOGETHER
  because their cross-validation (Activities ↔ Deliverables ↔ Success
  Criteria ↔ Timeline ↔ Roles) cannot be evaluated piecewise. Loaded by
  `sow-orchestrator` during Phase 2 Step B, AFTER `sow-requirements`
  populates the FR/NFR snapshot the plan must cover.
metadata:
  pattern: tight-cluster + cross-validation
  produces: activity_phases, deliverables, success_criteria, timeline, partner_roles, customer_roles, objectives, project_start_date, project_end_date
  inputs: extraction_manifest, sow_data snapshot (functional_requirements, non_functional_requirements)
  upstream-skill: sow-orchestrator
  references-skill: sow-shared
---

# SOW Delivery Plan

**Scope of this skill.** The delivery plan as a tightly-coupled cluster:
Activities, Deliverables (as workstreams), Success Criteria, Timeline,
and Roles (Partner + Customer). The five sections are produced in one
turn because every cross-validation between them needs both sides loaded
in context.

The decomposition plan that introduced this skill explicitly warns
against splitting effort / team / timeline into separate skills — that
split is precisely what generates the `timeline_vs_deliverables` and
`activities_vs_deliverables` findings the validation critic catches.

## Reference authority and depth rules

The loaded reference files are the **binding quality contract** for the
delivery plan — not optional examples, loose inspiration, or style
suggestions.

Priority order for generated/patched content quality:

1. `sow-shared/references/style-guide.md` — binding cross-cutting writing
   rules and the Self-sufficiency contract.
2. `sow-shared/references/id-stability-rules.md` — binding ID
   preservation when revising an existing delivery plan (workstream IDs
   `WS-NN`, phase order, role order).
3. `references/workstream-structure.md` — binding rules for Activities,
   Deliverables (workstreams), and Success Criteria.
4. `references/timeline-rules.md` — binding table shape and the
   Timeline ↔ Activities ↔ Deliverables invariant.
5. `references/roles-rules.md` — binding role-list rules (Partner must
   include PM + Architect; Customer must include Sponsor + SME; 2-3 line
   responsibilities; no Google roles; no hours/rates).
6. `references/effort-heuristics.md` — binding calibration for
   engagement-size coherence across the five sections.
7. This `SKILL.md` — workflow orchestration only (sub-step ordering,
   cross-validation gate).

If this skill says to do X and a reference defines how X must be
written/patched, the reference controls the content. Do not simplify,
shorten, or reinterpret reference requirements unless the reference
explicitly allows it.

**Brevity scope rule:** instructions such as "brief", "concise", "direct",
or "short" apply only to conversational orchestration messages,
confirmations, and error handling. They do NOT apply to SOW delivery-plan
content. For activities, deliverables, success criteria, timeline rows,
and role descriptions, follow the depth, structure, minimums, and quality
rules from the loaded references.

---

## Workflow — generate the five sections in one turn

The five sections are tightly coupled. Splitting them produces
contradictions even when each is internally correct. Produce all five in
one turn, then run the cross-validation gate before returning.

**Pre-step — Load and apply references (mandatory gate before any drafting):**

- `load_skill_resource(skill_name="sow-shared", file_path="references/style-guide.md")`
  — **Binding quality contract.**
- `load_skill_resource(skill_name="sow-shared", file_path="references/scope-examples.md")`
  — **Quality floor.** Match or exceed the depth shown for delivery sections.
- `load_skill_resource(skill_name="sow-shared", file_path="references/language-rules.md")`
  — **Binding language hygiene.**
- `load_skill_resource(skill_name="sow-delivery-plan", file_path="references/workstream-structure.md")`
  — **Binding Activities + Deliverables + Success Criteria contract.**
- `load_skill_resource(skill_name="sow-delivery-plan", file_path="references/timeline-rules.md")`
  — **Binding Timeline contract.**
- `load_skill_resource(skill_name="sow-delivery-plan", file_path="references/roles-rules.md")`
  — **Binding Roles contract.**
- `load_skill_resource(skill_name="sow-delivery-plan", file_path="references/effort-heuristics.md")`
  — **Binding engagement-size calibration.**

If you are patching an existing delivery plan (not generating from
scratch), also load
`load_skill_resource(skill_name="sow-shared", file_path="references/id-stability-rules.md")`
and treat the Patch contract there as overriding any sub-step instinct to
regenerate the plan. Workstream labels `WS-NN`, phase order, and role
order are frozen once the user has seen them.

Use as input:

- The Extraction Manifest — `manifest.extracted_items` for categories
  `Timeline`, `Briefing`, and `Constraints` — plus resolved `manifest.gaps`.
- The current `sow_data` snapshot with `functional_requirements` and
  `non_functional_requirements` already populated by `sow-requirements`.

### (1a) Classify the engagement shape

Pick the closest engagement type from
`references/effort-heuristics.md` → "Engagement-shape heuristics" based on
the Manifest (Briefing + Integrations + NFRs + Timeline). Note the
heuristic ranges for phases, workstreams, and deliverables — these
calibrate the size of the four sections below. The heuristic is **internal
calibration only**; the engagement type label does NOT appear in the SOW
text.

### (1b) Generate Activities (`activity_phases`)

Apply `references/workstream-structure.md` → "Activities — the operations
behind workstreams". Each `activity_phases` entry covers one phase
(`Phase N: <label>`), one short description, and a `tasks` list.

For each task, run the self-test from the same reference: *"Could this
exact task description appear unchanged in a different project?"* If yes,
rewrite with project-specific detail.

### (1c) Generate Deliverables (`deliverables`) as Workstreams

Apply `references/workstream-structure.md` → "Section layout
(deliverables-first, workstream-organized)". Each deliverable row uses
the workstream label in its `activity` column (`WS01: <label>`,
`WS02: <label>`, ...). One workstream produces one or more deliverable
rows.

Target: ≥ 10 deliverables for a 10-14 week project. Floor, not cap.
Include intermediate artifacts (test plan, data quality report, runbook,
KT documentation) — their absence is the most common gap.

### (1d) Generate Success Criteria (`success_criteria`)

Apply `references/workstream-structure.md` → "Success Criteria —
verifiable acceptance bar". Target: ≥ 5 unique criteria. Each criterion
references specific deliverables or FR ranges where possible.

Stop the acceptance bar at handover. Criteria that promise sustained
production reliability or post-handover behavior are defects (this is the
contractual mirror of the Reliability anti-uptime rule in `sow-requirements`).

### (1e) Generate Timeline (`timeline`)

Apply `references/timeline-rules.md`. The set of `Phase` rows MUST equal
the set of `activity_phases.name` entries — same count, same names, same
order. Pick one notation (week ranges or date ranges) and use it across all
rows.

Each `outcomes` value references specific workstreams and deliverables by
name. Generic phrasings are rejected.

If the Manifest captured concrete dates (`Timeline` category), use them
for `project_start_date`, `project_end_date`, and the date-range
timeframes. Otherwise emit week ranges and leave the date fields
unset (the orchestrator will surface `[TO BE DEFINED]` markers if the
user expects dates).

### (1f) Generate Roles (`partner_roles`, `customer_roles`)

Apply `references/roles-rules.md`. Partner list MUST include PM + Solution
Architect; Customer list MUST include Executive Sponsor + SME. Add
engineering specializations to the partner list based on the FR/NFR
coverage (per `references/effort-heuristics.md` → "Roles → Engagement-size
coherence").

Each role description is 2-3 sentences of concrete responsibilities — not
the role title rephrased.

### (1g) Generate Objectives (`objectives`)

Short bulleted list (typically 3-5 items) that captures the high-level
goals of the engagement. Each objective is one sentence, project-specific.
Pull from `manifest.extracted_items` category `Briefing` for the business
goals.

### Cross-validation gate (mandatory before exit)

Apply `references/workstream-structure.md` → "Cross-section coherence
(Activities ↔ Deliverables ↔ Success Criteria)" plus
`references/timeline-rules.md` → "Cross-section invariant" plus
`references/effort-heuristics.md` → "Coherence heuristics — keep the four
sections aligned".

If any check fails, **fix it in place before returning**. Returning a
contradiction to the orchestrator means the validation critic catches it
later — wasted round-trip.

### Step 1.5 — Reference Compliance (silent, mandatory before returning)

Self-test checklist (all items mandatory):

1. Does the phase order in `timeline` exactly match `activity_phases`?
2. Does every workstream `WS-NN` appear in both `deliverables` (as the
   `activity` column value) and at least one `timeline.outcomes` row?
3. Does every Success Criterion reference a verifiable artifact
   (deliverable name, FR range, deployment event, KT event) — never a
   generic "project success"?
4. Does `partner_roles` include PM + Solution Architect?
5. Does `customer_roles` include Executive Sponsor + SME (or equivalents)?
6. Does the engagement duration match the engagement-type heuristic
   (±2 weeks)?
7. Are inferred items marked with the conversation-language equivalent of
   `(inferred)`?
8. Are hours, rates, or rate cards absent from every section?

If patching an existing delivery plan, add:

9. Are existing workstream IDs (`WS-NN`), phase names, and role titles
   preserved byte-for-byte? Removals leave gaps in `WS-NN` sequence;
   additions append after the last existing ID.
   (`sow-shared/references/id-stability-rules.md` → Patch contract.)

---

## What this skill does NOT do

- It does not produce `assumptions`, `out_of_scope`,
  `change_request_policy_text`, or `handover_disclaimers` — those belong
  to `sow-scope-boundaries`, loaded in a different Phase Step. It does
  not produce `risks` — also `sow-scope-boundaries`.
- It does not produce `architecture_*` fields — that is `sow-architecture`.
- It does not produce the FR or NFR lists — that is `sow-requirements`,
  loaded BEFORE this skill.
- It does not call `stage_sow`, present the user-facing review, or call
  `confirm_phase_completion`. The orchestrator owns all three.
