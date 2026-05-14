# Finding → Reference mapping (binding)

For each `ValidationReport.findings` entry, this table names the target
section skill + reference to load via `load_skill_resource` BEFORE applying
the patch. `sow-revision` MUST load this file during its Pre-step and
consult it for every finding it processes.

## Schema

A `Finding` carries the following relevant attributes:

- `skill` — the validation dimension (e.g., `coverage`, `contradictions`,
  `contractual_exposure`, `disclosures`, `semantic_quality`,
  `self_sufficiency`, `language_hygiene`).
- `category` — the specific finding type within the dimension.
- `fields` — the `sow_data` top-level keys this finding wants to patch.
  `fields[0]` is the primary; additional entries are co-touched (relevant
  for cross-section findings like `timeline_vs_deliverables` or
  `architecture_vs_stack`).

Lookup is `(finding.skill, finding.category)`. When the table marks the
mapping as **field-dependent**, also inspect `finding.fields[0]` and use
the field-dependent table at the bottom of this file.

## Direct mapping

| skill | category | Target skill | Reference to load |
|---|---|---|---|
| `coverage` | `manifest_item_uncovered` | field-dependent | see field-dependent table |
| `contradictions` | `fr_vs_nfr` | `sow-requirements` | `references/anti-patterns.md` |
| `contradictions` | `fr_restated_as_nfr` | `sow-requirements` | `references/anti-patterns.md` |
| `contradictions` | `scope_vs_oos` | `sow-scope-boundaries` | `references/oos-categories.md` |
| `contradictions` | `architecture_vs_stack` | `sow-architecture` | `references/tech-stack-table-rules.md` |
| `contradictions` | `timeline_vs_deliverables` | `sow-delivery-plan` | `references/timeline-rules.md` |
| `contradictions` | `activities_vs_deliverables` | `sow-delivery-plan` | `references/workstream-structure.md` |
| `contractual_exposure` | `missing_consequence_clause` | `sow-scope-boundaries` | `references/assumption-patterns.md` |
| `contractual_exposure` | `missing_timing_anchor` | `sow-scope-boundaries` | `references/assumption-patterns.md` |
| `contractual_exposure` | `missing_handover_boundary` | `sow-scope-boundaries` | `references/handover-rules.md` |
| `contractual_exposure` | `missing_change_request_gate` | `sow-scope-boundaries` | `references/cr-policy-template.md` |
| `contractual_exposure` | `subjective_nfr_target` | `sow-requirements` | `references/anti-patterns.md` |
| `contractual_exposure` | `production_availability_commitment` | `sow-requirements` | `references/nfr-waf-pillars.md` |
| `disclosures` | `missing_ai_nondeterminism_disclosure` | `sow-scope-boundaries` | `references/handover-rules.md` |
| `disclosures` | `missing_pii_responsibility_disclosure` | `sow-scope-boundaries` | `references/handover-rules.md` |
| `disclosures` | `missing_multi_region_authority_disclosure` | `sow-scope-boundaries` | `references/handover-rules.md` |
| `semantic_quality` | `generic_architecture_labels` | `sow-architecture` | `references/audit-rules.md` |
| `semantic_quality` | `generic_capability` | `sow-requirements` | `references/fr-patterns.md` |
| `semantic_quality` | `compound_fr` | `sow-requirements` | `references/fr-patterns.md` |
| `semantic_quality` | `vague_phrasing_outside_nfr` | field-dependent | see field-dependent table; default `sow-shared` / `references/style-guide.md` → "General writing rules" |
| `semantic_quality` | `naming_drift` | `sow-shared` | `references/style-guide.md` |
| `self_sufficiency` | `self_sufficiency_break` | `sow-shared` | `references/style-guide.md` → "Self-sufficiency contract" |
| `language_hygiene` | `*` (any category) | `sow-shared` | `references/language-rules.md` |

## Field-dependent mapping

Used when the table above marks a row as **field-dependent**, or when an
unknown category needs routing by primary field. Inspect
`finding.fields[0]`:

| `finding.fields[0]` | Target skill | Default reference |
|---|---|---|
| `functional_requirements` | `sow-requirements` | `references/fr-patterns.md` |
| `non_functional_requirements` | `sow-requirements` | `references/nfr-waf-pillars.md` |
| `activity_phases` | `sow-delivery-plan` | `references/workstream-structure.md` |
| `deliverables` | `sow-delivery-plan` | `references/workstream-structure.md` |
| `success_criteria` | `sow-delivery-plan` | `references/workstream-structure.md` |
| `timeline` | `sow-delivery-plan` | `references/timeline-rules.md` |
| `partner_roles`, `customer_roles` | `sow-delivery-plan` | `references/roles-rules.md` |
| `assumptions` | `sow-scope-boundaries` | `references/assumption-patterns.md` |
| `out_of_scope` | `sow-scope-boundaries` | `references/oos-categories.md` |
| `handover_disclaimers` | `sow-scope-boundaries` | `references/handover-rules.md` |
| `risks` | `sow-scope-boundaries` | `references/risks-rules.md` |
| `change_request_policy_text` | `sow-scope-boundaries` | `references/cr-policy-template.md` |
| `architecture_description` | `sow-architecture` | `references/diagram-spec.md` (Part E) |
| `architecture_components`, `architecture_integrations` | `sow-architecture` | `references/diagram-spec.md` |
| `technology_stack` | `sow-architecture` | `references/tech-stack-table-rules.md` |
| `executive_summary` | `sow-narrative` | `references/exec-summary-template.md` |
| `partner_overview`, `customer_overview` | `sow-narrative` | `references/overview-rules.md` |
| `customer_primary_domain` | `sow-narrative` | `references/overview-rules.md` → "Domain capture rules" |

When the finding co-touches additional fields (`fields[1]`, `fields[2]`,
...), load those secondary references too — cross-section findings need
both sides loaded before the patch. Example: a `timeline_vs_deliverables`
finding with `fields = ["timeline", "deliverables"]` loads
`timeline-rules.md` AND `workstream-structure.md`.

## When the lookup misses

If a finding's `(skill, category)` is not in the direct mapping AND
`fields[0]` is not in the field-dependent table:

1. Do NOT guess a target reference. Patching uninformed reproduces the
   finding in the next round.
2. Fall back to `sow-shared` / `references/style-guide.md` for the
   general writing-quality contract.
3. Log the unmapped `(skill, category)` in `revision_log` so the mapping
   table can be extended for the next release.

Patching a field without first loading the mapped reference is a defect —
the correction will not know the rule it must satisfy. Always: lookup →
`load_skill_resource` → read → patch.
