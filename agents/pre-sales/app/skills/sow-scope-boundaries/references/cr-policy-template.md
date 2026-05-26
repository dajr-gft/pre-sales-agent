# Change Request Policy — template and rules (binding)

The Change Request (CR) Policy section sits immediately after Out-of-Scope.
It defines how scope, timeline, and cost changes are negotiated AFTER the
SOW is signed. Stored in `sow_data['change_request_policy_text']` as a
single string (multi-paragraph) — not a list.

## What the policy text MUST state

The policy text must explicitly contain ALL of the following points (in
the conversation language for the user-facing review, in English for the
final `.docx`):

1. **No out-of-scope work without an approved Change Request signed by
   both parties.** This is the contractual lock.
2. **Verbal agreements are not binding.** Email confirmations, chat
   messages, and conversational commitments do NOT change the SOW.
3. **The Partner reserves the right to pause work without a formal
   approved Change Request when the requested work falls outside the
   defined scope.** This is the operational safety valve.
4. **All Change Requests follow the same process** — both parties sign;
   impact is documented; the SOW addendum is dated and effective from
   the signature date forward.

## What the policy text MUST NOT contain

- **The 7 CR fields are static template text** provided by the document
  template itself: Date of MSA, Date of CR, Impacted SOW, Description of
  changes, Impact on resources / timeline, Cost change, Effective date.
  Do NOT generate these fields; the document template includes them as
  fixed scaffolding.
- **No hours, no rates, no rate cards.** Cost impact is described as
  "the parties will agree on the cost impact in writing" — never with
  numbers.
- **No specific timeline numbers.** Each CR carries its own timeline; the
  policy describes the process, not the magnitude.

## Suggested structure (3 paragraphs)

The text typically has three paragraphs:

1. **Lock paragraph** — Out-of-scope work requires a signed CR; verbal
   agreements are not binding; the Partner may pause non-CR'd work.
2. **Process paragraph** — Both parties sign; the SOW addendum is dated
   and effective from the signature date; the CR text references the
   template's 7 fixed fields.
3. **Cost paragraph** — Each CR carries its own cost / timeline impact,
   to be agreed in writing; the original SOW pricing is unaffected
   outside CRs.

The 3-paragraph shape is a suggestion, not a strict requirement. Two
paragraphs is acceptable if both lock and process fit naturally. Five
paragraphs is acceptable when the engagement has multi-tier change
governance.

## Anti-patterns

| Anti-pattern | Why it fails | Fix |
|---|---|---|
| Policy paragraph allows scope changes "with mutual agreement" without requiring a CR | Loophole — the customer can pressure the partner into informal scope creep | Replace with "signed Change Request" language |
| Policy includes hourly rates or rate cards | Direct violation of the no-rates rule and Cost section's placeholder model | Remove figures; describe process only |
| Policy duplicates the 7 CR fields | Document template already includes them; duplication creates inconsistency if the template changes | Reference the template fields as "the CR form provided by the SOW template"; do not enumerate them |
| Policy buried inside an OOS item | The CR mechanism is the contractual exit from OOS; it needs its own section right after OOS | Promote to a dedicated section: `Change Request Policy` |
