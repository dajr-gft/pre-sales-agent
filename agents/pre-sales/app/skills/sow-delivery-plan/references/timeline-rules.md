# Timeline — binding rules

The Timeline section is a numbered table mapping phases to weekly windows
and high-level outcomes. It is the time-axis view of the delivery plan and
must stay coherent with Activities (which lists the tasks per phase) and
Deliverables (which list the workstreams).

## Table shape

Stored in `sow_data['timeline']` as a list of rows. Three columns:

| Column | Content |
|---|---|
| **Phase** | Phase name exactly as it appears in `activity_phases.name`. Match casing, punctuation, ordering. |
| **Timeframe** | Week range (e.g., `Weeks 1-2`) OR absolute date range. Pick one form and use it consistently across all rows. |
| **Key Outcomes** | What is finished by the end of this phase. References deliverables and workstreams by name. |

```
{
  "activity": "Phase 1: Discovery",
  "timeframe": "Weeks 1-2",
  "outcomes": "Approved architecture design (WS01); validated FR/NFR set; signed-off integration inventory."
}
```

## Rules

### Phase alignment

- The set of `Phase` rows MUST equal the set of `activity_phases.name`
  entries — same count, same names, same order. Mismatch is a defect
  surfaced by the validator as `timeline_vs_deliverables` /
  `activities_vs_timeline`.
- Phase names use the form `Phase N: <descriptive label>` —
  `Phase 1: Discovery`, `Phase 2: Build`, `Phase 3: Deploy and Validate`.
  Never bare `Phase 1` or descriptive-only `Discovery`.

### Timeframe consistency

- Pick **one** notation across all rows:
  - **Week ranges** (`Weeks 1-2`, `Weeks 3-8`, `Weeks 9-10`) — preferred when
    no concrete start date is known.
  - **Date ranges** (`2026-05-01 to 2026-05-14`) — use when the user has
    committed to specific dates.
- Mixing week ranges and date ranges in the same timeline is a defect.
- Phase windows must NOT overlap. Phase 2 starts the week after Phase 1
  ends (no gap, no overlap).
- The sum of phase windows equals `project_end_date − project_start_date`
  (when dates are committed) or the duration in
  `references/effort-heuristics.md` engagement table (when only week
  ranges).

### Outcomes — what goes in the column

Each `outcomes` value is a single line (no multi-paragraph), naming:

- Specific workstreams that close in this phase
  (`WS01`, `WS02`, ...) — by label and short descriptor.
- Specific deliverables that are signed off in this phase
  ("Approved architecture design", "Validated data quality report").
- One business-level marker when relevant ("Go-live with production
  traffic" or "Approved production runbook").

Generic outcomes phrasings are rejected: "the team will deliver value",
"customer requirements satisfied", "all activities complete". Each outcome
must reference at least one concrete artifact.

### Cross-section invariant (Timeline ↔ Deliverables ↔ Activities)

Before emitting:

1. Every phase listed in `timeline` MUST match an entry in
   `activity_phases` (same name).
2. Every workstream referenced in `outcomes` MUST exist in
   `deliverables` with the same `WS-NN` label.
3. Every deliverable in `deliverables` MUST be referenced (by name or
   workstream) in at least one phase's `outcomes`.
4. The phase order in `timeline` MUST equal the order in
   `activity_phases`.

Mismatches are surfaced by the validation critic as
`contradictions:timeline_vs_deliverables` or
`contradictions:activities_vs_timeline` — fix them in this skill before
returning, not later.

## Anti-patterns

| Anti-pattern | Why it fails | Fix |
|---|---|---|
| Bare `Phase 1` / `Phase 2` labels | No descriptive context for the reader | Use `Phase N: <label>` form |
| Mixed timeframe notation (weeks + dates) | Inconsistent rendering | Pick one form for all rows |
| Outcomes column has only one entry like "Customer satisfied" | Not verifiable | Reference specific workstream / deliverable names |
| Total weeks across phases > engagement duration | The math does not add up | Recompute phase windows |
| Phase 2 starts before Phase 1 ends | Overlapping phases imply unstated parallelism | Re-sequence or document the parallelism explicitly |
| Phase exists in `activity_phases` but missing from `timeline` (or vice versa) | Cross-section drift | Sync the two lists |
