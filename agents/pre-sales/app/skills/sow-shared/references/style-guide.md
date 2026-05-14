# SOW Style Guide — cross-cutting writing rules

This file is the **binding quality contract** for every SOW section produced
by any workflow skill (`sow-architecture`, `sow-requirements`,
`sow-delivery-plan`, `sow-scope-boundaries`, `sow-narrative`, `sow-revision`).
It hosts only the rules that apply across all sections. Section-specific
rules (Executive Summary template, FR patterns, NFR WAF pillars, OOS
categories, assumption patterns, architecture rules, workstream structure,
roles rules, …) live in the matching workflow skill, never here.

## Language surface note

This guide defines the quality contract for the final English `.docx`
content. When the same section is presented in a user-facing review, the
structure, depth, and meaning remain binding, but the wording must be
localized to the user's conversation language. Exact English template
sentences (Executive Summary opening, Google funding sentence, etc.) are
mandatory only for the final `.docx` payload — and live in `sow-narrative`,
not here.

## General Writing Rules

- Clear, professional English. Active voice.
- Specific and quantifiable — no "up to", "various", "several".
- Exact targets or narrowly defined ranges.
- No marketing language in technical sections.
- Focus on **how**, not **what** or **why**.
- **Professionalize all input.** Rewrite user content in enterprise consulting
  language. Never echo casual phrasing. Preserve meaning, elevate tone.
- Never fabricate data. Use `[TO BE DEFINED]` for genuinely missing info.
- Mark inferred content with the conversation-language equivalent of
  "(inferred)" — `(inferido)` in Portuguese/Spanish, `(inferred)` in English,
  `(inféré)` in French. Never bury inference under un-flagged confidence.
- Use exact quantities — never approximate ranges as "up to" or "several".
- Never include hours, hourly rates, or rate cards anywhere in the document.
- Use scope boundary language where applicable: "strictly limited to",
  "exclusively", "explicitly excluded".

---

## Self-sufficiency contract (cross-cutting rule — applies to FRs, NFRs, OOS, Assumptions)

The SOW is a contractual document. It MUST be readable and executable
WITHOUT opening any other artifact. Every Functional Requirement,
Non-Functional Requirement, Out-of-Scope item, and Assumption is
self-contained.

**Rule 1 — No external scope delegation.** No FR, NFR, OOS, or Assumption
may delegate its definition to an external document. The document being
drafted IS the scope. Phrasings that reference external artifacts to define
what is or isn't in scope are FORBIDDEN, including:

- "as listed in [Appendix / Annex / Matrix / Capability Sheet / Attachment]"
- "all capabilities defined in [external doc name]"
- "strictly limited to the items in [external doc]"
- "the scope is bounded by [external doc]"
- Any equivalent phrasing in any language that makes scope dependent on
  opening a separate file.

When the discovery Manifest references a source document by name (capability
matrix, RACI, technical annex, kickoff deck, etc.), that source name is
metadata for the agent during generation — it MUST NOT appear in any
FR/NFR/OOS/Assumption text in the SOW. Translate the items themselves into
the SOW; never translate a pointer to the items.

**Rule 2 — Map Manifest items into requirements with flexible grouping.**
Each Manifest item whose category is in `[Briefing, Integrations, NFRs]`
MUST appear named literally (by name, feature, or direct description) in at
least one FR or NFR. Grouping is allowed when natural, required when not:

- **Group into ONE requirement** when items are instances of the same
  operation differing only by target/channel/system/parameter (e.g., items
  "X for A", "X for B", "X for C" → one FR: "The platform shall provide X
  for A, B, and C") — or when items are synonyms of the same concept (e.g.,
  "STT" and "Speech-to-Text support" → one FR).
- **Keep SEPARATE requirements** when items describe functionally distinct
  capabilities, even if related by domain. Examples: short-term
  session-scoped memory vs long-term cross-session memory are different
  mechanisms — two FRs; model training vs model serving are different
  lifecycle stages — two FRs.

**Self-sufficiency invariant (non-negotiable):** grouping consolidates
operations but NEVER erases names. If you group 4 channels into 1 FR, all
4 channel names appear in that FR's text. Every Manifest item must be
findable in the SOW by name.

**Decision test:** would the grouped requirement read naturally as
"operation X parameterized by [list]"? If yes, group. If you have to invent
a fake parent concept to glue items together (e.g., "STT", "DLP", and "RAG"
under "AI capabilities"), separate. When in doubt, separate — one extra
FR costs less than an artificial umbrella.

**Rule 3 — Counter ranges are floors when the Manifest is rich.** The
targets defined per section in the section skills (10-20 FRs, 5+ NFRs,
20-30 OOS, 15-25 Assumptions) are MINIMUM floors and SOFT design targets.
They are NEVER hard caps. When the Manifest covers more capabilities than
the soft target accommodates, exceed the target. A Manifest with 60
distinct capabilities in `[Briefing, Integrations, NFRs]` produces 50+
FRs/NFRs — that is the correct outcome, not an error to be compressed.
Compression that creates an "umbrella requirement" pointing at a Manifest
category or external document is a SEVERE failure of this contract.

**Anti-patterns (all rejected, in any language):**

> FR-XX: The solution shall implement all capabilities listed in the customer's capability matrix.
>
> FR-XX: The scope is strictly limited to the items defined in the project's technical annex.
>
> FR-XX: The solution shall comply with all requirements detailed in the reference documentation provided by the Customer.

Each is rejected on three grounds: (a) delegates scope to an external
document (Rule 1), (b) collapses N capabilities into one umbrella (Rule 2),
(c) makes the SOW non-self-sufficient. The defect is structural — it does
not depend on the document's specific name. The correct treatment for any
of them is to expand into N individual FRs, one per capability, each
naming the capability literally in the FR text — even if N is 50 or more
(Rule 3).

---

## Paragraph breaks in long-form narrative (cross-cutting rule)

Insert `\n\n` (encoded in the JSON payload sent to the document tool)
between distinct topics within long-form narrative fields, so the rendered
SOW reads as separate paragraphs. Use `\n\n` only; never emit a lone `\n`.

The trigger is semantic, not numeric — a paragraph break marks a topic
shift (business context → business value, one design decision → the next,
narrative → required closing sentence). Word count alone never justifies
a break.

**Apply to:** narrative fields covering multiple topics — Executive Summary,
Partner Overview, Customer Overview, Architecture Description.

**Calibration (anchored to `references/scope-examples.md`):**

- **Executive Summary** — when its skill defines a required content order,
  each item is a candidate paragraph boundary. Merge adjacent items only
  when they share a single line of reasoning. The Template-compliant
  example in `references/scope-examples.md` shows ~5 paragraphs for
  250-450 words; the required opening sentence and required Google funding
  sentence are never collapsed into surrounding prose.
- **Architecture Description** — typically 2-3 paragraphs covering, in
  order: primary data flow, key service justifications, cross-cutting
  concerns.
- **Partner / Customer Overview** — 1-2 paragraphs (4-6 lines per the
  overview rules in `sow-narrative`); split only on a clear topical shift
  (e.g., history → current market position).

**Do NOT apply to:** single-sentence fields (individual FRs, NFRs,
deliverables, tasks, success criteria, role responsibilities), tabular
content already split into rows, list items already split into entries,
or short labels and names.

**Anti-pattern (rejected):** a long-form narrative field delivered as one
uninterrupted block, even if every sentence individually flows. The
reviewer cannot scan for business value, technical outcomes, or scope
boundary because they are not visually separated. Correct treatment:
apply `\n\n` between the required content items, matching the
Template-compliant example's shape in `references/scope-examples.md`.

---

## Formatting hygiene

- Consistent heading hierarchy for TOC generation.
- Tables: clear headers, consistent column counts, no empty cells.
- Bullet points for lists. Bold for key terms only — not as a substitute
  for paragraph structure.
- Never include hours, hourly rates, rate cards, or fee schedules anywhere.
- No raw Manifest phrasing or user casual phrasing in any rendered output;
  professionalize before emitting.
