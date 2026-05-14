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

**Scope of this skill.** The contractual surface — what the partner is NOT
promising. The five lists are produced together because every assumption
needs an OOS counter-anchor, every handover statement mirrors a Reliability
NFR phrasing, and the cross-references between them are how the validation
critic catches `missing_consequence_clause`, `missing_handover_boundary`,
and `missing_ai_nondeterminism_disclosure` findings.

## Reference authority and depth rules

The loaded reference files are the **binding quality contract** for the
contractual surface — not optional examples, loose inspiration, or style
suggestions.

Priority order for generated/patched content quality:

1. `sow-shared/references/style-guide.md` — binding cross-cutting writing
   rules (scope-boundary language: "strictly limited to", "exclusively",
   "explicitly excluded") and the Self-sufficiency contract.
2. `sow-shared/references/id-stability-rules.md` — binding ID
   preservation when revising an existing scope (assumption order, OOS
   order, handover order).
3. `references/oos-categories.md` — binding 17-category coverage list
   plus the MANDATORY Category 17 (uptime/SLA denial).
4. `references/assumption-patterns.md` — binding consequence-clause
   pattern, 15 categories, and the counter-anchor walk.
5. `references/cr-policy-template.md` — binding Change Request Policy
   structure (what it MUST say, what it MUST NOT contain).
6. `references/handover-rules.md` — binding handover disclaimers
   (operational ownership, production-availability boundary, AI/ML
   non-determinism disclosure).
7. This `SKILL.md` — workflow orchestration only (sub-step ordering,
   cross-anchor gate, risks rules inline).

If this skill says to do X and a reference defines how X must be
written/patched, the reference controls the content. Do not simplify,
shorten, or reinterpret reference requirements unless the reference
explicitly allows it.

**Brevity scope rule:** instructions such as "brief", "concise", "direct",
or "short" apply only to conversational orchestration messages,
confirmations, and error handling. They do NOT apply to SOW scope-boundary
content. For assumptions, OOS items, CR policy text, handover disclaimers,
and risks, follow the depth, structure, minimums, required wording, and
quality rules from the loaded references.

---

## Workflow — generate the five lists in one turn

**Pre-step — Load and apply references (mandatory gate before any drafting):**

- `load_skill_resource(skill_name="sow-shared", file_path="references/style-guide.md")`
  — **Binding quality contract.**
- `load_skill_resource(skill_name="sow-shared", file_path="references/scope-examples.md")`
  — **Quality floor.** Match or exceed the depth shown for OOS and
  Assumptions sections.
- `load_skill_resource(skill_name="sow-shared", file_path="references/language-rules.md")`
  — **Binding language hygiene.**
- `load_skill_resource(skill_name="sow-scope-boundaries", file_path="references/oos-categories.md")`
  — **Binding 17-category OOS contract.**
- `load_skill_resource(skill_name="sow-scope-boundaries", file_path="references/assumption-patterns.md")`
  — **Binding consequence-clause pattern + 15 categories.**
- `load_skill_resource(skill_name="sow-scope-boundaries", file_path="references/cr-policy-template.md")`
  — **Binding CR policy structure.**
- `load_skill_resource(skill_name="sow-scope-boundaries", file_path="references/handover-rules.md")`
  — **Binding handover disclaimers.**

If you are patching an existing scope (not generating from scratch), also
load
`load_skill_resource(skill_name="sow-shared", file_path="references/id-stability-rules.md")`
and treat the Patch contract there as overriding any sub-step instinct to
regenerate the lists. OOS order, assumption order, and handover order are
frozen once the user has seen them.

Use as input:

- The Extraction Manifest — `manifest.extracted_items` for categories
  `Constraints`, `Decisions`, `Briefing`, and `manifest.gaps.pending_decisions`.
- The current `sow_data` snapshot with `functional_requirements`,
  `non_functional_requirements`, `deliverables`, and `activity_phases`
  already populated. The deliverables list is what supplies OOS
  counter-anchors and assumption phase-deadline references.

### (1a) Generate Out-of-Scope (`out_of_scope`)

Walk the 17 categories in `references/oos-categories.md`. For each
applicable category, write one or more OOS items naming specific
technologies, environments, integrations, or capabilities that are
excluded. Use the disambiguation rule when an OOS item could appear to
contradict an in-scope FR.

**Category 17 is mandatory** — pick one of the two approved phrasings in
`references/oos-categories.md` → "Approved phrasings for Category 17".

Target: 20-30 items minimum. Most engagements produce 25-35.

### (1b) Generate Assumptions (`assumptions`)

Walk the 15 categories in `references/assumption-patterns.md`. For each
applicable category, produce one or more assumptions following the
consequence-clause pattern: `"[Customer] must [obligation] [by when].
[Consequence if not met]."`

When the Manifest contains AI/ML components, include Category 12 (GenAI/ML
acknowledgment) — the canonical phrasing lives in
`references/handover-rules.md` → "AI / ML non-determinism disclosure"
because that's where the deeper disclosure lives. The assumption is a
shorter mirror of the handover statement.

Target: 15-25 assumptions minimum. Most engagements produce 20-30.

### (1c) Generate Change Request Policy (`change_request_policy_text`)

Apply `references/cr-policy-template.md`. Single string field
(multi-paragraph). The text MUST contain the four required points (no
out-of-scope without signed CR, verbal agreements not binding, partner
may pause non-CR'd work, all CRs follow the same process) and MUST NOT
contain hours / rates / specific timeline numbers or the 7 template
fields.

### (1d) Generate Handover Disclaimers (`handover_disclaimers`)

Apply `references/handover-rules.md`. The list MUST contain:

- The operational-ownership statement.
- The production-availability boundary statement.
- The AI/ML non-determinism disclosure IF the architecture / FR set
  includes any AI/ML component.
- The hypercare statement (either the inclusion form with the window or
  the explicit exclusion form).

### (1e) Generate Risks (`risks`)

Risks are conditional — the customer may opt to omit them during review;
when present, the section is 3-5 project-specific items with mitigations.

Each risk is an object with two fields:

```
{"description": "<risk statement>", "mitigation": "<mitigation strategy>"}
```

Rules:

- **3-5 risks**, project-specific. Generic "delivery risk" is rejected.
- **Each risk references specific systems, technologies, or
  stakeholders** named in the architecture or the FRs / NFRs.
- **Each risk has a concrete mitigation** the partner team can execute —
  not a passive "we will monitor" phrasing.
- **No risks that promise customer behavior** — those are assumptions.
- **No "risk: the project might fail"** — that is not a risk, it is a
  meta-statement. Risks name specific failure modes.

Inferred risks are marked with the conversation-language equivalent of
`(inferred)` per `sow-shared/references/language-rules.md`.

### Cross-anchor gate (mandatory before exit)

Walk the five lists pair-wise once. Verify:

1. **Assumption ↔ Consequence**: every customer-dependent assumption has
   a consequence clause (`references/assumption-patterns.md` →
   "The consequence-clause pattern").
2. **Assumption ↔ OOS counter-anchor**: every OOS Category 10 (excluded
   post-delivery) or Category 17 (uptime/SLA denial) item has a matching
   handover assumption that transfers ownership at KT.
3. **OOS ↔ Category 17 present**: Category 17 is in the OOS list with
   one of the approved phrasings.
4. **Handover ↔ Reliability NFR**: the production-availability handover
   statement is present AND the FR/NFR list (already populated upstream)
   uses the architectural-pattern phrasing for Reliability. If the
   Reliability NFR carries a forbidden percentage phrasing, this is a
   defect upstream — emit a finding back via the orchestrator instead of
   silently rewriting the NFR.
5. **Handover ↔ AI/ML disclosure**: if the architecture / FRs include
   any AI/ML component, the non-determinism disclosure is present in
   `handover_disclaimers` AND a matching assumption is in
   `assumptions` (Category 12).
6. **CR Policy ↔ no rates**: `change_request_policy_text` contains
   neither hours nor rates nor specific timeline numbers.

If any check fails, **fix it in place before returning**.

### Step 1.5 — Reference Compliance (silent, mandatory before returning)

Self-test checklist (all items mandatory):

1. Is OOS count ≥ 20, with all 17 categories considered (and the
   omissions justified)?
2. Is Category 17 (uptime/SLA denial) present with an approved phrasing?
3. Is assumption count ≥ 15, with every customer-dependent item carrying
   a consequence clause?
4. Does the CR policy state all four required points and exclude rates /
   hours / template fields?
5. Are operational-ownership and production-availability handover
   statements present?
6. If AI/ML is in scope, is the non-determinism disclosure present in
   `handover_disclaimers` AND mirrored as Category 12 in `assumptions`?
7. Are inferred items marked with the conversation-language equivalent
   of `(inferred)`?
8. Are scope-boundary phrasings used ("strictly limited to",
   "exclusively", "explicitly excluded") where applicable, with no
   softening verbs ("may", "intends to", "is expected to")?

If patching an existing scope, add:

9. Are existing OOS, Assumption, and Handover orders preserved
   byte-for-byte? Removals leave gaps in numbering; additions append
   after the last existing item.
   (`sow-shared/references/id-stability-rules.md` → Patch contract.)

---

## What this skill does NOT do

- It does not produce `functional_requirements`,
  `non_functional_requirements`, `activity_phases`, `deliverables`,
  `success_criteria`, `timeline`, `partner_roles`, `customer_roles`,
  or any architecture / narrative field. Those belong to other section
  skills.
- It does not call `stage_sow`, present the user-facing review, or call
  `confirm_phase_completion`. The orchestrator owns all three.
- It does not rewrite the FR/NFR list. If a Reliability NFR carries a
  forbidden uptime/SLA phrasing, that is a defect upstream — emit a
  finding (handled by `sow-revision`) instead of patching the NFR here.
