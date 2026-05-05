# Guided Discovery Blocks

Use only for Path A, when no artifacts exist. The conversation itself is source `A1`; anchors are `guided turn N / Block X`.

## Initialize the buffer

Before Block 1, silently call `initialize_extraction_buffer` with:

```json
[{"id":"A1","name":"guided conversation","type":"user-briefing","phase_0_hypothesis":"guided discovery via Blocks 1-4","source_language":"<conversation language>"}]
```

The user does not see this — it is the structural setup for Path A.

## Blocks

**Block 1 — Identity.** Partner is GFT Technologies; do not ask. Ask in one short message for: customer organization, project title or codename, and funding type (DAF, PSF, T&M, Fixed Price, Pure SOW, or unknown).

**Block 2 — Briefing.** Ask one open-ended question covering the customer problem, proposed solution direction, and high-level technical approach. Do not break this into multiple questions. The user should answer in a paragraph. If the user only describes the business problem with no technical hints, ask one short follow-up about GCP services or technical stack; otherwise continue.

**Block 2.5 — Integrations and data sources.** Ask which systems, APIs, channels, identity providers, or data sources the solution integrates with or consumes. Provide a few examples adapted to context: enterprise systems (ERP, CRM, ITSM, HR), data sources (warehouses, lakes, operational databases, file drops, event streams), document repositories or knowledge bases, customer-facing channels (web, mobile, voice, messaging, email), identity providers and authentication mechanisms, observability or DevOps tooling. If the user already mentioned integrations in Block 2, confirm and ask if there are others. If "none" or "GCP-only", record and continue.

**Block 3 — Scope, team, payment.** Ask in one message for: explicit out-of-scope items the customer or partner has already excluded; team composition (partner side and customer side); payment model (Fixed Price, milestone-based, T&M, single delivery).

**Block 4 — Mandatory targets and constraints.** Always ask, regardless of what came before:

1. **Engagement shape:** "Is this an assessment / discovery only (no implementation), a greenfield build, an enhancement to an existing platform, a migration, or a foundation/landing zone setup?" This primitive is structural — `sow-generator` cannot draft Activities and Deliverables without it.
2. Quantitative NFR targets — latency, scalability, accuracy, availability, compliance frameworks. Ask whether targets are set, or to be defined later (and by whom).
3. Project timeline — desired start date, end date or duration, business deadlines that constrain the schedule.
4. Known constraints or prerequisites — data residency, compliance, GCP organization, network/VPN constraints, security approvals, team availability windows.

If a Block 4 item is genuinely unknown to the user, do NOT push — record it as a hard gap to be filed at finalize time, with `user_response: "[TO BE DEFINED]"`.

**Conditional questions** (ask only when relevant): data volume and velocity (when project involves data processing, analytics, or ML); authentication and authorization model (when project involves user-facing systems or APIs); environment strategy (when deployment is implied).

**Maximum of 3 follow-up rounds across the whole guided discovery, with at most 3 questions per round.** After 3 rounds, anything still unanswered becomes `[TO BE DEFINED]` in the Manifest.

## Post-response routine — run after every user message

This is what prevents Path A from producing a worse Manifest than Path B. After every user response (Blocks 1–4 and any follow-up):

1. **Identify touched categories** using `extraction-rules.md`.
2. **Split distinct concepts.** A response naming three enterprise systems together produces three Integrations items, not one. A response mentioning multiple NFR targets produces multiple NFR items. Apply the operational tests in `extraction-rules.md` Cross-cutting rules — same connective tests as Path B.
3. **Populate all required primitives** for each category. Unknown primitive = `not_stated`.
4. **Confidence:** `stated` for facts the user gave directly; `implied` only when the user's framing clearly implies the value (e.g., "we want to build" implies `engagement_shape: "greenfield"`).
5. **Append silently** via `append_extraction_items`. Each item carries `source: [{artifact_id: "A1", anchor: "guided turn N / Block X"}]`. Use IDs `I-001`, `I-002`, ... continuing from the buffer count returned by the previous append call.
6. **Decide on follow-up.** If 1–2 critical primitives are missing AND the user can plausibly answer them in one short message, ask a single targeted follow-up before moving to the next block. If the missing primitives require deferred decisions or specialized knowledge the user does not have right now, leave them as `not_stated` and let Phase 3 escalate them as gaps if needed.

**Critical primitives that warrant a quick follow-up:** Integrations `direction` and `operations`; NFRs `target_value`; Reliability/Operational Excellence `responsibility_boundary`; Identity `engagement_shape` (asked explicitly in Block 4); Constraints `actor_responsibility`; Timeline duration or date anchor.

## Calibration on item granularity (Path A)

Same rules as Path B. A response listing four integrations is four items, not one. A response mentioning three NFR targets is three items. A response naming five auth mechanisms is five Integrations items (or five NFR-Security items, depending on framing). Do not collapse into "the user mentioned several enterprise systems."

## Block tracker

Track block progress visibly to yourself:

```
[x] Block 1 — Identity (3 items: customer, project_name, funding_type) — buffer at 3
[x] Block 2 — Briefing (2 items: problem statement, technical approach) — buffer at 5
[ ] Block 2.5 — Integrations
[ ] Block 3 — Scope, Team, Payment
[ ] Block 4 — Mandatory Targets and Constraints
```

The buffer count at each block must match the cumulative `total_items_in_buffer` returned by the most recent append call.

## Phase 1-A exit gate

Every block must have a `[x]` marker before Phase 2. Block 4 must have produced at least: one Identity item with `engagement_shape` populated, the funding type primitive populated, and either a Timeline item OR a hard gap (filed at finalize time) recording that timeline is `[TO BE DEFINED]`.
