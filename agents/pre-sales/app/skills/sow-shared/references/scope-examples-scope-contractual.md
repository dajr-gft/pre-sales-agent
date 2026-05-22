# OOS + Assumptions + CR Policy — quality calibration examples

Patterns from real SOWs approved by Google for DAF/PSF funding. Match this
level of specificity, professionalism, and rigor when producing
`out_of_scope`, `assumptions`, and `change_request_policy_text`. Note
especially the consequence-clause shape on assumptions and the
scope-boundary verbs on OOS items.


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

