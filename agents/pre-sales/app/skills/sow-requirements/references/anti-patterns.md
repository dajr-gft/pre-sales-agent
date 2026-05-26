# Requirements anti-patterns — binding rejection list

This file enumerates the defects that the validation critic finds most often
on the requirements section. Every workflow that generates or patches FRs /
NFRs must apply this list as a self-test before emitting the section.

## NFR anti-patterns

### `subjective_nfr_target` — vague qualitative NFRs

An NFR without a quantifiable target is a contractual hole. The validator
flags this as `contractual_exposure:subjective_nfr_target`.

| Rejected | Why | Acceptable replacement |
|---|---|---|
| `**Performance:** The system shall be fast.` | "fast" has no contractual meaning | `**Performance:** The system shall return responses within 2 seconds at p95 under nominal load.` |
| `**Security:** The system shall be secure.` | "secure" is a qualitative statement, not a target | `**Security:** The system shall encrypt data in transit with TLS 1.3 and at rest with AES-256.` |
| `**Reliability:** The system shall be reliable.` | "reliable" is a qualitative statement, not a pattern | `**Reliability:** The system shall be architected for high availability using multi-region Cloud Run and Cloud SQL automatic failover. Ongoing availability management remains with the Customer post-handover.` |
| `**Performance:** Sub-second response times when possible.` | "when possible" makes the target unenforceable | `**Performance:** API responses shall complete within 800 ms at p95 for synchronous reads.` |

### `production_availability_commitment` — uptime / SLA promises

Re-read `references/nfr-waf-pillars.md` → Pillar 2 — Reliability for the
exact rule. The phrasings below are FORBIDDEN regardless of language; their
analog in any other language is equally forbidden.

| Rejected (any language) | Fix |
|---|---|
| "shall maintain 99.5% uptime" | Replace with the canonical Reliability phrasing in `references/nfr-waf-pillars.md` |
| "guaranteed availability of 99.9%" | Same |
| "SLA of 99.95% availability" | Same |
| "uptime commitment of 99%" | Same |
| "the system will be available 99.X% of the time" | Same |

The Reliability pillar STILL must produce an NFR — just one that describes
the **architectural pattern** (multi-region, failover, retry, health checks)
without committing to a production percentage.

### `missing_pillar` — pillar coverage hole

A production-grade engagement is expected to cover all five WAF pillars. If
one is missing, justify it explicitly in your reasoning (e.g.,
"discovery-only engagement — Cost Optimization NFR omitted by design") or
add it.

## FR anti-patterns

### `generic_capability` — FR could describe any project

An FR that names no specific system, data flow, API, or behavior is a
defect. The validator flags this as `semantic_quality:generic_capability` or
similar.

| Rejected | Fix |
|---|---|
| `The solution shall provide an API.` | Name the API ("the credit opinion REST API"), the consumer, the resource. |
| `The solution shall integrate with the CRM.` | Name the CRM (Salesforce REST v58, SAP S/4HANA ECC), the integration mechanism (REST / SOAP / CDC / batch CSV), and what is exchanged. |
| `The solution shall store data.` | Name the data class, the store (BigQuery / Firestore / Cloud SQL), the retention. |
| `The solution shall be user-friendly.` | This is qualitative — either move to an NFR with a measurable target (response time, accessibility standard) or remove. |

### `compound_fr` — multiple behaviors in one FR

Two unrelated behaviors stitched together with "and" bypass the
one-behavior-per-FR rule. Split into separate FRs.

| Rejected | Fix |
|---|---|
| `The solution shall ingest CDC events from Oracle AND publish daily summaries to Salesforce.` | Two FRs: (1) ingest CDC events, (2) publish daily summaries. |

### `external_doc_delegation` — FR points at another document

Direct violation of the Self-sufficiency contract Rule 1 in
`sow-shared/references/style-guide.md`. Anti-pattern examples and the fix
live there; this file only re-states the prohibition.

## Cross-section anti-patterns (FR ↔ NFR)

### `fr_vs_nfr` — FR contradicts NFR

The validator's `contradictions:fr_vs_nfr` finding triggers when an FR
contradicts an NFR.

| Rejected pair | Why it conflicts | Fix |
|---|---|---|
| FR: "The system shall expose a public, anonymous read endpoint." + NFR: "The system shall authenticate every request via OIDC." | Mutually exclusive | Either (a) qualify the FR ("the public endpoint serves only the heartbeat probe; all data endpoints require OIDC") or (b) remove one of the two |
| FR: "The system shall retain raw payload logs for 7 years." + NFR: "The system shall implement a 90-day data retention policy across all stores." | Retention contradiction | Reconcile: separate retention policies per data class with a coherent FR + NFR pair |

### `fr_restated_as_nfr` — same behavior on both sides

If the same statement appears in both lists, only one is correct. Functional
behavior → FR; qualitative target → NFR. When in doubt, ask whether the
sentence is a `shall provide X` (FR) or `shall do X within Y` / `shall use
standard Z` (NFR).

## Self-test (apply before emitting the requirements section)

1. Does every FR name a specific system, data flow, API, or behavior?
2. Is every FR a single behavior (no compound `and` joining two unrelated
   capabilities)?
3. Does every NFR have a quantifiable target or a concrete standard?
4. Is the Reliability NFR phrased per the canonical pattern (architectural
   quality, no production uptime/SLA percentage)?
5. Do all five WAF pillars have at least one NFR (or is the omission
   explicitly justified)?
6. Does any FR contradict any NFR? If yes, fix before emitting.
7. Are inferred FRs and NFRs marked with the conversation-language
   equivalent of `(inferred)` per
   `sow-shared/references/language-rules.md`?
