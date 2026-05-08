# Extraction Rules

This file is the rubric `sow-discovery` Phase 1 applies to every artifact. Load it before extracting and apply it consistently across the full artifact set.

**Primitives must come from this file at extraction time, not from memory.** Each category below defines a closed list of required primitives. When extracting, you read the primitive names from the category section in this file and populate them on the item. Do not improvise primitive names from training memory — the schema is intentionally specific, and improvised primitives will not match what `sow-generator` consumes downstream.

Manifest items require: `category`, English `value`, English `value_detail`, complete category `primitives`, `source[{artifact_id, anchor}]`, `confidence: stated|implied`, and `notes.enumeration_index` for Path B. Unknown primitive = `not_stated`; never omit primitive keys.

## Engagement shapes

`assessment` = discovery/design only, no implementation. `greenfield` = new build. `brownfield` = enhancement of existing workload. `migration` = move workload/data/platform. `foundation` = GCP org/IAM/network/security baseline. `not_stated` = hard gap.

## Category cards

### 1. Identity

- **Purpose:** customer/project/funding/partners/shape.
- **Primitives:** `customer`, `project_name`, `funding_type` (DAF|PSF), `partner`, `secondary_partners`, `engagement_shape`, `engagement_phase`, `sector`, `geography`.
- **Capture:** legal entity/brand, codename, phase/v1/v2 marker, funding, partner roles, sector/geography, "discovery only / no implementation / enhance / migrate / landing zone".
- **Ignore:** marketing taglines, generic company descriptions, attendee names without responsibility.
- **Anchor:** page + section; cover = page 1.

### 2. Briefing

- **Purpose:** problem, solution direction, capability, high-level approach.
- **Primitives:** `problem_statement`, `business_capability`, `delivery_mode`, `business_outcomes`, `pilot_scope`.
- **Capture:** customer problem/rationale, proposed solution, technical approach, outcomes, pilot/PoV scope.
- **Ignore:** vague "modernize / transform / innovate" with no specifics.
- **Anchor:** page + paragraph or section + paragraph.

### 3. Integrations & Data Sources

- **Purpose:** every system/API/data source/channel/IdP/shared platform/existing dependency the solution connects to.
- **Primitives:** `system_name`, `direction` (source|target|bidirectional|not_stated), `operations`, `data_class`, `protocol` (REST|gRPC|batch file|CDC|event stream|SDK|message queue|SAP connector|other-named|not_specified), `ownership` (existing — customer|existing — third party|to_be_built|existing — partner platform|not_stated), `criticality` (core_in_scope|referenced_only|excluded|not_stated).
- **Capture:** named systems/APIs/vendors/services/databases, channels, auth mechanisms, protocols, data classes, existing shared GCP/customer services.
- **Ignore:** unnamed "internal systems / multiple channels" → flag as gap; GCP services newly introduced by the solution unless they are an existing shared dependency.
- **Anchor:** table cell/row, bullet, diagram label, or page + sentence.
- **Granularity (binding):** one named system / channel / source / auth method / protocol / data class = one item. Apply the operational tests in the Cross-cutting rules section.

### 4. Scope

- **Purpose:** included/excluded/deferred work, team, commercial shape.
- **Primitives:** `direction` (in_scope|out_of_scope), `subject`, `rationale`, `team_role`, `payment_model_attribute` (Fixed Price|Milestone-based|Time-and-Materials|Pure SOW|not_stated).
- **Capture:** explicit in-scope items, explicit exclusions, no-development/deployment/configuration statements, team roles/allocation, payment model, pilot vs rollout boundary.
- **Ignore:** future opportunities unless explicitly deferred.
- **Anchor:** section + paragraph/bullet.

### 5. NFRs

- **Purpose:** non-functional targets and acceptance framing.
- **Primitives:** `pillar` (Security|Reliability|Performance|Operational Excellence|Cost Optimization|Documentation|Tooling|Compliance|Other), `target_type` (quantitative|qualitative|compliance_framework|architectural_pattern), `target_value`, `responsibility_boundary` (partner_during_engagement|customer_post_handover|shared|not_stated).
- **Capture:** latency, throughput, accuracy, availability, volume, scalability, retention, RTO/RPO, compliance frameworks, encryption/IAM/KMS/audit, fallback/resilience, observability, cost, tooling constraints framed as quality requirements.
- **Ignore:** vague "fast / reliable / secure" without target or pattern → flag as gap.
- **Anchor:** requirement row/cell or page + sentence.
- **Reliability/availability NFRs MUST populate `responsibility_boundary` or be flagged as ambiguity.**

### 6. Timeline

- **Purpose:** project time structure.
- **Primitives:** `marker_type` (total_duration|phase_boundary|kickoff_date|end_date|milestone|dependency), `value`, `phase_label`, `dependency_target`.
- **Capture:** duration, start/end dates, week ranges, phase durations, milestones/deadlines, time-based approvals/dependencies.
- **Ignore:** "ASAP / urgent" as timeline facts.
- **Anchor:** page + section + sentence or schedule row.

### 7. Constraints

- **Purpose:** non-negotiable conditions shaping delivery.
- **Primitives:** `constraint_type` (data_residency|compliance_framework|network_access|tooling|gcp_org_structure|approval_gate|availability_window|baseline_alignment|other), `description`, `actor_responsibility` (partner|customer|both|not_stated), `consequence_if_violated`.
- **Capture:** residency, compliance, VPN/firewall/proxy/network, existing GCP org/folders/projects/policies, approval gates, mandated tools, resource availability windows, brownfield baseline alignment, migration parallel-run requirements.
- **Anchor:** page + section + sentence.

### 8. Decisions & Alignments

- **Purpose:** made or pending decisions and responsibility assignments, especially from transcripts/chats.
- **Primitives:** `decision_type` (architectural|commercial|scope|technology_choice|process|pending|responsibility_assignment), `decision_text`, `decided_by`, `expected_resolution_when_pending`.
- **Capture:** decided/agreed/confirmed/aligned statements, technology choices and rejected alternatives, deferred decisions, responsibility assignments.
- **Ignore:** speculative "maybe / could / might" unless resolved later.
- **Anchor:** transcript speaker+timestamp, chat sender+timestamp, meeting note bullet, or page + sentence.

## Cross-cutting rules

**No collapse — one concept, one item.** This is the most important rule in this file. When source mentions multiple distinct subjects together — multiple systems, channels, identity mechanisms, compliance frameworks, stakeholders — each subject becomes its own `extracted_item`. Connectives in source text are signals of multiplicity, not signals of a grouped item.

**Operational tests, applied at extraction time:**

- **Comma test.** If your `value` field contains a comma between two distinct nouns, split into separate items. Apply regardless of how source phrased them together.
- **Connective test.** If your `value` field contains " and ", " e ", " y ", " / ", or "&" between two distinct subjects, split.
- **Acronym-group test.** Source text of the form "Concept (X/Y/Z/W)" or "Concept including X, Y, Z" lists members of the umbrella concept. Members are individual items; the umbrella label is editorial framing, not a system. Both the members and the umbrella may warrant items, but typically in different categories — the umbrella often becomes a Briefing or NFR item, the members become Integrations items.
- **Visual layout test.** A bullet list of N items produces N items. A capability matrix with N labeled rows produces close to N items (minus header/category-label rows). A diagram with N labeled boxes produces close to N items. Visual structure dictates count; do not summarize visual elements into prose like "the diagram shows the platform components".

*Illustrative patterns* (these are concrete instantiations of the principles above; they apply identically across all project domains):

- Multiple distinct subjects listed together as a single line in a diagram or table (e.g., three product names labeling one architectural layer) → one item per distinct subject. Visual grouping is editorial layout, not a single subject.
- Multiple options listed in one bullet under an umbrella label (e.g., a single bullet listing N customer-facing channels of a platform, or N supported authentication mechanisms, or N data sources of a pipeline) → N items, one per option.
- A capability matrix or RACI with substantially more visible rows than items extracted (low extraction-to-row ratio, regardless of absolute count) → defect by definition. Such artifacts exist precisely to enumerate distinct items; producing few items from a multi-row artifact means visible content was silently dropped.

**Category duplication is allowed.** If one fact belongs to multiple categories, create multiple items and use `cross_refs` to link them by ID.

**Language.** Values and primitives are in English. Preserve important source wording in `notes.original_language_quote`.

**Anchors — use the finest available precision:** PDF page+section+paragraph/sentence; table name+row+column/cell; slide number+region+label; screenshot region+literal label; transcript speaker+timestamp; chat sender+timestamp; guided conversation `turn N / Block X`.

**Artifact-shape reminders.** Capability matrices require near row-level coverage. RACI matrices: enumerate every row and every responsibility assignment; partner Responsible/Accountable cells matter most, but do not silently drop other explicit responsibilities. Screenshots/images: count visible labels/boxes/rows/annotations before extracting. Transcripts/chats: capture informal decisions, contradictions, responsibilities, and pending items.

## Triage tier defaults

When classifying artifacts in Phase 0.5, use these defaults; the user can override:

- **Primary** (deepest pass, processed first): capability matrices, RACI tables, requirement specifications, integration lists, NFR tables, formal proposals with structured sections, scope sheets. These artifacts densely enumerate in-scope items.
- **Secondary** (processed after Primary): meeting transcripts, kick-off slide decks, executive briefings, architecture overview diagrams, alignment notes, project chat logs. These confirm or contextualize what Primary artifacts state.
- **Context** (processed last): standalone screenshots, individual slide snapshots, short attachments, peripheral notes, supporting reference material.

Tier assignment is based on artifact type, name, and the Phase 0 hypothesis — not on opening the artifact. Honor user adjustments over defaults.

## When the user has no artifacts (Path A — Guided Discovery)

The conversation itself is the source. Inventory has a single entry `A1` of type `user-briefing` named "guided conversation". Anchors are `"guided turn N / Block X"` where N is the user message number in the interview and X is the block label (1, 2, 2.5, 3, or 4). The same eight-category extraction applies, but is performed incrementally — one block per user response — rather than scanning a finished document. See `guided-discovery-blocks.md` for block-by-block instructions.
