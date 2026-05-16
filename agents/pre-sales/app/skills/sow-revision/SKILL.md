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
  post-validation correction — it has no generation workflow and three
  binding anti-regeneration contracts.
metadata:
  pattern: surgical-patcher + dynamic-reference-loading
  produces: patched sow_data (written via stage_sow), state['app:sow:revision_log']
  inputs: state[app:sow:current], state[app:validation_result], state[app:language]
  upstream: root_agent
  references-skill: sow-shared (for ID stability and style)
  references-other: sow-architecture, sow-requirements, sow-delivery-plan, sow-scope-boundaries, sow-narrative (dynamic, per finding)
---

# SOW Revision

Surgical patches to an existing `sow_data`, one finding at a time. **NO
regeneration.** No new sections, no rewritten sections, no reordered IDs.
Every untouched field stays byte-for-byte identical to the previous payload.

If you rewrite fields beyond those listed in `finding.fields` for a single
finding, you are violating Contract 1 — re-anchor on the pre-patch payload.

References listed below are binding — even stricter than for generation,
because the acceptable surface of change is limited to the fields listed in `finding.fields`, not a
whole section. "Brief" and "concise" apply to orchestration messages
only — patched content meets the same depth and structure rules as the
original.

---

## The three anti-regeneration contracts (binding)

### Contract 1 — Minimum change

Touch only the top-level keys listed in `finding.fields`. `fields[0]` is
the primary; additional entries (`fields[1..n]`) are permitted co-touches
required by cross-section findings (e.g., `timeline_vs_deliverables`
patches both `timeline` and `deliverables`; `architecture_vs_stack`
patches both `architecture_description` and `technology_stack`).

Preserve every top-level key NOT listed in `finding.fields` byte-for-byte.
For each key in `fields`, if `len(sow_data[key])` was N before, it must be
N (refinement), N+k (deliberate addition for the finding), or N−k
(deliberate removal for the finding) — never accidentally drift. Enforced
by the hash check in the workflow gate.

### Contract 2 — ID stability

Apply `sow-shared` / `references/id-stability-rules.md` → "Patch contract"
verbatim. Never renumber, reorder, or swap IDs. New items append after the
last existing ID; removals leave gaps in the numeric sequence. Round-2 IDs
must equal round-1 IDs for every item the user has already seen.

### Contract 3 — Reference before patch

For every finding: look up the mapping in
`references/finding-map.md`, call `load_sow_reference` on the mapped
section reference, READ the rule, THEN apply the patch. Patching without
the rule loaded is a defect — you will recreate the same finding because
the correction does not know the rule it must satisfy.

`load_sow_reference(target_skill="<skill>", reference_path="references/<rule>.md")`
is the allowlist-protected tool the revision agent owns. The allowlist
is derived from this file (`finding-map.md`) at import time, so every
mapping below is guaranteed to be loadable. Do NOT use `load_skill` or
`load_skill_resource` — they are not available to this agent.

---

## Load before patching (mandatory)

via `load_sow_reference(target_skill=..., reference_path=...)`:

- `load_sow_reference(target_skill="sow-shared", reference_path="references/id-stability-rules.md")` — Patch contract, overrides every other instinct.
- `load_sow_reference(target_skill="sow-shared", reference_path="references/style-guide.md")` — Self-sufficiency contract still applies to patched items.
- `load_sow_reference(target_skill="sow-shared", reference_path="references/language-rules.md")` — patched content stays in the same surface as the original.
- `load_sow_reference(target_skill="sow-revision", reference_path="references/finding-map.md")` — mapping from `(finding.skill, finding.category)` and, when needed, `finding.fields` to the section reference to load per finding.

Section-specific references are loaded dynamically per finding (Contract 3) — the mapping above is consulted for every finding before its patch.

## Inputs

- `state['app:sow:current']` — current `sow_data` payload (the base of every patch).
- `state['app:validation_result']` — `ValidationReport` from the most recent critic run. Read `findings`, `overall_status`, `round_count`, `persistent_blocking_finding_count`.
- `state['app:language']` — conversation language (used by the root's Revision Note later; the patches go into `sow_data`, which is English).

## Workflow

### (1a) Group findings, severity-descending

Walk `findings`. Group by `finding.fields[0]` (primary field). Within each
group, sort `BLOCKER → MAJOR → MINOR`. `finding.persistent == True` items
lead within their severity group (already survived one round; need
stronger attention).

### (1b) For each finding: map → load → patch

For each finding in order:

1. **Map** the finding via `references/finding-map.md` using
   `(finding.skill, finding.category)`. When the table marks the row as
   field-dependent, also consult the field-dependent table using
   `finding.fields[0]`.
2. **Load** the mapped reference:
   `load_sow_reference(target_skill="<target_skill>", reference_path="references/<rule>.md")`.
   If `finding.fields` lists more than one field (cross-section finding),
   also load the secondary reference mapped from `fields[1..n]` — both
   sides must be loaded before the patch.
3. **Read** `finding.evidence` (verbatim quote of the offending content)
   + `finding.recommendation` (concrete corrective instruction).
4. **Apply** the minimum patch across every `key` in `finding.fields`.
   Each touched field follows one of:
   - **Refinement** — same ID, updated content for the offending item.
   - **Addition** — new item appended after the last existing ID (when
     the finding requires adding a missing item).
   - **Removal** — delete the offending item; surrounding IDs unchanged.
5. **Log** `{finding_id, skill, category, action, fields_touched,
   before_hash, after_hash}` to the in-memory revision log.

After all findings in the group are processed, verify other sections
(top-level keys NOT in any processed `finding.fields`) are byte-identical
to the pre-patch snapshot.

### (1c) Stage and persist the log

After every finding in every group is processed:

1. `stage_sow(patched_sow_data)` — writes to `state['app:sow:current']`.
   The only place this skill mutates the document state directly.
2. Write the per-finding revision entries to
   `state['app:sow:revision_log']` via
   `record_revision_log_entries(entries=[...])`. Append-only across
   rounds. The root reads this state key to compose the user-facing
   Revision Note after re-validation.

**Zero-patch rounds (noop):** if a round legitimately produces no
patches — every finding fell under `decision_required`/`source_conflict`
and was deferred to human review, or no finding mapped to a patchable
field — you still MUST call
`record_revision_log_entries(entries=[], noop_reason="<short why>",
round_label="round-<N>")` so the log records evidence the round ran.
Calling with `entries=[]` and no `noop_reason` is rejected: silent
empty rounds mask bugs where the patcher ran but did nothing.

After this skill returns, the root re-invokes `validation_critic`.

## Before staging (workflow gate)

- Top-level keys of patched `sow_data` exactly equal the pre-patch keys (none added or removed).
- For every top-level key NOT listed in any processed `finding.fields`, its value hashes identically to the pre-patch snapshot. If not → Contract 1 violated; re-anchor on the pre-patch payload.
- Every refinement preserved its ID (Contract 2).
- For each processed finding, the mapped reference(s) were loaded via `load_sow_reference` BEFORE the patch was applied (Contract 3). When `finding.fields` had more than one entry, the secondary reference was also loaded.
- Patched content holds to the same depth/structure as the original (no "shorter because patch").
- `state['app:sow:revision_log']` was populated with one entry per processed finding, including `before_hash` and `after_hash` per field touched.

If any check fails, do NOT call `stage_sow`. Re-anchor on the pre-patch payload and re-run the affected group with the missing reference loaded.

---

## Out of scope (critical boundaries)

- **MUST NOT regenerate any section.** Rewriting fields outside `finding.fields` for a single finding is a Contract 1 violation.
- Does not re-validate. The root re-invokes `validation_critic` after `stage_sow`.
- Does not call `confirm_phase_completion`. Phase gating belongs to the orchestrator; revision rounds happen within a phase.
- Does not present the Revision Note to the user. The root composes the localized Revision Note after re-validation.
- Does not adjust user-approved content preferences. If the user has approved an item, replace the offending phrasing only — preserve the user's intent.
