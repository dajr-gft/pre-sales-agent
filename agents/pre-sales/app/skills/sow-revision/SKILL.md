---
name: sow-revision
description: >
  **Surgical patching of non-structural fields on the staged SOW after
  the validation critic returns `blocked`.** Baked into the instruction
  of `revision_agent`, invoked by `sow_quality_loop` (the
  QualityLoopAgent) when
  `state['app:validation_result'].overall_status == 'blocked'`. Reads
  `state['app:sow:current']` plus `state['app:validation_result']`,
  patches one field at a time via `apply_sow_global_patch`, appends
  one entry per processed finding via `record_revision_log_entries`;
  then `sow_quality_loop` re-invokes `validation_critic` for the next
  round. Structural fields (section bundles) and manifest-derived
  fields are blocked at the tool level — those findings are routed to
  the per-section repair agents by the loop's partitioner. The
  reviser is the global safety net for textual / non-structural
  fields only.
metadata:
  pattern: restricted-field-patcher + dynamic-reference-loading
  produces: patched sow_data (one field at a time via apply_sow_global_patch), state['app:sow:revision_log']
  inputs: state[app:sow:current], state[app:validation_result]
  upstream: sow_quality_loop (QualityLoopAgent)
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

## Workflow

### (1a) Group findings, severity-descending — and respect the per-round budget

Walk `findings`. Group by `finding.fields[0]` (primary field). Within each
group, sort `BLOCKER → MAJOR → MINOR`. `finding.persistent == True` items
lead within their severity group (already survived one round; need
stronger attention).

**Per-round patch budget (binding).** Process at most **5 significant findings** (severity `BLOCKER` or `MAJOR`) per round. Any other significant findings in the report are deferred to the next round; the QualityLoopAgent will re-run `validation_critic` and then call the revision_agent again with the next report, so the deferred findings reach you next round. `MINOR` findings are cosmetic and never block status — process them only if they share a `fields[0]` with a significant finding you are already patching this round.

Selection order (apply in sequence; first 5 win):

1. `severity == BLOCKER` and `persistent == True`.
2. `severity == BLOCKER` and `persistent == False`.
3. `severity == MAJOR` and `persistent == True`.
4. `severity == MAJOR` and `persistent == False`.

**Why a cap.** Rewriting more than ~5 top-level fields in a single round drifts the SOW: the LLM loses anchor on which fields are being touched and which are not, Contract 1 violations multiply, and the critic on the next round flags as many *new* findings as the reviser resolved. The result is a loop that exhausts its round budget while reading like real progress in the revision log. A small batch each round keeps each patch traceable and lets the critic discriminate signal from drift.

**Resolution mode filter (binding).** Skip any finding whose `resolution_mode` is not `auto_fixable`. Those are `decision_required`, `source_conflict`, or `not_fixable_by_agent` — the QualityLoopAgent's gate routes them straight to `needs_human_review` once no auto-fixable findings remain. Trying to patch them here either invents data (Contract 3 violation, since the reference cannot supply what only the user can decide) or churns the SOW without resolving the underlying ambiguity. If, after the filter and the budget cap, you have **zero** findings to patch, call `record_revision_log_entries(entries=[], noop_reason="all-remaining-findings-need-human-decision", round_label="round-<N>")` and return; the loop's next critic run will flip `overall_status` to `needs_human_review`.

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

### (1c) Patch fields and persist the log

For each accepted finding, apply patches field by field:

1. `apply_sow_global_patch(field=<key>, value=<new value>)` —
   overwrites a single top-level field of `state['app:sow:current']`.
   Call once per touched field; the tool is the only writer of the
   staged SOW available to you. The tool REJECTS by construction any
   field owned by a section bundle (e.g. `functional_requirements`,
   `deliverables`, `assumptions`, `architecture_components`, …) and
   any field derived from the extraction manifest (e.g.
   `partner_name`, `customer_name`, project dates). If you receive
   such a rejection, the router mis-routed the finding — emit a
   short diagnostic naming the rejected field and stop the round;
   the section repair agent will pick the finding up in the next
   round once routing is corrected.
2. Write the per-finding revision entries to
   `state['app:sow:revision_log']` via
   `record_revision_log_entries(entries=[...])`. Append-only across
   rounds. The root orchestrator reads this state key after the loop
   terminates to compose the user-facing Revision Note in Phase 3.

**Do NOT call `stage_sow`.** The tool is not available to this
agent; stage transitions (`content` → `full`) belong to the root
orchestrator, and the QualityLoopAgent reassembles the flat SOW
from bundles after every section repair — no in-loop re-stage is
ever needed.

**Zero-patch rounds (noop):** if a round legitimately produces no
patches — every finding fell under `decision_required`/`source_conflict`
and was deferred to human review, or every routable finding belongs to
a section bundle (so the global patcher refuses) — you still MUST call
`record_revision_log_entries(entries=[], noop_reason="<short why>",
round_label="round-<N>")` so the log records evidence the round ran.
Calling with `entries=[]` and no `noop_reason` is rejected: silent
empty rounds mask bugs where the patcher ran but did nothing.

After this skill returns, `sow_quality_loop` re-invokes `validation_critic` for the next round (or terminates if the round budget is exhausted).

## Per-patch gate

Before each `apply_sow_global_patch(field, value)` call:

- The field name is the literal top-level key from `finding.fields` — never an invented variant.
- For each processed finding, the mapped reference(s) were loaded via `load_sow_reference` BEFORE the patch was applied (Contract 3). When `finding.fields` had more than one entry, the secondary reference was also loaded.
- The new `value` preserves every existing ID inside it (Contract 2) and holds to the same depth/structure as the original (no "shorter because patch").

When `apply_sow_global_patch` returns `status: 'error'` with a `reviser_blocked_structural_field` suggestion:

- The finding was mis-routed by the loop's partitioner. Surface a short diagnostic naming the rejected field and stop the round; do NOT retry with a different field.
- `state['app:sow:revision_log']` must still be populated with a noop entry citing the rejection so the diagnostic survives the loop.

---

## Out of scope (critical boundaries)

- **MUST NOT regenerate any section.** Rewriting fields outside `finding.fields` for a single finding is a Contract 1 violation.
- **MUST NOT call `stage_sow`.** The tool is not available to this agent. Stage transitions (`content` → `full`) belong to the root orchestrator; the QualityLoopAgent reassembles the flat SOW from bundles after every section repair, so no in-loop re-stage is ever needed.
- **MUST NOT touch bundle-owned fields.** `apply_sow_global_patch` refuses them at the Python level (the section repair agents own them); a rejection is the router's fault, not yours.
- Does not re-validate. `sow_quality_loop` re-invokes `validation_critic` after every revision round.
- Does not call `confirm_phase_completion`. Phase gating belongs to the root orchestrator agent; revision rounds happen within a phase.
- Does not present the Revision Note to the user. The root orchestrator composes the localized Revision Note after the loop terminates.
- Does not adjust user-approved content preferences. If the user has approved an item, replace the offending phrasing only — preserve the user's intent.
