---
name: sow-narrative
description: >
  Produces the narrative fields of `sow_data` — `executive_summary`,
  `partner_overview`, `customer_overview`, and `customer_primary_domain`
  — by synthesizing the snapshots produced by the upstream section
  skills (requirements, delivery, scope, architecture). Runs the four
  web search queries for partner/customer enrichment and the customer
  homepage. Loaded by `sow-orchestrator` LAST in Phase 2 (Step E), so
  the Executive Summary has every other section to synthesize from.
metadata:
  pattern: synthesis + web-search-enrichment
  produces: executive_summary, partner_overview, customer_overview, customer_primary_domain
  inputs: extraction_manifest, sow_data snapshot (all other sections), web search tool
  upstream-skill: sow-orchestrator
  references-skill: sow-shared
---

# SOW Narrative

**Scope of this skill.** The three narrative fields — Executive Summary,
Partner Overview, Customer Overview — plus the `customer_primary_domain`
captured from a dedicated web search query. This is the LAST section
skill loaded by the orchestrator because the Executive Summary
synthesizes content from every other section.

## Reference authority and depth rules

The loaded reference files are the **binding quality contract** for the
narrative section — not optional examples, loose inspiration, or style
suggestions.

Priority order for generated/patched content quality:

1. `sow-shared/references/style-guide.md` — binding cross-cutting
   writing rules and the paragraph-break rule (especially relevant for
   the Executive Summary's 5-paragraph shape).
2. `sow-shared/references/id-stability-rules.md` — binding ID
   preservation when revising existing narrative content.
3. `sow-shared/references/language-rules.md` — binding language hygiene
   (the Executive Summary localizes the template wording for reviews,
   but the final `.docx` carries the exact English opening + funding
   sentence).
4. `references/exec-summary-template.md` — binding Executive Summary
   contract: exact English template wording, depth requirements,
   7-item content order, quality bar.
5. `references/overview-rules.md` — binding Partner/Customer overview
   rules and the 4 web search queries (the 4th captures the homepage
   domain for the logo).
6. This `SKILL.md` — workflow orchestration only (search ordering,
   synthesis order, when to skip web search).

If this skill says to do X and a reference defines how X must be
written/patched, the reference controls the content. Do not simplify,
shorten, or reinterpret reference requirements unless the reference
explicitly allows it.

**Brevity scope rule:** instructions such as "brief", "concise",
"direct", or "short" apply only to conversational orchestration
messages, confirmations, and error handling. They do NOT apply to SOW
narrative content. For the Executive Summary, Partner Overview, and
Customer Overview, follow the depth, structure, minimums, required
wording, and quality rules from the loaded references.

---

## Workflow — search, then synthesize

The narrative is synthesized from BOTH the upstream sow_data snapshot
(requirements, delivery, scope, architecture all already populated) AND
the web search results captured in this turn. The Executive Summary
runs LAST because it synthesizes everything else.

**Pre-step — Load and apply references (mandatory gate before any drafting):**

- `load_skill_resource(skill_name="sow-shared", file_path="references/style-guide.md")`
  — **Binding quality contract.** Paragraph-break rule applies to the
  Executive Summary.
- `load_skill_resource(skill_name="sow-shared", file_path="references/scope-examples.md")`
  — **Quality floor.** The Template-compliant Executive Summary example
  there is the canonical shape.
- `load_skill_resource(skill_name="sow-shared", file_path="references/language-rules.md")`
  — **Binding language hygiene.** The localized-meaning rule for the
  Executive Summary template lives here.
- `load_skill_resource(skill_name="sow-narrative", file_path="references/exec-summary-template.md")`
  — **Binding Executive Summary contract.**
- `load_skill_resource(skill_name="sow-narrative", file_path="references/overview-rules.md")`
  — **Binding Partner/Customer overview rules and search queries.**

If you are patching an existing narrative (not generating from scratch),
also load
`load_skill_resource(skill_name="sow-shared", file_path="references/id-stability-rules.md")`
and treat the Patch contract there as overriding any sub-step instinct
to regenerate the narrative.

Use as input:

- The Extraction Manifest — `manifest.extracted_items` for categories
  `Identity` and `Briefing`.
- The current `sow_data` snapshot with every other section already
  populated by the upstream skills (requirements, delivery, scope,
  architecture). The Executive Summary synthesizes from this snapshot.

### (1a) Run the 4 web search queries (in order)

Apply `references/overview-rules.md` → "Web search queries". Run the
four queries sequentially using the search tool available to the
orchestrator. Queries 1-3 produce material for `partner_overview` and
`customer_overview`; query 4 EXCLUSIVELY produces
`customer_primary_domain`.

If the web search tool is unavailable or returns no usable results, fall
through to the no-search fallback in
`references/overview-rules.md` → "When web search is unavailable".

### (1b) Generate Partner Overview (`partner_overview`)

Apply `references/overview-rules.md` → "Partner Overview" with the
correct line range based on search availability (4-6 lines with search;
3-4 lines without). Anchor every numeric or factual claim to a
specific search result captured in this turn. Generic puffery
("leading", "world-class") is a defect — replace with concrete
specializations and capabilities.

### (1c) Generate Customer Overview (`customer_overview`)

Apply `references/overview-rules.md` → "Customer Overview". Same
line-range rule as Partner Overview. Use search results from queries
2 and 3 ONLY (NOT query 4). Generic "leader in their industry"
phrasings are defects — replace with named sector / segment / region.

### (1d) Capture customer_primary_domain

Apply `references/overview-rules.md` → "Domain capture rules" using
ONLY query 4's results. The domain comes from the URL field of an
official homepage result observed in this turn — not from prior
knowledge or constructed from the customer's name. Apply the format
rules (strip scheme, path, `www.`; lowercase; keep public-suffix TLD).

If no result returned an official homepage, leave the field UNSET. Do
NOT guess — a placeholder logo is preferable to a silently wrong logo.

### (1e) Generate Executive Summary (`executive_summary`) — LAST

The Executive Summary is generated LAST because it synthesizes
content from every other section. Apply
`references/exec-summary-template.md` → "Required content order" (7
items, in order) using the current `sow_data` snapshot as the source of
truth.

- The exact English opening sentence is mandatory in the final `.docx`;
  the user-facing review localizes the meaning to the conversation
  language.
- The funding sentence is mandatory — either the DAF or PSF variant
  (from the Manifest's Identity category) or `[TO BE DEFINED]` when the
  funding is genuinely not stated.
- Depth: 250-450 words for implementation / platform / migration /
  multi-phase engagements; 150-250 words for assessment-only.
- Apply paragraph breaks per
  `sow-shared/references/style-guide.md` → "Paragraph breaks in
  long-form narrative". The Template-compliant example in
  `sow-shared/references/scope-examples.md` shows ~5 paragraphs for
  250-450 words.

### Step 1.5 — Reference Compliance (silent, mandatory before returning)

Self-test checklist (all items mandatory):

1. Does the Executive Summary start with the exact English template
   opening (final `.docx` payload)?
2. Does the Executive Summary end with the Google funding sentence
   (DAF / PSF / `[TO BE DEFINED]`)?
3. Does the Executive Summary cover all 7 required content-order
   items?
4. Is the Executive Summary length within the band for its engagement
   type (250-450 words / 150-250 words)?
5. Is the paragraph-break structure ~5 paragraphs (vs. one
   uninterrupted block)?
6. Are Partner Overview and Customer Overview within their line ranges
   (4-6 with search, 3-4 without)?
7. Is every numeric or factual claim in Partner / Customer Overview
   anchored to a search result captured in this turn (or sourced from
   the Manifest)?
8. If `customer_primary_domain` is set, was it extracted from a URL
   field observed in query 4's tool call (not constructed)?

If patching an existing narrative, add:

9. Is the Executive Summary's exact English opening + funding sentence
   preserved byte-for-byte? Adjusting body paragraphs is allowed;
   touching the opening/funding sentences requires the rationale to be
   explicit in the patch.

---

## What this skill does NOT do

- It does not produce FRs, NFRs, activities, deliverables, success
  criteria, timeline, roles, assumptions, OOS, CR policy, handover
  disclaimers, risks, or architecture artifacts. Those belong to the
  other section skills, loaded by the orchestrator BEFORE this one.
- It does not call `stage_sow`, present the user-facing Architecture
  Review, or call `confirm_phase_completion`. The orchestrator owns
  all three.
- It does not render the customer logo. The document template does
  that at render time using `customer_primary_domain`.
