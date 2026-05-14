# Architectural Thinking Process — binding reasoning rules

Before generating any architecture content or diagram, execute this reasoning
sequence in order (Steps 1-5). Do NOT skip steps. Do NOT produce output
before completing Step 5 (validation).

## Step 1 — Identify Architectural Layers

Every enterprise solution has layers. Map the project's requirements to these
canonical layers:

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

**Rule:** Every project MUST have at minimum: Entry Point, Compute, Data, and
Integration layers. If the project uses AI/ML, that layer is also mandatory.
Observability and Security layers should be included for any production-grade
architecture — infer them from NFRs if the user didn't mention them explicitly.

## Step 2 — Map Requirements to Components

For each FR and NFR, identify which architectural component(s) fulfill it:

- FR mentions API consumption → Integration layer node + edge with protocol label
- FR mentions data persistence → Data layer node with specific GCP service
- NFR mentions encryption → **Text only** (mention TLS/AES-256 in description
  and edge labels — not a diagram node)
- NFR mentions logging/audit → Observability layer node (Cloud Logging)
- NFR mentions availability/SLA → Compute layer design (serverless vs. managed)
- NFR mentions access control → **Text only** (describe IAM policies in the
  architecture description — IAM is not a diagram node, it is a policy layer)

**Rule:** If an FR or NFR implies a GCP service that is NOT yet in the
architecture, ADD it — as a diagram node if it is a runtime component, or as
text-only if it is a policy/configuration layer. Cross-reference the full FR
and NFR lists before finalizing.

## Step 3 — Identify Cross-Cutting Concerns

Cross-cutting concerns are services that touch multiple layers. They must
appear in the **architecture description** even if the user never mentioned
them. However, not all cross-cutting concerns become diagram nodes — some are
policy layers described only in text:

| Concern | When to include | GCP Service | In diagram? |
|---|---|---|---|
| Logging | Always (any production system) | Cloud Logging | Yes — as node |
| Monitoring | Always (any production system) | Cloud Monitoring | Yes — as node |
| Secret management | When APIs require keys/credentials | Secret Manager | Yes — as node |
| Identity & access | When multiple services communicate | IAM | **No** — text and edge labels only |
| Encryption at rest | When NFR mentions data security | KMS (or note built-in) | No — text only |

**Rule:** For every external API the solution consumes, ask: "Where are the
API credentials stored?" If the answer is not in the architecture → add Secret
Manager.

**Rule:** For every compute service, ask: "How is this monitored?" If the
answer is not in the architecture → add Cloud Monitoring + Cloud Logging.

## Step 4 — Define Data Flow

Before creating diagram edges, write out the primary data flow as a narrative
chain:

```
[Entry Point] → [Edge/Security] → [Compute] → [External API 1] → [Compute] → [AI Service] → [Compute] → [Data Store] → [Compute] → [Entry Point]
```

This chain becomes the primary path in the diagram. Secondary flows
(monitoring, logging, secret access) branch off the main path.

**Rule:** The diagram must tell a story. A reader should be able to trace the
primary request/response flow from left to right (LR) or top to bottom (TB)
without backtracking.

## Step 5 — Validate Before Output

**This step is mandatory. Do NOT produce any architecture output (text, table,
or diagram) until all checks below pass.**

After completing Steps 1-4, you have a draft list of nodes and edges. Before
producing any output, walk through every node and answer these questions:

**For EACH node, ask:**

1. **"Is this a runtime component that processes requests, stores data, or
   transforms information?"**
   - If YES → keep as diagram node.
   - If NO (it is a policy, configuration, or encryption layer — e.g., IAM,
     TLS, AES-256) → remove from diagram. Represent in textual description
     and/or edge labels only.

2. **"Does this node represent two different systems merged into one?"**
   - If YES (e.g., a GCP gateway + the external service behind it) → split
     into 2 separate nodes with appropriate `parent_cluster` each.
   - If NO → keep as single node.

3. **"Is this product's infrastructure hosted on Google Cloud?"**
   - If YES → declare `parent_cluster: Google Cloud Platform`, regardless of
     who manages or administers it.
   - If NO → declare `parent_cluster: Customer Environment` (on-premises or
     internal) or `Third-Party Services` (SaaS, partner APIs).

4. **"Is this node an entry point (user, portal, API consumer)?"**
   - If YES → declare `parent_cluster: User / Consumer`. Entry points are
     always separate from third-party services.

**For the diagram as a whole, ask:**

5. **"Can I trace the complete primary data flow from entry point to
   response?"** → If not, something is missing.
6. **"Does every edge have a protocol or data label?"** → If not, add labels.
7. **"Are there any nodes with no edges?"** → If yes, either connect them or
   remove them.

**If any check fails, fix it before proceeding.** Only after all checks pass,
produce the three outputs (textual description, technology stack table,
diagram).
