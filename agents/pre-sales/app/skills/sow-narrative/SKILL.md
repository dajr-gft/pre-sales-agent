---
name: sow-narrative
description: >
  Produces the narrative fields of `sow_data` — `executive_summary`,
  `partner_overview`, `customer_overview`, and `customer_primary_domain`
  — by synthesizing the snapshots produced by the upstream section
  skills (requirements, delivery, scope, architecture). Runs the four
  web search queries for partner/customer enrichment and the customer
  homepage. Invoked by the root SOW orchestrator agent LAST in Phase 2
  (Step E), so the Executive Summary has every other section to
  synthesize from.
metadata:
  pattern: synthesis + web-search-enrichment
  produces: executive_summary, partner_overview, customer_overview, customer_primary_domain
  inputs: project documents (via load_artifacts), upstream bundles (all other sections), web search tool
  references-skill: sow-shared
  adk_additional_tools:
    - google_search_agent
---

# SOW Narrative

The three narrative fields + `customer_primary_domain`. Invoked LAST in
Phase 2 because the Executive Summary synthesizes every other section.

References below are binding — they override any paraphrase here. Depth
and exact template wording follow the references; "brief" and "concise"
apply to orchestration messages only.

## Load before drafting (mandatory)

via `load_skill_resource`:

- `sow-shared` / `references/style-guide.md` — quality contract + paragraph-break rule.
- `sow-shared` / `references/scope-examples-executive-summary.md` — Template-compliant Executive Summary calibration.
- `sow-shared` / `references/language-rules.md` — final `.docx` is English; user-facing review localizes meaning.
- `sow-narrative` / `references/exec-summary-template.md` — exact English template wording, depth, 7-item content order.
- `sow-narrative` / `references/overview-rules.md` — Partner/Customer rules + the 4 web search queries.

When patching: also `sow-shared` / `references/id-stability-rules.md`. The exact English opening + funding sentence are preserved byte-for-byte; rationale required to touch them.

## Inputs

Two required inputs:

1. **Upstream project context** — substantive content the narrative synthesizes:
   - **Path A (guided intake):** the persisted intake_summary at `state['app:sow:intake_summary']`. Use `customer_name`, `project_title`, `problem_goal`, and `solution_direction` from the persisted summary (all four are guaranteed to be real values — the intake tool rejects markers there) as the factual basis for the Executive Summary and the Customer Overview. Other intake fields marked `'(inferred)'` or `'[TO BE DEFINED]'` should NOT be folded verbatim into the narrative — by the time this skill runs, the prior section skills will have either filled inferred values or kept `[TO BE DEFINED]` placeholders inside their bundles; read those resolved bundles, not the raw intake markers.
   - **Path B (documental):** source documents loaded through artifacts for the current generation step. Use them as the factual basis for the Executive Summary and the Customer Overview.
   - The Partner Overview and `customer_primary_domain` come from the web search queries either way — neither Path replaces the enrichment step.
2. **Upstream bundles** — current `sow_data` snapshot with every other section already populated by the prior section skills. The Executive Summary synthesizes from these.

> **Coverage scope.** Section completeness against the upstream project context (walking it exhaustively) is the validation critic's `coverage` skill responsibility, not this skill's. Do not duplicate that walk here; produce content grounded in the inputs above and let the critic flag uncovered items in a later round.

## Generate (one turn)

1. **Run the 4 web search queries in order** per `overview-rules.md` → "Web search queries". Queries 1-3 feed `partner_overview` + `customer_overview`; query 4 feeds EXCLUSIVELY `customer_primary_domain`. If web search is unavailable, fall through to `overview-rules.md` → "When web search is unavailable".
2. **Partner Overview** (`partner_overview`). Apply `overview-rules.md` → "Partner Overview". 4-6 lines with search; 3-4 without. Every numeric/factual claim anchored to a query result captured in this turn. No marketing puffery.
3. **Customer Overview** (`customer_overview`). Apply `overview-rules.md` → "Customer Overview". Use queries 2 + 3 only (NOT query 4). Name the actual sector/segment/region — no "leader in their industry".
4. **`customer_primary_domain`**. Apply `overview-rules.md` → "Domain capture rules" using ONLY query 4. Extract from a URL field actually observed; never construct from the customer's name. If no official homepage returned, leave UNSET — a placeholder logo is preferable to a wrong one.
5. **Executive Summary** (`executive_summary`) — LAST. Apply `exec-summary-template.md`. Required content order (7 items). In the final `.docx`: exact English opening sentence + exact Google funding sentence (DAF / PSF / `[TO BE DEFINED]`). In user-facing reviews: localized meaning, structure and template intent preserved. Depth: 250-450 words for implementation/platform/migration/multi-phase; 150-250 for assessment-only. Apply paragraph breaks per `style-guide.md` (~5 paragraphs for 250-450 words).

## Before returning (workflow gate)

- Executive Summary final `.docx` opens with the exact English template sentence and ends with the exact funding sentence (DAF / PSF / `[TO BE DEFINED]`).
- All 7 required content-order items covered.
- Length within the band for the engagement type.
- Paragraph structure ~5 paragraphs (not one block).
- Overview line counts match search-available state (4-6 / 3-4).
- Every numeric or factual claim in overviews is anchored to a query result observed in this turn (or to the upstream context).
- `customer_primary_domain`, when set, was extracted from query 4's URL field (not constructed).
- When patching: exact English opening + funding sentence preserved byte-for-byte per `id-stability-rules.md`.

## Out of scope

- Does not produce FRs, NFRs, delivery, scope-boundary, or architecture artifacts (already populated upstream).
- Does not present the Architecture Review or call `stage_sow` / `confirm_phase_completion`.
- Does not render the customer logo — the document template does that at render time from `customer_primary_domain`.
