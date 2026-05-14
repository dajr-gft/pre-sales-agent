---
name: sow-scope-boundaries
description: >
  Produces the contractual-surface fields of `sow_data` — `assumptions`,
  `out_of_scope`, `change_request_policy_text`, `handover_disclaimers`,
  and `risks` — as one tightly-coupled cluster. The five lists are
  produced together because every assumption needs an OOS
  counter-anchor, every handover statement needs a matching NFR /
  Reliability phrasing, and the AI/ML non-determinism disclosure must
  reconcile across assumptions, OOS, and handover. Loaded by
  `sow-orchestrator` during Phase 2 Step C, AFTER delivery plan
  (Step B) so deliverables are available as counter-anchors.
metadata:
  pattern: contractual-cluster + counter-anchor validation
  produces: assumptions, out_of_scope, change_request_policy_text, handover_disclaimers, risks
  inputs: extraction_manifest, sow_data snapshot (FR/NFR + delivery plan)
  upstream-skill: sow-orchestrator
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
- `sow-shared` / `references/scope-examples/scope-contractual.md` — quality floor for OOS + Assumptions + CR Policy.
- `sow-shared` / `references/scope-examples/risks.md` — quality floor for Risks.
- `sow-shared` / `references/language-rules.md` — language hygiene.
- `sow-scope-boundaries` / `references/oos-categories.md` — 17-category contract + mandatory Category 17.
- `sow-scope-boundaries` / `references/assumption-patterns.md` — consequence-clause pattern + 15 categories.
- `sow-scope-boundaries` / `references/cr-policy-template.md` — Change Request Policy structure.
- `sow-scope-boundaries` / `references/handover-rules.md` — handover disclaimers (operational, availability, AI/ML, hypercare).
- `sow-scope-boundaries` / `references/risks-rules.md` — Risks rules.

When patching: also `sow-shared` / `references/id-stability-rules.md`. OOS / assumption / handover order are frozen once shown to the user.

## Inputs

- `manifest.extracted_items` for `[Constraints, Decisions, Briefing]` + `manifest.gaps.pending_decisions`.
- Current `sow_data` snapshot with FRs, NFRs, deliverables, and activity_phases already populated. Deliverables supply OOS counter-anchors and assumption phase-deadline references.

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

   Fix in place. If a Reliability NFR upstream carries a forbidden uptime/SLA percentage, STOP and signal the orchestrator to reload `sow-requirements` and correct that NFR before continuing — never silently rewrite an upstream NFR from this skill (see Out of scope).

## Before returning (workflow gate)

- OOS count ≥ 20 with all 17 categories considered; Category 17 present with an approved phrasing.
- Assumption count ≥ 15; every customer-dependent item carries the consequence clause.
- CR policy contains all four required points; no rates / hours / template fields.
- Operational-ownership + production-availability + AI-ML-when-applicable + hypercare statements all present.
- Scope-boundary verbs used (no "may", "intends to", "is expected to").
- When patching: existing OOS, Assumption, and Handover orders preserved byte-for-byte per `id-stability-rules.md`.

## Out of scope

- Does not rewrite FRs/NFRs. A Reliability NFR with a forbidden uptime/SLA percentage is an upstream defect — stop and instruct the orchestrator to reload `sow-requirements` to correct that field. Never silently patch an upstream NFR from this skill.
- Does not produce architecture / narrative / delivery-plan fields.
- Does not call `stage_sow` or `confirm_phase_completion`.
