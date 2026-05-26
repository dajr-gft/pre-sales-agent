# Assumptions & Prerequisites — patterns and rules (binding)

The Assumptions section captures every condition the partner relies on to
execute the engagement, plus the consequence if that condition is not met.
Stored in `sow_data['assumptions']` as a list of strings.

## Target

**Target: 15-25 assumptions.** Floor, not cap. Real-world engagements with
heavy customer dependencies routinely produce 25-35 assumptions.

## The consequence-clause pattern (NON-NEGOTIABLE for customer-dependent items)

Every customer-dependent assumption MUST include an explicit consequence
clause. The pattern is:

> "[Customer] must [obligation] [by when]. [Consequence if not met]."

Examples (the names below are placeholders — substitute the actual
project's terms):

- "The Customer must provide GCP project access and IAM roles for the
  partner team by Day 0 of Phase 1. Delays in access provisioning will
  result in a proportional extension of the Phase 1 timeline at additional
  cost."
- "The Customer must validate and approve each Phase deliverable within 3
  business days of submission. Feedback delivered after that window is
  treated as acceptance by silence, and post-approval revisions follow the
  Change Request process."
- "The Customer must designate a named Tech Lead with authority to make
  technical decisions and unblock the partner team within 2 business days
  per blocker. Unresolved blockers beyond that window extend the affected
  phase at additional cost."

### Why the consequence clause is mandatory

Without the consequence clause, the assumption is a hope, not a contract.
The validation critic flags such items as
`contractual_exposure:missing_consequence_clause`. The fix is always to
add the consequence — never to delete the assumption.

## The 15 categories (cover what applies; adapt to project)

Walk these categories before emitting the assumptions list. Most
engagements cover 10-15 of the 15.

1. **Contractual** — Pricing model, effective dates, scope limitation,
   confidentiality.
2. **Execution model** — Remote vs on-site, fixed-price, working hours,
   time zone.
3. **Platform prerequisites** — GCP services industrialized and available
   by when. **Link each prerequisite to the specific phase or workstream
   that depends on it** (e.g., "prior to the start of WS03" — not generic
   "before kickoff"). Each GCP service should have its own deadline.
   Unavailability = extension + cost.
4. **Access and credentials** — All credentials, service accounts, VPN
   provisioned before kickoff. Delays = proportional extension.
5. **Stakeholder availability** — Named roles available for
   workshops/validations. Response SLA (e.g., 3 business days). No
   feedback within SLA = acceptance.
6. **Data and documentation** — What the customer provides before each
   phase. Delays = timeline impact.
7. **Workshop cancellation** — < 1 day notice → reschedule. Repeated
   (2+) → timeline review + costs.
8. **Blocker resolution** — Customer blockers resolved within an SLA
   (e.g., 2 business days). Unresolved = extension + cost.
9. **Deliverable review and approval** — Feedback within SLA. No feedback
   = acceptance. Post-approval changes = Change Request.
10. **Scope protection** — Scope is fixed. Changes require a formal
    Change Request with cost/timeline impact.
11. **Data quality** — Partner not responsible for quality / completeness
    of customer data. Data must be sanitized and compliant before
    ingestion.
12. **GenAI / ML acknowledgment** (when applicable) — Non-deterministic
    behavior acknowledged. No 100% accuracy guarantee. Outputs are
    advisory, subject to human review. See `references/handover-rules.md`
    for the canonical disclosure phrasing.
13. **GCP service dependency** — Performance depends on GCP services
    outside partner control.
14. **Partner technical autonomy** — Full autonomy to develop. May use
    internal tools if no customer data is exposed.
15. **Partner liability for own delays** — Partner-side delays are
    completed at no additional cost.

## Anti-patterns

| Anti-pattern | Why it fails | Fix |
|---|---|---|
| Assumption without a consequence clause | The customer is not held to anything | Add the consequence; pattern is "[Customer] must [obligation] [by when]. [Consequence]." |
| Customer-dependent assumption phrased softly ("the customer is expected to provide") | "Expected to" is not contractual | Use "must" + explicit deadline + consequence |
| Generic deadline ("before kickoff") for a platform prerequisite | Multiple prerequisites collapse into one window with no per-item deadline | Link each prerequisite to the specific Phase / Workstream that needs it |
| Two assumptions saying the same thing in different words | Redundancy weakens the contract | Pick the stronger phrasing, drop the other |
| Assumption duplicating an OOS item | The OOS item already excludes it; the assumption is noise | Pick the right list: an EXCLUSION goes in OOS; a CUSTOMER OBLIGATION goes here |
| AI/ML engagement with no non-determinism acknowledgment | Direct contractual exposure on model accuracy | Add the GenAI/ML acknowledgment from Category 12; see `references/handover-rules.md` for the phrasing |

## Cross-section coherence (Assumptions ↔ Deliverables ↔ OOS)

Walk the three lists in order before emitting:

1. For every customer-dependent Deliverable acceptance, is there an
   Assumption covering the customer's review SLA + acceptance-by-silence
   clause?
2. For every GCP service required by the architecture, is there an
   Assumption covering its availability + per-phase deadline?
3. For every OOS item that excludes ongoing operations (Category 10,
   Category 17), is there a matching handover assumption that transfers
   ownership at the KT milestone? (See `references/handover-rules.md`.)

The validation critic surfaces missing counter-anchors as
`contractual_exposure:missing_handover_boundary`. Fix them inside this
skill before returning.
