# Effort sizing — heuristics (binding)

Effort sizing is the implicit dimension behind Activities, Deliverables,
Timeline, and Roles — wrong sizing produces a `timeline_vs_deliverables`
contradiction even when each section is internally correct. These
heuristics anchor the four sections to a coherent total.

## Cost rules (non-negotiable)

These constraints from `sow-shared/references/style-guide.md` are repeated
here because they affect how effort is communicated, not because they are
new:

- **No hours.** Never include hours, hourly rates, rate cards, or fee
  schedules anywhere in the SOW. Effort is expressed through phase
  duration, workstream count, deliverable count, and team size — never
  raw hours.
- **No rate cards in any section.** The Cost section is a placeholder for
  manual fill. The agent does not generate it.

## Engagement-shape heuristics (calibration only, not contractual)

These are agent-side calibration numbers — internal guidance for the LLM to
produce a coherent delivery plan. They do NOT appear in the SOW text.

| Engagement type | Typical duration | Phases | Workstreams | Deliverables |
|---|---|---|---|---|
| Discovery / assessment | 4-6 weeks | 1-2 | 1-2 | 5-8 |
| Implementation (small) | 8-10 weeks | 3 | 3-4 | 10-14 |
| Implementation (standard) | 10-14 weeks | 3-4 | 4-5 | 12-18 |
| Implementation (complex) | 14-20 weeks | 4-5 | 5-7 | 18-25 |
| Platform / foundation | 12-20 weeks | 3-4 | 4-6 | 14-22 |
| Migration | 10-16 weeks | 4 | 4-5 | 12-18 |

Calibrate the project's engagement type from the Manifest (Briefing +
Timeline + Integrations + NFRs categories). Pick the closest row and use
the upper end of each range when the Manifest is rich or the NFR list
covers all five WAF pillars.

## Coherence heuristics — keep the four sections aligned

After producing Activities, Deliverables, Timeline, and Roles, check
internal consistency:

### Activity → Timeline coherence

- The phases in `activity_phases` must match the rows in `timeline`
  one-to-one. Same phase names. Same order.
- Each phase's `tasks` count is proportional to its `timeframe` length.
  A 1-week phase with 12 tasks is a defect; either the timeframe is wrong
  or several tasks belong to other phases.

### Deliverable → Timeline coherence

- Every workstream `WS-NN` must produce its deliverables within the
  matching phase window. A workstream that spans Phase 2 but produces a
  deliverable in Phase 1's outcomes is a defect.
- Each phase row in `timeline.outcomes` must reference at least one
  deliverable produced in that phase, by name or by workstream label.

### Roles → Engagement-size coherence

| Engagement size | Partner roles (min) | Customer roles (min) |
|---|---|---|
| Discovery / assessment | PM + Solution Architect | Executive Sponsor + SME |
| Implementation (small) | PM + Solution Architect + Engineer | Executive Sponsor + Tech Lead + SME |
| Implementation (standard / complex / platform / migration) | PM + Solution Architect + 2-3 Engineers (specializations vary) | Executive Sponsor + Tech Lead + 1-2 SMEs |

A 14-week implementation with only PM + Architect on the partner side is a
defect — the math does not work. Add the engineering specializations the
Manifest implies (Data, ML, Cloud Infrastructure, Integration, depending
on the FR coverage).

### Self-test (apply before emitting the delivery plan)

1. Does the engagement duration match the engagement-type heuristic above
   (within ±2 weeks)? If much longer, are the additional weeks justified by
   complexity in the Manifest?
2. Does the deliverable count match the engagement-type range? If lower,
   intermediate artifacts are missing.
3. Do the partner roles cover every specialization implied by the FR/NFR
   coverage (data, ML, infra, integration, security)?
4. Does the timeline `outcomes` text reference deliverables by name or by
   workstream, NOT in generic terms ("the team will deliver value")?
