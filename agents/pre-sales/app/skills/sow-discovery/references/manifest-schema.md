# Extraction Manifest — Schema and Worked Examples

This file defines the JSON contract between `sow-discovery` (producer) and `sow-generator` (consumer). The schema is binding — `sow-generator` looks up items by exact field names, so deviations break the hand-off silently.

Load this file in `sow-discovery` Phase 4 before emitting the Manifest. Use the worked examples at the end to calibrate granularity, anchor format, primitives population, and `notes` usage.

---

## Top-level structure

```
{
  "manifest_version": "1.0",
  "created_at": "ISO-8601 timestamp",
  "conversation_language": "pt-BR | en | es | ...",
  "inventory": [...],
  "extracted_items": [...],
  "gaps": {
    "hard_gaps": [...],
    "pending_decisions": [...],
    "ambiguities": [...],
    "to_be_defined": [...]
  },
  "self_audit": {
    "all_artifacts_contributed": boolean,
    "all_required_categories_covered": boolean,
    "contradictions_resolved_or_flagged": boolean,
    "user_interview_turns": integer
  }
}
```

---

## `inventory` — array of artifact records

One entry per artifact provided by the user, including any user-briefing pseudo-artifact.

```
{
  "id": "A1",
  "name": "BV Appendix.pdf",
  "type": "pdf | docx | txt | image | audio | transcript | chat-log | screenshot | user-briefing",
  "uploaded_at": "ISO-8601 timestamp or null",
  "phase_0_hypothesis": "one-line working hypothesis from Phase 0",
  "items_extracted": 0,
  "categories_found": ["Identity", "Integrations", ...],
  "source_language": "pt | en | es | ...",
  "notes": "free-form, e.g. 'no items extracted because artifact is an unrelated dashboard'"
}
```

The Pydantic schema auto-populates `items_extracted` and `categories_found` based on the actual `extracted_items` content — emit them as `0` and `[]` if unsure; the validator rewrites them.

---

## `extracted_items` — array of fact records

One entry per concrete item, post-reconciliation.

```
{
  "id": "I-001",
  "category": "Identity | Briefing | Integrations | Scope | NFRs | Timeline | Constraints | Decisions",
  "value": "Salesforce",
  "value_detail": "CRM platform, integration target",
  "primitives": {
    "system_name": "Salesforce",
    "direction": "bidirectional",
    "operations": "...",
    "data_class": "...",
    "protocol": "REST",
    "ownership": "existing — customer"
  },
  "source": [{"artifact_id": "A2", "anchor": "Appendix B / row 7"}],
  "confidence": "stated | implied",
  "cross_refs": [],
  "notes": ""
}
```

### `primitives`

The structured sub-extraction dict. Required keys depend on `category` — see `extraction-rules.md` per-category "Required primitives" sections. Always populated; when a primitive cannot be determined, set to `"not_stated"` rather than omitting the key.

The primitives are what `sow-generator` reads to draft each section of the SOW. Without them, the generator falls back to parsing prose from `value_detail` — which is exactly the failure mode that motivated splitting discovery from generation.

### Other fields

- `id`: prefix `I-` followed by zero-padded sequence. Stable across edits — if an item is removed, its ID is retired, not reassigned.
- `category`: must match exactly one of the eight category names in `extraction-rules.md`. Items belonging to two categories are duplicated, with `cross_refs` listing the sibling ID.
- `value`: short canonical name in English.
- `value_detail`: longer paraphrase. May be empty for self-explanatory items.
- `source`: list of `{artifact_id, anchor}` pairs.
- `confidence`: `stated` for explicit content; `implied` only when derived from explicit content within the same artifact via direct logical inference.
- `cross_refs`: list of sibling item IDs that share this item's underlying fact across categories.
- `notes`: free-form. Used for: acronym status, original-language quote, contradiction flags, open ambiguities that did not warrant moving to gaps.

---

## `gaps` — four-part structure

### `hard_gaps`

Items the SOW will need but that no artifact provides AND that the user could not provide during Phase 3 (or that Phase 3 did not reach in three turns).

```
{
  "id": "G-001",
  "category": "NFRs",
  "description": "Quantitative latency targets for the conversational interface",
  "interview_turn_asked": 1,
  "user_response": "deferred to PSO Phase 1" | "[TO BE DEFINED]" | "<user's actual answer>",
  "blocks_sow_generation": true | false
}
```

`blocks_sow_generation: true` flags gaps so critical that `sow-generator` should refuse to proceed without resolution. `false` flags gaps that can be filled with `[TO BE DEFINED]` placeholders in the SOW.

### `pending_decisions`

Items the project plan itself defers. These are NOT user gaps — they are facts about how the project is structured.

```
{
  "id": "P-001",
  "description": "Scope refinement after Google PSO Phase 1 (weeks 1-6)",
  "deferral_source": [{"artifact_id": "A4", "anchor": "user briefing turn 3"}],
  "expected_resolution": "End of week 6 — PSO recommendations delivered"
}
```

### `ambiguities`

Items present in the artifacts but unclear, partially specified, or context-dependent.

```
{
  "id": "AM-001",
  "category": "Integrations",
  "description": "Artifacts mention 'MCP' without expansion or specification of the broker implementation",
  "source": [{"artifact_id": "A2", "anchor": "Appendix B / row 14"}],
  "interview_turn_asked": 2,
  "user_response": "to be defined during PSO Phase 1"
}
```

### `to_be_defined`

A roll-up list of every `[TO BE DEFINED]` token that should appear in the final SOW. Convenience field — duplicates information in `hard_gaps` and `ambiguities` for `sow-generator` to consume directly.

---

## Worked Example A — AI Agent, Greenfield Implementation

This worked example is pulled from a real approved SOW (BTG Pactual PM Agent). It demonstrates extraction shape for a Greenfield AI Agent engagement on PSF funding.

```json
{
  "manifest_version": "1.0",
  "created_at": "2026-04-29T14:00:00Z",
  "conversation_language": "pt-BR",
  "inventory": [
    {
      "id": "A1",
      "name": "BTG_PM_Agent_Brief.pdf",
      "type": "pdf",
      "phase_0_hypothesis": "Customer brief outlining the PM Agent capabilities and constraints",
      "items_extracted": 12,
      "categories_found": ["Identity", "Briefing", "Integrations", "Scope", "NFRs", "Timeline", "Constraints"],
      "source_language": "en",
      "notes": ""
    },
    {
      "id": "A2",
      "name": "kickoff_transcript.txt",
      "type": "transcript",
      "phase_0_hypothesis": "Kick-off meeting between BTG and partner — likely contains decisions and alignments not in formal docs",
      "items_extracted": 4,
      "categories_found": ["Decisions", "Timeline"],
      "source_language": "pt-BR",
      "notes": ""
    }
  ],
  "extracted_items": [
    {
      "id": "I-001",
      "category": "Identity",
      "value": "Banco BTG Pactual S.A.",
      "value_detail": "Customer organization commissioning the AI-Powered PM Agent",
      "primitives": {
        "customer": "Banco BTG Pactual S.A.",
        "project_name": "AI-Powered PM Agent",
        "funding_type": "PSF",
        "partner": "GFT Brasil Consultoria Informática LTDA",
        "secondary_partners": "not_stated",
        "engagement_shape": "greenfield",
        "engagement_phase": "initial engagement",
        "sector": "financial services",
        "geography": "service delivery remote from Brazil to Brazil; deployment in BTG GCP tenant"
      },
      "source": [{"artifact_id": "A1", "anchor": "p.1 / Cover and Engagement Details table"}],
      "confidence": "stated",
      "cross_refs": [],
      "notes": ""
    },
    {
      "id": "I-002",
      "category": "Briefing",
      "value": "AI-assisted product management lifecycle",
      "value_detail": "Solution supports Product Managers from discovery through backlog refinement to delivery tracking; autonomous Epic/Feature/User Story generation with RAG-enriched context from Confluence",
      "primitives": {
        "problem_statement": "accelerate product delivery capabilities",
        "business_capability": "AI-driven product management across discovery, Epic generation, Feature breakdown, User Story creation, effort estimation, and Confluence knowledge retrieval",
        "delivery_mode": "autonomous generation via Gemini with RAG enrichment",
        "business_outcomes": "support Product Managers throughout the entire product lifecycle",
        "pilot_scope": "not_stated"
      },
      "source": [{"artifact_id": "A1", "anchor": "p.3 / Executive Summary"}],
      "confidence": "stated",
      "cross_refs": [],
      "notes": ""
    },
    {
      "id": "I-003",
      "category": "Integrations",
      "value": "Confluence (BTG Pactual workspace)",
      "value_detail": "Customer's existing Confluence workspace; source of governance, standards, and organizational context for RAG enrichment",
      "primitives": {
        "system_name": "Confluence (BTG Pactual workspace)",
        "direction": "source",
        "operations": "retrieve content via similarity search for RAG enrichment of agent responses",
        "data_class": "governance documents, internal standards, organizational context",
        "protocol": "indexed via Vertex AI Search",
        "ownership": "existing — customer",
        "criticality": "core_in_scope"
      },
      "source": [{"artifact_id": "A1", "anchor": "p.5 / Functional Requirements / FR06"}],
      "confidence": "stated",
      "cross_refs": [],
      "notes": ""
    },
    {
      "id": "I-004",
      "category": "Integrations",
      "value": "Google Gemini Enterprise",
      "value_detail": "Conversational interaction layer; PMs interact with the agent through Google Workspace via Gemini Enterprise",
      "primitives": {
        "system_name": "Google Gemini Enterprise",
        "direction": "bidirectional",
        "operations": "publish PM Agent for conversational access; deliver responses to Product Managers",
        "data_class": "user prompts, agent responses, conversation history",
        "protocol": "Gemini Enterprise platform-native",
        "ownership": "existing — partner platform",
        "criticality": "core_in_scope"
      },
      "source": [{"artifact_id": "A1", "anchor": "p.5 / Functional Requirements / FR07"}],
      "confidence": "stated",
      "cross_refs": ["I-005"],
      "notes": ""
    },
    {
      "id": "I-005",
      "category": "Scope",
      "value": "No custom user interface developed",
      "value_detail": "Gemini Enterprise is the sole interaction layer; explicit boundary stated",
      "primitives": {
        "direction": "out_of_scope",
        "subject": "custom user interface, web app, mobile app, or portal",
        "rationale": "Gemini Enterprise is the sole interaction layer per FR07"
      },
      "source": [{"artifact_id": "A1", "anchor": "p.5 / Functional Requirements / FR07"}],
      "confidence": "stated",
      "cross_refs": ["I-004"],
      "notes": ""
    },
    {
      "id": "I-006",
      "category": "Scope",
      "value": "DEV and UAT environments only",
      "value_detail": "Production deployment explicitly out of scope",
      "primitives": {
        "direction": "out_of_scope",
        "subject": "production environment deployment",
        "rationale": "scope bounded to DEV and UAT only per NFR04"
      },
      "source": [{"artifact_id": "A1", "anchor": "p.6 / NFR04"}],
      "confidence": "stated",
      "cross_refs": ["I-007"],
      "notes": ""
    },
    {
      "id": "I-007",
      "category": "NFRs",
      "value": "Reliability — DEV/UAT scope, customer operates production",
      "value_detail": "Architecture targets DEV and UAT only; production operational responsibility remains with the customer post-handover",
      "primitives": {
        "pillar": "Reliability",
        "target_type": "architectural_pattern",
        "target_value": "scope bounded to DEV and UAT; no production availability commitment",
        "responsibility_boundary": "customer_post_handover"
      },
      "source": [{"artifact_id": "A1", "anchor": "p.6 / NFR04"}],
      "confidence": "stated",
      "cross_refs": ["I-006"],
      "notes": "consultancy scope rule applies — generator must use architectural-quality phrasing not uptime commitment"
    },
    {
      "id": "I-008",
      "category": "NFRs",
      "value": "Security — TLS 1.3 / AES-256, BTG policies",
      "value_detail": "Industry-standard encryption (TLS 1.3 in transit, AES-256 at rest); compliance with BTG security and privacy policies",
      "primitives": {
        "pillar": "Security",
        "target_type": "compliance_framework",
        "target_value": "TLS 1.3 in transit, AES-256 at rest; compliance with BTG Pactual security and privacy policies"
      },
      "source": [{"artifact_id": "A1", "anchor": "p.6 / NFR01"}],
      "confidence": "stated",
      "cross_refs": [],
      "notes": ""
    },
    {
      "id": "I-009",
      "category": "Timeline",
      "value": "Estimated start April 7, 2026",
      "value_detail": "Subject to Google PSF approval; confirmed 20 business days after approval",
      "primitives": {
        "marker_type": "kickoff_date",
        "value": "April 7, 2026",
        "phase_label": "not_stated",
        "dependency_target": "Google PSF approval"
      },
      "source": [{"artifact_id": "A1", "anchor": "p.2 / Engagement Details"}],
      "confidence": "stated",
      "cross_refs": [],
      "notes": ""
    },
    {
      "id": "I-010",
      "category": "Constraints",
      "value": "Tooling — exclusively GCP native tools",
      "value_detail": "All development uses Vertex AI, ADK, Agent Engine, Gemini Enterprise, Vertex AI Search; no custom integrations or third-party persistence layers without formal Change Request",
      "primitives": {
        "constraint_type": "tooling",
        "description": "all development uses exclusively Google Cloud native tools (Vertex AI, ADK, Agent Engine, Gemini Enterprise, Vertex AI Search)",
        "actor_responsibility": "partner",
        "consequence_if_violated": "requires formal Change Request"
      },
      "source": [{"artifact_id": "A1", "anchor": "p.6 / NFR03"}],
      "confidence": "stated",
      "cross_refs": [],
      "notes": ""
    },
    {
      "id": "I-011",
      "category": "Decisions",
      "value": "Agent Engine selected as primary runtime",
      "value_detail": "Agent Engine handles session lifecycle, conversational memory, execution context, traceability — no custom session infrastructure",
      "primitives": {
        "decision_type": "technology_choice",
        "decision_text": "Agent Engine selected as the primary execution runtime; native session management adopted to avoid custom session infrastructure",
        "decided_by": "joint customer + partner technical alignment"
      },
      "source": [
        {"artifact_id": "A1", "anchor": "p.5 / FR08"},
        {"artifact_id": "A2", "anchor": "speaker:Tech Lead BTG / 14:22"}
      ],
      "confidence": "stated",
      "cross_refs": [],
      "notes": ""
    }
  ],
  "gaps": {
    "hard_gaps": [
      {
        "id": "G-001",
        "category": "NFRs",
        "description": "Quantitative latency targets for the PM Agent conversational interface",
        "interview_turn_asked": 1,
        "user_response": "[TO BE DEFINED]",
        "blocks_sow_generation": false
      }
    ],
    "pending_decisions": [],
    "ambiguities": [],
    "to_be_defined": [
      {"item": "Latency target for PM Agent conversational interface", "source_gap_id": "G-001"}
    ]
  },
  "self_audit": {
    "all_artifacts_contributed": true,
    "all_required_categories_covered": true,
    "contradictions_resolved_or_flagged": true,
    "user_interview_turns": 1
  }
}
```

---

## Worked Example B — Data Platform, Greenfield

This worked example is illustrative — it demonstrates how the same primitives apply to a different archetype the team executes. The shape of items differs (no Vertex AI / ADK; instead Dataflow / BigQuery / Looker), but the primitives are identical.

```json
{
  "manifest_version": "1.0",
  "created_at": "2026-04-29T14:00:00Z",
  "conversation_language": "pt-BR",
  "inventory": [
    {
      "id": "A1",
      "name": "Retail_Analytics_Platform_Brief.pdf",
      "type": "pdf",
      "phase_0_hypothesis": "Customer brief for unified analytics across 200 stores",
      "items_extracted": 8,
      "categories_found": ["Identity", "Briefing", "Integrations", "NFRs", "Timeline", "Constraints"],
      "source_language": "pt-BR",
      "notes": ""
    }
  ],
  "extracted_items": [
    {
      "id": "I-001",
      "category": "Identity",
      "value": "[Retail Customer]",
      "value_detail": "Retail chain operating 200 stores; commissioning a unified analytics platform on Google Cloud",
      "primitives": {
        "customer": "[Retail Customer]",
        "project_name": "Unified Sales Analytics Platform",
        "funding_type": "DAF",
        "partner": "GFT Technologies",
        "secondary_partners": "not_stated",
        "engagement_shape": "greenfield",
        "engagement_phase": "initial engagement (Phase 1 — sales domain)",
        "sector": "retail",
        "geography": "deployment in southamerica-east1; service delivery remote from Brazil"
      },
      "source": [{"artifact_id": "A1", "anchor": "p.1 / Cover"}],
      "confidence": "stated",
      "cross_refs": [],
      "notes": ""
    },
    {
      "id": "I-002",
      "category": "Briefing",
      "value": "Unified sales analytics across 200 stores",
      "value_detail": "Replace siloed reporting (SAP for inventory, custom POS for transactions, Excel for analyst output); analyst cycle currently 5+ days",
      "primitives": {
        "problem_statement": "siloed sales data across SAP/POS/Excel; analyst cycles exceed 5 days",
        "business_capability": "unified sales analytics across 200 stores with near-real-time visibility",
        "delivery_mode": "batch ingestion from SAP + streaming ingestion from POS into BigQuery medallion architecture; surfaced via Looker",
        "business_outcomes": "reduce analyst cycle time from 5+ days to near-real-time"
      },
      "source": [{"artifact_id": "A1", "anchor": "p.2 / Background"}],
      "confidence": "stated",
      "cross_refs": [],
      "notes": ""
    },
    {
      "id": "I-003",
      "category": "Integrations",
      "value": "SAP S/4HANA",
      "value_detail": "Customer's existing inventory and master-data system; daily extract source",
      "primitives": {
        "system_name": "SAP S/4HANA",
        "direction": "source",
        "operations": "daily full extract of master data; incremental load of inventory transactions via change-timestamp delta",
        "data_class": "inventory levels, product master, supplier master",
        "protocol": "SAP standard connectors (BW Open Hub or RFC)",
        "ownership": "existing — customer",
        "criticality": "core_in_scope"
      },
      "source": [{"artifact_id": "A1", "anchor": "p.4 / Source Systems table / row 1"}],
      "confidence": "stated",
      "cross_refs": [],
      "notes": ""
    },
    {
      "id": "I-004",
      "category": "Integrations",
      "value": "POS API gateway",
      "value_detail": "Customer's transaction event source; near-real-time stream",
      "primitives": {
        "system_name": "POS API gateway",
        "direction": "source",
        "operations": "subscribe to transaction events for streaming ingestion into BigQuery",
        "data_class": "point-of-sale transactions, store-level events",
        "protocol": "REST polling + event stream via Pub/Sub bridge (TBD by partner)",
        "ownership": "existing — customer",
        "criticality": "core_in_scope"
      },
      "source": [{"artifact_id": "A1", "anchor": "p.4 / Source Systems table / row 2"}],
      "confidence": "stated",
      "cross_refs": [],
      "notes": "protocol detail partially TBD; flagged in AM-001"
    },
    {
      "id": "I-005",
      "category": "NFRs",
      "value": "Performance — daily SAP window 4h",
      "value_detail": "Daily SAP extract must complete within 4-hour window to feed next-morning Looker refresh",
      "primitives": {
        "pillar": "Performance",
        "target_type": "quantitative",
        "target_value": "daily SAP extract processed within 4-hour window"
      },
      "source": [{"artifact_id": "A1", "anchor": "p.5 / NFR table / row 1"}],
      "confidence": "stated",
      "cross_refs": [],
      "notes": ""
    },
    {
      "id": "I-006",
      "category": "NFRs",
      "value": "Performance — POS sustained 5000 events/sec",
      "value_detail": "POS event ingestion sustains 5,000 events/sec at peak (Black Friday)",
      "primitives": {
        "pillar": "Performance",
        "target_type": "quantitative",
        "target_value": "5,000 events/sec sustained at peak"
      },
      "source": [{"artifact_id": "A1", "anchor": "p.5 / NFR table / row 2"}],
      "confidence": "stated",
      "cross_refs": [],
      "notes": ""
    },
    {
      "id": "I-007",
      "category": "Timeline",
      "value": "Hard deadline November 30 for Black Friday dashboard",
      "value_detail": "Black Friday analytics dashboard must be live by November 30",
      "primitives": {
        "marker_type": "milestone",
        "value": "November 30",
        "phase_label": "Black Friday readiness",
        "dependency_target": "not_stated"
      },
      "source": [{"artifact_id": "A1", "anchor": "p.6 / Timeline"}],
      "confidence": "stated",
      "cross_refs": [],
      "notes": "business-critical deadline; downstream FR/Activity content should reference"
    },
    {
      "id": "I-008",
      "category": "Constraints",
      "value": "Data residency — Brazil only",
      "value_detail": "All data must reside in southamerica-east1; enforced via Org Policy",
      "primitives": {
        "constraint_type": "data_residency",
        "description": "all data resident in southamerica-east1; enforced via Org Policy",
        "actor_responsibility": "both",
        "consequence_if_violated": "regulatory non-compliance"
      },
      "source": [{"artifact_id": "A1", "anchor": "p.7 / Compliance"}],
      "confidence": "stated",
      "cross_refs": [],
      "notes": ""
    }
  ],
  "gaps": {
    "hard_gaps": [],
    "pending_decisions": [],
    "ambiguities": [
      {
        "id": "AM-001",
        "category": "Integrations",
        "description": "POS API gateway exposes REST polling, but the briefing also implies an event-stream is preferred — the Pub/Sub bridge implementation responsibility is not stated",
        "source": [{"artifact_id": "A1", "anchor": "p.4 / Source Systems table / row 2"}],
        "interview_turn_asked": 1,
        "user_response": "partner builds the Pub/Sub bridge as part of WS01"
      }
    ],
    "to_be_defined": []
  },
  "self_audit": {
    "all_artifacts_contributed": true,
    "all_required_categories_covered": true,
    "contradictions_resolved_or_flagged": true,
    "user_interview_turns": 1
  }
}
```

---

## What `sow-generator` does with the primitives

When `sow-generator` drafts each section, it queries the Manifest by category and reads primitives directly. Concretely:

- **Functional Requirements:** for each Integration item, the FR is drafted from `primitives.system_name` + `primitives.direction` + `primitives.operations` + `primitives.data_class`. Example: an item with `direction: "bidirectional"`, `operations: "extract assessments, register comments, publish opinions"`, `data_class: "vendor assessment data"`, `ownership: "existing — customer"` becomes "shall consume existing [Customer] [System] APIs to extract assessments, register comments, and publish opinions on vendor assessment data."
- **Non-Functional Requirements:** for each NFRs item, the NFR is drafted from `primitives.pillar` + `primitives.target_type` + `primitives.target_value`. The `responsibility_boundary` primitive on Reliability items determines whether the consultancy scope rule phrasing is applied.
- **Out-of-Scope:** for each Scope item with `direction: "out_of_scope"`, the OOS bullet is drafted from `primitives.subject` + `primitives.rationale`.
- **Assumptions:** for each Constraints item with `actor_responsibility: "customer"`, the Assumption is drafted as "[Customer] must [description]. [If actor primitive specifies consequence: that consequence; otherwise default to "Delays will result in proportional timeline extension and additional cost."]"
- **Activities and Deliverables:** structured around the `engagement_shape` primitive in Identity. Assessment shape produces workshop-driven activities and document deliverables; Greenfield/Brownfield produce implementation activities and software deliverables.
- **Risks:** drafted around Integrations with `criticality: "core_in_scope"` plus `ownership: "existing — customer"`, and around any Decisions with `decision_type: "pending"`.

This is the contract that makes `sow-discovery` and `sow-generator` worth splitting. Without these primitives, both skills fall back to re-reading raw artifacts and the original failure mode returns.

---

## Validation rules `sow-generator` enforces on load

When `sow-generator` loads the Manifest at its own Phase 1 entry, it checks:

1. `manifest_version` is recognized.
2. Every `extracted_items[].source[].artifact_id` resolves to an entry in `inventory`.
3. Every `cross_refs` entry resolves to a sibling item ID.
4. `self_audit.all_required_categories_covered` is `true`.
5. `gaps.hard_gaps[].blocks_sow_generation == true` items are resolved or the user is re-prompted before generation begins.
6. **At least one Identity item exists with `primitives.engagement_shape != "not_stated"`.** Without engagement shape, the generator cannot structure its output.

If any check fails, `sow-generator` reports the failure to the user and asks whether to re-run `sow-discovery` or proceed with `[TO BE DEFINED]` placeholders.