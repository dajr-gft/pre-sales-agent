# Technology Stack + Architecture Description — quality calibration examples

Patterns from real SOWs approved by Google for DAF/PSF funding. Tech stack
descriptions are project-specific, NOT GCP product page copy. Architecture
descriptions justify design decisions, NOT just list services.

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