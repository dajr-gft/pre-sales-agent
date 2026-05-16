# Technology Stack Table Rules — binding

The Technology Stack table is one row per GCP service in the architecture. It
must be consistent with the architecture description and with the diagram
spec — those three artifacts are derived from the same source of truth
(the description from sub-step 1b).

## Consistency rules (three-way invariant)

- Every GCP service in the **architecture description** → MUST appear in the
  table.
- Every GCP service in the **table** → MUST appear in the diagram as a node.
- Every GCP service in the **diagram** → MUST appear in the table.

Violation of any of these is a defect surfaced by the structural audit (see
`references/audit-rules.md`).

## Row construction

Build one row per GCP service. Each row has two columns:

| Column | Content |
|---|---|
| **Service** | The GCP product name (e.g., `Cloud Run`, `BigQuery`, `Vertex AI`, `Secret Manager`). Match the casing of the official product name. |
| **Description** | The project-specific role this service plays — what it does for THIS customer in THIS engagement. |

## Description rules

Each service description must be:

- **Specific to THIS project.** Anti-pattern: "Serverless compute platform"
  → that is the GCP product page. Pattern: "Hosts the credit analysis
  orchestration API, autoscaling from 0 to handle variable daily request
  volumes."
- **Aligned with the architecture description.** If the description says
  Cloud Run "hosts the credit analysis API", the table row for Cloud Run
  must echo that role — not "general-purpose compute".
- **Reference FRs/NFRs when the role is requirement-driven.** Example:
  "Hosts the backend API, autoscaling per NFR-02 availability target without
  dedicated infrastructure management."
- **Free of marketing language.** No "powerful", "industry-leading",
  "best-in-class". Plain consulting prose.
- **Bounded.** 1-2 sentences per row. Multi-paragraph descriptions belong in
  the architecture description, not the table.

## What does NOT go in the table

- **IAM** — not a runtime component, never a row. Discuss IAM in the
  architecture description and in edge labels.
- **Built-in encryption** (TLS, AES-256) — discuss in NFRs and edge labels,
  not as a table row.
- **Non-GCP systems** — the customer's internal ERP, third-party SaaS, etc.
  These belong in the integrations section of the architecture description
  and the diagram, not the Technology Stack table.

## Anti-patterns

| Anti-pattern | Why it fails | Fix |
|---|---|---|
| Row described as the GCP product page | No project context; reader cannot tell what this service does HERE | Rewrite with the functional role and at least one FR/NFR anchor |
| Service in description but not in table | Audit will fail; the three-way invariant is broken | Add the row or remove the service from the description |
| Table row whose service does not appear in the diagram | Audit will fail | Add the diagram node or remove the row |
| IAM as a table row | IAM is a policy layer, not a runtime service | Remove the row; mention IAM in description / edge labels |
| Two rows for the same service with slightly different labels | The icon and audit collapse them; the duplicate row is noise | Keep one row and merge the descriptions if needed |
