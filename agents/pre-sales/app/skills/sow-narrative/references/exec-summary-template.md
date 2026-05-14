# Executive Summary — template wording and rules (binding)

The Executive Summary is the document's opening narrative — the only
section a busy executive sponsor may read in full. It synthesizes the
engagement's business value, technical outcomes, scope boundary, and
funding source. Stored in `sow_data['executive_summary']` as a single
string (multi-paragraph; apply `\n\n` between paragraphs per
`sow-shared/references/style-guide.md` → "Paragraph breaks in long-form
narrative").

## Final `.docx` requirement — exact English template wording

In the final `.docx`, the Executive Summary MUST start with this exact
English sentence pattern, replacing the bracketed instruction with
project-specific content:

> "This Statement of Work (SOW) outlines the scope, activities,
> deliverables, and estimated timelines for [project-specific business
> value and high-level technical outcomes]."

The final `.docx` Executive Summary MUST end with this exact English
sentence, selecting the correct funding type from the Manifest:

> "This scope of work will be funded with Google [Deal Acceleration Funds
> (DAF) | Google Partner Services Funds (PSF)]."

If the funding type is not stated:

> "This scope of work will be funded with Google [TO BE DEFINED]."

These English sentences are **mandatory template text** for the final
document, not examples. Do NOT leave bracketed instruction text in the
document; only the funding placeholder may remain `[TO BE DEFINED]` when
funding is genuinely not stated.

## User-facing review requirement — localized template meaning

When presenting the Executive Summary in a user-facing review, use the
conversation language. Preserve the same structure and meaning as the
required template wording, but localize it naturally.

For example, in Portuguese the review version may start with:

> "Este Statement of Work (SOW) descreve o escopo, as atividades, os
> entregáveis e os cronogramas estimados para [valor de negócio
> específico do projeto e resultados técnicos de alto nível]."

And may end with:

> "Este escopo de trabalho será financiado com recursos Google [A
> DEFINIR]."

A non-English review is compliant when it preserves the required template
meaning in the user's language. The final `.docx` is compliant only when
it uses the exact English template wording above.

## Depth requirements

- **Implementation, platform, migration, foundation, or multi-phase
  engagements**: 250-450 words.
- **Assessment-only engagements**: 150-250 words.

A 100-word Executive Summary is a defect — too shallow for any engagement
that produces a SOW. A 600-word Executive Summary is a defect — it should
move detail to the body sections.

## Required content order (7 items)

The Executive Summary covers, in order:

1. **Customer business context** and why the engagement matters.
2. **Business value expected** from the engagement, such as operational
   efficiency, scalability, governance, modernization, acceleration,
   risk reduction, or improved customer experience.
3. **High-level technical outcomes**, naming the platform, workload,
   systems, integrations, or Google Cloud services when known.
4. **Main activities and deliverables** at a high level — NOT a detailed
   task list.
5. **Timeline or engagement shape** when material to scope (e.g., "a
   10-week, three-phase engagement").
6. **Scope boundary statement**, including what the engagement is
   strictly limited to when relevant.
7. **Required Google funding sentence**: localized meaning in user-facing
   reviews, exact English template wording in the final `.docx`.

Each item is a candidate paragraph boundary. Merge adjacent items only
when they share a single line of reasoning. The Template-compliant example
in `sow-shared/references/scope-examples.md` shows ~5 paragraphs for
250-450 words; the required opening sentence and the required Google
funding sentence are NEVER collapsed into surrounding prose.

## Quality bar

- Do NOT write a shallow project description that merely restates the
  solution.
- Do NOT start directly with a technical implementation detail.
- Do NOT use generic phrases such as "digital transformation",
  "innovative solution", or "leveraging AI" unless immediately grounded
  in the customer's actual business context.
- Do NOT include unsupported metrics, market claims, or benefits not
  present in the Manifest, approved inference, web-verified research, or
  user-approved content.
- The Executive Summary must be readable by an executive sponsor AND
  still be technically credible to a reviewer.
- Bullet points are allowed only when they improve readability; they are
  not required by default. The required template opening and funding
  sentence remain mandatory even when bullets are used.

## Anti-patterns

| Anti-pattern | Why it fails | Fix |
|---|---|---|
| Executive Summary as one uninterrupted block | Reviewer cannot scan for business value, technical outcomes, or scope boundary | Apply `\n\n` between the required content items per the paragraph-break rule |
| Opening sentence rephrased ("This SOW describes a project that ...") | Template requirement violation; the exact opening is mandatory | Use the exact English opening in the `.docx`; localize meaning in the review |
| Funding sentence absent | Direct contractual gap | Add the funding sentence as the final paragraph (or `[TO BE DEFINED]` when truly unknown) |
| Executive Summary that names no GCP services | Reader cannot tell what the technical solution looks like | Name the key GCP services in item 3 (high-level technical outcomes) |
| Marketing language ("transformational", "best-in-class") | Not consulting tone | Replace with concrete project context |
| Detailed task list in items 4 | Tasks belong in Activities, not the Executive Summary | Lift to engagement-level outcomes (workstream count, deliverable categories) |
