# Project Roles — binding rules

The Roles section lists the partner-side and customer-side roles required
to execute the engagement. Stored as two lists in `sow_data`:
`partner_roles` and `customer_roles`. Each list is rendered as a table.

## Table shape

Three columns per row:

| Column | Content |
|---|---|
| **Role** | The role title (e.g., `Project Manager`, `Solution Architect`, `Cloud Data Engineer`). |
| **Description** | 2-3 lines of concrete responsibilities — NOT just the role title rephrased. |
| **Organization** | Either `Partner` (for `partner_roles`) or `Customer` (for `customer_roles`). |

The JSON shape is:

```
{
  "role": "Solution Architect",
  "responsibilities": "Designs the end-to-end solution architecture and ensures alignment with GCP best practices. Reviews all technical deliverables and provides guidance on service selection across workshops."
}
```

## Hard rules

### Partner side

- **MUST include a Project Manager.** The PM is responsible for timeline
  management, risk tracking, stakeholder communication, status reporting,
  and the formal change-request workflow. A SOW that lists only technical
  partner roles is structurally incomplete.
- **MUST include a Solution Architect.** The Architect owns architectural
  alignment with GCP best practices, technical deliverable review, and
  service-selection rationale.
- **Engineering specializations** beyond PM + Architect are added based
  on the FR/NFR coverage (see
  `references/effort-heuristics.md` → Roles → Engagement-size coherence).
- **No Google roles.** Google Account Managers, Customer Engineers, or
  Field Solution Architects are not partner roles. They do not appear in
  this list.
- **No hours / rates.** Never include effort hours, hourly rates, or rate
  cards. The role list communicates who is needed, not how much they cost.

### Customer side

- **MUST include an Executive Sponsor** (or equivalent: "Project Sponsor",
  "Senior Stakeholder"). The Sponsor is the escalation point and the
  decision authority for scope / timeline / budget changes.
- **MUST include at least one Subject Matter Expert** (SME). The SME is
  the source of business-rule knowledge during requirement validation.
- **Decision authority is explicit** where applicable. "Must have authority
  to validate and sign off requirements" / "Final escalation point for
  scope changes" — these phrases live in the `responsibilities` column,
  not in the role title.

## Description rules — 2-3 lines, concrete responsibilities

Each `responsibilities` value is 2-3 sentences naming concrete actions:

- **Good**: `"Responsible for managing the project timeline, risk
  mitigation, and stakeholder communication. Conducts weekly status
  meetings and tracks milestone delivery. Acts as primary point of contact
  for the customer's PMO."`
- **Bad**: `"Project Manager"` — same as the role title, no information
  added.
- **Bad**: `"Manages the project."` — single tautological sentence; reader
  cannot tell what the PM actually does on this engagement.
- **Bad**: `"Responsible for project success."` — non-actionable.

## Anti-patterns

| Anti-pattern | Why it fails | Fix |
|---|---|---|
| Partner list with no PM | Workflow has no owner; CR process has no enforcer | Add PM with the canonical responsibilities |
| Customer list with no Executive Sponsor | Escalation path is missing | Add Sponsor with explicit decision authority |
| Role description = role title | No information added | Expand to 2-3 sentences of concrete responsibilities |
| Google role listed as partner | Google is not the partner; this is the contractual surface between partner and customer | Remove the Google role |
| Hours / rates in description | Direct violation of the no-rates rule | Remove the figures; effort is communicated through phase duration |
| "Responsible for project success" (generic) | Non-actionable, applies to any role | Rewrite with role-specific actions |
| Customer role: "Will be available as needed" | No SLA, no decision authority | Specify the response SLA and what authority the role carries |

## Cross-section coherence

- Each `partner_role.role` should correspond to a real specialization
  required by the FR/NFR coverage. A 14-week ML implementation without a
  data engineer or ML engineer in `partner_roles` is a defect.
- Customer roles should align with the assumptions in
  `assumptions` (produced by `sow-scope-boundaries`) — if an assumption
  requires "customer SME response within 3 business days", the customer
  SME role's responsibilities must reflect that SLA.
