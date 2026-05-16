# FR + NFR — quality calibration examples

Patterns from real SOWs approved by Google for DAF/PSF funding. Match this
level of specificity, professionalism, and rigor when producing
`functional_requirements` and `non_functional_requirements`. Note in
particular the binding Bad/Good Reliability pair — the FORBIDDEN production
availability commitment vs. the REQUIRED architectural-pattern phrasing.

---

## Functional Requirements Patterns

### Good: "Shall" language with specific technical context
> "FR06: The solution shall implement Retrieval-Augmented Generation (RAG) using
> Vertex AI Search to retrieve contextual information from [Customer]'s
> Confluence workspace, supporting governance, standards, and organizational
> context enrichment."

**Why it works**: Names exact technology, data source, and purpose.

### Good: Boundary-setting within a requirement
> "FR07: The solution shall be published as an Agent via Google Gemini Enterprise,
> enabling Product Managers to interact conversationally with the PM Agent through
> the Google Workspace interface already provisioned within [Customer]'s
> environment. No custom user interface will be developed."

**Why it works**: Defines what IS built AND what is NOT in the same requirement.

### Good: API consumption with clear responsibility boundary
> "FR01: The solution shall consume existing [Customer] OneTrust APIs to extract
> vendor assessment data, register comments and technical findings, and publish
> consolidated architecture opinions."

**Why it works**: Specifies "consume existing" — makes clear that the Partner uses
APIs, does not build them. The API development responsibility stays with the customer.

### Good: Session and state management with named technology
> "FR06: The solution shall use Google Cloud Firestore for session persistence,
> including session state, conversation history, tool invocations, checkpoints,
> and execution metadata."

**Why it works**: Names the exact persistence mechanism and lists what must be persisted.

### Good: Agent enhancement with specific scope
> "FR08: The COE/GenAI Reviewing Agent shall be enhanced with automatic Jira issue
> creation, Confluence publication, end-to-end automated workflow, prompt
> improvements for deterministic decision logic, and operational resilience controls."

**Why it works**: Lists exactly what "enhancement" means — not vague "improvements."

### Good: Bug fix with observable behavior
> "FR10: The Data Reviewing Agent shall be corrected to address intermittent
> 'No Response' outputs, incorrect review classification (score vs. result),
> and conditions with incorrect domain findings."

**Why it works**: Names the specific bugs being fixed, not just "bug fixes."

---

## Non-Functional Requirements Patterns

### Good: Security with specific standards
> "NFR01: Security: All data accessed by [Partner] must be sanitized and compliant
> with [Customer] security and privacy policies. Industry-standard encryption
> (TLS 1.3, AES-256) is required for data in transit and at rest."

### Good: Scope-limiting NFR
> "NFR04: Environments: Scope limited to DEV and UAT environments only. Production
> deployment is explicitly OUT OF SCOPE."

### Good: Tooling constraint
> "NFR03: Tooling: All development using exclusively Google Cloud native tools
> (Vertex AI, ADK, Agent Engine, Gemini Enterprise, Vertex AI Search). No custom
> integrations, third-party persistence layers, or non-native tools without
> explicit [Customer] consent and formal Change Request."

**Why it works**: Constrains the technology choices AND defines what happens if
a deviation is needed (Change Request).

### Good: Reliability as architecture, not availability commitment
> "NFR05: Reliability: The platform shall be architected for high availability
> using Cloud Run multi-region deployment across us-central1 and us-east1, Cloud
> SQL with automatic failover, and health checks on all managed services. Ongoing
> availability management and production SLA monitoring remain the Customer's
> responsibility post-handover."

**Why it works**: Describes what Partner IMPLEMENTS (multi-region deployment,
failover, health checks) and explicitly hands operational responsibility to the
Customer. No uptime percentage is committed. This is the REQUIRED phrasing
pattern for the Reliability pillar.

### Bad: Reliability NFR phrased as production service-level commitment
> "NFR05: Reliability: The platform shall maintain 99.9% uptime and guarantee
> service availability over a rolling 30-day window."

**Why it fails**: Partner does not operate the platform in production and cannot
commit to sustained availability. This NFR creates a contractual obligation
the Partner cannot enforce post-handover. It is a FORBIDDEN phrasing under the
consultancy scope rule. Rewrite as an architectural quality (see "Good: Reliability
as architecture" above).

