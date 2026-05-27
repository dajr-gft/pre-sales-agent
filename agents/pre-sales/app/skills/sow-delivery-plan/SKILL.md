---
name: sow-delivery-plan
description: >
  Produces the delivery-planning fields of `sow_data` — `activity_phases`,
  `deliverables`, `success_criteria`, `timeline`, `partner_roles`,
  `customer_roles`, `objectives`, `project_start_date`, `project_end_date` —
  as a single coherent plan. The five sections are generated TOGETHER
  because their cross-validation (Activities ↔ Deliverables ↔ Success
  Criteria ↔ Timeline ↔ Roles) cannot be evaluated piecewise. Invoked
  by the root SOW orchestrator agent during Phase 2 Step B, AFTER
  `sow-requirements` populates the FR/NFR snapshot the plan must cover.
metadata:
  pattern: tight-cluster + cross-validation
  produces: activity_phases, deliverables, success_criteria, timeline, partner_roles, customer_roles, objectives, project_start_date, project_end_date
  inputs: project documents (via load_artifacts), upstream bundles (requirements)
  references-skill: sow-shared
---

# SOW Delivery Plan

The delivery cluster — Activities, Deliverables (workstreams), Success
Criteria, Timeline, Roles, Objectives — produced in one turn because every
cross-validation between them needs both sides loaded. Splitting effort /
team / timeline into separate skills is what generates the
`timeline_vs_deliverables` and `activities_vs_deliverables` findings.

References listed below are binding — they override any paraphrase here.
Depth, structure, minimums, and required wording follow the references;
"brief" and "concise" apply to orchestration messages only.

## Load before drafting (mandatory)

via `load_skill_resource`:

- `sow-shared` / `references/style-guide.md` — quality contract.
- `sow-shared` / `references/scope-examples-delivery.md` — quality floor for Activities + Deliverables.
- `sow-shared` / `references/language-rules.md` — language hygiene.
- `sow-delivery-plan` / `references/workstream-structure.md` — Activities + Deliverables + Success Criteria contract.
- `sow-delivery-plan` / `references/timeline-rules.md` — Timeline contract + invariant.
- `sow-delivery-plan` / `references/roles-rules.md` — Partner (PM + Architect mandatory) + Customer (Sponsor + SME mandatory).
- `sow-delivery-plan` / `references/effort-heuristics.md` — engagement-size calibration.

When patching: also `sow-shared` / `references/id-stability-rules.md`. Workstream IDs (`WS-NN`), phase order, and role order are frozen once shown to the user.

## Inputs

- `manifest.extracted_items` for `[Timeline, Briefing, Constraints]` + resolved `manifest.gaps`.
- Current `sow_data` snapshot with `functional_requirements` + `non_functional_requirements` populated by `sow-requirements`.

If `state['app:sow:intake_summary']` is populated (Path A — guided intake), treat it as upstream project context equivalent to the project documents. Use it as the factual basis for Activities, Deliverables, Timeline, and Roles. Honor the marker contract on each field:

- **Real value** → use as factual context.
- **`'(inferred)'`** → propose a safe consulting default per `references/effort-heuristics.md` and the relevant section reference (typical engagement-shape roles for `partner_team` / `customer_team`, typical engagement-shape phases for inferred scope). Mark the produced rows / roles with `(inferred)` per `sow-shared` / `references/language-rules.md`.
- **`'[TO BE DEFINED]'`** → for `timeline` specifically, keep `[TO BE DEFINED]` in the timeframe cells rather than inventing dates; the deliverables and activities can still be planned, only the calendar is open. For `operational_constraints` marked open, file the gap into the corresponding assumption / role responsibility rather than dropping the workstream. Never invent a date or a constraint value.

`inferred_items` and `open_items` are the explicit roll-ups; iterate them when deciding which deliverables / roles need the default-fill versus placeholder-keep behavior.

> **Coverage scope.** Per-item manifest coverage (walking `extracted_items` exhaustively) is the validation critic's `coverage` skill responsibility, not this skill's. Do not duplicate that walk here; produce content grounded in the inputs above and let the critic flag uncovered items in a later round.

## Generate (one turn)

1. **Classify engagement shape.** Pick the closest row in `effort-heuristics.md` → "Engagement-shape heuristics". Internal calibration only — the engagement-type label does NOT appear in the SOW.
2. **Activities** (`activity_phases`). Apply `workstream-structure.md` → "Activities". One entry per phase; tasks pass the "could this appear unchanged in a different project?" self-test.
3. **Deliverables** (`deliverables`) as workstreams. Apply `workstream-structure.md` → "Section layout". Target ≥ 10 for 10-14 week projects. Include intermediate artifacts (test plan, data quality report, runbook, KT docs).
4. **Success Criteria** (`success_criteria`). Apply `workstream-structure.md` → "Success Criteria". Target ≥ 5, each referencing specific deliverables or FR ranges. Stop the bar at handover — no sustained-production promises.
5. **Timeline** (`timeline`). Apply `timeline-rules.md`. Phase rows MUST equal `activity_phases.name` (count, names, order). Pick one notation (weeks OR dates) across all rows. `outcomes` reference specific workstreams/deliverables by name. If Manifest captured dates, populate `project_start_date` / `project_end_date`.
6. **Roles** (`partner_roles`, `customer_roles`). Apply `roles-rules.md` + `effort-heuristics.md` → "Roles → Engagement-size coherence". 2-3 sentences of concrete responsibilities per role.
7. **Objectives** (`objectives`). 3-5 single-sentence project-specific goals pulled from Manifest `Briefing`.
8. **Cross-validate.** Apply `workstream-structure.md` → "Cross-section coherence" + `timeline-rules.md` → "Cross-section invariant" + `effort-heuristics.md` → "Coherence heuristics". Fix mismatches in place.

## Before returning (workflow gate)

- Phase order in `timeline` = phase order in `activity_phases` (exact).
- Every `WS-NN` appears in `deliverables` AND in at least one `timeline.outcomes` row.
- Partner list includes PM + Solution Architect; Customer list includes Executive Sponsor + SME.
- Duration matches engagement-type heuristic (±2 weeks) or rationale is in inferred markers.
- No hours, rates, or rate cards anywhere.
- When patching: existing `WS-NN`, phase names, and role titles preserved byte-for-byte per `id-stability-rules.md`.

## Out of scope

- Does not produce assumptions / OOS / CR policy / handover / risks (→ `sow-scope-boundaries`).
- Does not produce architecture or narrative fields.
- Does not call `stage_sow` or `confirm_phase_completion`.
