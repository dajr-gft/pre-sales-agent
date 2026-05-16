# Risks — binding rules

The `risks` field is conditional — the customer may opt to omit the section
during review. When present, it contains 3-5 project-specific entries.

Each risk is an object:

```
{"description": "<risk statement>", "mitigation": "<mitigation strategy>"}
```

## Rules

- **3-5 risks**, all project-specific. Generic "delivery risk" is rejected.
- **Each risk names a specific system, technology, or stakeholder** drawn
  from the architecture, FRs, or NFRs.
- **Each mitigation is actionable by the partner team** — not a passive
  "we will monitor".
- **No risks that promise customer behavior** — those belong in
  `assumptions`.
- **No meta-risks** like "the project might fail". Risks name specific
  failure modes (data quality, access provisioning, model accuracy,
  integration constraint, etc.).
- **Inferred risks** are marked with the conversation-language equivalent
  of `(inferred)` per `sow-shared/references/language-rules.md`.

## Calibration

Quality anchor: `sow-shared/references/scope-examples/risks.md`. Match the
depth shown there — each risk names the specific system, the failure mode,
and a concrete mitigation the partner controls.
