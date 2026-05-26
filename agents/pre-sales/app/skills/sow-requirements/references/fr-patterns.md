# Functional Requirements — binding patterns

The Functional Requirements section is a numbered table of `shall` statements,
each scoped to a specific behavior the solution implements during the
engagement. The Self-sufficiency contract from
`sow-shared/references/style-guide.md` applies in full: every Manifest-captured
capability MUST be named literally in at least one FR.

## Format

- Numbered table with unique IDs: `FR-01`, `FR-02`, ... assigned in order.
- Two columns: `Number` and `Description`. The description is the FR body
  itself — no separate "title" column.
- `Description` always uses **"shall" language** ("The platform shall ...",
  "The solution shall ..."). Modal verbs `must`, `will`, `should` are not
  substitutes — only `shall` is contractually unambiguous.
- One behavior per FR. Compound FRs that bundle two unrelated capabilities
  with "and" are a defect (see Anti-pattern: Compound FR).

## Target

**Target: 10-20 FRs** for a typical 10-14 week engagement. This is a FLOOR
and a SOFT design target — never a hard cap. When the Manifest covers more
capabilities than the soft target accommodates, exceed it (per
Self-sufficiency Rule 3 in `sow-shared/references/style-guide.md`).

## Required FR shape

Each FR description MUST name at least one of:

- A **specific system** the solution interacts with
  ("the customer's SAP S/4HANA ECC", "the Salesforce REST API v58", "the
  internal data lake on Cloud Storage")
- A **specific data flow** ("ingest CDC events from Oracle Database@Home into
  BigQuery", "stream audit records into Cloud Logging")
- A **specific API or behavior** ("authenticate users via OIDC against the
  customer's IdP", "produce a credit opinion summary using Gemini 2.5 Pro")

Generic capability statements ("The solution shall provide an API.") are a
defect — they do not name what API, doing what, for whom.

## Inferred-implicit FRs

Beyond the explicit Manifest items, infer the following implicit FRs unless
the Manifest already covers them. Each MUST be marked with the conversation-
language equivalent of `(inferred)` per
`sow-shared/references/language-rules.md`:

- Authentication / authorization (which IdP, which protocol, which scopes).
- Error handling at the integration boundaries (retry, dead-letter, fallback).
- Audit logging for material business actions.
- Data validation at every external-system ingress.
- Admin or operator monitoring view, if no observability FR is present.
- Edge cases the Manifest implies but does not state (rate limits, idempotency).

If you add an inferred FR, also surface it in the user-facing review with the
inference marker so the user can confirm or remove it.

## Cross-section coherence (FR ↔ Activities ↔ Deliverables)

Every FR should map to at least one Activity (which builds it) and one
Deliverable (which evidences it). This mapping is NOT in the FR text — but
the LLM that generates Activities and Deliverables (in `sow-delivery-plan`)
reads the FRs and must produce coverage. Generating FRs that no Activity
implements is a defect surfaced later by the validation critic.

## Anti-patterns (rejected)

| Anti-pattern | Why it fails | Fix |
|---|---|---|
| Generic capability ("The solution shall provide an API") | Could describe any project | Name the specific API, system, data flow, or behavior |
| Compound FR ("The solution shall ingest data AND notify users") | Two behaviors with different lifecycle, validators, and review needs | Split into two FRs |
| FR delegating to an external doc ("as listed in the capability matrix") | Violates Self-sufficiency Rule 1 (`sow-shared/references/style-guide.md`) | Translate items literally into N individual FRs |
| FR that restates an NFR ("The solution shall be secure") | NFRs handle qualitative attributes; FRs handle functional behavior | Move to NFR section, rewrite with quantifiable target |
| FR with no system named ("The solution shall integrate with the CRM") | Reader cannot tell which CRM, which API, which version | Name the system + version when known (per Self-sufficiency Rule 2) |
| FR using `will` or `must` instead of `shall` | Modal verb ambiguity creates contractual exposure | Use `shall` |
