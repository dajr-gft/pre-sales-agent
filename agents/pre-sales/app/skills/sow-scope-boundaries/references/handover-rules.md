# Handover rules — binding boundaries

The handover boundary is the contractual moment at which operational
ownership transfers from the partner to the customer. The SOW must make
this boundary explicit in three places:

1. **NFR Reliability** — the architectural quality is delivered; ongoing
   availability stays with the customer (handled by `sow-requirements`).
2. **OOS Category 17** — uptime / SLA / availability percentages are
   excluded from the engagement (handled by `references/oos-categories.md`).
3. **Assumptions** — the handover event itself is a customer obligation
   with consequences (handled here).

Stored in `sow_data['handover_disclaimers']` as a short list of strings
that the document template renders near the end of the scope section.

## Required handover disclaimers

The handover_disclaimers list must contain at least the following
statements, adapted to the engagement (canonical English below; translate
for user-facing reviews while preserving contractual meaning):

### Operational ownership

> "Upon successful completion of the knowledge-transfer milestone, the
> Customer assumes operational ownership of the deployed solution,
> including but not limited to incident response, capacity planning,
> performance tuning, security patching, and ongoing maintenance. The
> Partner's responsibilities for the engagement conclude at this
> milestone unless extended via a separately-signed Change Request or
> Statement of Work."

### Production availability boundary

> "The Partner delivers the solution as architected for the reliability
> patterns described in the Non-Functional Requirements (multi-region
> deployment, failover, retry policies, health checks). The Partner does
> NOT commit to a production-availability percentage or service-level
> agreement (SLA); sustained production availability is the Customer's
> responsibility after handover."

This is the contractual mirror of the Reliability NFR phrasing in
`sow-requirements/references/nfr-waf-pillars.md` → Pillar 2 — Reliability.
The two statements (NFR + handover) must agree.

### Hypercare / warranty (when applicable)

When the engagement includes a hypercare window, name it explicitly:

> "The Partner provides a hypercare support window of [N] business days
> after the knowledge-transfer milestone. During the hypercare window,
> the Partner assists the Customer with operational issues caused by
> defects in the delivered solution; the Customer remains responsible
> for environmental, infrastructure, and out-of-scope issues. The
> hypercare scope, hours, and SLAs are defined in
> [reference deliverable name]."

When the engagement does NOT include hypercare, add the explicit
exclusion:

> "No hypercare, post-go-live support, or warranty period is included in
> the engagement scope. Operational issues arising after the
> knowledge-transfer milestone fall under a separately-engaged support
> contract or a new Statement of Work."

## AI / ML non-determinism disclosure (mandatory when applicable)

When the solution uses generative AI, LLMs, ML models, or any
non-deterministic component, the handover list MUST include:

> "The solution incorporates generative AI / machine-learning components
> whose outputs are non-deterministic by design. The Partner does NOT
> guarantee 100% accuracy, factual correctness, or absence of
> hallucination in model-generated content. All AI-generated outputs are
> advisory and subject to human review before they are acted upon by
> downstream business processes. Model behavior may drift over time as
> the underlying foundation model is updated by its provider; ongoing
> validation of model output quality is the Customer's responsibility
> after handover."

The validation critic flags missing AI disclosures as
`disclosures:missing_ai_nondeterminism_disclosure`. The fix is always to
add this statement — never to delete the AI/ML component from the
solution description.

## Cross-section coherence (Handover ↔ NFR ↔ OOS)

Walk the three surfaces before emitting:

1. The Reliability NFR (in `functional_requirements` /
   `non_functional_requirements` already populated by `sow-requirements`)
   uses the architectural-pattern phrasing — NOT a production percentage.
2. OOS Category 17 (uptime/SLA denial) is present in `out_of_scope`.
3. Operational-ownership and production-availability handover statements
   are present in `handover_disclaimers`.
4. If the architecture includes any AI/ML component, the AI/ML
   non-determinism disclosure is present in `handover_disclaimers`.

If any of (1)-(4) is missing, fix it — but only the surface owned by this
skill ((2)-(4)). For (1), the FR/NFR list is already frozen by
`sow-requirements`; if it carries a production-percentage phrasing, the
appropriate fix is a finding back to `sow-revision`, not a silent rewrite
here. (See `sow-shared/references/id-stability-rules.md` for the patch
contract.)

## Anti-patterns

| Anti-pattern | Why it fails | Fix |
|---|---|---|
| Handover list missing the operational-ownership statement | The transfer moment is not contractually defined | Add the canonical operational-ownership disclaimer |
| Handover list missing the production-availability boundary | The Reliability NFR sits unguarded by a matching handover disclaimer | Add the production-availability handover statement |
| AI/ML solution without the non-determinism disclosure | Direct contractual exposure on model accuracy / hallucination | Add the AI/ML non-determinism statement |
| Hypercare promised verbally but not in the handover list | Customer reads scope as ongoing support | Either add explicit hypercare scope + window, or add the explicit exclusion |
| Handover statement softened to "the Customer is expected to manage" | "Expected to" is not a contractual transfer | Use "the Customer assumes operational ownership of ..." with the explicit milestone |
