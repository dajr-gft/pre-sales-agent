# Coverage Protocol

Purpose: prevent silent collapse — extracting a few umbrella items from dense artifacts and finalizing as if complete.

## Core loop per artifact

1. **Pre-compute density:** artifact shape, expected categories, visible rows/bullets/boxes/labels/responsibility cells/slide sections/named subjects.
2. **Enumerate:** numbered internal list of every concrete element.
3. **Extract or skip:** each enumerated element becomes exactly one item or one skip-with-reason.
4. **Ledger:** pass coverage invariants.
5. **Append:** only after coverage passes; chunk dense artifacts.
6. **Receipt:** one user-visible coverage receipt; no internal details.

## Concrete elements

Enumerate anything that could become a Manifest item: bullets; table rows/cells naming systems, capabilities, actions, responsibilities, concepts; RACI rows/assignments; capability rows; named systems/APIs/data sources/channels/protocols/frameworks/tools/services; diagram boxes/arrows/labels; numeric targets; dates/milestones/phases/dependencies; scope boundaries; roles/stakeholders/owners; decisions/assumptions/constraints/prerequisites.

## Enumeration rules

One concept = one entry. Split distinct subjects joined by commas, slashes, `and/e/y/&`. Visual layout controls count: N bullets/rows/boxes → N entries. Do not cluster similar rows. Do not filter by priority/rank/tier/MoSCoW/severity. Do not deduplicate across artifacts. Headers/labels/boilerplate must be enumerated first, then skipped with reason.

## Visible count gate

For structured artifacts, internally set `visible_element_count = N` and `enumerated_count = M`. Gate: `M == N`. If `M < N`, re-open and re-enumerate. For prose, count named systems/concepts/scope statements/constraints/targets/dates/assumptions/decisions section by section and reconcile.

## Extract-or-skip rule

For every enumerated element:
- Extract if SOW-relevant. Include category, value, value_detail, primitives, source, confidence, and `notes.enumeration_index`.
- Skip only true noise: header, duplicated category label, footer, logo, decorative separator, page number, copyright.

No third option. Cross-artifact duplicates are still extracted with their own source anchors.

### Primary structured artifact skip restriction

For Primary structured artifacts, skipping is tightly restricted. If `artifact_shape` is `capability_matrix`, `raci_matrix`, `requirement_table`, `project_plan`, `timeline_table`, `integration_table`, `nfr_table`, or any structured table/list that defines project scope, every non-header row, responsibility cell, capability entry, named system, phase, milestone, integration, activity, deliverable, role, or constraint is presumed extractable.

Allowed skips are limited to: table header, repeated column/category label with no standalone meaning, page footer/header, decorative visual element, empty cell, page number, logo, or legal/copyright boilerplate unrelated to project scope.

Invalid skip reasons include: low priority, already captured in another artifact, only contextual, only informative, Google-owned responsibility, customer-owned responsibility, not directly assigned to GFT, too detailed for the SOW, belongs to a group already extracted, similar to another row, or implementation-specific.

RACI rows are extractable whenever they define who is responsible, accountable, consulted, or informed for a project activity. If GFT is only informed or not responsible, capture the row as a Scope, Constraint, Decision, or responsibility-boundary item. Do not skip it.

Capability rows are extractable whenever they name a platform capability, integration, guardrail, operational control, data source, channel, tool, model behavior, evaluation mechanism, memory mechanism, security control, observability feature, deployment capability, or testing/acceptance capability. Do not skip capabilities because they seem grouped, future-facing, owned by another party, or implementation-specific.

## Coverage ledger

One internal entry per artifact:

```json
{"artifact_id":"A10","artifact_name":"...","tier":"Primary","artifact_shape":"capability_matrix|raci_matrix|table|slide_deck|prose|screenshot|transcript|mixed","visible_element_count":0,"enumerated_count":0,"extracted_count":0,"skipped_count":0,"structural_skip_count":0,"semantic_skip_count":0,"accounted_count":0,"append_calls":0,"items_appended_for_artifact":0,"coverage_status":"pass|fail","risk_flags":[]}
```

Invariants: `accounted_count = extracted_count + skipped_count`; `skipped_count = structural_skip_count + semantic_skip_count`; `accounted_count == enumerated_count`; structured artifacts require `enumerated_count == visible_element_count`; every enumeration index appears exactly once; after append, `items_appended_for_artifact == extracted_count`.

`structural_skip_count` means skips caused only by headers, empty cells, repeated labels, footers, logos, decorative elements, page numbers, or boilerplate. `semantic_skip_count` means skipped elements that contain project meaning: capabilities, responsibilities, integrations, systems, activities, milestones, roles, constraints, decisions, targets, or delivery scope.

## Fail and reprocess when

- `semantic_skip_count > 0` for a Primary structured artifact;
- a capability matrix, RACI matrix, requirement table, integration table, NFR table, timeline table, or project-planning table has more skipped elements than extracted elements;
- `visible_element_count >= 10` and `extracted_count < 80% of visible_element_count`, unless every skipped element is a structural skip;
- a structured Primary artifact has many visible elements but few extracted items;
- capability/RACI/requirements/integration/NFR/project-planning tables produce umbrella items;
- skip reasons include “already captured”, “similar”, “low priority”, “context only”, “only informative”, “not important”, “not assigned to GFT”, “Google-owned”, “customer-owned”, or “too detailed”;
- priority/rank/tier was used to exclude rows;
- any row, bullet, responsibility, capability, integration, activity, deliverable, role, milestone, or constraint with project meaning was skipped instead of extracted.

## Dense artifact chunking

If `visible_element_count > 40`, append chunks of at most 25 enumeration indices: 1–25, 26–50, etc. Preserve `notes.enumeration_index`. One append call per chunk. No user-visible chunk progress. One receipt only after all chunks succeed. Never mix artifacts in one append call. `items_appended_for_artifact` must equal the sum of `items_appended_this_call` across all successful chunk append calls for that artifact. If a chunk errors, fix/re-append rejected items only.

## Receipt

After coverage and append succeed:

```text
✓ [artifact_id] processed — coverage [accounted]/[visible] visible elements accounted for ([extracted] extracted, [skipped] skipped). Moving to [next_artifact_id].
```

For final artifact: replace last sentence with `Reviewing for gaps...`. Translate. Do not show buffer counts, skip reasons, enumeration, or raw tool fields.

## Global gate before finalize

Path B may finalize only if every confirmed artifact has one passing ledger; every structured Primary artifact has non-zero consistent counts; `semantic_skip_count == 0` for every Primary structured artifact; sum of `items_appended_for_artifact` equals total appended Path B items; no Primary artifact has suspiciously low extraction; no row/bullet/capability/responsibility/integration/activity/milestone detail was skipped or collapsed; every receipt matches its ledger. Otherwise reprocess before finalize.
