# Architecture Guide — SOW Generator

This reference governs how the agent reasons about architecture, selects GCP services, and generates architecture diagrams. Every rule here is binding. Load this file in Phase 2 Step 3 before generating any architecture content.

---

## Part 1 — Architectural Thinking Process

Before generating any architecture content or diagram, execute this reasoning sequence in order (Steps 1-5). Do NOT skip steps. Do NOT produce output before completing Step 5 (validation).

### Step 1 — Identify Architectural Layers

Every enterprise solution has layers. Map the project's requirements to these canonical layers:

| Layer | Question to answer | Example components |
|---|---|---|
| **Entry Point** | How do users/systems reach the solution? | Portal, Mobile App, API Gateway, Load Balancer |
| **Edge / Security** | What sits between the outside world and business logic? | Cloud Armor, IAP, API Gateway, Apigee |
| **Compute / Orchestration** | Where does the business logic run? | Cloud Run, Agent Engine, GKE, Cloud Functions |
| **AI / ML** | What models or AI services are used and how? | Vertex AI, Gemini, AutoML, Vertex AI Search |
| **Data / Storage** | Where is data persisted, cached, or queried? | Firestore, BigQuery, Cloud SQL, Cloud Storage, Memorystore |
| **Integration** | What external systems are consumed? How? | Customer APIs, third-party services, on-prem systems |
| **Observability** | How is the system monitored, logged, and alerted? | Cloud Monitoring, Cloud Logging |
| **Security / Identity** | How are credentials, secrets, and access managed? | Secret Manager (node), IAM (text only), KMS (text only) |

**Rule:** Every project MUST have at minimum: Entry Point, Compute, Data, and Integration layers. If the project uses AI/ML, that layer is also mandatory. Observability and Security layers should be included for any production-grade architecture — infer them from NFRs if the user didn't mention them explicitly.

### Step 2 — Map Requirements to Components

For each FR and NFR, identify which architectural component(s) fulfill it:

- FR mentions API consumption → Integration layer node + edge with protocol label
- FR mentions data persistence → Data layer node with specific GCP service
- NFR mentions encryption → **Text only** (mention TLS/AES-256 in description and edge labels — not a diagram node)
- NFR mentions logging/audit → Observability layer node (Cloud Logging)
- NFR mentions availability/SLA → Compute layer design (serverless vs. managed)
- NFR mentions access control → **Text only** (describe IAM policies in the architecture description — IAM is not a diagram node, it is a policy layer)

**Rule:** If an FR or NFR implies a GCP service that is NOT yet in the architecture, ADD it — as a diagram node if it is a runtime component, or as text-only if it is a policy/configuration layer. Cross-reference the full FR and NFR lists before finalizing.

### Step 3 — Identify Cross-Cutting Concerns

Cross-cutting concerns are services that touch multiple layers. They must appear in the **architecture description** even if the user never mentioned them. However, not all cross-cutting concerns become diagram nodes — some are policy layers described only in text:

| Concern | When to include | GCP Service | In diagram? |
|---|---|---|---|
| Logging | Always (any production system) | Cloud Logging | Yes — as node |
| Monitoring | Always (any production system) | Cloud Monitoring | Yes — as node |
| Secret management | When APIs require keys/credentials | Secret Manager | Yes — as node |
| Identity & access | When multiple services communicate | IAM | **No** — text and edge labels only |
| Encryption at rest | When NFR mentions data security | KMS (or note built-in) | No — text only |

**Rule:** For every external API the solution consumes, ask: "Where are the API credentials stored?" If the answer is not in the architecture → add Secret Manager.

**Rule:** For every compute service, ask: "How is this monitored?" If the answer is not in the architecture → add Cloud Monitoring + Cloud Logging.

### Step 4 — Define Data Flow

Before creating diagram edges, write out the primary data flow as a narrative chain:

```
[Entry Point] → [Edge/Security] → [Compute] → [External API 1] → [Compute] → [AI Service] → [Compute] → [Data Store] → [Compute] → [Entry Point]
```

This chain becomes the primary path in the diagram. Secondary flows (monitoring, logging, secret access) branch off the main path.

**Rule:** The diagram must tell a story. A reader should be able to trace the primary request/response flow from left to right (LR) or top to bottom (TB) without backtracking.

### Step 5 — Validate Before Output

**This step is mandatory. Do NOT produce any architecture output (text, table, or diagram) until all checks below pass.**

After completing Steps 1-4, you have a draft list of nodes, clusters, and edges. Before producing any output, walk through every node and every cluster and answer these questions:

**For EACH node, ask:**

1. **"Is this a runtime component that processes requests, stores data, or transforms information?"**
   - If YES → keep as diagram node.
   - If NO (it is a policy, configuration, or encryption layer — e.g., IAM, TLS, AES-256) → remove from diagram. Represent in textual description and/or edge labels only.

2. **"Does this node represent two different systems merged into one?"**
   - If YES (e.g., a GCP gateway + the external service behind it) → split into 2 separate nodes in their correct clusters.
   - If NO → keep as single node.

**For EACH cluster assignment, ask:**

3. **"Is this product's infrastructure hosted on Google Cloud?"**
   - If YES → the node MUST be in the Google Cloud cluster, regardless of who manages or administers it.
   - If NO (it runs on customer premises or third-party infrastructure) → place in the appropriate external cluster.

4. **"Is this node an entry point (user, portal, API consumer) sharing a cluster with third-party services (SaaS, payment gateways, credit bureaus)?"**
   - If YES → separate them. Entry points go in a "User / Consumer" cluster; third-party services go in a "Third-Party Services" cluster. They are never in the same cluster.

**For the diagram as a whole, ask:**

5. **"Can I trace the complete primary data flow from entry point to response?"** → If not, something is missing.
6. **"Does every edge have a protocol or data label?"** → If not, add labels.
7. **"Are there any nodes with no edges?"** → If yes, either connect them or remove them.

**If any check fails, fix it before proceeding.** Only after all checks pass, produce the three outputs (textual description, technology stack table, diagram).

---

## Part 2 — Diagram Construction Rules

### Cluster Strategy

Organize nodes into clusters by **responsibility zone**, following Google Cloud's official diagram conventions:

**Mandatory clusters:**
- **Google Cloud Platform** — all GCP services used by the solution (including services managed by the customer like Apigee X, Cloud Build, etc.)
- **Customer Environment** (or "[Customer Name] On-Premises") — systems running on the customer's own infrastructure: internal portals, legacy servers, proprietary databases, on-prem ERPs

**Conditional clusters (use when the architecture includes the corresponding node type):**
- **Third-Party / External** — external services not owned by the customer or GCP: SaaS products, payment gateways, credit bureaus, partner APIs
- **User / Consumer** — end users, devices, portals, mobile apps, or external API consumers that initiate the primary data flow (entry points)

**Cluster separation rule:** Entry points (users, portals, API consumers) and third-party services (SaaS, payment gateways, credit bureaus) serve fundamentally different roles — one initiates the flow, the other is consumed by it. They MUST be in separate clusters, even when both are outside GCP. Never group them in a single "External" cluster.

**Cluster naming conventions (aligned with Google Cloud official guidelines):**
- Use clear, descriptive names: "Google Cloud Platform", "Customer On-Premises", "Third-Party Services", "User Applications"
- For customer-specific clusters, prefer the customer name: "[Customer Name] — Internal Systems"
- Avoid generic names like "External" or "Other" — name the environment specifically. "External" as a standalone cluster name is a defect.
- **Automatic color coding:** The diagram tool auto-detects cluster type from the name and applies Google's official zone colors. Use these keywords in cluster names to trigger the correct color:
  - "Google Cloud" → blue (#E3F2FD)
  - "On-Premises" or "Internal" → warm gray (#EFEBE9)
  - "Third-Party" or "External" → teal (#E0F2F1)
  - "User" or "Consumer" or "Portal" → white (#FFFFFF)

**Recommended Google Cloud sub-clusters (use when ≥ 6 GCP nodes):**
- "Google Cloud — Compute & Orchestration"
- "Google Cloud — AI / ML"
- "Google Cloud — Data & Storage"
- "Google Cloud — Security & Identity"
- "Google Cloud — Observability"

**When to use a single Google Cloud cluster:** When the project has ≤ 5 GCP nodes total. Splitting into sub-clusters with 1 node each looks worse than one cluster.

**When to use sub-clusters:** When there are ≥ 6 GCP nodes. Group by responsibility. Each sub-cluster should have ≥ 2 nodes.

### Cluster Assignment Rule

Assign nodes to clusters based on **where the product runs**, NOT who manages it:
- **Google Cloud cluster**: ALL GCP products, even if managed by the customer. Examples: Apigee X, Cloud Build, BigQuery, Cloud SQL — these are GCP products regardless of who administers them.
- **Customer On-Premises / Internal cluster**: Only systems that run on the customer's own infrastructure — legacy servers, internal portals, proprietary databases, on-prem ERPs.
- **Third-Party cluster**: External services not owned by the customer or GCP — SaaS products, payment gateways, credit bureaus, partner APIs.
- **User / Consumer cluster**: Entry points that initiate the primary data flow — end-user applications, portals, mobile apps, API consumers. These are the starting nodes of the architecture, not integration targets.

**Common mistake 1:** Placing Apigee in the customer's on-prem cluster because "the customer manages it." Apigee X is a Google Cloud product — it belongs in the GCP cluster. The same applies to Cloud Build, BigQuery, or any GCP service the customer administers.

**Common mistake 2:** Grouping the client application (entry point) in the same cluster as third-party services like payment gateways or credit bureaus. The client initiates the flow; third-party services are consumed by it. They belong in separate clusters ("User Applications" vs. "Third-Party Services"), never in a shared "External" cluster.

### Node Granularity Rules

**Include as separate nodes:**
- Every GCP service that appears in the Technology Stack table (except IAM — see below)
- Every external system the solution integrates with
- Every entry point (user, portal, API consumer)
- **Gateway split**: When an external service is consumed through a GCP gateway, proxy, or API management layer (e.g., Apigee X, API Gateway, Cloud Endpoints), create 2 nodes: the GCP component in the Google Cloud cluster + the external service in its proper external cluster. Example: `[GCP Gateway] → [External System]` — two nodes, two clusters, one edge between them with the protocol label. Do NOT merge them into a single node, even when the external service is "accessed via" the gateway.

**NEVER include as diagram nodes:**
- **IAM** — it is a policy layer, not a runtime component. It does not process requests, store data, or participate in the data flow. Represent it only in the textual description and optionally in edge labels (e.g., "Auth via IAM"). Creating an IAM node with edges distorts the diagram layout.
- **Built-in encryption** (TLS, AES-256) — mention in edge labels or NFR, not as a node
- **Generic concepts** ("Security", "Governance") — use specific services

### Edge Rules

Every edge in the diagram corresponds to a data-flow sentence in the architecture description (see Part 3). This section has two layers: **Edge Derivation** (how to turn description sentences into edges) and **Edge Hygiene** (what every edge must have once it exists).

#### Edge Derivation — from description to spec

Re-read the architecture description literally. Build edges by extracting from that text — do not infer edges from how components "typically" interact in reference architectures.

- **One edge per data-flow sentence.** Each sentence that describes data moving between components produces one or more edges (one per hop in that sentence).

- **Honor the hops the description names.** When a sentence routes a call through an intermediary — trigger words include "routed through", "via", "fronted by", "proxied by", "orchestrated by", "published to", "consumed through", "brokered by" — emit one edge per hop. Never a shortcut that bypasses the intermediary.
  Pattern: description says *"Backend calls System X through Gateway G"* → edges are `Backend → G` and `G → X`. Never `Backend → X`.

- **Honor the hops the description omits.** An intermediary mediates only the flows the description assigns to it, not every integration of its upstream callers. The presence of a gateway, broker, or proxy elsewhere in the description does not make it a mandatory hop everywhere.
  Pattern: description says *"Backend calls System Y directly"* while *"Backend calls System X through Gateway G"* appears in a different sentence → edges are `Backend → Y` and, separately, `Backend → G` and `G → X`. Do not insert G into the Y flow.

- **Labels come from the description.** Every edge label is the protocol or data mechanism named in the corresponding sentence (`REST API`, `gRPC`, `HTTPS`, `Pub/Sub`, `Batch (CSV)`, `CDC`, `SQL`). If a sentence names no protocol, the protocol is missing from the description — fix the description first, do not invent one in the spec.

#### Edge Hygiene

- Every edge MUST have a label describing the protocol or data type: `REST API`, `gRPC`, `Pub/Sub`, `SQL`, `Batch (CSV)`, `Streaming`, `HTTPS`, etc.
- For external API consumption, include the version or identifier when known: `REST v3.2`, `API via Apigee`.
- Monitoring/Logging connections: use short functional labels (`logs`, `metrics`) to reduce visual noise — the tool does not support dashed edges.
- **Max edges per node:** if > 5, consider whether the node should be decomposed into multiple nodes, or whether some edges are cross-cutting and can be noted textually instead of drawn (e.g., "everything logs to Cloud Logging" → single annotation rather than N edges).

### Node Labeling Rules

Each node has two independent fields: `service` (the `GcpServiceEnum`, which selects the icon) and `label` (the text shown under the icon). They serve different purposes — treat them separately.

**The `service` field** selects the icon. Pick the most specific enum available (`CLOUD_RUN`, `FIRESTORE`, `GEMINI`, `APIGEE`, `LOGGING`, `MONITORING`, etc.). Never use `GENERIC` for a GCP service — only for non-GCP systems with no visual match. The icon is what tells the reader *which GCP product* is in play.

**The `label` field** is the project-specific functional role this component plays in THIS architecture — what the component *does for this customer*, not what the product is. The icon already communicates the product; the label communicates the responsibility.

Rules for `label`:

1. **Functional and project-specific.** Name the role, not the product. `Credit Analysis API`, `Opinion Store`, `Audit Trail`, `Session History`, `Credit Bureau API`.
2. **2–4 words.** Longer labels break the layout.
3. **Never generic.** Reject `Backend`, `Database`, `Model`, `Logs`, `Gateway`, `API`, `Server`, `Storage` as standalone labels. Generic label = defect. If the functional role is unclear, re-read Phase 1 discovery and the FRs to find what this component actually does for the project.
4. **Never repeat the service name.** The icon already shows it. `Cloud Run Backend` is wrong. `Credit Analysis API` is right.
5. **External systems: include the system name and version when known.** Examples across domains: `Salesforce REST API v58`, `SAP S/4HANA ECC`, `Stripe Payments API v2023-10-16`, `Internal CRM Connector`.

**Pattern:**
- `service=CLOUD_RUN`, `label="Credit Analysis API"` → icon shows Cloud Run, label shows what it does in this project.
- `service=FIRESTORE`, `label="Opinion Store"` → icon shows Firestore, label shows what it stores.
- `service=GEMINI`, `label="Credit Opinion Generator"` → icon shows Gemini, label shows the role.

**Anti-pattern:**
- `service=CLOUD_RUN`, `label="Backend"` → generic. Defect.
- `service=FIRESTORE`, `label="Database"` → generic. Defect.
- `service=GEMINI`, `label="Model"` → generic. Defect.
- `service=CLOUD_RUN`, `label="Cloud Run Backend API"` → repeats the product name. Defect.

### Direction Selection

| Architecture type | Direction | Rationale |
|---|---|---|
| Request/response pipeline (A calls B calls C) | `LR` | Natural reading flow |
| Layered architecture (frontend → backend → data) | `TB` | Layers stack vertically |
| Hub-and-spoke (orchestrator calls many services) | `LR` | Reduces crossing edges |
| ≥ 4 external integrations on one side | `LR` | External systems line up vertically on the right |

### Layout Optimization

- **Prefer linear chains** over hub-and-spoke: if the backend calls external services sequentially (or data flows through them in order), chain them literally: `Backend → System A → Backend → System B → Backend → AI Service`. This is preferable to three parallel edges `Backend → System A`, `Backend → System B`, `Backend → AI Service`, which suggest parallel calls when the flow is actually sequential.
- **When hub-and-spoke is unavoidable** (orchestrator truly calls services in parallel), place the hub in the center with spokes radiating out. Use `TB` direction so spokes fan horizontally.
- **Cross-cutting services** (Monitoring, Logging) go at the bottom or side, connected to the main compute node only — not to every node individually.

---

## Part 3 — Architecture Description Rules

The textual architecture description accompanies the diagram. It must:

1. **Follow the data flow narrative** from Step 4 — describe the architecture in the order data moves through it, not as a list of services.

2. **Justify every service choice** by referencing a specific FR, NFR, or design decision:
   - BAD: "Cloud Run hosts the backend API." (what does it do in THIS project?)
   - GOOD: "Cloud Run was selected as the compute layer because the solution requires serverless autoscaling to handle variable request volumes (NFR-01: 99.5% availability) without dedicated infrastructure management."

3. **Explain integration patterns**, not just integration targets:
   - BAD: "The solution integrates with the customer's CRM."
   - GOOD: "The solution consumes the customer's CRM REST API (v4) to extract account records on demand. API credentials are stored in Secret Manager and rotated automatically. Responses are validated against a predefined schema before being passed to the AI layer."

4. **Address cross-cutting concerns** in a dedicated paragraph:
   - "Observability is provided by Cloud Logging for structured audit logs (satisfying FR-09) and Cloud Monitoring for SLA tracking against the 99.5% availability target (NFR-01). All inter-service authentication uses Workload Identity with least-privilege IAM roles (NFR-05)."

### Self-Test (apply before presenting)

For each GCP service in the architecture, ask:
1. "Which FR or NFR does this service satisfy?" → If none, remove it.
2. "Could this description appear on the GCP product page?" → If yes, rewrite with project-specific context.
3. "Is this service in the Technology Stack table?" → If not, add it (or remove the service from the architecture).

For the architecture as a whole, ask:
4. "Can I trace a complete request from entry to response using only the described components?" → If not, something is missing.
5. "Are API credentials, logging, and monitoring accounted for?" → If not, add the missing cross-cutting components.

---

## Part 4 — Technology Stack Table Rules

The Technology Stack table must be consistent with the architecture description and diagram.

**Consistency rules:**
- Every GCP service in the Architecture Description → MUST appear in the table.
- Every GCP service in the table → MUST appear in the diagram as a node.
- Every GCP service in the diagram → MUST appear in the table.
- Violation of any of these is a defect.

**Description rules:**
- Each service description must be specific to THIS project.
- Anti-pattern: "Serverless compute platform" → this is the GCP product page.
- Pattern: "Hosts the credit analysis orchestration API, autoscaling from 0 to handle variable daily request volumes of ~60 requests/day (1,200/month)."

---

## Part 5 — Minimum Component Checklist

Before finalizing the architecture, verify against this checklist. Check every applicable item:

### Always required
- [ ] At least one Entry Point node (user, portal, system consumer) in its own "User / Consumer" cluster — never grouped with third-party services
- [ ] At least one Compute node (Cloud Run, GKE, Agent Engine, etc.)
- [ ] At least one Data/Storage node (Firestore, BigQuery, Cloud SQL, etc.)
- [ ] Cloud Logging node or textual mention (for any system with audit NFRs)
- [ ] Cloud Monitoring node or textual mention (for any system with SLA NFRs)

### Required when consuming external APIs
- [ ] Each truly external system as a separate node in an external/on-prem cluster (e.g., customer's legacy ERP, SaaS product, partner API). Note: if the external API is consumed through a GCP gateway (e.g., Apigee X), the gateway node belongs in the Google Cloud cluster, not the external cluster.
- [ ] Secret Manager for API credential storage (unless customer manages credentials entirely)
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
- [ ] Network boundary notation (VPC, Cloud Armor) for security-sensitive projects

---

## Part 6 — Common Anti-Patterns

| Anti-pattern | Why it fails | Fix |
|---|---|---|
| Only 3-4 GCP nodes for a 10+ FR project | Architecture doesn't cover all requirements | Run Step 2 again — map every FR/NFR to a component |
| No monitoring or logging in diagram | Unrealistic — every production system needs observability | Add Cloud Logging + Cloud Monitoring |
| Secret Manager missing when consuming 3+ external APIs | Where are the API keys stored? | Add Secret Manager node |
| "Google Cloud" as single cluster with 8+ nodes | Visual clutter, no logical grouping | Split into responsibility-based sub-clusters |
| Hub-and-spoke with > 5 spokes | Unreadable layout | Linearize the primary data flow, branch secondary flows |
| Edge labels missing | Reader can't understand the integration pattern | Every edge must have a protocol/data label |
| Architecture description is a bullet list of services | No design reasoning, could be any project | Rewrite as data-flow narrative with justifications |
| GCP product (e.g., Apigee, Cloud Build, BigQuery) placed in customer on-prem cluster | GCP products run on Google Cloud infrastructure regardless of who manages them | Move to the Google Cloud cluster — on-prem is only for truly on-premises systems |
| IAM as a standalone node with edges | IAM is a policy layer, not a runtime component — creates visual noise and stretches layout | Remove as node; mention in architecture description or edge labels instead |
| Generic node labels (`Backend`, `Database`, `Model`, `Gateway`) | Labels must describe the component's role in THIS project, not the product category — the icon already shows the product | Rename to functional role (e.g., `Credit Analysis API`, `Opinion Store`, `Credit Bureau API`) per Part 2 labeling rules |
| Shortcut edges that skip an intermediary named in the description | Diagram drifts from the architecture text — reader sees a different integration pattern than what was written | Apply Part 2 → "Edge Derivation": one edge per hop the description names |
| Intermediary inserted into every external flow (even ones the description sends directly) | Gateway/broker becomes a fantasy hub; diagram contradicts the description | Apply Part 2 → "Edge Derivation": an intermediary mediates only the flows assigned to it in the description |
| Entry point (user, portal, client app) grouped in the same cluster as third-party services | Conflates flow initiators with integration targets — wrong color, wrong semantics, confusing layout | Separate into "User / Consumer" cluster (white) for entry points and "Third-Party Services" cluster (teal) for consumed services. Never use a shared "External" cluster for both. |

---

## Part 7 — Structural Audit (enforced by tool)

The `generate_architecture_diagram` tool runs a deterministic structural audit against the spec before rendering. The audit is mechanical and invisible — you do not emit an audit block, list, or JSON anywhere in the conversation.

The tool requires four arguments that together form the audit surface: `nodes`, `edges`, `architecture_description` (the text from sub-step 1b), and `technology_stack` (the table from sub-step 1c). Pass all four on every call — the audit cross-checks them against each other.

### Tool behavior

- If all BLOCKER checks pass, the tool returns success and the diagram is rendered.
- If any BLOCKER check fails, the tool returns a `ToolError` listing the defects. You then:
  1. Silently revise the offending artifact: (1b) description, (1c) technology stack, or (1d) diagram spec.
  2. Call `generate_architecture_diagram` again with the corrected arguments.
  3. Do not mention the audit, the failures, or the retry to the user.
- WARNING failures are logged but do not block. You do not need to react to them during the conversation.
- Maximum 3 consecutive retries. If the tool still fails after the third attempt, describe the remaining defects to the user in the conversation language and ask how to proceed.

### What the audit checks

The audit enforces rules from Parts 2–6 mechanically. You do not need to mentally evaluate each check — focus on producing a high-quality description, table, and spec that naturally satisfy those rules. In particular:

- Node labels must be functional and project-specific (Part 2 → Node Labeling Rules).
- IAM must never appear as a diagram node (Part 2 → Node Granularity Rules).
- Every edge must have a protocol/data label (Part 2 → Edge Hygiene).
- Entry points must not share a cluster with third-party services (Part 2 → Cluster Strategy).
- GCP products must sit in the Google Cloud cluster (Part 2 → Cluster Assignment Rule).
- Every GCP service mentioned in the description must also appear in the Technology Stack table and as a diagram node (Part 4).
- At minimum one Entry Point, one Compute, and one Data node must be present (Part 5).

### What NOT to do

- Never write `<architecture_audit>` in any output.
- Never list check IDs or statuses in the conversation.
- Never say "All checks passed" or "Running self-audit."
- If the user asks how the architecture is validated, describe the process conceptually in prose. Do not reproduce any checklist.