# Architecture Guide — SOW Generator

This reference governs how the agent reasons about architecture, selects GCP services, and generates architecture diagrams. Every rule here is binding. Load this file in Phase 2 Step 3 before generating any architecture content.

---

## Part 1 — Architectural Thinking Process

Before generating any architecture content or diagram, execute this reasoning sequence. Do NOT skip steps. Do NOT jump directly to listing GCP services.

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
| **Security / Identity** | How are credentials, secrets, and access managed? | IAM, Secret Manager, KMS |

**Rule:** Every project MUST have at minimum: Entry Point, Compute, Data, and Integration layers. If the project uses AI/ML, that layer is also mandatory. Observability and Security layers should be included for any production-grade architecture — infer them from NFRs if the user didn't mention them explicitly.

### Step 2 — Map Requirements to Components

For each FR and NFR, identify which architectural component(s) fulfill it:

- FR mentions API consumption → Integration layer node + edge with protocol label
- FR mentions data persistence → Data layer node with specific GCP service
- NFR mentions encryption → Security layer (KMS or built-in encryption notation)
- NFR mentions logging/audit → Observability layer (Cloud Logging)
- NFR mentions availability/SLA → Compute layer design (serverless vs. managed)
- NFR mentions access control → Security layer (IAM, service accounts)

**Rule:** If an FR or NFR implies a GCP service that is NOT yet in the architecture, ADD it. The architecture must cover every requirement. Cross-reference the full FR and NFR lists before finalizing.

### Step 3 — Identify Cross-Cutting Concerns

Cross-cutting concerns are services that touch multiple layers. They must appear in the diagram even if the user never mentioned them:

| Concern | When to include | GCP Service |
|---|---|---|
| Logging | Always (any production system) | Cloud Logging |
| Monitoring | Always (any production system) | Cloud Monitoring |
| Secret management | When APIs require keys/credentials | Secret Manager |
| Identity & access | When multiple services communicate | IAM |
| Encryption at rest | When NFR mentions data security | KMS (or note built-in) |

**Rule:** For every external API the solution consumes, ask: "Where are the API credentials stored?" If the answer is not in the architecture → add Secret Manager.

**Rule:** For every compute service, ask: "How is this monitored?" If the answer is not in the architecture → add Cloud Monitoring + Cloud Logging.

### Step 4 — Define Data Flow

Before creating diagram edges, write out the primary data flow as a narrative chain:

```
[Entry Point] → [Edge/Security] → [Compute] → [External API 1] → [Compute] → [AI Service] → [Compute] → [Data Store] → [Compute] → [Entry Point]
```

This chain becomes the primary path in the diagram. Secondary flows (monitoring, logging, secret access) branch off the main path.

**Rule:** The diagram must tell a story. A reader should be able to trace the primary request/response flow from left to right (LR) or top to bottom (TB) without backtracking.

---

## Part 2 — Diagram Construction Rules

### Cluster Strategy

Organize nodes into clusters by **responsibility zone**, not by "Google Cloud vs. External":

**Mandatory clusters:**
- One cluster per external environment (e.g., "Banco Safra On-Premises", "Third-Party Services")
- At least one Google Cloud cluster

**Recommended Google Cloud sub-clusters (use when ≥ 6 GCP nodes):**
- "Google Cloud — Compute & Orchestration"
- "Google Cloud — AI / ML"
- "Google Cloud — Data & Storage"
- "Google Cloud — Security & Identity"
- "Google Cloud — Observability"

**When to use a single Google Cloud cluster:** When the project has ≤ 5 GCP nodes total. Splitting into sub-clusters with 1 node each looks worse than one cluster.

**When to use sub-clusters:** When there are ≥ 6 GCP nodes. Group by responsibility. Each sub-cluster should have ≥ 2 nodes.

### Node Granularity Rules

**Include as separate nodes:**
- Every GCP service that appears in the Technology Stack table
- Every external system the solution integrates with
- Every entry point (user, portal, API consumer)

**Do NOT include as separate nodes:**
- IAM (it's a policy layer, not a runtime component) — unless the architecture has a dedicated identity proxy (IAP)
- Built-in encryption (TLS, AES-256) — mention in edge labels or NFR, not as a node
- Generic concepts ("Security", "Governance") — use specific services

**Exception:** If IAM, KMS, or Secret Manager are central to the architecture (e.g., the project IS about security infrastructure), include them as nodes.

### Edge Rules

- Every edge MUST have a label describing the protocol or data type: `REST API`, `gRPC`, `Pub/Sub`, `SQL`, `Batch (CSV)`, `Streaming`, `HTTPS`, etc.
- For external API consumption, include the version or identifier if known: `REST v3.2`, `API via Apigee`
- Monitoring/Logging connections: use dashed-style or unlabeled edges to reduce visual noise (the tool does not support dashed edges — use a short label like `logs` or `metrics` instead)
- **Max edges per node:** If a node has > 5 edges, consider whether it should be decomposed into multiple nodes or whether some edges are implicit (e.g., "everything logs to Cloud Logging" can be noted textually instead of drawn)

### Direction Selection

| Architecture type | Direction | Rationale |
|---|---|---|
| Request/response pipeline (A calls B calls C) | `LR` | Natural reading flow |
| Layered architecture (frontend → backend → data) | `TB` | Layers stack vertically |
| Hub-and-spoke (orchestrator calls many services) | `LR` | Reduces crossing edges |
| ≥ 4 external integrations on one side | `LR` | External systems line up vertically on the right |

### Layout Optimization

- **Prefer linear chains** over hub-and-spoke: if the Backend API calls APIs sequentially (or the data flows through them), chain them: `Backend → Core Banking → Backend → Serasa → Backend → AI` rather than `Backend → Core Banking`, `Backend → Serasa`, `Backend → AI`.
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
   - BAD: "The solution integrates with Core Banking."
   - GOOD: "The solution consumes the Core Banking REST v3.2 API to extract historical financial data. API credentials are stored in Secret Manager and rotated automatically. Responses are validated against a predefined schema before being passed to the AI layer."

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
- [ ] At least one Entry Point node (user, portal, system consumer)
- [ ] At least one Compute node (Cloud Run, GKE, Agent Engine, etc.)
- [ ] At least one Data/Storage node (Firestore, BigQuery, Cloud SQL, etc.)
- [ ] Cloud Logging node or textual mention (for any system with audit NFRs)
- [ ] Cloud Monitoring node or textual mention (for any system with SLA NFRs)

### Required when consuming external APIs
- [ ] Each external API as a separate node in an external cluster
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