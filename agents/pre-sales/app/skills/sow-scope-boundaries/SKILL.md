---
name: sow-scope-boundaries
description: >
  Produces the contractual-surface fields of `sow_data` — `assumptions`,
  `out_of_scope`, `change_request_policy_text`, `handover_disclaimers`,
  and `risks` — as one tightly-coupled cluster. The five lists are
  produced together because every assumption needs an OOS
  counter-anchor, every handover statement needs a matching NFR /
  Reliability phrasing, and the AI/ML non-determinism disclosure must
  reconcile across assumptions, OOS, and handover. Invoked by the root
  SOW orchestrator agent during Phase 2 Step C, AFTER delivery plan
  (Step B) so deliverables are available as counter-anchors.
metadata:
  pattern: contractual-cluster + counter-anchor validation
  produces: assumptions, out_of_scope, change_request_policy_text, handover_disclaimers, risks
  inputs: project documents (via load_artifacts), upstream bundles (requirements + delivery plan)
  references-skill: sow-shared
---

# SOW Scope Boundaries

The contractual surface — what the partner is NOT promising. The five
lists are produced together because every assumption needs an OOS
counter-anchor, every handover statement mirrors a Reliability NFR
phrasing, and AI/ML non-determinism must reconcile across all three lists.

References below are binding — they override any paraphrase here. Use
scope-boundary language ("strictly limited to", "exclusively", "explicitly
excluded") with no softening verbs. "Brief" and "concise" apply to
orchestration messages only, never to content.

## Load before drafting (mandatory)

via `load_skill_resource`:

- `sow-shared` / `references/style-guide.md` — quality contract.
- `sow-shared` / `references/scope-examples-scope-contractual.md` — quality floor for OOS + Assumptions + CR Policy.
- `sow-shared` / `references/scope-examples-risks.md` — quality floor for Risks.
- `sow-shared` / `references/language-rules.md` — language hygiene.
- `sow-scope-boundaries` / `references/oos-categories.md` — 17-category contract + mandatory Category 17.
- `sow-scope-boundaries` / `references/assumption-patterns.md` — consequence-clause pattern + 15 categories.
- `sow-scope-boundaries` / `references/cr-policy-template.md` — Change Request Policy structure.
- `sow-scope-boundaries` / `references/handover-rules.md` — handover disclaimers (operational, availability, AI/ML, hypercare).
- `sow-scope-boundaries` / `references/risks-rules.md` — Risks rules.

When patching: also `sow-shared` / `references/id-stability-rules.md`. OOS / assumption / handover order are frozen once shown to the user.

## Inputs

Two required inputs:

1. **Upstream project context** — substantive content the scope-boundary lists derive from:
   - **Path A (guided intake):** the persisted intake_summary at `state['app:sow:intake_summary']`. Honor the marker contract on each field:
     - **Real value** → use as factual context.
     - **`'(inferred)'`** (e.g. `out_of_scope`, `regulatory_constraints`) → expand with consulting-grade defaults per `references/oos-categories.md` / `references/assumption-patterns.md`. Mark inferred items with `(inferred)` per `sow-shared` / `references/language-rules.md` so the Content Review surfaces them.
     - **`'[TO BE DEFINED]'`** (e.g. `operational_constraints`, `timeline`) → file an assumption that captures the open decision verbatim with `[TO BE DEFINED]` in the consequence clause; do NOT invent the constraint or guess a value. The Content Review is where the user resolves these.
     - `inferred_items` and `open_items` are the explicit roll-ups; iterate them when deciding which OOS / assumption / risk entries need the default-fill versus placeholder-keep behavior.
   - **Path B (documental):** source documents loaded through artifacts for the current generation step. Read them directly as the factual basis for assumptions, OOS, and risks; gaps are whatever the documents fail to state.
2. **Upstream bundle** — current `sow_data` snapshot with FRs, NFRs, deliverables, and activity_phases already populated by the prior section skills. Deliverables supply OOS counter-anchors and assumption phase-deadline references.

> **Completeness scope.** Exhaustive source-by-source completeness validation belongs to the validation critic, not this skill. Generate a complete section bundle from the upstream project context; the quality loop validates completeness later.

## Generate (one turn)

1. **OOS** (`out_of_scope`). Walk the 17 categories in `oos-categories.md`; skip only when genuinely inapplicable. **Category 17 is mandatory** — pick one of the two approved phrasings. Target 20-30+ items.
2. **Assumptions** (`assumptions`). Walk the 15 categories in `assumption-patterns.md`. Every customer-dependent assumption follows the consequence-clause pattern: `[Customer] must [obligation] [by when]. [Consequence if not met].` When AI/ML is in scope, include Category 12 (mirror of the handover disclosure). Target 15-25+.
3. **CR Policy** (`change_request_policy_text`). Apply `cr-policy-template.md`. Single multi-paragraph string. MUST state: (a) no out-of-scope work without signed CR, (b) verbal agreements not binding, (c) partner may pause non-CR'd work, (d) all CRs follow the same process. MUST NOT contain hours, rates, or the 7 CR template fields.
4. **Handover** (`handover_disclaimers`). Apply `handover-rules.md`. MUST contain: operational-ownership statement; production-availability boundary statement; AI/ML non-determinism disclosure (IF any AI/ML component); hypercare statement (inclusion-with-window OR explicit exclusion).
5. **Risks** (`risks`). Apply `risks-rules.md`.
6. **Cross-anchor walk.** Verify:
   - Every customer-dependent assumption has a consequence clause.
   - OOS Category 10/17 items each have a matching handover assumption.
   - Category 17 phrasing is one of the two approved variants.
   - Production-availability handover statement is present AND the upstream Reliability NFR uses the architectural-pattern phrasing.
   - If AI/ML is in scope, non-determinism disclosure is in `handover_disclaimers` AND mirrored as Category 12 in `assumptions`.

   Fix in place. If a Reliability NFR upstream carries a forbidden uptime/SLA percentage, do NOT silently rewrite it from this skill (see Out of scope). Emit the bundle as-is, leaving the upstream NFR untouched; the `contractual_exposure` critic will flag the forbidden phrasing as `production_availability_commitment` and the `revision_agent` will patch the NFR in a later round using `sow-requirements` / `references/nfr-waf-pillars.md`.

## Before returning (workflow gate)

- OOS count ≥ 20 with all 17 categories considered; Category 17 present with an approved phrasing.
- Assumption count ≥ 15; every customer-dependent item carries the consequence clause.
- CR policy contains all four required points; no rates / hours / template fields.
- Operational-ownership + production-availability + AI-ML-when-applicable + hypercare statements all present.
- Scope-boundary verbs used (no "may", "intends to", "is expected to").
- When patching: existing OOS, Assumption, and Handover orders preserved byte-for-byte per `id-stability-rules.md`.

## Out of scope

- Does not rewrite FRs/NFRs. A Reliability NFR with a forbidden uptime/SLA percentage is an upstream defect — emit the bundle without altering the upstream NFR and rely on the validation critic + `revision_agent` to patch the NFR in a later round.
- Does not produce architecture / narrative / delivery-plan fields.
- Does not call `stage_sow` or `confirm_phase_completion`.
