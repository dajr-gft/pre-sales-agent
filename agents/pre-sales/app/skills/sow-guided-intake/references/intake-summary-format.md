# Intake Summary Format

The `<intake_summary>` block is the only artifact this skill emits.
It is the structured handoff the root reads as upstream project
context before calling `save_sow_metadata` and walking the section
skills.

The summary is plain text, NOT JSON, NOT YAML, NOT a tool call. The
root parses it as human-readable upstream context.

## Required shape

Emit the block exactly once at the end of the interview, in the
following structure. Each labelled section must be present; values
that are not in evidence carry `[TO BE DEFINED]` or `(inferred)` per
`inference-policy.md`.

```text
<intake_summary>
Customer:
- <customer organization>

Partner:
- GFT Technologies

Project:
- <project title or working name>

Funding:
- <DAF | PSF | [TO BE DEFINED]>

Problem / Goal:
- <one or two lines on the business problem or objective>

Solution Direction:
- <one or two lines on the proposed direction>

Engagement Shape:
- <assessment | greenfield | brownfield | migration | foundation | (inferred)>

Main Scope:
- <bullet per scope item, or "(inferred)" when none stated>

Out of Scope:
- <bullet per OOS item the user mentioned, or "(inferred)" when none stated>

Integrations / Systems:
- <bullet per integration: system name, direction, protocol, operations — fields not stated marked accordingly>

Team:
- Partner side: <composition or "(inferred)">
- Customer side: <composition or "(inferred)">

Timeline:
- <duration, start, end, hard deadlines — or "[TO BE DEFINED]">

NFR / Quality Targets:
- <one bullet per target (latency, scalability, accuracy, availability, compliance) — or "[TO BE DEFINED]" if none stated>

Assumptions / Constraints:
- <operational constraints stated by the user — required portion>
- <regulatory / compliance constraints — required when stated, otherwise "(inferred)" for industry-standard frameworks>

Open Items:
- <each [TO BE DEFINED] or (inferred) item rolled up here so the root can see the gap list at a glance>
</intake_summary>
```

## Field rules

- **Customer / Partner / Project / Funding** — the metadata envelope
  the root needs for `save_sow_metadata`. Customer organization and
  project title are required. Funding is one of `DAF`, `PSF`, or
  `[TO BE DEFINED]`.
- **Problem / Goal** and **Solution Direction** — short narrative
  paragraphs (1–2 lines each). These are required; they cannot be
  `[TO BE DEFINED]` after a successful interview.
- **Engagement Shape** — one of the five labels, or `(inferred)`.
- **Main Scope / Out of Scope / Team** — bullets. When the user left
  the block to inference, write the literal token `(inferred)` as the
  single bullet for that section.
- **Integrations / Systems** — one bullet per integration. Include
  fields the user provided (name, direction, protocol, operations);
  mark unspecified fields as `not stated` inline.
- **Timeline / NFR / Quality Targets** — required. `[TO BE DEFINED]`
  when missing.
- **Assumptions / Constraints** — split between stated operational
  constraints (required) and regulatory / compliance frameworks
  (inference-eligible).
- **Open Items** — a rolled-up index of every `[TO BE DEFINED]` and
  `(inferred)` value from the summary above. The root uses this to
  decide which items need targeted user confirmation at the Content
  Review gate.

## Markers

- `[TO BE DEFINED]` — required value that the user could not provide
  after the follow-up budget.
- `(inferred)` — inference-eligible value the user left for downstream
  agents to propose.
- `not stated` — used inline within an integration bullet for missing
  sub-fields (direction, protocol, operations).

## Anti-patterns

- Do NOT emit the `<intake_summary>` more than once. The first emission
  ends the skill.
- Do NOT include free-form prose before or after the
  `<intake_summary>` block in the same message — the root parses the
  block, not surrounding chatter. A short single-sentence handoff
  acknowledgement is fine; long commentary is not.
- Do NOT call any tool inside this message — the summary is plain
  text.
- Do NOT translate the field labels (Customer, Project, Funding,
  Problem / Goal, etc.). The label set is the parsing contract for the
  root. The values are written in the user's language when the user
  provided them in that language; the labels themselves stay in
  English.
- Do NOT silently drop a field. Every label in the template above must
  be present, even when its single bullet is `[TO BE DEFINED]` or
  `(inferred)`.
