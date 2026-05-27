# Inference Policy

Some items the user may not be able to answer during a guided intake.
This reference defines which items are **required** (skipping records
`[TO BE DEFINED]`) and which are **inference-eligible** (skipping
records `(inferred)` for downstream review). It also defines the rules
for signalling optionality to the user.

The agent ALWAYS asks the block question. The difference is what
happens when the user does not have an answer.

## Required items

If the user does not have an answer for a required item, the value
lands in the `<intake_summary>` as `[TO BE DEFINED]`. Do not infer,
do not pattern-match against typical projects.

- **Block 1** — Customer organization, project title. Funding type is
  required but accepts "unknown" as a literal answer that becomes
  `[TO BE DEFINED]` in the summary.
- **Block 2** — Problem statement and solution direction.
- **Block 3** — Integrations and data sources (entire block; integration
  facts are client-specific and never safely inferable).
- **Block 5 #2** — Quantitative NFR targets (commitments to the
  customer).
- **Block 5 #3** — Project timeline (commitments to the customer).
- **Block 5 #4 (operational portion)** — Specific operational
  constraints: network / VPN, GCP organization, security approvals,
  stakeholder availability windows.

## Inference-eligible items

If the user skips an inference-eligible item, leave the value in the
summary as `(inferred)`. Do NOT record it as a hard `[TO BE DEFINED]`.
The downstream section skills will propose a concrete value, and the
user reviews and confirms (or adjusts) at the Content Review gate.

- **Block 2** — Technology stack and specific GCP services.
- **Block 4** — Out-of-scope items.
- **Block 4** — Team composition (partner side and customer side).
- **Block 4** — Responsibility split between partner and customer.
- **Block 5 #1** — Engagement shape (assessment / greenfield /
  brownfield / migration / foundation).
- **Block 5 #4 (regulatory portion)** — Industry-standard regulatory
  and compliance constraints (LGPD, Bacen, data residency for a
  Brazilian customer, sector-standard frameworks).

## Signalling optionality (mandatory)

When an item is inference-eligible, the user MUST hear about the
inference and review path AT LEAST ONCE before they decide to skip.
Hidden inference is exactly what the Content Review gate protects
against — the user must walk into that gate already knowing some
values were inferred.

Hard rules:

- Signal optionality in the same message that asks the question (or at
  the start of the block when the entire block is inference-eligible,
  like Block 4).
- Phrasing is up to the agent — improvise in the conversation
  language, fit the tone. Do NOT paste a fixed template. Do NOT repeat
  the disclaimer mechanically in every question.
- Never silently mark an item as `(inferred)` without the user knowing
  that the inference path exists.

Examples (rephrase in the user's language and tone):

- "If you don't have the stack defined yet, that's fine — I can
  propose typical GCP services for this kind of project, and you'll
  review them before we finalize."
- "Se vocês ainda não definiram time, posso propor uma composição
  típica da GFT para esse tipo de engajamento; você revisa antes de
  fechar."

When a required item is missing, do NOT offer the inference path —
record `[TO BE DEFINED]` and continue.

## Budget interaction

The follow-up budget (3 × 3) applies the same way to both categories.
The difference is what happens at exit:

- Required items still missing after the budget → `[TO BE DEFINED]`.
- Inference-eligible items still missing after the budget →
  `(inferred)`.

## Confidence convention in the summary

When emitting the `<intake_summary>`:

- Facts the user stated directly are written as-is, with no marker.
- Facts the user implied (clear paraphrase, not a leap) carry no
  marker either — record them as captured facts.
- Values left for downstream inference carry `(inferred)`.
- Values genuinely unknown to the user carry `[TO BE DEFINED]`.
