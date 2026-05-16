# Workstreams, Activities, Deliverables, and Success Criteria — binding rules

These three lists are produced together because the dependency chain
Activity → Deliverable → Success Criterion is structural — splitting them
generates `activities_vs_deliverables` and `success_criteria_vs_deliverables`
contradictions that the validation critic catches.

## Section layout (deliverables-first, workstream-organized)

Deliverables are organized as numbered **Workstreams** (`WS01`, `WS02`,
...), each containing:

- **Objective** — what this workstream delivers (1-2 sentences).
- **Subtopics** — specific bounded activities inside this workstream.
- **Outcomes** — concrete, verifiable results.

This structure is preferred over a flat table because it lets the reader
see the delivery contract at a glance: each workstream is a self-contained
unit of value with its own objective, its own activities, and its own
verifiable outcomes.

### Workstream-row JSON shape (referenced by validators)

Each deliverable row in `sow_data['deliverables']` is structured as:

```
{
  "activity": "<workstream label, e.g. 'WS01: Architecture Foundation'>",
  "name": "<deliverable name>",
  "description": "<what it contains; 1-2 sentences>",
  "format": "<Document | Presentation | Spreadsheet | Code | Demonstration | Video>"
}
```

The `activity` column carries the workstream label so the rendered table
groups deliverables by workstream. Multiple rows share the same
`activity` when the workstream produces several deliverables.

## Activities — the operations behind workstreams

Activities are the operations the partner team performs to produce the
deliverables. Stored in `sow_data['activity_phases']`, structured as one
entry per project phase:

```
{
  "name": "Phase 1: Discovery",
  "description": "Define architecture and validate requirements.",
  "tasks": [<list of task descriptions>]
}
```

### Activity-task rules

- **Action verbs only**: describe, investigate, review, document, design,
  develop, perform, integrate, configure, test, validate, deploy. No noun
  phrases ("Architecture diagram" is not a task).
- **Organize by phases**. Use meaningful phase names — `Phase 1: Discovery`,
  `Phase 2: Build`, `Phase 3: Deploy and Validate` — never just `Phase 1`.
- **Each task names specific systems, GCP services, and technical approach.**
  Not just the action verb.
- **Anti-pattern**: `Set up GCP environment`, `Develop and test pipelines`.
  These could describe any project — too shallow.
- **Self-test** (mandatory before emitting): *"Could this exact task
  description appear unchanged in a different project?"* If yes, it is too
  generic. Rewrite with details unique to THIS project — the specific data
  being processed, the specific API being consumed, the specific business
  rule being implemented, or the specific validation being performed.

## Deliverables — measurable artifacts

- **Target: minimum 10 deliverables** for a 10-14 week project with 3-4
  phases. Floor, not cap. If you have fewer, intermediate artifacts are
  missing.
- **Each deliverable maps to at least one activity** in
  `activity_phases.tasks`. The mapping is implicit by workstream
  alignment, not literal cross-reference.
- **Every phase ≥ 1 deliverable.** A phase with no deliverable is a phase
  the customer cannot evidence.
- **Intermediate deliverables are mandatory.** Examples: Test Plan, Data
  Quality Report, Go-Live Runbook, Knowledge Transfer documentation,
  Architecture Decision Records, Performance Baseline Report.

### Deliverable-format constraints

Use one of the canonical formats: `Document`, `Presentation`,
`Spreadsheet`, `Code`, `Demonstration`, `Video`. Free-form formats
("Slideshow", "Report") rendered into the table read as informal — the
formats above match the Google template's expectation.

### Anti-patterns

| Anti-pattern | Why it fails | Fix |
|---|---|---|
| Flat table with name + format only | Reader cannot tell what the workstream delivers, only what file format | Use workstream structure (Objective / Subtopics / Outcomes) |
| One deliverable per phase, ≤ 3 total | Misses intermediate artifacts (test plan, data quality, runbook, KT docs) | Add intermediate deliverables; target ≥ 10 |
| Deliverable name = activity name | The deliverable IS the artifact, not the operation that produced it | Rename to the artifact ("Performance Baseline Report" not "Run performance tests") |
| Deliverable description is the GCP product page | No project context | Rewrite with what THIS engagement's deliverable contains |

## Success Criteria — verifiable acceptance bar

Stored in `sow_data['success_criteria']` as a list of strings. Each
criterion is one of:

- Deployment evidence (e.g., "Successful deployment of all solution
  components to the target GCP environment").
- Acceptance event (e.g., "Customer acceptance of all deliverables listed
  in Section 4").
- Knowledge transfer (e.g., "Completion of knowledge transfer sessions
  with customer technical team").
- Requirement coverage (e.g., "All functional requirements
  (FR-01 through FR-12) demonstrated and validated").
- Architecture sign-off (e.g., "Architecture documentation approved by
  customer Solution Architect").

### Rules

- **Target: minimum 5 unique criteria.**
- **Each criterion is verifiable.** A check the customer can perform at
  project close. "The solution will be high quality" is NOT a criterion.
- **No repetition.** Two criteria that paraphrase each other are one
  criterion in two forms. Pick the stronger phrasing and drop the other.
- **Tie to specific deliverables or FR ranges where possible.** "All FRs
  demonstrated and validated" is stronger than "the system works as
  expected".
- **Anti-pattern**: criteria that promise outcomes the partner cannot
  guarantee post-handover (production reliability, sustained user
  adoption). Stop at handover; the customer owns operations.

## Cross-section coherence (Activities ↔ Deliverables ↔ Success Criteria)

Walk the three lists in order before emitting:

1. For every Activity-task, is there at least one Deliverable that evidences
   it? (Some activities may share a deliverable; that is fine.)
2. For every Deliverable, is there at least one Activity-task that produces
   it? Standalone deliverables with no activity behind them are a defect.
3. For every Workstream, is there at least one Success Criterion that
   covers it? Workstreams without an acceptance bar are a contractual hole.
4. For every Success Criterion that cites FR IDs, do those FRs exist in
   `functional_requirements` (which was generated earlier by
   `sow-requirements`)? Dangling FR references are a defect.
