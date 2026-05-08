# Guided Discovery Blocks

Use only for Path A, when no artifacts exist. The conversation itself is source `A1`; anchors are `guided turn N / Block X`.

Path A should feel like a guided pre-sales conversation, not a questionnaire. Ask one compact question per block, let the user answer naturally, and extract structured Manifest items silently after each response.

## Commercial model rule

For this SOW template, the pricing model is Fixed Price by default. Do not ask the user to choose between Fixed Price, T&M, milestone-based pricing, single-delivery pricing, or any other commercial pricing model during guided discovery.

Funding type is different from pricing model. Continue asking for the Google funding type when unknown: Deal Acceleration Funds (DAF), Partner Services Funds (PSF), or unknown.

If the user voluntarily provides payment milestone or invoicing information, capture it as context if relevant, but do not ask for it as a mandatory guided-discovery question.

## Inference policy

Some items the user may not have an answer to during guided discovery. The agent always asks the question, but for **inference-eligible** items the agent makes optionality explicit: if the user skips, the primitive is recorded as `not_stated`, no hard gap is filed, and `sow-generator` infers the value at SOW review time and presents it in the Inference Summary for the user to confirm before generating the SOW.

**Inference-eligible items** — skipping is acceptable; do NOT file a hard gap:

- Block 2 — technical stack and GCP service selection
- Block 3 — out-of-scope items
- Block 3 — team composition (partner side and customer side)
- Block 4 #1 — engagement shape
- Block 4 #4 — typical regulatory or compliance constraints for the customer's industry (LGPD, Bacen, data residency in Brazil, sector-standard compliance)

**Required items** — if the user does not have an answer, record `[TO BE DEFINED]` and file as a hard gap:

- Block 1 — customer organization, project name (funding type accepts "unknown" without becoming a hard gap)
- Block 2 — problem statement and solution direction
- Block 2.5 — integrations and data sources (client-specific, not inferable)
- Block 4 #2 — quantitative NFR targets (commitments to the customer)
- Block 4 #3 — project timeline (commitments to the customer)
- Block 4 #4 — specific operational constraints (network, VPN, organization-specific access, stakeholder availability windows)

**Signaling optionality is mandatory, not optional.** When asking an inference-eligible item, the agent must make clear to the user — in the same message that asks the question — that the user does not need to answer if they don't have it defined yet, because the agent will infer based on project context and present the inferred value in the Inference Summary for the user to confirm before the SOW is generated.

Phrasing is up to the agent — improvise in the conversation language, fit the tone of the conversation. Do NOT use a fixed template and do NOT repeat the disclaimer mechanically in every question. The hard rules are:

- The user must hear about the inference + review path at least once before the inference-eligible item is recorded as `not_stated`.
- The agent must never silently infer an inference-eligible item without the user knowing the inference exists. Hidden inference at SOW generation time is exactly what the Inference Summary protects against — the user must enter that summary already aware that some values were inferred.
- When a whole block is inference-eligible (Block 3), signal at the start of the block. When only part of a block is (Block 2, Block 4), signal on that specific item or naturally within the question.

## Initialize the buffer

Before Block 1, silently call `initialize_extraction_buffer` with:

```json
[{"id":"A1","name":"guided conversation","type":"user-briefing","phase_0_hypothesis":"guided discovery via Blocks 1-4","source_language":"<conversation language>"}]
```

The user does not see this — it is the structural setup for Path A.

## Blocks

**Block 1 — Identity.** Partner is GFT Technologies; do not ask. Ask in one short message for: customer organization, project name, and Google funding type (DAF, PSF, or unknown). All three are required (funding type accepts "unknown" as a valid answer).

**Block 2 — Project Briefing.** Ask one open-ended question covering the customer problem, proposed solution direction, and high-level technical approach. Do not break this into multiple questions. The user should answer in a paragraph.

The problem statement and solution direction are required. The technical stack and specific GCP services are inference-eligible: if the user describes only the business problem with no technical hints, signal that you can infer the stack from project context and the user reviews in the Inference Summary before the SOW is generated. Do not push for technical detail the user did not volunteer.

**Block 2.5 — Integrations and Data Sources.** Ask which systems, APIs, channels, identity providers, or data sources the solution integrates with or consumes. This block is required — integrations are client-specific and cannot be inferred safely.

Provide a few examples adapted to context: ERP, CRM, ITSM, HR systems, internal APIs, operational databases, file drops, event streams, document repositories, knowledge bases, web, mobile, voice, messaging, email, SSO, OAuth, API keys, observability, or DevOps tooling.

Capture system name, data direction when known (source, target, or bidirectional), protocol when known (REST, gRPC, batch file, CDC, Pub/Sub, streaming), and operations when known (read, write, create ticket, query customer profile, publish event, etc.).

If the user says "none" or "only GCP services", record and continue. If the user already described integrations in Block 2, confirm them briefly and ask if there are others.

**Block 3 — Scope and Team.** Ask in one message for: explicit out-of-scope items the customer or partner has already excluded, and team composition on both sides (partner side and customer side). Both items are inference-eligible — if the user does not have these details, signal that you can propose typical out-of-scope items and a typical GFT team for this engagement, and the user reviews in the Inference Summary.

**Block 4 — Mandatory Targets and Constraints.** Always ask all four items. Skip behavior differs per item — refer to the inference policy:

1. **Engagement shape** *(inference-eligible)*: assessment/discovery only, greenfield build, enhancement to an existing platform, migration, or foundation/landing zone setup. If the user does not state explicitly, the agent may infer from the Block 2 briefing — for example, "we want to build" implies greenfield. This primitive is structural; if it remains `not_stated` after guided discovery, `sow-generator` must infer a value before drafting Activities and Deliverables.
2. **Quantitative NFR targets** *(required)*: latency, scalability, accuracy, availability, compliance frameworks. These are commitments to the customer and cannot be inferred silently. If targets are not set, record as `[TO BE DEFINED]` and file as a hard gap.
3. **Project timeline** *(required)*: desired start date, end date or duration, and business deadlines tied to events or commitments. If unknown, record as `[TO BE DEFINED]` and file as a hard gap.
4. **Known constraints or prerequisites**: typical regulatory and compliance constraints for the customer's industry are *inference-eligible* (e.g., LGPD, Bacen, data residency for a Brazilian bank). Specific operational constraints (network/VPN, GCP organization, security approvals, stakeholder availability windows) are *required* and become `[TO BE DEFINED]` if skipped.

For required items genuinely unknown to the user, do not push — record as a hard gap with `user_response: "[TO BE DEFINED]"`. For inference-eligible items skipped, populate the primitive as `not_stated` and continue.

**Conditional questions** ask only when relevant:

- Data volume and velocity, when the project involves data processing, analytics, or ML.
- Authentication and authorization model, when the project involves user-facing systems or APIs.
- Environment strategy, when deployment is implied.
- Ambiguous technical choices or data formats, only when they materially affect scope or architecture and cannot be inferred from the user's answer.

**Maximum of 3 follow-up rounds across the whole guided discovery, with at most 3 questions per round.** After 3 rounds, anything still unanswered becomes `[TO BE DEFINED]` (for required items) or remains `not_stated` for `sow-generator` to infer (for inference-eligible items).

**Infer silently — do not ask:** FRs, NFR categories, architecture, assumptions, success criteria, risks, and out-of-scope expansion beyond what the user mentioned. (Inference-eligible items above are different — those are asked but allow skipping.)

## Post-response routine — run after every user message

This is what prevents Path A from producing a worse Manifest than Path B. After every user response (Blocks 1–4 and any follow-up):

1. **Identify touched categories** using `extraction-rules.md`.
2. **Split distinct concepts.** A response naming three enterprise systems together produces three Integrations items, not one. A response mentioning multiple NFR targets produces multiple NFR items. Apply the operational tests in `extraction-rules.md` Cross-cutting rules — same connective tests as Path B.
3. **Populate all required primitives** for each category. Unknown primitive = `not_stated`.
4. **Confidence:** `stated` for facts the user gave directly; `implied` only when the user's framing clearly implies the value (e.g., "we want to build" implies `engagement_shape: "greenfield"`).
5. **Append silently** via `append_extraction_items`. Each item carries `source: [{artifact_id: "A1", anchor: "guided turn N / Block X"}]`. Use IDs `I-001`, `I-002`, ... continuing from the buffer count returned by the previous append call.
6. **Decide on follow-up.** If 1–2 critical primitives are missing AND the user can plausibly answer them in one short message, ask a single targeted follow-up before moving to the next block. If the missing primitives require deferred decisions or specialized knowledge the user does not have right now, leave them as `not_stated` and let Phase 3 escalate them as gaps if needed.
7. **Apply the inference policy on skips.** If the user skipped an inference-eligible item, do not file a hard gap — leave the primitive as `not_stated` and continue. If the user skipped a required item, file as a hard gap with `user_response: "[TO BE DEFINED]"`.

**Critical primitives that warrant a quick follow-up:** Integrations `direction` and `operations`; NFRs `target_value`; Reliability/Operational Excellence `responsibility_boundary`; Identity `engagement_shape` (asked explicitly in Block 4); Constraints `actor_responsibility`; Timeline duration or date anchor.

## Calibration on item granularity (Path A)

Same rules as Path B. A response listing four integrations is four items, not one. A response mentioning three NFR targets is three items. A response naming five auth mechanisms is five Integrations items (or five NFR-Security items, depending on framing). Do not collapse into "the user mentioned several enterprise systems."

Path A may produce fewer items than Path B when the user gives a short briefing. That is acceptable. The quality requirement is exhaustive extraction over what the user actually stated, not artificial inflation.

## Block tracker

Track block progress visibly to yourself:

```text
[x] Block 1 — Identity (3 items: customer, project_name, funding_type) — buffer at 3
[x] Block 2 — Briefing (2+ items: problem statement, solution direction, technical approach when stated) — buffer at N
[ ] Block 2.5 — Integrations and Data Sources
[ ] Block 3 — Scope and Team
[ ] Block 4 — Mandatory Targets and Constraints
```

The buffer count at each block must match the cumulative `total_items_in_buffer` returned by the most recent append call.

## Phase 1-A exit gate

Every block must have a `[x]` marker before Phase 2. Block 4 must have produced at least:

1. one Identity item with `engagement_shape` populated, OR `engagement_shape: not_stated` flagged for `sow-generator` to infer (engagement shape is inference-eligible, so it does not become a hard gap on skip);
2. the Google funding type primitive populated as DAF, PSF, or `[TO BE DEFINED]`;
3. either a Timeline item OR a hard gap recording that timeline is `[TO BE DEFINED]` (timeline is required).