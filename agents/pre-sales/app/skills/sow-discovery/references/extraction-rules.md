# Extraction Rules — Reference

This file is the rubric `sow-discovery` Phase 1 applies to every artifact. Load it once before starting Phase 1 and apply it consistently across the full artifact set.

It is the single most important reference for extraction quality. Inconsistent application across artifacts is the most common cause of missed items during reconciliation.

**Primitives must come from this file at extraction time, not from memory.** Each category below defines a closed list of required primitives. When extracting, you read the primitive names from the category section in this file and populate them on the item. Do not improvise primitive names from training memory — the schema is intentionally specific, and improvised primitives will not match what `sow-generator` consumes downstream. If you find yourself producing primitives without having this file open in the current session, stop and load it.

---

## How extraction works

For each artifact, walk the eight categories below. For every concrete item you find, capture:

- **`category`** — one of the eight names below.
- **`value`** — the canonical short name (system name, decision summary, target value).
- **`value_detail`** — paraphrase capturing context and qualifier.
- **`primitives`** — a structured dict of sub-fields specific to the category. **This is what makes extraction usable downstream.** Treat it as a checklist, not optional metadata.
- **`source`** — one or more `{artifact_id, anchor}` pairs. Anchor must be the finest precision available (page, section, slide, table cell, timestamp).
- **`confidence`** — `stated` or `implied`.

For each category below you will find:

1. **Purpose** — what this category captures and why the SOW will need it.
2. **Required primitives** — the sub-fields the model must populate inside `primitives`. Generic across project types.
3. **What counts as a concrete item** — the recognition criteria.
4. **What to ignore** — the noise the category attracts.
5. **Anchor type** — the location precision expected.
6. **Examples by engagement shape and archetype** — at least two contrasting examples per category, marked as either *real* (extracted from real approved SOWs the team has produced) or *illustrative* (grounded but synthesized for archetype coverage). Examples cover three engagement shapes (Assessment, Greenfield, Brownfield) and multiple GCP industrialization archetypes (AI agent, data platform, API modernization, landing zone, observability, FinOps, migration).

The order of categories below is also the order to apply them per artifact. Walking the artifact eight times (once per category) is correct behavior even when it feels redundant — different categories surface different signals from the same passage.

---

## Glossary — engagement shapes

The discovery captures the project's **engagement shape**. This is a structural primitive that propagates through every section the SOW will produce.

- **Assessment** — discovery, requirement elicitation, design documents, no implementation. Output is documentation (backlogs, user stories, technical solution proposals, architecture decision records). The SOW will have NO Technology Stack as a built artifact, and Success Criteria are document-acceptance based.
- **Greenfield Implementation** — building a new workload or platform on Google Cloud from scratch. The SOW will have a full Technology Stack table, deployment activities, and runtime acceptance criteria.
- **Brownfield Enhancement** — modifying, extending, or correcting an existing workload already on Google Cloud. The SOW references a baseline (often labeled "v1") and frames most FRs as "[Component] shall be enhanced with..." or "shall consume existing [System] APIs to...".
- **Migration** — moving an existing workload to Google Cloud or between Google Cloud regions/products. The SOW emphasizes source-to-target mappings, cutover strategy, parallel-run periods.
- **Foundation / Landing Zone** — establishing the GCP organization, IAM, networking, and security baseline for future workloads. The SOW emphasizes org structure, policy enforcement, network design.

When discovery cannot determine the shape from the artifacts, it goes to gaps as a hard gap — `sow-generator` cannot structure the output without it.

---

## Category 1: Identity

**Purpose.** Establishes who the SOW is for, what the project is called, who is paying, who the partners are, and what the engagement shape is. Identity primitives propagate to every subsequent section.

**Required primitives.**
- `customer` — full legal entity name where stated.
- `project_name` — codename or formal title.
- `funding_type` — DAF | PSF | T&M | Fixed Price | Pure SOW | not_stated.
- `partner` — implementing partner (typically GFT Technologies in this team's context, but capture as stated).
- `secondary_partners` — any third parties (e.g., Google PSO advising) and their roles.
- `engagement_shape` — assessment | greenfield | brownfield | migration | foundation | not_stated.
- `engagement_phase` — e.g., "Phase 2 of multi-phase engagement", "follow-up to v1", "initial engagement", or "not_stated".
- `sector` — customer's industry sector if stated.
- `geography` — deployment location and service-delivery region if stated.

**What counts as a concrete item.**
- Customer organization name, including the legal entity if mentioned (e.g., capture "Customer Inc." or "Customer S.A." rather than the shortened brand alone when both forms appear in source).
- Project name or codename (e.g., a one-word codename, a formal program title, or a phase identifier such as "Phase 2" or "v2").
- Funding type explicitly named.
- Partner names involved.
- Industry sector if explicitly stated.
- Geographic scope of deployment if explicitly stated.
- Engagement shape and phase markers (e.g., "Phase 2", "v1 baseline", "discovery only", "no development included").

**What to ignore.**
- Marketing taglines and value-prop language.
- Boilerplate company descriptions copied from the customer's website.
- Names of meeting attendees unless their role implies a project responsibility.

**Anchor type.** Page number + section heading. Cover pages get `page=1`. Executive summaries get the section heading from the document table of contents.

**Examples.**

*Real, AI Agent, Assessment shape.* From an approved SOW: "Banco BV intends to formally assess the feasibility, scope definition, and technical requirements for a domain-oriented multi-agent framework... This engagement is strictly limited to the assessment and definition phase. No development, deployment, or configuration of agents is included in this Statement of Work." Extracted as one Identity item with primitives `{customer: "Banco BV", project_name: "Architecture Technical Reviewer Agent Assessment", engagement_shape: "assessment", funding_type: "DAF", partner: "GFT Technologies"}`.

*Real, AI Agent, Brownfield shape.* From an approved SOW: "Banco BV is strengthening its governance... by adopting an advanced, AI-driven multi-agent architecture... the solution will refine and enhance five specialized Reviewing Agents based on stakeholder feedback and operational improvements." Note "refine and enhance five specialized Reviewing Agents" + Phase 2 framing. Extracted with primitives `{engagement_shape: "brownfield", engagement_phase: "Phase 2 — refinements to v1 multi-agent architecture", funding_type: "PSF"}`.

*Illustrative, Data platform, Greenfield shape.* A briefing reads: "Customer is building a unified analytics platform on Google Cloud to replace siloed reporting in regional units. The new platform will ingest from SAP, POS systems, and CRM into a centralized BigQuery warehouse." Extracted with primitives `{engagement_shape: "greenfield", project_name: "Unified Analytics Platform", funding_type: "DAF"}`. Note: in absence of a stated funding type, this primitive would be `not_stated` and become a hard gap rather than a guess.

**Anti-patterns.**
- "We are excited to partner with [Customer]" → marketing fluff, do not extract as a separate item if the customer name has already been captured.
- A shortened brand or acronym alone, when the artifact also gives the fuller form elsewhere — capture the fuller form in `value` and note the abbreviation in `notes`.

---

## Category 2: Briefing

**Purpose.** Captures the problem statement, the proposed solution direction, and the technical approach. This is the strategic narrative that will feed the SOW's Executive Summary.

**Required primitives.**
- `problem_statement` — what the customer needs to solve and why.
- `business_capability` — the named capability the solution provides (e.g., "AI-driven Product Discovery", "near-real-time POS data ingestion", "centralized identity for GCP organization"). Always capture in *capability* terms, not in implementation terms.
- `delivery_mode` — the *how* at a high level: "autonomous generation", "RAG-enriched response", "batch ETL pipeline", "self-service dashboard", "managed service migration".
- `business_outcomes` — stated outcomes (e.g., "reduce time-to-backlog by 40%", "consolidate reporting from N regions"). When no quantitative outcome is stated, capture the qualitative one.
- `pilot_scope` — when the engagement includes a pilot or proof-of-value phase, capture which workload it covers.

**What counts as a concrete item.**
- Problem statements ("the customer needs X because Y").
- Proposed solution direction ("we will build a platform that does X").
- Technical approach choices ("using Vertex AI and ADK", "using Dataflow + BigQuery + Looker").
- Stated business outcomes.
- Pilot project descriptions if the engagement includes one.

**What to ignore.**
- Feature wishlists without rationale.
- Generic statements like "modernize the platform" with no specifics.

**Anchor type.** Page + paragraph index, or section + paragraph.

**Examples.**

*Real, AI Agent, Greenfield.* From an approved SOW: "Banco BTG Pactual is accelerating its product delivery capabilities by adopting an advanced, AI-driven agent designed to support Product Managers throughout the entire product lifecycle — from discovery to backlog refinement and delivery tracking." Extracted with primitives `{business_capability: "AI-assisted product management across the product lifecycle (discovery → backlog → delivery tracking)", delivery_mode: "autonomous Epic/Feature/User Story generation with RAG-enriched context from Confluence", problem_statement: "accelerate product delivery capabilities", business_outcomes: "support Product Managers throughout the entire product lifecycle"}`.

*Illustrative, Data platform, Greenfield.* A briefing reads: "Retail customer needs unified visibility into sales performance across 200 stores. Current state has SAP for inventory, custom POS for transactions, and Excel for reporting — analyst cycles are 5+ days." Extracted with primitives `{problem_statement: "siloed sales data across SAP/POS/Excel; analyst cycles exceed 5 days", business_capability: "unified sales analytics across 200 stores", delivery_mode: "batch + streaming ingestion into BigQuery medallion architecture, surfaced via Looker", business_outcomes: "reduce analyst cycle time from 5+ days to near-real-time"}`.

*Illustrative, Migration, Brownfield.* A briefing reads: "Customer is migrating its legacy on-prem PostgreSQL inventory database to Cloud SQL, with parallel-run validation before cutover. Pilot covers the warehouse-management workload only." Extracted with primitives `{problem_statement: "on-prem PostgreSQL maintenance burden", business_capability: "managed PostgreSQL on Google Cloud", delivery_mode: "lift-and-shift migration with parallel-run validation", pilot_scope: "warehouse-management workload only", business_outcomes: "reduce DBA operational burden"}`.

**Anti-patterns.**
- "Leverage cutting-edge AI to drive transformation" → consultancy fluff, no concrete content. Skip.

---

## Category 3: Integrations & Data Sources

**Purpose.** The single highest-leverage category. Customer-side appendices, capability matrices, and architecture diagrams enumerate every system, API, and data flow the project must connect to. Missing items here cascades into wrong assumptions throughout the SOW. **This is the category most likely to be under-extracted because models pattern-match to "common" integrations and stop walking the source.**

**Required primitives.**
- `system_name` — proper name as written in source.
- `direction` — source | target | bidirectional. ("Source" = customer system feeds the solution; "target" = solution writes to the customer system; "bidirectional" = both.)
- `operations` — comma-separated list of verbs the solution performs against the system (e.g., "extract assessments, register comments, publish opinions, update status"; or "daily full extract, incremental load via change timestamps").
- `data_class` — the kind of data flowing (e.g., "vendor assessment data and AVF technical findings"; "transactional sales records"; "vendor master data"; "user identity and role assignments"; "log events"; "model artifacts").
- `protocol` — REST | gRPC | batch file | CDC | event stream | SDK | message queue | SAP connector | other-named — exactly as stated when known; mark `not_specified` when the source does not say.
- `ownership` — `existing — customer` | `existing — third party` | `to_be_built` | `existing — partner platform`. This primitive prevents the SOW from mistakenly committing the partner to building APIs that already exist on the customer side.
- `criticality` — `core_in_scope` | `referenced_only` | `excluded` if the source explicitly distinguishes integrations that are core scope from those mentioned for context.

**What counts as a concrete item.**
- Every named system, platform, vendor, or service.
- Every named channel (WhatsApp, voice, web, app, email, file drop).
- Every named identity provider or auth mechanism (IAM, SSO, OTP, biometric, mTLS).
- Every named integration pattern or protocol.
- Every data class mentioned.
- Every internal or shared infrastructure component the project depends on (existing BigQuery dataset, shared GKE cluster, central VPC, shared Vertex AI Search index).

**What to ignore.**
- Generic descriptions like "internal systems" without specific names — these go to `gaps.ambiguities` for clarification, not to `extracted_items`.
- GCP services that the SOW will introduce as part of the solution architecture (those belong in technology decisions, not in integrations to existing systems). Exception: a shared GCP service the customer already operates and the solution must connect to — that IS an integration.

**Anchor type.** When the artifact has a structured list (table, bullet list, capability matrix), the anchor includes the table cell or bullet position (e.g., "p.4 / Capabilities Matrix / row 7"). When the mention is in prose, page + sentence-level anchor.

**Granularity rules — binding, not advisory.**

Integration items are the most frequently collapsed in practice. The model tends to merge multiple distinct subjects into a single item when they appear together in source text. The rules below are enforced at SKILL.md Phase 1.1 enumeration time and again at Phase 1.3 reconciliation, regardless of project domain or archetype.

The principle: **one concept, one item.** Connectives in source text — commas, slashes, "and", "or", "/", "e", "y" — between distinct subjects (system names, channels, identity mechanisms, compliance frameworks, stakeholders, environments, regions) are signals of multiplicity, not signals of a grouped umbrella item.

Operational tests, applied at extraction time:

- **Comma test.** If your `value` field contains a comma between two distinct nouns, split into separate items. Apply regardless of how source phrased them together.
- **Connective test.** If your `value` field contains " and ", " e ", " y ", " / ", or "&" between two distinct subjects, split.
- **Acronym-group test.** Source text of the form "Concept (X/Y/Z/W)" or "Concept including X, Y, Z" lists members of the umbrella concept. Members are individual items; the umbrella label is editorial framing, not a system. Both the members and the umbrella may warrant items, but in different categories — the umbrella often becomes a Briefing or NFR item, the members become Integrations items.
- **Visual layout test.** A bullet list of N items produces N items. A capability matrix with N labeled rows produces close to N items (minus header/category-label rows). A diagram with N labeled boxes produces close to N items. Visual structure dictates count; do not summarize visual elements into prose like "the diagram shows the platform components".

*Illustrative examples* (these are concrete instantiations of the above principles, drawn from a real project; the principles apply identically across all project domains):

- Multiple distinct subjects listed together as a single line in a diagram or table (e.g., three product names labeling one architectural layer) → one item per distinct subject. The fact that the diagram visually grouped them is editorial layout, not a single subject.
- Multiple options listed in one bullet under an umbrella label (e.g., a single bullet listing N customer-facing channels of a platform, or N supported authentication mechanisms, or N data sources of a pipeline) → N items, one per option. The umbrella label may also warrant a separate item in a different category (Briefing or NFR-Security, for example), but the options themselves are individual items.
- A capability matrix or RACI with substantially more visible rows than items extracted (a low extraction-to-row ratio, regardless of absolute count) → defect by definition. Such artifacts exist precisely to enumerate distinct capabilities or assignments; producing few items from a multi-row artifact means visible content was silently dropped.

**Examples.**

*Real, AI Agent, Brownfield.* From an approved SOW: "FR01: The solution shall consume existing Banco BV OneTrust APIs to extract vendor assessment (AVF) data, register comments and technical findings, and publish consolidated architecture opinions." Extracted as one Integration item with primitives `{system_name: "OneTrust", direction: "bidirectional", operations: "extract vendor assessments, register comments and technical findings, publish consolidated architecture opinions, update status", data_class: "vendor assessment data, AVF findings, architecture opinions", protocol: "REST via Apigee proxy", ownership: "existing — customer", criticality: "core_in_scope"}`.

*Real, AI Agent, Greenfield.* From an approved SOW: "FR06: The solution shall implement Retrieval-Augmented Generation (RAG) using Vertex AI Search to retrieve contextual information from Banco BTG Pactual's Confluence workspace, supporting governance, standards, and organizational context enrichment." Extracted with primitives `{system_name: "Confluence (BTG Pactual workspace)", direction: "source", operations: "retrieve content via similarity search for RAG enrichment", data_class: "governance documents, internal standards, organizational context", protocol: "indexed via Vertex AI Search", ownership: "existing — customer", criticality: "core_in_scope"}`.

*Illustrative, Data platform, Greenfield.* A capability matrix lists "SAP S/4HANA — daily inventory and master data extracts" and "POS API gateway — real-time transaction stream". Extracted as TWO items: SAP with primitives `{system_name: "SAP S/4HANA", direction: "source", operations: "daily full extract of master data, incremental load of inventory transactions via change timestamps", data_class: "inventory levels, product master data, supplier master data", protocol: "SAP standard connectors (BW Open Hub or RFC)", ownership: "existing — customer", criticality: "core_in_scope"}`; POS with primitives `{system_name: "POS API gateway", direction: "source", operations: "subscribe to transaction events for near-real-time ingestion", data_class: "point-of-sale transactions, store-level events", protocol: "REST / event stream", ownership: "existing — customer"}`.

*Illustrative, Foundation, Greenfield.* A briefing reads: "The new GCP organization will federate identities from the customer's Azure AD via Cloud Identity, with SSO for all administrative access." Extracted with primitives `{system_name: "Azure AD", direction: "source", operations: "federate user identities and groups for SSO into GCP", data_class: "user identity, group membership, role mappings", protocol: "SAML / SCIM via Cloud Identity", ownership: "existing — customer"}`.

**Anti-patterns.**
- "We will integrate with the customer's existing CRM" when the artifact explicitly names the CRM (Salesforce). Capture the named system, not the generic role.
- "The platform will support multiple channels" — extract only if specific channels are named elsewhere; if not, this is a gap, not an item.

---

## Category 4: Scope

**Purpose.** Captures both what is in scope and what is explicitly out of scope, plus team composition, payment model, and the engagement-shape boundary statements that the SOW echoes verbatim in its Executive Summary and Out-of-Scope sections.

**Required primitives.**
- `direction` — `in_scope` | `out_of_scope`. Required.
- `subject` — what is being included or excluded (verbatim short phrase).
- `rationale` — when the source provides a reason for the boundary, capture it.
- `team_role` (when item is about team composition) — role title and side (partner / customer).
- `payment_model_attribute` (when item is about commercials) — Fixed Price | Milestone-based | Time-and-Materials | Pure SOW | not_stated.

**What counts as a concrete item.**
- Explicit in-scope statements ("the project includes X").
- Explicit out-of-scope statements ("X is not part of this engagement", "isso fica fora", "this is excluded", "no [activity] is included in this SOW").
- Team composition statements ("dedicated full-time GFT team", "two architects from the customer side").
- Payment model statements.
- Pilot vs. full-rollout distinctions.

**What to ignore.**
- Speculative statements about future expansion ("could be extended to Y" — that's a future opportunity, not current scope).

**Anchor type.** Section heading + paragraph or bullet position.

**Examples.**

*Real, AI Agent, Assessment.* From an approved SOW: "This engagement is strictly limited to the assessment and definition phase. No development, deployment, or configuration of agents is included in this Statement of Work." Extracted as TWO Scope items: one with primitives `{direction: "in_scope", subject: "assessment and definition phase only"}`; one with primitives `{direction: "out_of_scope", subject: "development, deployment, or configuration of agents", rationale: "engagement strictly limited to assessment shape"}`.

*Real, AI Agent, Greenfield.* From an approved SOW: "FR07: The solution shall be published as an Agent via Google Gemini Enterprise... No custom user interface will be developed." Extracted as a Scope item with primitives `{direction: "out_of_scope", subject: "custom user interface development", rationale: "Gemini Enterprise is the sole interaction layer"}`.

*Illustrative, Data platform, Greenfield.* Briefing states: "Phase 1 covers the sales domain only. Inventory and CRM domains will be addressed in a future engagement." Extracted as TWO Scope items: one `{direction: "in_scope", subject: "sales domain ingestion and analytics"}` and one `{direction: "out_of_scope", subject: "inventory and CRM domains", rationale: "explicitly deferred to future engagement"}`.

*Illustrative, Foundation, Greenfield.* Briefing states: "Customer team will provide one Cloud Architect dedicated 50% to the engagement. Partner team is fully dedicated." Extracted as TWO Scope items, both with `team_role` primitive: customer side `{team_role: "Cloud Architect", side: "customer", allocation: "50%"}` and partner side `{team_role: "fully dedicated team", side: "partner"}`.

**Anti-patterns.**
- "We expect great results" → not scope, skip.

---

## Category 5: NFRs (Non-Functional Requirements)

**Purpose.** Quantitative and qualitative targets the system must meet. Quantitative targets are the hardest gap class to fill from inference and the most common reason `sow-generator` falls back to `[TO BE DEFINED]`.

**Required primitives.**
- `pillar` — Security | Reliability | Performance | Operational Excellence | Cost Optimization | Documentation | Tooling | Compliance | Other. Aligned with GCP Well-Architected Framework where possible.
- `target_type` — `quantitative` | `qualitative` | `compliance_framework` | `architectural_pattern`.
- `target_value` — when quantitative, the actual number with units (e.g., "p95 < 500ms"); when compliance, the framework name (e.g., "LGPD", "SOC 2 Type II", "PCI-DSS"); when qualitative, the descriptive target.
- `responsibility_boundary` — for Reliability/Operational Excellence: who operates the system in production (`partner_during_engagement` | `customer_post_handover` | `shared`). **Critical primitive — informs whether the SOW's Reliability NFR will use the architectural-quality phrasing or be flagged for rewrite.** See `style-guide.md` consultancy scope rule.

**What counts as a concrete item.**
- Latency targets (e.g., "p95 < 500ms").
- Throughput targets (e.g., "1000 requests per minute").
- Accuracy targets (e.g., "≥ 95% intent classification accuracy", "MAPE < 15%").
- Availability targets — capture if stated, but always populate `responsibility_boundary` because the SOW's Reliability phrasing depends on it.
- Compliance frameworks named.
- Security posture statements (encryption standards, key management, access control patterns).
- Resilience or fallback requirements (e.g., "must include fallback when [model] is unavailable", "must support circuit breaker on [API]").
- Observability requirements (logging retention, alerting thresholds, dashboard scope).
- Cost-optimization requirements (committed-use-discount strategy, tiered storage, autoscaling policies).
- Tooling constraints (e.g., "all development using exclusively Google Cloud native tools").

**What to ignore.**
- Vague qualitative statements like "must be fast" or "must be reliable" without numbers — these become entries in `gaps.hard_gaps` for the user to quantify.

**Anchor type.** Page + sentence, or table cell if from a structured requirements table.

**Examples.**

*Real, AI Agent, Greenfield.* From an approved SOW: "NFR01: Security: All data accessed by GFT must be sanitized and compliant with BTG Pactual security and privacy policies. Industry-standard encryption (TLS 1.3, AES-256) is required for data in transit and at rest." Extracted with primitives `{pillar: "Security", target_type: "compliance_framework", target_value: "TLS 1.3 in transit, AES-256 at rest; compliance with BTG security and privacy policies"}`.

*Real, AI Agent, Greenfield (consultancy scope rule applied).* The same SOW does NOT commit to an availability percentage for production — instead Reliability is expressed as "Environments: Scope limited to DEV and UAT environments only. Production deployment is explicitly OUT OF SCOPE." Extracted with primitives `{pillar: "Reliability", target_type: "architectural_pattern", target_value: "scope bounded to DEV and UAT; production deployment explicitly out of scope", responsibility_boundary: "customer_post_handover"}`. Note how the `responsibility_boundary` primitive is what tells `sow-generator` to phrase the Reliability NFR per the consultancy scope rule rather than committing to uptime.

*Illustrative, Data platform, Greenfield.* A briefing states: "Pipeline must process the daily SAP extract within a 4-hour window to feed the next-morning Looker refresh. POS event ingestion must keep pace with peak volume of 5,000 events/sec sustained." Extracted as TWO NFR items: `{pillar: "Performance", target_type: "quantitative", target_value: "daily SAP extract processed within 4-hour window"}` and `{pillar: "Performance", target_type: "quantitative", target_value: "POS ingestion sustains 5,000 events/sec at peak"}`.

*Illustrative, Foundation, Greenfield.* A briefing states: "All organization policies must enforce data residency in southamerica-east1. Customer-managed encryption keys (CMEK) required for all storage." Extracted as TWO NFR items: `{pillar: "Compliance", target_type: "compliance_framework", target_value: "data residency in southamerica-east1 enforced via Org Policy"}` and `{pillar: "Security", target_type: "architectural_pattern", target_value: "CMEK required on all storage; key management via Cloud KMS"}`.

**Anti-patterns.**
- "Must be highly performant" → vague, no number — goes to `gaps.hard_gaps`.
- An NFR phrased "shall maintain 99.9% uptime" without `responsibility_boundary` populated — that's an extraction defect; either the source named who operates production, or `responsibility_boundary` is `not_stated` and goes to `gaps.ambiguities`.

---

## Category 6: Timeline

**Purpose.** Captures the time structure of the project: start, end, duration, phase boundaries, internal milestones, and time-based dependencies.

**Required primitives.**
- `marker_type` — `total_duration` | `phase_boundary` | `kickoff_date` | `end_date` | `milestone` | `dependency`.
- `value` — the time value (e.g., "18 weeks", "6 weeks", "April 1, 2026", "End of week 4").
- `phase_label` — when applicable, the phase name this marker bounds.
- `dependency_target` — when `marker_type == dependency`, what this depends on (e.g., "Google PSO recommendations delivery", "customer VPN provisioning").

**What counts as a concrete item.**
- Total project duration if stated.
- Phase boundaries with their durations.
- Specific dates if stated.
- Milestones with deliverable expectations.
- Time-based dependencies on third parties.

**What to ignore.**
- "ASAP", "as soon as possible", "urgent" — these are pressure signals, not timeline facts.

**Anchor type.** Page + section + sentence.

**Examples.**

*Real, AI Agent, Brownfield.* From an approved SOW: "Estimated Start Date: April 1, 2026 (confirmed 20 business days after Google PSF approval). Estimated End Date: July 1, 2026. Duration: 12 weeks." Extracted as THREE Timeline items: `{marker_type: "kickoff_date", value: "April 1, 2026", notes: "subject to Google PSF approval + 20 business days"}`, `{marker_type: "end_date", value: "July 1, 2026"}`, `{marker_type: "total_duration", value: "12 weeks"}`.

*Real, AI Agent, Brownfield.* From the same SOW's Effort Estimate: "W1-2: Setup, Discovery & BV Tools Onboarding. W3-10: Agent Development & Enhancement. W11-12: Tests & Handover." Extracted as THREE phase-boundary items, e.g. `{marker_type: "phase_boundary", value: "Weeks 1-2", phase_label: "Setup, Discovery & BV Tools Onboarding"}`.

*Illustrative, Data platform, Greenfield.* Briefing reads: "Project duration is 16 weeks. First 4 weeks dedicated to source-system discovery before any pipeline development. Hard deadline of November 30 for the Black Friday analytics dashboard." Extracted as: `{marker_type: "total_duration", value: "16 weeks"}`; `{marker_type: "phase_boundary", value: "Weeks 1-4", phase_label: "Source-system discovery"}`; `{marker_type: "milestone", value: "November 30 deadline", notes: "Black Friday analytics dashboard must be live"}`.

*Illustrative, AI Agent, Greenfield with third-party dependency.* Briefing reads: "Total duration 18 weeks. First 6 weeks led by Google PSO delivering implementation directives; partner full execution begins at week 7." Extracted as: `{marker_type: "total_duration", value: "18 weeks"}`; `{marker_type: "phase_boundary", value: "Weeks 1-6", phase_label: "PSO-led discovery and directive definition"}`; `{marker_type: "dependency", value: "partner execution begins at week 7", dependency_target: "Google PSO recommendations delivery at end of week 6"}`.

---

## Category 7: Constraints

**Purpose.** Captures non-negotiable conditions the project must respect. These shape assumptions, architecture choices, and out-of-scope items in `sow-generator`.

**Required primitives.**
- `constraint_type` — `data_residency` | `compliance_framework` | `network_access` | `tooling` | `gcp_org_structure` | `approval_gate` | `availability_window` | `baseline_alignment` | `other`.
- `description` — the constraint as written.
- `actor_responsibility` — who is bound by this constraint (`partner` | `customer` | `both`).
- `consequence_if_violated` — when the source describes the consequence of non-compliance, capture it.

**What counts as a concrete item.**
- Data residency requirements.
- Compliance certifications required (already partly covered in NFRs — capture here when framed as a constraint rather than a target).
- VPN, firewall, or network access constraints.
- Existing GCP organization or project structure to fit within.
- Customer-side approval gates.
- Tooling constraints.
- Resource availability windows.
- Baseline-alignment constraints (Brownfield: "must reuse v1 architectural foundation"; Migration: "must keep parallel run for 30 days post-cutover").

**Anchor type.** Page + section + sentence.

**Examples.**

*Real, AI Agent, Brownfield.* From an approved SOW: "All Technical Solution Proposals will be designed exclusively for Google Cloud native tools and the ADK (Agent Development Kit), consistent with the v1 architecture." Extracted with primitives `{constraint_type: "tooling", description: "all solutions designed exclusively for Google Cloud native tools and ADK; must align with v1 architecture", actor_responsibility: "partner"}`.

*Real, AI Agent, Brownfield.* From an approved SOW: "Access to the Apigee proxy (apigix-onet-base-protecao-dados-out-v2) with service credentials for the agent must be provided by Banco BV." Extracted with primitives `{constraint_type: "network_access", description: "Apigee proxy access (apigix-onet-base-protecao-dados-out-v2) with service credentials required for OneTrust integration", actor_responsibility: "customer"}`.

*Illustrative, Data platform, Greenfield.* Briefing states: "All data must remain in Brazil (data residency). Customer's GCP organization is already established and the project must use the existing 'analytics-platform' folder." Extracted as TWO items: `{constraint_type: "data_residency", description: "all data resident in Brazil"}` and `{constraint_type: "gcp_org_structure", description: "must use existing 'analytics-platform' folder within customer GCP organization"}`.

*Illustrative, Migration.* Briefing states: "30-day parallel run required between source and target databases before cutover. Cutover window restricted to weekend maintenance slots." Extracted as: `{constraint_type: "baseline_alignment", description: "30-day parallel run between source and target before cutover"}` and `{constraint_type: "availability_window", description: "cutover restricted to weekend maintenance slots"}`.

---

## Category 8: Decisions & Alignments

**Purpose.** Captures decisions already made by stakeholders, including informal alignments mentioned in transcripts and chat logs that don't appear in formal documents. These are the items most often missed when only formal PDFs are read.

**Required primitives.**
- `decision_type` — `architectural` | `commercial` | `scope` | `technology_choice` | `process` | `pending` (decision deferred) | `responsibility_assignment`.
- `decision_text` — the decision as stated.
- `decided_by` — who made the decision (e.g., "customer technical leadership", "joint customer + partner alignment", "Google PSO recommendation").
- `expected_resolution_when_pending` — for `decision_type == pending`, when the decision is expected.

**What counts as a concrete item.**
- "We decided to use X" / "ficou decidido que Y" / "agreed to Z".
- "The customer accepted that W will be deferred to phase 2".
- Implicit alignments revealed by absence of objection ("nobody pushed back when we proposed using ADK").
- Pending decisions explicitly marked as such ("we will decide the model size after the PSO discovery").
- Responsibility assignments ("API development remains BV's responsibility", "GFT will not develop or modify any APIs").

**What to ignore.**
- Speculative comments ("maybe we could try X").
- Hypothetical questions raised but not answered.

**Anchor type.** For transcripts: speaker + timestamp. For chat logs: sender + message timestamp. For meeting notes: section + bullet.

**Examples.**

*Real, AI Agent, Brownfield.* From an approved SOW: "GFT will not develop, modify, or test any code, agents, integrations, or infrastructure components during this Assessment Phase. All technical activities are limited to analysis, documentation, and solution design." Extracted with primitives `{decision_type: "responsibility_assignment", decision_text: "partner activities limited to analysis, documentation, and solution design — no code, agent, integration, or infrastructure modification during the Assessment Phase", decided_by: "joint partner + customer scope alignment"}`.

*Real, AI Agent, Brownfield.* From an approved SOW: "Banco BV will not be responsible for developing or modifying any APIs provided by BV's internal systems (OneTrust, CMDB, COUPA, or others). The assessment will document required API capabilities, but API development remains BV's responsibility." Extracted with primitives `{decision_type: "responsibility_assignment", decision_text: "API development for OneTrust, CMDB, COUPA, and other customer internal systems remains customer responsibility; partner consumes only", decided_by: "customer responsibility statement"}`.

*Illustrative, AI Agent, Greenfield with deferral.* Briefing states: "Quantitative latency and throughput targets will be defined during the first phase led by Google PSO and incorporated into the SOW via Change Request if needed." Extracted with primitives `{decision_type: "pending", decision_text: "quantitative latency and throughput targets to be defined during PSO Phase 1", decided_by: "joint alignment", expected_resolution_when_pending: "end of week 6 — at PSO directive delivery"}`.

*Illustrative, Data platform, Brownfield.* Briefing states: "Customer confirmed BigQuery as the warehouse target — Snowflake was evaluated and rejected on cost grounds." Extracted with primitives `{decision_type: "technology_choice", decision_text: "BigQuery selected as the warehouse target; Snowflake rejected on cost grounds", decided_by: "customer technical leadership"}`.

---

## Cross-cutting rules

**No collapse — one concept, one item.** This is the single most important rule in this file and the most frequent failure mode in practice. When an artifact mentions multiple distinct subjects together — multiple systems, multiple channels, multiple identity mechanisms, multiple compliance frameworks, multiple stakeholders — each subject becomes its own `extracted_item`. Connectives in source text ("X, Y, and Z" / "X / Y / Z" / "X e Y e Z") are signals of multiplicity, not signals of a single grouped item. If you find yourself writing a `value` field that contains a comma between two distinct nouns, an `and`/`e` between two systems, or a slash between two protocols — stop and split. The Manifest is granular by design; `sow-generator` cannot draft per-system FRs from a value field that lists three systems together.

**Cite verbatim when the phrasing matters.** For decisions, contradictions, and exclusion phrases, the `value` field should be a faithful paraphrase that preserves the original framing. For factual lookups (system names, numbers, dates), a literal capture is fine. When in doubt, lean toward verbatim — `sow-generator` may need the original phrasing to draft a paraphrase that aligns with the customer's voice.

**Always paraphrase into English in the `value` and `value_detail` fields.** Even if the artifact is in Portuguese, Spanish, German, or another language. Faithful translation, not interpretation. If the original phrasing carries a nuance that does not translate cleanly, capture both: English paraphrase in `value_detail`, original short phrase in `notes.original_language_quote`. Primitives are also in English.

**Cross-reference between categories.** When an item belongs to two categories (a phase boundary in Timeline that is also a Decision in a transcript), create one entry in each category and use `cross_refs` to link them by ID. The Manifest is denormalized on purpose — `sow-generator` looks up by category, so duplication across categories is correct.

**Primitives are required, not optional.** Every extracted item populates the primitives defined for its category. When a primitive cannot be determined from the source, set it to `not_stated` rather than omitting the key. `not_stated` is a signal to `sow-generator` that the field needs human input; an absent key looks like an extraction bug.

**When an artifact is a screenshot, image, or OCR-derived content.** This artifact type is at highest risk of partial reading — the model may process only a portion of the visible content and silently move on. Mitigation, applied in `SKILL.md` Phase 1.1:

1. Before enumerating elements, declare a **visible-element count**. State in your reasoning: "this image visibly contains approximately N rows / N labeled boxes / N capability entries / N diagram nodes." This number is not required to be exact — it anchors expectations for Phase 1.3 reconciliation.
2. Enumerate every labeled element: every box in a diagram, every row in a matrix, every annotation, every legend entry. Anchors for images include slide number, region (top-left, center, lower-right grid cell, etc.), and the literal label text from the image.
3. If your visible-element count is N and your enumeration produces substantially fewer entries (say, fewer than 70% of N), re-examine the artifact before proceeding. The most likely cause is partial OCR or visual scanning, not genuine sparseness.

**When an artifact is audio or a transcript.** Treat it as a structured document. Anchors are speaker + timestamp. If multiple speakers contradict, capture both readings in `extracted_items` and flag the contradiction in Phase 2.

**When an artifact is a capability matrix or RACI.** These artifacts exist to enumerate things in scope. A capability matrix with N rows is expected to produce close to N items (some rows may be header labels or category names, captured as Phase 1.2 skips with reason). A RACI with N rows × M actors produces up to N × M assignment items — though most extractions focus on the rows where the partner has Responsible or Accountable, not every cell. Producing single-digit item counts from artifacts of this type is almost always a defect.

**Triage tier defaults (Phase 0.5).** When classifying artifacts in the triage step, use these defaults as starting point — the user can always override:

- **Primary** (deepest pass, processed first): capability matrices, RACI tables, requirement specifications, integration lists, NFR tables, formal proposals with structured sections, scope sheets. These artifacts densely enumerate in-scope items and are the highest-leverage source of project facts.
- **Secondary** (processed after Primary): meeting transcripts, kick-off slide decks, executive briefings, architecture overview diagrams, alignment notes, chat logs of project discussions. These confirm or add context to what Primary artifacts state, and capture informal decisions or constraints that formal docs miss.
- **Context** (processed last): standalone screenshots, individual slide snapshots, short attachments, peripheral notes, supporting reference material. Often contributes single items or confirmations rather than enumerable scope.

The tier assignment is based on artifact type, name, and the Phase 0 hypothesis — not on opening the artifact. A "Capabilities" appendix is Primary regardless of file type. A transcript is Secondary regardless of length. Tier defaults can be overridden by the user during Phase 0.5; honor the user's adjustment over the default.

**When the user has no artifacts (Path A — Guided Discovery).** The conversation itself is the source. Inventory has a single entry `A1` of type `user-briefing` named "guided conversation". Anchors are `"guided turn N / Block X"` where N is the user message number in the interview and X is the block label (1, 2, 2.5, 3, or 4). The same eight-category extraction applies, but is performed incrementally — one block per user response — rather than scanning a finished document. Each user response is treated as an excerpt; the post-response routine in `SKILL.md` Phase 1-A walks the response through the same category checks a Path B artifact pass would apply.

---

## Calibration: real vs. illustrative examples

The examples above marked *real* are pulled from approved SOWs the team has produced and represent the quality bar for that engagement shape × archetype intersection. Examples marked *illustrative* are grounded in standard GCP industrialization patterns but not drawn from a specific delivered project — they exist to demonstrate that the same primitives apply across archetypes the team executes (data platform, API modernization, foundation/landing zone, observability, FinOps, migration) for which no canonical real example was available at the time this rubric was written.

Treat both as authoritative for *structure*. Treat *real* examples as additionally authoritative for *tone and specificity*. When you encounter a project that resembles an *illustrative* archetype more than a *real* one, follow the primitives; the rubric is generic by design.