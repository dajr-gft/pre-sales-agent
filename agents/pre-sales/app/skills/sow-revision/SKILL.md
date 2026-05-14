---
name: sow-revision
description: >
  **Surgical patching of an existing `sow_data` payload after the
  validation critic returns `blocked`.** Loaded by the root agent
  (NOT by `sow-orchestrator`) when
  `state['app:validation_result'].overall_status == 'blocked'`. Reads
  `state['app:sow:current']` (current sow_data) plus
  `state['app:validation_result']` (ValidationReport), applies minimum
  patches per finding, calls `stage_sow` with the patched payload, then
  the root re-invokes `validation_critic`. This skill exists to BREAK
  the loop pattern observed when `sow-generator` was reused for
  post-validation correction — it has no generation workflow and four
  binding anti-regeneration contracts.
metadata:
  pattern: surgical-patcher + dynamic-reference-loading
  produces: patched sow_data (written via stage_sow), revision_log (artifact)
  inputs: state[app:sow:current], state[app:validation_result], state[app:language]
  upstream: root_agent
  references-skill: sow-shared (for ID stability and style)
  references-other: sow-architecture, sow-requirements, sow-delivery-plan, sow-scope-boundaries, sow-narrative (dynamic, per finding)
---

# SOW Revision

**Scope of this skill.** Surgical patches to an existing `sow_data`, one
finding at a time. **NO regeneration.** No new sections, no rewritten
sections, no reordered IDs. Every other field stays byte-for-byte
identical to the previous payload.

If you find yourself rewriting more than one field for a single finding,
you are violating Contract 1 — re-anchor on the binding rules below.

## Reference authority and depth rules

The loaded reference files are the **binding quality contract** for
patching — even stricter than for generation, because the surface area
of acceptable change is one field per finding, not a whole section.

Priority order for patched content quality:

1. `sow-shared/references/id-stability-rules.md` — **binding patch
   contract.** Section "Patch contract" overrides every other instinct.
   Order, IDs, and untouched fields are frozen.
2. `sow-shared/references/style-guide.md` — binding cross-cutting
   writing rules; the Self-sufficiency contract still applies to any
   patched item.
3. `sow-shared/references/language-rules.md` — binding language hygiene
   (the patched content stays in the same language surface as the
   original; reviews localize, the `.docx` payload is English).
4. **The section skill's reference mapped from the finding** (loaded
   dynamically per Contract 3 below).
5. This `SKILL.md` — patching workflow, the 4 anti-regeneration
   contracts, and the finding-to-reference mapping table.

If this skill says to do X and a reference defines how X must be
patched, the reference controls the content. Do not simplify, shorten,
or reinterpret reference requirements unless the reference explicitly
allows it.

**Brevity scope rule:** instructions such as "brief", "concise",
"direct", or "short" apply only to conversational orchestration
messages, confirmations, and error handling. They do NOT apply to the
patched content. A patched FR / NFR / OOS / Assumption MUST meet the
same depth and structure rules as the original — patching is not an
opportunity to shorten.

---

## The four anti-regeneration contracts (binding)

These contracts are why `sow-revision` exists. If you find yourself
violating one, STOP and re-anchor.

### Contract 1 — Minimum change

Touch only the `Finding.fields[0]` named by the finding. Preserve all
other top-level keys of `sow_data` byte-for-byte. If
`len(sow_data['X'])` was N before, it must be N (refinement), N+k
(deliberate addition for the finding), or N-k (deliberate removal for
the finding) — never accidentally drift.

The contract is enforced by a hash check (Step 1.5 below). If untouched
keys do not hash identically, you regenerated instead of patched.

### Contract 2 — ID stability

Apply `sow-shared/references/id-stability-rules.md` → "Patch contract"
verbatim. Never renumber, reorder, or swap IDs. New items append after
the last existing ID; removals leave gaps in the numeric sequence.

This contract is non-negotiable across rounds: round-2 IDs must equal
round-1 IDs for every item the user has already seen.

### Contract 3 — Reference on demand (Pre-step is dynamic per finding)

Before applying the patch for a finding, load the section skill's
reference that defines how that field must be written:

- Map `Finding.fields[0]` and `Finding.category` to the target skill +
  reference via the mapping table below.
- Call
  `load_skill_resource(skill_name="<target_skill>", file_path="references/<rule>.md")`.
- Read the rule. THEN apply the patch.

Patching a field without first loading the section-specific reference
is a defect — you will recreate the same finding because the
correction does not know the rule it must satisfy.

### Contract 4 — Adherence to the obligatory reference pattern

This skill adopts Section 5.9 of the decomposition plan in its
patching mode:

- **Mechanism 1** (priority block + Brevity scope rule) — see the
  block above.
- **Mechanism 2** (Pre-step Load gate) — DYNAMIC per finding (see
  Contract 3); the base Pre-step below ALWAYS runs first.
- **Mechanism 3** (Reference Compliance gate) — see Step 1.5 below.
- **Mechanism 4** (inline citations) — every reference path in this
  SKILL.md sits inside backticks.

---

## Workflow — patch per finding, hash-check at exit

**Pre-step — Load and apply references (mandatory gate before any patching):**

These references are ALWAYS loaded before processing the first finding.
Section-specific references are loaded dynamically per finding under
Contract 3.

- `load_skill_resource(skill_name="sow-shared", file_path="references/id-stability-rules.md")`
  — **Binding patch contract.** Overrides any sub-step instinct.
- `load_skill_resource(skill_name="sow-shared", file_path="references/style-guide.md")`
  — **Binding quality contract** for the patched content.
- `load_skill_resource(skill_name="sow-shared", file_path="references/language-rules.md")`
  — **Binding language hygiene.**

Use as input:

- `state['app:sow:current']` — the current `sow_data` payload (the
  base of every patch).
- `state['app:validation_result']` — the `ValidationReport` from the
  most recent critic run. Read `findings`, `overall_status`,
  `round_count`, `persistent_blocking_finding_count`.
- `state['app:language']` — the conversation language for any
  user-facing artifact (the patches themselves go into `sow_data`,
  which is English; only the Revision Note rendered later by the
  root is localized).

### (1a) Group findings by primary field, severity-descending

Walk `state['app:validation_result'].findings`. Group entries by
`finding.fields[0]` (the primary top-level key the finding wants to
patch). Within each group, sort by severity:
`BLOCKER → MAJOR → MINOR`.

Persistent findings (`finding.persistent == True`) are prioritized
within their severity group — they have already survived one round and
need stronger attention.

### (1b) For each group: load the mapped reference, then patch

For each group, walk findings in order:

1. **Map** the finding to a target skill + reference via the mapping
   table below.
2. **Load** the reference:
   `load_skill_resource(skill_name="<target_skill>", file_path="references/<rule>.md")`.
3. **Read** `finding.evidence` (verbatim quote of the offending content)
   and `finding.recommendation` (the concrete corrective instruction).
4. **Apply** the minimum patch to `sow_data[finding.fields[0]]`. The
   patch is one of:
   - **Refinement** — same ID, updated content for the offending item.
   - **Addition** — new item appended after the last existing ID
     (when the finding requires adding a missing item).
   - **Removal** — delete the offending item; surrounding IDs unchanged.
5. **Log** `{finding_id, action, fields_touched, before_hash,
   after_hash}` into the in-memory revision log.

After all findings in the group are processed, verify that other
sections (top-level keys NOT in `finding.fields`) are byte-identical
to the pre-patch snapshot.

### (1c) Stage the patched payload and emit the revision log

After every finding in every group is processed:

1. Call `stage_sow(patched_sow_data)` to write the patched payload to
   `state['app:sow:current']`. This is the only place this skill
   mutates state directly.
2. Emit the `revision_log` (the list of per-finding entries) as an
   artifact for downstream telemetry and for the user-facing Revision
   Note that the root composes after re-validation.

After this skill returns, the root re-invokes `validation_critic`. The
critic's aggregator increments `round_count` and marks reappearing
fingerprints as `persistent` — see the aggregator's round-tracking
logic for details.

### Step 1.5 — Reference Compliance (silent, mandatory before returning)

Self-test checklist (all items mandatory):

1. Does the patched `sow_data` have exactly the same top-level keys as
   the pre-patch snapshot? (No keys added or removed.)
2. For every top-level key NOT named in any `finding.fields[0]`, does
   that key's value hash identically to the pre-patch snapshot? If
   not, you violated Contract 1 — re-anchor on the pre-patch payload.
3. For every patched item that was a refinement, did its ID stay the
   same? (Renumbering is a Contract 2 violation.)
4. For every patched group, was the mapped section-skill reference
   loaded via `load_skill_resource` BEFORE the patch was applied? If
   not, you violated Contract 3 — the patch is uninformed and likely
   to recreate the finding.
5. Was the patched content held to the same depth and structure rules
   as the original? (No "shorter because it's a patch" — Brevity scope
   rule above.)
6. Is `revision_log` populated with one entry per processed finding,
   including `before_hash` and `after_hash` for each
   `fields_touched`?

If any check fails, do NOT call `stage_sow`. Re-anchor on the
pre-patch payload and re-run the group with the missing reference
loaded.

---

## Finding-to-reference mapping (the dynamic Pre-step source of truth)

For each combination of `Finding.skill` (= dimension), `Finding.category`,
and `Finding.fields[0]`, the table below names the target reference to
load via `load_skill_resource`. When the table says "field-dependent",
inspect `finding.fields[0]` and pick the matching section skill.

| Finding | Target skill | Reference to load |
|---|---|---|
| `coverage:manifest_item_uncovered` | field-dependent — see below | matches the field |
| `contradictions:fr_vs_nfr` | `sow-requirements` | `references/anti-patterns.md` |
| `contradictions:fr_restated_as_nfr` | `sow-requirements` | `references/anti-patterns.md` |
| `contradictions:scope_vs_oos` | `sow-scope-boundaries` | `references/oos-categories.md` |
| `contradictions:architecture_vs_stack` | `sow-architecture` | `references/tech-stack-table-rules.md` |
| `contradictions:timeline_vs_deliverables` | `sow-delivery-plan` | `references/timeline-rules.md` |
| `contradictions:activities_vs_deliverables` | `sow-delivery-plan` | `references/workstream-structure.md` |
| `contractual_exposure:missing_consequence_clause` | `sow-scope-boundaries` | `references/assumption-patterns.md` |
| `contractual_exposure:missing_handover_boundary` | `sow-scope-boundaries` | `references/handover-rules.md` |
| `contractual_exposure:subjective_nfr_target` | `sow-requirements` | `references/anti-patterns.md` |
| `contractual_exposure:production_availability_commitment` | `sow-requirements` | `references/nfr-waf-pillars.md` |
| `disclosures:missing_ai_nondeterminism_disclosure` | `sow-scope-boundaries` | `references/handover-rules.md` |
| `semantic_quality:generic_architecture_labels` | `sow-architecture` | `references/audit-rules.md` |
| `semantic_quality:generic_capability` | `sow-requirements` | `references/fr-patterns.md` |
| `semantic_quality:compound_fr` | `sow-requirements` | `references/fr-patterns.md` |
| `semantic_quality:naming_drift` | `sow-shared` | `references/style-guide.md` |

### Field-dependent mapping for `coverage:manifest_item_uncovered`

Use `finding.fields[0]` to pick the section skill:

| `finding.fields[0]` | Target skill |
|---|---|
| `functional_requirements`, `non_functional_requirements` | `sow-requirements` |
| `activity_phases`, `deliverables`, `success_criteria`, `timeline`, `partner_roles`, `customer_roles` | `sow-delivery-plan` |
| `assumptions`, `out_of_scope`, `handover_disclaimers`, `risks`, `change_request_policy_text` | `sow-scope-boundaries` |
| `architecture_description`, `architecture_components`, `architecture_integrations`, `technology_stack` | `sow-architecture` |
| `executive_summary`, `partner_overview`, `customer_overview`, `customer_primary_domain` | `sow-narrative` |

Within the target skill, pick the reference most specific to the
manifest item type (e.g., a missing GCP service goes through
`tech-stack-table-rules.md`; a missing assumption goes through
`assumption-patterns.md`).

---

## What this skill does NOT do

- It does not regenerate any section. If you find yourself rewriting
  more than one field for a single finding, you are violating
  Contract 1.
- It does not re-validate. The root re-invokes `validation_critic` after
  this skill calls `stage_sow`.
- It does not call `confirm_phase_completion`. Phase gating is the
  orchestrator's responsibility; revision rounds happen within a
  phase, not across phases.
- It does not present the Revision Note to the user. The root composes
  the localized Revision Note after re-validation completes.
- It does not adjust the user's content preferences captured at earlier
  reviews. If the user has approved an item, the patch must preserve
  the user's voice — replace the offending phrasing, not the user's
  intent.
