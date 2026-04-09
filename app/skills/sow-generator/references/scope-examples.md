# SOW Quality Reference — Patterns from Real Engagements

Quality patterns extracted from real SOWs approved by Google for DAF/PSF funding.
Use as calibration — match this level of specificity, professionalism, and rigor.

---

## Executive Summary Patterns

### Good: Business-first, technical-second, outcome-driven
> "Banco BTG Pactual is accelerating its product delivery capabilities by adopting
> an advanced, AI-driven agent designed to support Product Managers throughout the
> entire product lifecycle — from discovery to backlog refinement and delivery
> tracking."

**Why it works**: Customer's strategic initiative first, specific capability named,
lifecycle scope defined in one sentence.

### Good: Scope boundary in the summary itself
> "This engagement is strictly limited to the assessment and definition phase.
> No development, deployment, or configuration of agents is included in this
> Statement of Work."

**Why it works**: Reviewer knows exactly what this SOW covers and doesn't before
reading details.

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

---

## Out-of-Scope Patterns

Each item: complete, self-contained, unambiguous. Use "including but not limited to"
for broad coverage with named technologies. Target: 20-30 items.

### Category: Excluded functionality
> "Development of any functionality, behavior, workflow, or agent capability not
> explicitly defined within this SOW."

### Category: Excluded integrations
> "Integration with Jira, Azure DevOps, or any other issue tracking, project
> management, or task management platform. Such integrations are explicitly
> excluded from this engagement."

> "Integration with systems, tools, platforms, repositories, or services not
> explicitly identified in this SOW."

### Category: Excluded API work
> "Development, modification, remediation, stabilization, or enhancement of APIs,
> connectors, middleware, adapters, or integration layers for any [Customer]
> internal or external system."

> "Any responsibility for API authentication, authorization, network configuration,
> firewall rules, API gateways, rate limits, quotas, or access provisioning, which
> remain entirely under [Customer] responsibility."

> "Any retry, fallback, caching, throttling, resiliency, or compensation logic
> to mitigate unavailable, unstable, undocumented, or rate-limited APIs."

### Category: Excluded UI/UX
> "Development or customization of any user interface, including Streamlit,
> custom web applications, portals, or mobile applications. [Interaction layer]
> is the sole end-user interaction layer."

### Category: Excluded infrastructure and CI/CD
> "Implementation of CI/CD pipelines, build automation, Terraform configurations,
> or infrastructure provisioning of any kind."

> "Infrastructure outside of Google Cloud."

### Category: Excluded data work
> "Ingestion, creation, cleansing, normalization, enrichment, or migration of
> [Customer] internal documentation or datasets."

> "Test data creation, cleansing, anonymization, or migration of historical datasets."

> "Any responsibility for data quality, completeness, correctness, freshness, or
> consistency of documentation consumed via Vertex AI Search or RAG."

### Category: Excluded testing
> "Penetration testing, vulnerability scanning, red-team exercises, performance,
> load, stress, or scalability testing frameworks or scripts."

### Category: Excluded post-delivery
> "Ongoing maintenance, support, corrective actions, or continuous evolution of
> the solution after knowledge transfer."

> "Assisted production support, hypercare, or operational support beyond any
> explicitly agreed initial stabilization period."

### Category: Excluded compliance
> "Formal compliance certifications, regulatory approvals, legal sign-offs,
> or audit attestations."

### Category: Excluded code alignment
> "Merging with, refactoring, or aligning with other [Customer] projects,
> initiatives, programs, or incidents in progress."

### Category: Excluded post-approval revisions
> "Revisions to approved deliverables (Consolidated Backlogs, User Stories,
> Technical Solution Proposals) after formal sign-off by [Customer].
> Post-approval changes require a formal Change Request and may impact
> timeline and costs."

> "Re-execution of workshops due to [Customer] stakeholder unavailability,
> incomplete participation, or insufficient preparation. If key stakeholders
> are absent during scheduled sessions, [Partner] will document the gaps and
> proceed. Rescheduled workshops will require timeline extensions and may
> result in additional costs."

### Category: Excluded training
> "Training for end users or business users beyond technical knowledge transfer
> sessions with [Customer] technical teams."

### Category: Catch-all
> "Any additions, enhancements, or modifications to the scope of this project
> without a formally approved Change Request."

### Disambiguation rule
When an Out-of-Scope item could appear to contradict an in-scope FR, the item
MUST distinguish between what is excluded and what is included, with a
cross-reference to the relevant FR. Apply ONLY when both exist in the document.
If the FR was removed, write the OOS item normally without disambiguation.

---

## Assumptions & Prerequisites Patterns

Every customer-dependent assumption includes consequence if not met.
Target: 15-25 items covering all categories from the style guide.

### Category: Platform prerequisites (with timing and consequence)
> "Google Gemini Enterprise must be active, licensed, and available within
> [Customer]'s Google Workspace tenant prior to the start of WS04. Any
> unavailability or non-industrialization will result in timeline extension
> and additional cost."

> "Agent Engine must be industrialized, approved, and available within
> [Customer]'s Google Cloud tenant prior to the start of WS01. Any
> unavailability or non-industrialization will result in timeline extension
> and additional cost."

### Category: Access and credentials
> "[Customer] must provide all required access credentials and service accounts
> fully provisioned and validated prior to project kickoff. Delays in credential
> provisioning will result in proportional timeline extension and additional cost."

> "Any permission or access request must be processed within 1 business day
> to prevent project delays."

### Category: Stakeholder availability
> "All [Customer] stakeholders required for decision-making, validation, and
> feedback must be available within the agreed timelines. Delays caused by
> stakeholder unavailability will result in proportional timeline extension
> and additional cost."

### Category: Workshop cancellation
> "If a scheduled workshop is cancelled or postponed by [Customer] with less
> than 1 business day's notice, [Partner] reserves the right to reschedule at
> the next available slot. Repeated cancellations (2 or more) will trigger a
> formal timeline review and may result in additional costs."

### Category: Data and documentation
> "[Customer] will provide access to v1 production feedback, issue logs, known
> defects, and any existing backlog documentation prior to the start of business
> workshops. Delays in providing this information will directly impact workshop
> quality and may extend the assessment timeline."

> "API documentation for OneTrust, CMDB, and COUPA (including any changes
> since v1) will be made available for the technical workshops. [Partner]
> requires read-only access to API specifications; no live system access
> is needed during the assessment."

### Category: Deliverable review and approval
> "Each deliverable will be submitted to [Customer] for review. [Customer] must
> provide feedback or approval within 3 business days. Absence of feedback
> within this period constitutes acceptance."

> "Once a deliverable is formally approved by [Customer], its content is
> considered frozen. Any modifications to approved deliverables after sign-off
> will require a formal Change Request, which may impact timeline and costs."

### Category: Blocker resolution
> "Any blocker that prevents [Partner] from progressing on assessment activities
> must be resolved by [Customer] within a maximum of 2 business days. Delays
> exceeding this limit will require proportional timeline extensions and may
> result in additional costs."

### Category: Data quality and responsibility
> "[Partner] will not be responsible for scope gaps resulting from incomplete,
> inaccurate, or delayed information provided by [Customer]. The quality of
> outputs is directly dependent on the quality and completeness of inputs."

> "All data made accessible to [Partner] must be sanitized, anonymized where
> applicable, and compliant with [Customer] security, privacy, and regulatory
> policies."

### Category: Scope protection
> "This SOW covers exclusively the Assessment Phase: business workshops,
> technical workshops, backlog consolidation, User Story development, and
> Technical Solution Proposals. No development, testing, deployment, or
> production support activities are included."

> "Any requirement, feature, or capability not explicitly documented and
> validated during the workshops of this Assessment Phase is out of scope.
> Requirements discovered after the formal sign-off will require a separate
> Change Request."

### Category: GenAI/ML acknowledgment
> "The assessment acknowledges that LLM models present inherent response
> variability. Technical Solution Proposals will address quality improvement
> strategies, but [Partner] does not guarantee 100% accuracy in AI Agent
> responses. The inherent limitations of GenAI models are risks accepted
> by [Customer]."

> "System performance projections depend directly on Google Cloud service
> performance and latency, which are outside [Partner]'s control."

### Category: Partner liability
> "In the event of delays attributable solely to [Partner], [Partner]
> commits to completing the execution of the defined scope at no additional
> cost to [Customer]."

### Category: Timeline impact summary
> "⚠ TIMELINE AND COST IMPACT: Any delay caused by late provision of
> required information, missing or restricted access, non-industrialized
> or unavailable tools and environments, or insufficient stakeholder
> availability will result in: 1. A proportional extension of the project
> timeline; AND 2. Additional costs associated with the extension.
> These impacts are independent of [Partner] performance and will be
> formalized through a Change Request signed by both parties."

---

## Change Request Policy Pattern

> "⚠ CRITICAL: Change Request Policy
>
> No work on out-of-scope items will be performed without an approved Change Request
> signed by both parties. Verbal agreements are not binding. [Partner] reserves the
> right to pause work if scope changes are requested without formal approval.
>
> Any change to this SOW shall not take effect unless and until a Change Request is
> fully executed by Customer and Partner."

---

## Risks Patterns

Each risk names a specific system, technology, or stakeholder. Mitigation is actionable.

**Data platform example:**
> "Risk: Source system data quality from legacy SAP ERP may contain inconsistencies,
> missing fields, or undocumented business rules that are only discovered during
> pipeline development.
> Mitigation: Allocate a dedicated data validation sprint in Phase 2. Implement
> automated data quality checks at ingestion. Escalate critical data quality issues
> to the customer's Data Architect for resolution within 3 business days."

**Access provisioning example:**
> "Risk: Delays in customer provisioning of access credentials, VPN tunnels, or
> service accounts for on-premise source systems may block pipeline development.
> Mitigation: Deliver a pre-kickoff access checklist to the customer with specific
> deadlines. Flag access blockers in weekly status reports. Reserve 1 week of buffer
> in the timeline for access-related delays."

**ML accuracy example:**
> "Risk: The demand forecasting model may not achieve the target MAPE < 15% on the
> first training iteration due to insufficient or noisy historical data.
> Mitigation: Plan for 2-3 model iteration cycles within the Phase 3 timeline.
> Define a minimum viable accuracy threshold (e.g., MAPE < 20%) for initial
> deployment, with a roadmap for improvement post-engagement."

**AI/agent example:**
> "Risk: Gemini model responses may not meet business accuracy expectations for
> complex, domain-specific queries without extensive prompt engineering.
> Mitigation: Include a dedicated prompt tuning and evaluation phase. Define
> acceptance criteria for response quality with concrete test cases before UAT."

**Integration example:**
> "Risk: Integration with customer's existing Confluence workspace may be
> constrained by permission models or API rate limits not identified during
> the assessment phase.
> Mitigation: Conduct a technical spike on Confluence API integration in the
> first week of development. Document API constraints and escalate blockers early."

---

## Activities Patterns

### Good: Phase description with clear boundary
> "This engagement is strictly limited to the Assessment Phase: business workshops,
> technical workshops, backlog consolidation, User Story development, and Technical
> Solution Proposals. No development, testing, deployment, or production support
> activities are included."

### Bad: Tasks too shallow — could describe any project
> - "Set up GCP environment and services"
> - "Develop and test Dataflow ingestion pipelines"
> - "Perform data integration testing"

**Why it fails**: No technical specificity. Doesn't say *what* is being ingested,
*from where*, *how*, or *what constitutes success*.

### Good: Tasks with technical depth specific to THIS project
> - "Design and implement Dataflow streaming pipelines for near real-time ingestion
>   of POS transaction events via the existing REST API, including error handling
>   and dead-letter queue configuration"
> - "Develop batch extraction jobs for SAP inventory and master data using SAP
>   standard connectors, with incremental load strategy based on change timestamps"
> - "Implement BigQuery data model with partitioning by transaction date and
>   clustering by product category, organized in Raw, Trusted, and Refined layers"
> - "Configure Cloud Composer DAGs for end-to-end pipeline orchestration with
>   dependency management, retry policies, and alerting on failure"
> - "Perform feature engineering on historical sales data, including seasonality
>   indicators, promotional flags, and store-level demand patterns"

**Why it works**: Each task names the specific system, the technical approach, and
the design decisions. A reader can understand what the team will actually do.

---

## Deliverables Patterns

### Good: Specific output per phase with format
> "Consolidated Incremental Backlog: Per-agent backlog with requirements mapped
> to sources (workshops, production feedback, gap analysis). Format: Document (.docx)"

### Good: Deliverable with acceptance criteria
> "Technical Solution Proposal: Per-agent architecture changes, Gemini prompt
> strategy, memory modifications, and integration requirements, validated
> against BV enterprise standards. Format: Document (.docx)"

---

## Technology Stack Table Pattern

### Bad: Generic GCP documentation descriptions
| GCP Service | Purpose in Architecture |
|---|---|
| Dataflow | Serverless data processing for ETL pipelines. |
| BigQuery | Centralized data warehouse for analytics. |
| Cloud Composer | Workflow orchestration service. |

**Why it fails**: These descriptions could be copied from the GCP product page.
They don't explain what each service does IN THIS PROJECT.

### Good: Project-specific descriptions (data platform)
| GCP Service | Purpose in Architecture |
|---|---|
| Dataflow | Batch and streaming ingestion engine processing SAP extracts and POS API events into the Cloud Storage landing zone, with built-in data validation. |
| BigQuery | Centralized data warehouse with medallion architecture (Raw, Trusted, Refined), partitioned by transaction date for query performance. |
| Cloud Composer | Orchestrates the end-to-end pipeline lifecycle, managing dependencies between ingestion, transformation, model retraining, and dashboard refresh. |
| Vertex AI | Training and serving environment for the demand forecasting model, consuming Refined-layer features from BigQuery. |

### Good: Project-specific descriptions (AI agent)
| GCP Service | Purpose in Architecture |
|---|---|
| Agent Engine | Primary execution runtime for the PM Agent. Manages agent orchestration, session lifecycle, conversational memory persistence, and interaction traceability natively. |
| Gemini Enterprise | End-user conversational interface. Product Managers interact with the PM Agent directly through Google Workspace. |
| Vertex AI Search | Similarity search and RAG enablement against indexed Confluence content repositories. |
| Firestore | Sessions and opinions persistence, execution checkpoints. |

---

## Architecture Description Pattern

### Bad: Lists components without justification
> "Data will be ingested via Dataflow, stored in Cloud Storage, processed into BigQuery,
> orchestrated by Cloud Composer, and visualized in Looker."

**Why it fails**: Describes the *what* but not the *why*. A reviewer cannot assess
whether the architecture is sound because no design decisions are explained.

### Good: Describes design decisions with justification
> "Dataflow was selected as the ingestion engine because the architecture requires both
> batch extraction from SAP (daily full loads) and streaming ingestion from POS APIs
> (near real-time events) within a single unified framework. Cloud Storage serves as
> the immutable landing zone, decoupling ingestion from transformation and enabling
> replay of raw data if reprocessing is needed. BigQuery's native partitioning and
> clustering capabilities support the query patterns required by the Looker dashboards
> — primarily time-range scans partitioned by transaction date and filtered by product
> category. Cloud Composer manages pipeline dependencies to ensure that downstream
> transformations only execute after upstream ingestion completes successfully."

**Why it works**: Each service choice is justified by a specific project requirement.
The reviewer understands not just what was chosen but why.

---

## Google Cloud Consumption Plan Pattern

### Bad: Single paragraph with approximate ranges
> "Post-implementation, the estimated Google Cloud consumption is projected to start
> at approximately $5,000/month, scaling to $8,000/month as data volume increases."

**Why it fails**: No per-service breakdown, no monthly granularity, no basis for the
estimates. Insufficient for PSF approval.

### Good: Monthly table with per-service breakdown

> | Month | BigQuery | Dataflow | Vertex AI | Composer | Storage | Looker | Total |
> |---|---|---|---|---|---|---|---|
> | 1 | $1,200 | $800 | $1,500 | $400 | $200 | $1,000 | $5,100 |
> | 2 | $1,200 | $800 | $500 | $400 | $250 | $1,000 | $4,150 |
> | 3 | $1,300 | $850 | $500 | $400 | $300 | $1,000 | $4,350 |
> | ... | ... | ... | ... | ... | ... | ... | ... |
> | 11 | $1,800 | $1,200 | $600 | $400 | $600 | $1,000 | $5,600 |
> | 12 | $1,600 | $1,000 | $600 | $400 | $650 | $1,000 | $5,250 |
>
> Notes: Month 1 includes Vertex AI training costs (~$1,500) for initial model
> development. Months 2+ reflect prediction-only costs (~$500). BigQuery estimates
> assume 5TB stored with ~2TB scanned/month. Storage grows ~50GB/month.

**Why it works**: Per-service breakdown shows the reviewer that costs are grounded in
real workload estimates. Monthly granularity shows growth trajectory. Notes explain
the assumptions behind the numbers.

---

## Key Engagement Details Table Pattern

| Detail | Value |
|---|---|
| Partner | [Partner full legal name] |
| Customer | [Customer full legal name] |
| Effective Date | Subject to Google PSF Approval |
| GCP Deployment | [Customer] Tenant ([Customer] billed for GCP consumption) |
| Service Delivery | Remote from [Country] |
| Pricing Model | Fixed Price ([DAF/PSF type]) |