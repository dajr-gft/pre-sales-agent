# Diagram Specification — binding rules

These rules govern how nodes, edges, and the narrative description are built
once the reasoning sequence (`references/reasoning-rules.md`) is complete.
They apply to every architecture artifact produced by `sow-architecture`.

---

## Part A — Cluster Model

Each node declares two cluster fields.

**`parent_cluster`** — required enum, one of four values:

| Value | Use for |
|---|---|
| `Google Cloud Platform` | ALL GCP products, even when customer-administered (Apigee, Cloud Build, BigQuery remain GCP regardless of who manages them). |
| `Customer Environment` | On-premises or internal systems running on the customer's own infrastructure. |
| `Third-Party Services` | SaaS products, payment gateways, credit bureaus, partner APIs. |
| `User / Consumer` | Entry points that initiate the flow: end users, portals, mobile apps, API consumers. |

The tool rejects incoherent service/zone pairs. Vertex AI cannot be placed
outside `Google Cloud Platform`, and a `Client/User` service cannot be placed
outside `User / Consumer`.

**`sub_cluster`** — optional free-form string. Creates a named sub-group
rendered as a box inside `parent_cluster`. Leave null when a single top-level
zone suffices.

| Inside | When to set | Example labels |
|---|---|---|
| `Google Cloud Platform` | ≥ 6 GCP nodes total | `AI / ML`, `Data & Storage`, `Observability`, `Compute & Orchestration`, `Security & Identity` |
| `Customer Environment` | Multiple distinct internal systems | `Legacy ERP`, `Internal Data Lake`, `Identity Provider` |
| `Third-Party Services` | Multiple services grouped by category | `Payment Providers`, `Credit Bureaus` |
| `User / Consumer` | Rare — only when multiple distinct entry-point groups exist | Usually leave null |

Each sub-cluster should have ≥ 2 nodes. A sub-cluster with a single node adds
visual noise without value.

---

## Part B — Node Granularity Rules

**Include as separate nodes:**

- Every GCP service that appears in the Technology Stack table (except IAM —
  see below)
- Every external system the solution integrates with
- Every entry point (user, portal, API consumer)
- **Gateway split**: when an external service is consumed through a GCP
  gateway, proxy, or API management layer (e.g., Apigee X, API Gateway, Cloud
  Endpoints), create 2 nodes: the GCP component with
  `parent_cluster: Google Cloud Platform` + the external service with
  `parent_cluster: Customer Environment` or `Third-Party Services`. Example:
  `[GCP Gateway] → [External System]` — two nodes, two zones, one edge between
  them with the protocol label. Do NOT merge them into a single node.

**NEVER include as diagram nodes:**

- **IAM** — it is a policy layer, not a runtime component. It does not process
  requests, store data, or participate in the data flow. Represent it only in
  the textual description and optionally in edge labels (e.g., "Auth via
  IAM"). Creating an IAM node with edges distorts the diagram layout.
- **Built-in encryption** (TLS, AES-256) — mention in edge labels or NFR, not
  as a node.
- **Generic concepts** ("Security", "Governance") — use specific services.

---

## Part C — Edge Rules

Every edge in the diagram corresponds to a data-flow sentence in the
architecture description (Part E). This section has two layers: **Edge
Derivation** (how to turn description sentences into edges) and **Edge
Hygiene** (what every edge must have once it exists).

### Edge Derivation — from description to spec

Re-read the architecture description literally. Build edges by extracting
from that text — do not infer edges from how components "typically" interact
in reference architectures.

- **One edge per data-flow sentence.** Each sentence that describes data
  moving between components produces one or more edges (one per hop in that
  sentence).

- **Honor the hops the description names.** When a sentence routes a call
  through an intermediary — trigger words include "routed through", "via",
  "fronted by", "proxied by", "orchestrated by", "published to", "consumed
  through", "brokered by" — emit one edge per hop. Never a shortcut that
  bypasses the intermediary. Pattern: description says *"Backend calls System
  X through Gateway G"* → edges are `Backend → G` and `G → X`. Never
  `Backend → X`.

- **Honor the hops the description omits.** An intermediary mediates only the
  flows the description assigns to it, not every integration of its upstream
  callers. The presence of a gateway, broker, or proxy elsewhere in the
  description does not make it a mandatory hop everywhere. Pattern:
  description says *"Backend calls System Y directly"* while *"Backend calls
  System X through Gateway G"* appears in a different sentence → edges are
  `Backend → Y` and, separately, `Backend → G` and `G → X`. Do not insert G
  into the Y flow.

- **Labels come from the description.** Every edge label is the protocol or
  data mechanism named in the corresponding sentence (`REST API`, `gRPC`,
  `HTTPS`, `Pub/Sub`, `Batch (CSV)`, `CDC`, `SQL`). If a sentence names no
  protocol, the protocol is missing from the description — fix the description
  first, do not invent one in the spec.

### Edge Hygiene

- Every edge MUST have a label describing the protocol or data type: `REST
  API`, `gRPC`, `Pub/Sub`, `SQL`, `Batch (CSV)`, `Streaming`, `HTTPS`, etc.
- For external API consumption, include the version or identifier when known:
  `REST v3.2`, `API via Apigee`.
- Monitoring/Logging connections: use short functional labels (`logs`,
  `metrics`) to reduce visual noise — the tool does not support dashed edges.
- **Max edges per node:** if > 5, consider whether the node should be
  decomposed into multiple nodes, or whether some edges are cross-cutting and
  can be noted textually instead of drawn (e.g., "everything logs to Cloud
  Logging" → single annotation rather than N edges).

---

## Part D — Node Labeling Rules

Each node has two independent fields: `service` (the `GcpServiceEnum`, which
selects the icon) and `label` (the text shown under the icon). They serve
different purposes — treat them separately.

**The `service` field** selects the icon. Pick the most specific enum
available (`CLOUD_RUN`, `FIRESTORE`, `GEMINI`, `APIGEE`, `LOGGING`,
`MONITORING`, etc.). Never use `GENERIC` for a GCP service — only for non-GCP
systems with no visual match. The icon is what tells the reader *which GCP
product* is in play.

**The `label` field** is the project-specific functional role this component
plays in THIS architecture — what the component *does for this customer*, not
what the product is. The icon already communicates the product; the label
communicates the responsibility.

Rules for `label`:

1. **Functional and project-specific.** Name the role, not the product.
   `Credit Analysis API`, `Opinion Store`, `Audit Trail`, `Session History`,
   `Credit Bureau API`.
2. **2–4 words.** Longer labels break the layout.
3. **Never generic.** Reject `Backend`, `Database`, `Model`, `Logs`,
   `Gateway`, `API`, `Server`, `Storage` as standalone labels. Generic label
   = defect. If the functional role is unclear, re-read Phase 1 discovery and
   the FRs to find what this component actually does for the project.
4. **Never repeat the service name.** The icon already shows it. `Cloud Run
   Backend` is wrong. `Credit Analysis API` is right.
5. **External systems: include the system name and version when known.**
   Examples across domains: `Salesforce REST API v58`, `SAP S/4HANA ECC`,
   `Stripe Payments API v2023-10-16`, `Internal CRM Connector`.

**Pattern:**

- `service=CLOUD_RUN`, `label="Credit Analysis API"` → icon shows Cloud Run,
  label shows what it does in this project.
- `service=FIRESTORE`, `label="Opinion Store"` → icon shows Firestore, label
  shows what it stores.
- `service=GEMINI`, `label="Credit Opinion Generator"` → icon shows Gemini,
  label shows the role.

**Anti-pattern:**

- `service=CLOUD_RUN`, `label="Backend"` → generic. Defect.
- `service=FIRESTORE`, `label="Database"` → generic. Defect.
- `service=GEMINI`, `label="Model"` → generic. Defect.
- `service=CLOUD_RUN`, `label="Cloud Run Backend API"` → repeats the product
  name. Defect.

---

## Part E — Architecture Description Rules

The textual architecture description accompanies the diagram and is the
**single source of truth** from which the technology stack table and the
diagram spec are derived. It must:

1. **Follow the data flow narrative** — describe the architecture in the order
   data moves through it, not as a list of services.

2. **Justify every service choice** by referencing a specific FR, NFR, or
   design decision:
   - BAD: "Cloud Run hosts the backend API." (what does it do in THIS project?)
   - GOOD: "Cloud Run was selected as the compute layer because the solution
     requires serverless autoscaling to handle variable request volumes
     (NFR-01: 99.5% availability) without dedicated infrastructure management."

3. **Explain integration patterns**, not just integration targets:
   - BAD: "The solution integrates with the customer's CRM."
   - GOOD: "The solution consumes the customer's CRM REST API (v4) to extract
     account records on demand. API credentials are stored in Secret Manager
     and rotated automatically. Responses are validated against a predefined
     schema before being passed to the AI layer."

4. **Address cross-cutting concerns** in a dedicated paragraph:
   - "Observability is provided by Cloud Logging for structured audit logs
     (satisfying FR-09) and Cloud Monitoring for SLA tracking against the
     99.5% availability target (NFR-01). All inter-service authentication uses
     Workload Identity with least-privilege IAM roles (NFR-05)."

5. **Apply paragraph breaks** per `sow-shared/references/style-guide.md` →
   "Paragraph breaks in long-form narrative". Typically 2-3 paragraphs:
   primary data flow → key service justifications → cross-cutting concerns.

### Architecture Description Self-Test (apply before emitting)

For each GCP service in the architecture, ask:

1. "Which FR or NFR does this service satisfy?" → If none, remove it.
2. "Could this description appear on the GCP product page?" → If yes, rewrite
   with project-specific context.
3. "Is this service in the Technology Stack table?" → If not, add it (or
   remove the service from the architecture).

For the architecture as a whole, ask:

4. "Can I trace a complete request from entry to response using only the
   described components?" → If not, something is missing.
5. "Are API credentials, logging, and monitoring accounted for?" → If not,
   add the missing cross-cutting components.

---

## Part F — Direction & Layout

| Architecture type | Direction | Rationale |
|---|---|---|
| Request/response pipeline (A calls B calls C) | `LR` | Natural reading flow |
| Layered architecture (frontend → backend → data) | `TB` | Layers stack vertically |
| Hub-and-spoke (orchestrator calls many services) | `LR` | Reduces crossing edges |
| ≥ 4 external integrations on one side | `LR` | External systems line up vertically on the right |

**Layout optimization:**

- **Prefer linear chains** over hub-and-spoke: if the backend calls external
  services sequentially (or data flows through them in order), chain them
  literally: `Backend → System A → Backend → System B → Backend → AI
  Service`. This is preferable to three parallel edges
  `Backend → System A`, `Backend → System B`, `Backend → AI Service`, which
  suggest parallel calls when the flow is actually sequential.
- **When hub-and-spoke is unavoidable** (orchestrator truly calls services in
  parallel), place the hub in the center with spokes radiating out. Use `TB`
  direction so spokes fan horizontally.
- **Cross-cutting services** (Monitoring, Logging) go at the bottom or side,
  connected to the main compute node only — not to every node individually.

---

## Part G — Minimum Component Checklist

Before finalizing the architecture, verify against this checklist. Check
every applicable item.

### Always required

- [ ] At least one Entry Point node (service `Client/User` or `Users`,
  `parent_cluster: User / Consumer`)
- [ ] At least one Compute node (Cloud Run, GKE, Agent Engine, etc.)
- [ ] At least one Data/Storage node (Firestore, BigQuery, Cloud SQL, etc.)
- [ ] Cloud Logging node or textual mention (for any system with audit NFRs)
- [ ] Cloud Monitoring node or textual mention (for any system with SLA NFRs)

### Required when consuming external APIs

- [ ] Each external system as a separate node with
  `parent_cluster: Customer Environment` (on-prem/internal) or
  `Third-Party Services` (SaaS/partner APIs). If the external API is consumed
  through a GCP gateway (e.g., Apigee X), the gateway node uses
  `parent_cluster: Google Cloud Platform`.
- [ ] Secret Manager for API credential storage (unless customer manages
  credentials entirely)
- [ ] Edge labels showing protocol and version for each integration

### Required for AI/ML projects

- [ ] AI/ML service node (Vertex AI, Gemini, etc.)
- [ ] Data flow showing what data reaches the AI service and in what format
- [ ] If RAG: Vertex AI Search or vector store node + data source node

### Required for agent-based projects

- [ ] Agent Engine or orchestration runtime as central compute node
- [ ] Session/state persistence node (Firestore, Memorystore)
- [ ] LLM service node separate from orchestration node
- [ ] If multi-agent: each agent as a separate node or a sub-cluster

### Optional but recommended

- [ ] IAM notation (can be textual rather than a node)
- [ ] KMS or encryption notation for data-at-rest requirements
- [ ] Network boundary notation (VPC, Cloud Armor) for security-sensitive
  projects

---

## Part H — Common Anti-Patterns

| Anti-pattern | Why it fails | Fix |
|---|---|---|
| Only 3-4 GCP nodes for a 10+ FR project | Architecture doesn't cover all requirements | Run reasoning Step 2 again — map every FR/NFR to a component |
| No monitoring or logging in diagram | Unrealistic — every production system needs observability | Add Cloud Logging + Cloud Monitoring |
| Secret Manager missing when consuming 3+ external APIs | Where are the API keys stored? | Add Secret Manager node |
| "Google Cloud" as single cluster with 8+ nodes | Visual clutter, no logical grouping | Split into responsibility-based sub-clusters |
| Hub-and-spoke with > 5 spokes | Unreadable layout | Linearize the primary data flow, branch secondary flows |
| Edge labels missing | Reader can't understand the integration pattern | Every edge must have a protocol/data label |
| Architecture description is a bullet list of services | No design reasoning, could be any project | Rewrite as data-flow narrative with justifications |
| IAM as a standalone node with edges | IAM is a policy layer, not a runtime component — creates visual noise and stretches layout | Remove as node; mention in architecture description or edge labels instead |
| Generic node labels (`Backend`, `Database`, `Model`, `Gateway`) | Labels must describe the component's role in THIS project, not the product category — the icon already shows the product | Rename to functional role per Part D labeling rules |
| Shortcut edges that skip an intermediary named in the description | Diagram drifts from the architecture text — reader sees a different integration pattern than what was written | Apply Part C → "Edge Derivation": one edge per hop the description names |
| Intermediary inserted into every external flow (even ones the description sends directly) | Gateway/broker becomes a fantasy hub; diagram contradicts the description | Apply Part C → "Edge Derivation": an intermediary mediates only the flows assigned to it in the description |
