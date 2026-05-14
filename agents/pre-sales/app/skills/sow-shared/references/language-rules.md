# Language rules (non-negotiable, cross-cutting)

These rules apply to every user-facing output produced by any SOW workflow
skill — Inference Summary, Content Review, Architecture Review, Revision
Note, and the final `.docx` payload. They override any apparent language
signal in skill files or references.

## Language anchor (the only rule that decides output language)

Your output language is determined **EXCLUSIVELY by the user's most recent
message in the current conversation** — NEVER by examples or labels present
in any skill file or reference. All examples and labels in this library
are written in English as canonical references. Their presence does NOT
mean the output should be in English when the conversation is in another
language. Re-verify the conversation language before emitting any review,
confirmation, or Revision Note.

## Two surfaces, two languages

- **Conversation surface** (Phase 1-2 reviews, confirmations, redirects,
  Revision Notes, error messages, clarification questions) — ALWAYS in
  the user's conversation language. Detect the language from the user's
  first message and maintain it across every turn.
- **Document surface** (the final `.docx` payload passed to
  `generate_sow_document`) — ALWAYS in English, regardless of the
  conversation language. Section content (FRs, NFRs, OOS, Assumptions,
  Activities, Deliverables, Roles, narratives) is generated in English
  for the document and translated to the conversation language only when
  shown back in a review.

When a workflow skill emits the document JSON, every field that ends up in
the rendered `.docx` is English. When it presents a review of the same
fields, the content is rendered in the conversation language.

## Section labels in user-facing reviews

Section labels and headings shown to the user (e.g., "Functional
Requirements", "Architecture", "Executive Summary") MUST be translated to
the conversation language — not just the body text. Every English label
in a skill file is a canonical reference; translate it before presenting.

Examples (canonical references only — apply analogous translations for any
language the user is speaking):

- PT-BR: "Functional Requirements" → "Requisitos Funcionais"
- ES: "Functional Requirements" → "Requisitos Funcionales"
- FR: "Functional Requirements" → "Exigences Fonctionnelles"
- DE: "Functional Requirements" → "Funktionale Anforderungen"

## Examples in skill files are structural references, not language samples

Examples in any skill file are canonical English demonstrations of
**structure and tone**. When the conversation is in another language,
reproduce the same structure and tone in that language using your own
wording. Do NOT copy English text verbatim when the conversation is in
another language. Do NOT be influenced by English examples to switch your
output language away from the user's.

## Inferred-content marker

Mark inferred items (data the agent derived rather than read from the
Manifest) with the conversation-language equivalent of "(inferred)":

- PT-BR / PT-PT: `(inferido)`
- ES: `(inferido)` / `(inferida)`
- EN: `(inferred)`
- FR: `(inféré)` / `(inférée)`

The marker is mandatory whenever a value was not literally present in the
Manifest or in user-confirmed content. Burying inference under un-flagged
confidence is a quality defect.

## Conversational tone constraints

- No emojis anywhere — this is a professional pre-sales document.
- No casual phrasing in either surface; professionalize input before
  echoing it back (see `references/style-guide.md` General Writing Rules).
- Continuation replies ("yes", "go ahead", "ok", any language) authorize
  ONLY the immediately next workflow step — never a multi-step jump.
