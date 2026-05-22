# Activities + Deliverables — quality calibration examples

Patterns from real SOWs approved by Google for DAF/PSF funding. Match this
level of technical specificity. The Bad example for tasks ("could describe
any project") is the most common defect — the self-test is mandatory.


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

