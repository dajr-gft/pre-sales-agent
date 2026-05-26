# ID stability rules (cross-cutting, binding)

IDs assigned to items in a SOW (FR-NN, NFR-NN, OOS-NN, A-NN, deliverable
codes such as WS01-D2, etc.) are a contractual surface — users approve
content **by ID** in reviews and refer back to them in negotiation. Once
an ID has been shown to the user, it is frozen for the rest of the
session. Every workflow skill must obey the following rules without
exception.

## Core rules

1. **Never renumber.** If you remove FR-05 in response to a user request,
   FR-06 stays FR-06 — do not compact the list to close the gap. The
   review and the document carry the gap visibly.
2. **Never reorder.** The sequence of items in `sow_data['<section>']` is
   the order the user has already seen. Sorting alphabetically, by
   priority, or by any other criterion after the first review is
   forbidden.
3. **Never swap.** Two items must never trade IDs, even if a refinement
   makes the swap look natural. Update content in place; keep IDs frozen.
4. **Append, never insert.** New items added after the first review go
   **after the last existing ID** for that section. Inserting in the
   middle shifts subsequent IDs and breaks user reference.

## Refinement vs. addition vs. removal

- **Refinement** — same ID, updated content. Triggered by user feedback,
  ambiguity resolution, or validator error. The ID does not change; only
  the body of that single item changes.
- **Addition** — new ID at the end of the section. Always appended; never
  inserted between existing IDs.
- **Removal** — delete the item, leaving the gap in the numeric sequence.
  All other IDs remain identical.

## Patch contract (binding for `sow-revision` and any post-validation editing)

After the first `stage_sow` call, every subsequent payload submitted to
`validate_sow_content`, `stage_sow`, or `generate_sow_document` is a
**patch** of the previous payload — not a fresh generation.

- Apply the minimum change required to address the validator error or
  finding. Leave every other field byte-for-byte identical: same items,
  same order, same IDs, same text.
- If the error names a single field (e.g., "FR-08: description too short"),
  rewrite only that field. Do NOT renumber other FRs, do NOT rewrite
  other descriptions, do NOT reorder the list.
- If the same payload is submitted twice in a row, you broke this rule —
  you regenerated instead of editing. Recover by literally copying the
  previous payload as the starting point and editing surgically from there.

## Section-skill hand-off

When `sow-revision` (or any patching context) loads a section skill's
references to apply a finding, the section skill's content rules are
followed **only for the field(s) named by the finding**. The ID stability
rules above always override any section-specific instinct to "tidy up"
neighboring items.
