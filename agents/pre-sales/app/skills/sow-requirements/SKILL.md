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

Produces FRs + NFRs in one turn so cross-validation has both sides loaded.
Returns populated arrays; orchestrator stages and reviews.

References listed below are binding — where a reference defines how a field
must be written, the reference overrides any paraphrase here. Depth,
structure, minimums, and required wording follow the references; "brief"
and "concise" apply to orchestration messages only, never to FR/NFR content.

## Load before drafting (mandatory)

via `load_skill_resource`:

- `sow-shared` / `references/style-guide.md` — quality contract + Self-sufficiency Rules 1-3.
- `sow-shared` / `references/scope-examples/fr-nfr.md` — quality floor (includes the binding Bad/Good Reliability pair).
- `sow-shared` / `references/language-rules.md` — language hygiene + `(inferred)` marker.
- `sow-requirements` / `references/fr-patterns.md` — FR shape, target, inferred-implicit list.
- `sow-requirements` / `references/nfr-waf-pillars.md` — NFR pillars + binding Reliability anti-uptime rule.
- `sow-requirements` / `references/anti-patterns.md` — rejection list + self-test.

When patching an existing list: also `sow-shared` / `references/id-stability-rules.md`. Its Patch contract overrides any instinct to regenerate.

## Inputs

- `manifest.extracted_items` for `[Briefing, Integrations, NFRs]` + resolved `manifest.gaps`.

## Generate (one turn)

1. **Map Manifest → FRs.** Apply Self-sufficiency Rule 2 (`style-guide.md`): group operations differing only by target/channel; keep functionally distinct capabilities separate. Apply `fr-patterns.md` → "Required FR shape".
2. **Infer implicit FRs.** Add the items in `fr-patterns.md` → "Inferred-implicit FRs" unless already covered. Mark each with `(inferred)` in conversation language.
3. **Generate NFRs across the five WAF pillars.** Apply `nfr-waf-pillars.md`. For Reliability, use the REQUIRED phrasing verbatim; FORBIDDEN uptime/SLA phrasings are rejected in any language. If the Manifest carries an availability percentage, translate the architectural pattern into the NFR and leave the percentage for an Assumption (handled in Step C).
4. **Cross-validate FR ↔ NFR.** Walk pair-wise. Fix `fr_vs_nfr` contradictions and `fr_restated_as_nfr` duplicates in place — never return contradictions to the orchestrator.

## Before returning (workflow gate)

- Run the self-test in `anti-patterns.md` → "Self-test (apply before emitting the requirements section)". All items mandatory.
- Verify the Self-sufficiency invariant: every Manifest item in `[Briefing, Integrations, NFRs]` is findable by name in at least one FR or NFR.
- Counts: FR ≥ 10 (exceed target when Manifest warrants — Self-sufficiency Rule 3); NFR ≥ 5 with all applicable pillars covered.
- When patching: existing IDs preserved per `id-stability-rules.md` (removals leave gaps, additions append).

## Out of scope

- Does not call `stage_sow`, present the Content Review, or call `confirm_phase_completion` — orchestrator owns those.
- Does not derive Activities, Deliverables, or Timeline from the FRs — that is `sow-delivery-plan` (loaded in Step B).
