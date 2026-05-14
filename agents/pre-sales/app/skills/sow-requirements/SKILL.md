---
name: sow-requirements
description: >
  Produces the requirements fields of `sow_data` —
  `functional_requirements` and `non_functional_requirements` — with
  cross-validation between the two lists so `fr_vs_nfr` contradictions and
  `subjective_nfr_target` defects are caught BEFORE the validation critic
  ever sees them. Loaded by `sow-orchestrator` during Phase 2 Step A,
  BEFORE delivery plan, scope boundaries, architecture, or narrative.
  Production-grade NFR Reliability rules (anti-uptime / anti-SLA) are
  enforced in this skill; downstream skills inherit the assumption that
  Reliability is already correctly phrased.
metadata:
  pattern: paired-generation + cross-validation
  produces: functional_requirements, non_functional_requirements
  inputs: extraction_manifest
  upstream-skill: sow-orchestrator
  references-skill: sow-shared
---

# SOW Requirements

**Scope of this skill.** Only the FR and NFR lists, generated together so
the cross-validation between them happens in the same turn. The skill
returns the two populated arrays; the orchestrator stages them and decides
the user-facing review.

## Reference authority and depth rules

The loaded reference files are the **binding quality contract** for the
FR and NFR sections — not optional examples, loose inspiration, or style
suggestions.

Priority order for generated/patched content quality:

1. `sow-shared/references/style-guide.md` — binding cross-cutting writing
   rules and the Self-sufficiency contract (Rules 1-3 applied to FRs AND
   NFRs).
2. `sow-shared/references/id-stability-rules.md` — binding ID preservation
   when revising an existing FR/NFR list.
3. `references/fr-patterns.md` — binding FR format, target, shape, inferred-
   implicit list, and anti-patterns.
4. `references/nfr-waf-pillars.md` — binding NFR format, the five WAF
   pillars, and the **Reliability consultancy scope rule** that forbids
   uptime/SLA percentages.
5. `references/anti-patterns.md` — binding rejection list applied as a
   self-test before emitting.
6. This `SKILL.md` — workflow orchestration only (sub-step ordering, when
   to load which reference, cross-validation gate).

If this skill says to do X and a reference defines how X must be
written/patched, the reference controls the content. Do not simplify,
shorten, or reinterpret reference requirements unless the reference
explicitly allows it.

**Brevity scope rule:** instructions such as "brief", "concise", "direct",
or "short" apply only to conversational orchestration messages,
confirmations, and error handling. They do NOT apply to SOW requirements
content. For FRs and NFRs, follow the depth, structure, minimums, required
wording, and quality rules from the loaded references.

---

## Workflow — generate FRs and NFRs together (one turn)

The FR and NFR lists must be produced in the same turn so the
cross-validation between them is grounded in both. Splitting generation
across turns loses the joint-view needed to catch `fr_vs_nfr`
contradictions.

**Pre-step — Load and apply references (mandatory gate before any drafting):**

- `load_skill_resource(skill_name="sow-shared", file_path="references/style-guide.md")`
  — **Binding quality contract.** Self-sufficiency Rules 1-3 apply to
  every FR and every NFR.
- `load_skill_resource(skill_name="sow-shared", file_path="references/scope-examples.md")`
  — **Quality floor.** FR and NFR depth must match or exceed the calibration
  shown there.
- `load_skill_resource(skill_name="sow-shared", file_path="references/language-rules.md")`
  — **Binding language hygiene.** Inferred-item marker is mandatory.
- `load_skill_resource(skill_name="sow-requirements", file_path="references/fr-patterns.md")`
  — **Binding FR contract.**
- `load_skill_resource(skill_name="sow-requirements", file_path="references/nfr-waf-pillars.md")`
  — **Binding NFR contract.** Reliability anti-uptime is non-negotiable.
- `load_skill_resource(skill_name="sow-requirements", file_path="references/anti-patterns.md")`
  — **Binding rejection list.** Applied as a self-test before emitting.

If you are patching an existing FR/NFR list (not generating from scratch),
also load
`load_skill_resource(skill_name="sow-shared", file_path="references/id-stability-rules.md")`
and treat the Patch contract there as overriding any sub-step instinct to
regenerate the list. IDs are frozen once the user has seen them.

Use as input:

- The Extraction Manifest — `manifest.extracted_items` for categories
  `Briefing`, `Integrations`, and `NFRs` — plus resolved `manifest.gaps`.

### (1a) Map Manifest items to FRs

Walk `manifest.extracted_items` filtered to categories `[Briefing,
Integrations]`. For each item, decide whether it produces a new FR or
extends an existing one (per Self-sufficiency Rule 2 in
`sow-shared/references/style-guide.md`):

- Items that are instances of the same operation differing only by
  target/channel/system → group into ONE FR naming each instance literally.
- Items that describe functionally distinct capabilities → keep SEPARATE.

Apply `references/fr-patterns.md` → "Required FR shape" to every FR:
specific system, specific data flow, or specific behavior. Generic
capability statements are a defect.

### (1b) Infer implicit FRs

Beyond the Manifest items, infer the implicit FRs listed in
`references/fr-patterns.md` → "Inferred-implicit FRs" — authentication,
error handling at integration boundaries, audit logging, data validation,
admin monitoring, edge cases — unless the Manifest already covers them.

Mark each inferred FR with the conversation-language equivalent of
`(inferred)` per `sow-shared/references/language-rules.md` →
"Inferred-content marker".

### (1c) Generate NFRs across the five WAF pillars

Walk `manifest.extracted_items` filtered to category `NFRs`. Distribute
items across the five pillars in `references/nfr-waf-pillars.md` (Security,
Reliability, Performance, Operational Excellence, Cost Optimization).

**Reliability requires special handling.** Apply
`references/nfr-waf-pillars.md` → "Pillar 2 — Reliability" verbatim:

- The FORBIDDEN phrasings list is binding in any language.
- The REQUIRED phrasing must be used (canonical English shown there;
  reproduce the contractual meaning when the user-facing review is in
  another language).

If the Manifest contains an uptime / SLA / availability percentage from the
customer, do NOT translate it directly into an NFR. Translate the
**architectural pattern** the customer needs to achieve that target
(multi-region, failover, retry, health checks); leave the percentage out of
the NFR and into a separate Assumption (handled by `sow-scope-boundaries`).

Add at least one NFR per applicable pillar. Cover all five for a
production-grade engagement; justify any omission explicitly.

### (1d) Cross-validation gate (mandatory before exit)

Walk every FR and every NFR pair-wise once. Flag any of the following:

- **`fr_vs_nfr` contradiction** — an FR commits the solution to behavior
  that an NFR forbids, or vice versa. Apply
  `references/anti-patterns.md` → "Cross-section anti-patterns" for the
  canonical pairs and the resolution patterns.
- **`fr_restated_as_nfr`** — the same statement appears in both lists.
  Decide whether it is functional behavior (FR) or qualitative target
  (NFR) and remove the duplicate.

If you find a contradiction, **fix it in place before returning**. Returning
contradictions to the orchestrator means the validation critic catches it
later — wasted round-trip.

### Step 1.5 — Reference Compliance (silent, mandatory before returning)

Run the self-test in `references/anti-patterns.md` → "Self-test (apply
before emitting the requirements section)" — every item is mandatory. Also
verify:

1. Does every FR / NFR have a unique sequential ID
   (`FR-01`, `FR-02`, ... / `NFR-01`, `NFR-02`, ...)?
2. Is the count of FRs ≥ 10 and ≤ the natural cap implied by the
   Manifest (per Self-sufficiency Rule 3, exceeding 20 when warranted)?
3. Is the count of NFRs ≥ 5 with coverage of all applicable pillars?
4. Does every Manifest item in `[Briefing, Integrations, NFRs]` appear
   literally named in at least one FR or NFR? Walk the Manifest list and
   tick each item off — this is the Self-sufficiency invariant.
5. Are inferred FRs and NFRs flagged with the conversation-language
   equivalent of `(inferred)`?

If patching an existing list, add:

6. Are existing IDs preserved? Removals leave gaps; additions append after
   the last existing ID — never insert in the middle.
   (`sow-shared/references/id-stability-rules.md` → Patch contract.)

---

## What this skill does NOT do

- It does not call `stage_sow`. The orchestrator commits the artifacts to
  state after this skill returns.
- It does not present the Content Review to the user. The orchestrator
  reads `functional_requirements` and `non_functional_requirements` from
  state and assembles the review with the other sections.
- It does not call `confirm_phase_completion`. The orchestrator owns
  phase gating.
- It does not derive Activities, Deliverables, or Timeline from the FRs —
  that is `sow-delivery-plan`'s job, loaded in a later Phase Step.
