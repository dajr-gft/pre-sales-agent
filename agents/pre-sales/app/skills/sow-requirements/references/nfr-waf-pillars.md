# Non-Functional Requirements — GCP WAF pillars (binding)

The Non-Functional Requirements section is a numbered table of `shall`
statements aligned with the five Google Cloud Well-Architected Framework
(WAF) pillars. Every NFR must reference quantifiable targets and concrete
standards — vague qualitative statements ("the system shall be performant")
are defects.

## Format

- Numbered table with unique IDs: `NFR-01`, `NFR-02`, ... assigned in order.
- Two columns: `Number` and `Description`. The description carries the
  pillar label inline (e.g., `**Security:** ...`) so the reader can see which
  pillar each NFR covers without an extra column.
- `Description` always uses **"shall" language**, same as FRs.
- One pillar per NFR. Cross-pillar bundling is a defect.

## Target

**Target: 5+ NFRs.** Floor, not cap. A production-grade engagement covers
all five pillars and may have 2-3 NFRs per pillar when the requirements
warrant it.

## The five WAF pillars

Generate at least one NFR per pillar relevant to the engagement. Skip a
pillar only if it is structurally inapplicable (rare — even discovery-only
engagements usually have Security and Cost NFRs).

### Pillar 1 — Security

Quantifiable, standard-anchored statements covering:

- Encryption in transit (`TLS 1.3` minimum, no `TLS 1.0` / `1.1`).
- Encryption at rest (`AES-256`, or KMS-managed when the customer requires).
- Authentication mechanism (`OIDC`, `SAML 2.0`, mTLS, service-account-based).
- Authorization model (`IAM roles`, scopes, least-privilege).
- Secrets management (`Secret Manager`, rotation cadence when known).

Pattern: `**Security:** The platform shall encrypt all data in transit with
TLS 1.3 and at rest with AES-256 using Cloud KMS-managed keys.`

### Pillar 2 — Reliability — **CONSULTANCY SCOPE RULE (non-negotiable)**

NFRs in this pillar describe **architectural qualities** the partner
implements during the engagement — they do NOT commit the partner to a
production uptime / availability percentage. The customer owns
ongoing-availability after handover.

**FORBIDDEN phrasings** (rejected in any language):

- "shall maintain [N]% uptime"
- "guaranteed availability of [X]%"
- "SLA of [Y]% availability"
- "uptime commitment of [Z]%"
- "the system will be available 99.X% of the time"
- Any variant that commits the Partner to a production availability
  percentage.

**REQUIRED phrasing** for the Reliability pillar (canonical English; localize
the labels but preserve the contractual meaning in any language):

`**Reliability:** The platform shall be architected for high availability
using [specific services/patterns — e.g., multi-region Cloud Run deployment,
Cloud SQL automatic failover, health checks, retry with exponential
backoff]. Ongoing availability management remains with the Customer
post-handover.`

This rule applies **strictly to availability/uptime/SLA phrasings**;
quantitative targets in OTHER pillars (latency, throughput, accuracy,
encryption standards) are NOT affected by this rule.

### Pillar 3 — Performance

Quantifiable targets with specific metric definitions:

- API latency (`p50`, `p95`, `p99` percentile; absolute milliseconds).
- Throughput (requests per second, batch records per hour).
- Concurrency (max concurrent users, max parallel jobs).
- Storage / query performance (BigQuery slot-hours, Firestore read/write QPS).

Pattern: `**Performance:** The platform shall return credit-opinion
responses within 2 seconds at the 95th percentile for synchronous API
calls under nominal load (60 requests/day).`

### Pillar 4 — Operational Excellence

Observability + lifecycle automation:

- Logging: structured logs in Cloud Logging (with what fields).
- Monitoring: SLI/SLO dashboards in Cloud Monitoring (which metrics).
- Alerting: PagerDuty / email / chat hook on which thresholds.
- CI/CD: which pipelines are in scope (note: deployment automation is often
  out-of-scope — check the OOS list before promising).

Pattern: `**Operational Excellence:** The platform shall emit structured
audit logs to Cloud Logging for every credit-opinion request, including the
request id, customer id, model version, and response status code.`

### Pillar 5 — Cost Optimization

Architectural choices that bound cost predictably:

- Serverless-first architecture where applicable (scale-to-zero).
- Resource tier or commitment (e.g., BigQuery on-demand vs. flat-rate).
- Data retention policy (Cloud Storage lifecycle rules).
- Caching strategy (Memorystore, CDN).

Pattern: `**Cost Optimization:** The platform shall use serverless GCP
services (Cloud Run, BigQuery on-demand, Firestore) so compute and storage
costs scale linearly with usage; no always-on infrastructure is provisioned
for the engagement.`

## Self-sufficiency contract

The Self-sufficiency contract from `sow-shared/references/style-guide.md`
applies in full to NFRs:

- Every Manifest-captured NFR (category `NFRs` in the Manifest) MUST appear
  literally named in at least one generated NFR.
- No NFR may delegate to "as detailed in the security policy" or any
  external document.
- Grouping is allowed when multiple Manifest NFR items are instances of the
  same pillar attribute (e.g., multiple encryption requirements → one
  Security NFR listing all algorithms) — but each item must still be
  findable by name in the NFR text.
