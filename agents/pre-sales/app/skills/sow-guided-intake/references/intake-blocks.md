# Guided Intake Blocks

The five blocks below are the spine of the guided interview. Walk them
in order, one compact question per block. The user's typed answers are
captured in your internal notes and rolled up into the persisted
``IntakeSummary`` dict per `intake-summary-format.md`. The dict is
committed at the end via ``save_sow_intake_summary`` — never as text
in the chat.

The interview should feel like a guided pre-sales conversation, not a
questionnaire. Translate questions and optionality signals into the
user's language. Do not paste literal templates from this reference —
they are coverage targets, not scripts.

## Commercial model rule

For this SOW template, the pricing model is Fixed Price by default. Do
NOT ask the user to choose between Fixed Price, T&M, milestone-based,
single-delivery, or any other commercial pricing model during guided
intake. Funding type is different from pricing model — that is asked
explicitly in Block 1.

If the user volunteers payment-milestone or invoicing information,
record it as context for the summary, but do not solicit it as a
mandatory question.

## Block 1 — Identity

Partner is GFT Technologies; do NOT ask the user for the partner name.
Ask in one short message for:

- Customer organization (the company the SOW is for).
- Project title or working name.
- Google funding type (DAF, PSF, or "unknown" / "to be defined").

All three are required. Funding type accepts "unknown" as a valid
answer (it lands in the summary as `[TO BE DEFINED]` rather than
becoming a follow-up loop).

## Block 2 — Project Briefing

Ask one open-ended question covering:

- The customer's problem or business goal.
- The proposed solution direction.
- The high-level technical approach when the user wants to volunteer
  it.

Let the user reply in a paragraph; do not split this into multiple
questions.

The problem statement and the solution direction are **required**. The
technology stack and specific GCP services are **inference-eligible**
per `inference-policy.md` — if the user describes only the business
problem with no technical hints, signal the inference path once and
move on. Do not push for technical detail the user did not volunteer.

## Block 3 — Integrations and Systems

Ask which systems, APIs, channels, identity providers, or data sources
the solution will integrate with or consume. This block is **required**
— integrations are client-specific and cannot be inferred safely.

Provide a few examples adapted to context, in the user's language:
ERP, CRM, ITSM, HR systems, internal APIs, operational databases,
file drops, event streams, document repositories, knowledge bases,
web, mobile, voice, messaging, email, SSO / OAuth / API keys,
observability, or DevOps tooling.

For each integration captured, write the system name, the data
direction when known (source, target, or bidirectional), the protocol
when known (REST, gRPC, batch, CDC, Pub/Sub, streaming), and the
operations when known (read, write, create ticket, query profile,
publish event, etc.). When a detail is missing, record `not stated` in
your internal notes rather than guessing.

If the user says "none" or "only GCP services", record that and
continue.

## Block 4 — Scope and Team

Ask in one message for:

- Explicit out-of-scope items the customer or partner has already
  excluded.
- Team composition on both sides (partner side and customer side),
  when the user has it.
- Responsibility split between the partner and the customer, when the
  user volunteers it.

Both items are **inference-eligible** per `inference-policy.md`. If the
user does not have the details, signal once that you can propose
typical out-of-scope items and a typical GFT team for this engagement
shape, and that the user reviews them at the Content Review gate.

## Block 5 — Targets and Constraints

Always cover the four items below. Skip behaviour differs per item —
see `inference-policy.md`.

1. **Engagement shape** (assessment / greenfield / brownfield /
   migration / foundation). Inference-eligible — the user does not have
   to commit to a label; the agent may infer from the Block 2 briefing
   (for example, "we want to build" implies greenfield). When the user
   does not state it, leave the primitive as `(inferred)`; downstream
   section skills will pick a concrete shape before drafting
   Activities and Deliverables.
2. **Quantitative NFR targets** (latency, scalability, accuracy,
   availability, compliance frameworks). **Required** — these are
   commitments to the customer and cannot be inferred silently. If
   targets are not set, the summary records `[TO BE DEFINED]`.
3. **Project timeline** (desired start, end or duration, hard deadlines
   tied to events). **Required**. If unknown, the summary records
   `[TO BE DEFINED]`.
4. **Known constraints or prerequisites**. Industry-standard regulatory
   and compliance constraints (LGPD, Bacen, data residency for a
   Brazilian customer, etc.) are **inference-eligible**. Specific
   operational constraints — network / VPN access, GCP organization,
   security approvals, stakeholder availability windows — are
   **required** and become `[TO BE DEFINED]` if skipped.

## Conditional follow-ups

Ask only when relevant to the user's briefing:

- Data volume and velocity — when the project involves data
  processing, analytics, or ML.
- Authentication and authorization model — when the project involves
  user-facing systems or APIs.
- Environment strategy — when deployment is implied.
- Ambiguous technical choices or data formats — only when they
  materially affect scope or architecture and cannot be inferred from
  the user's previous answers.

Conditional questions consume the same 3 × 3 follow-up budget as the
mandatory block questions.

## Block tracker (internal)

Track block progress in your reasoning, never in the user-facing
output:

```text
[ ] Block 1 — Identity (customer, project title, funding type)
[ ] Block 2 — Project Briefing (problem, solution, tech approach)
[ ] Block 3 — Integrations and Systems
[ ] Block 4 — Scope and Team (OOS, team composition, responsibilities)
[ ] Block 5 — Targets and Constraints (engagement shape, NFRs, timeline, constraints)
```

Tick a block only after the user has answered or after you have
explicitly accepted a skip / inference per `inference-policy.md`.

## Exit gate (internal)

The interview is complete when:

1. Every block has been walked exactly once (or the user has
   acknowledged skipping with the inference path explained when
   applicable).
2. The follow-up budget is exhausted OR you judge further questioning
   will not improve the summary.
3. ``customer_name``, ``project_title``, ``problem_goal``, and
   ``solution_direction`` carry real values (no markers, no blanks).
4. ``funding_type`` is one of ``'DAF'``, ``'PSF'``, or
   ``'[TO BE DEFINED]'``.
5. ``timeline`` is either a real value or ``'[TO BE DEFINED]'``.
6. ``engagement_shape`` is either one of the five labels or
   ``'(inferred)'``.

When all six hold, build the ``IntakeSummary`` dict per
``intake-summary-format.md`` and call
``save_sow_intake_summary(intake_summary=<dict>)``. Do NOT print the
dict to the user.
