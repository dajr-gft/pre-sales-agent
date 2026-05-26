# Out-of-Scope — categories and rules (binding)

The Out-of-Scope (OOS) section is a numbered list that defines, by
exclusion, the contractual boundary of the engagement. Stored in
`sow_data['out_of_scope']` as a list of strings. Every item is complete,
self-contained, and unambiguous — the customer reads the OOS list to
confirm what is NOT promised.

## Format

- One item per list entry. Each entry is a single sentence or a short
  paragraph (2-3 sentences when scoping needs clarification).
- Numbered implicitly by list position (`OOS-01`, `OOS-02`, ...). The
  number is a stable ID after the first review — see
  `sow-shared/references/id-stability-rules.md`.
- Use **"including but not limited to"** when a category needs broad
  coverage with named technologies inside it.

## Target

**Target: 20-30 items.** Floor, not cap. Real-world engagements with
heavy integration surfaces routinely produce 30-40 OOS items.

## Disambiguation rule (vs. FRs)

When an OOS item could appear to contradict an in-scope FR, the OOS item
MUST distinguish excluded vs. included with a cross-reference. Apply ONLY
when both the FR and the conflicting OOS exist. If the FR was removed,
write the OOS normally.

**FR/OOS pre-generation cross-check:**

- The Manifest explicitly contains the capability → keep the FR,
  disambiguate the OOS item.
- The capability was inferred (not present in Manifest, not in user
  answers) → remove the FR, keep the OOS as-is.
- Concrete pattern: if OOS mentions model maintenance / retraining / model
  ops post go-live → do NOT infer an FR for automated retraining unless
  the Manifest explicitly captures it.

## The 17 categories (cover what applies; adapt to project)

Walk these categories before emitting the OOS list. Skip a category only
when it is genuinely inapplicable to the engagement; do not skip because
"it sounds obvious".

1. **Excluded functionality** — Any feature/workflow/capability not
   defined in the SOW.
2. **Excluded integrations** — Name specific tools NOT in scope, plus a
   catch-all for unidentified systems.
3. **Excluded API work** — Development / modification / remediation of
   the customer's APIs. The partner consumes only.
4. **Excluded UI/UX** — Custom UIs, web apps, mobile apps. Name the sole
   interaction layer the engagement uses.
5. **Excluded environments** — Production if not in scope, or beyond
   DEV/UAT.
6. **Excluded infrastructure** — Customer network (VPNs, firewalls, DNS),
   on-prem environments, out-of-scope cloud resources.
7. **Excluded CI/CD** — Build automation, release orchestration,
   deployment pipelines (if not in scope).
8. **Excluded data work** — Migration, cleansing, normalization beyond
   the defined scope. Source-data quality remediation.
9. **Excluded testing types** — Penetration, load, stress, performance,
   security testing.
10. **Excluded post-delivery** — Hypercare, SRE/NOC, ongoing
    maintenance, evolution after knowledge transfer.
11. **Excluded compliance** — Certifications, regulatory approvals, legal
    sign-offs, security audits.
12. **Excluded training** — End-user training beyond technical KT to the
    project team.
13. **Excluded code/project alignment** — Merging with other customer
    projects, code equalization.
14. **Excluded documentation processing** — Ingestion / preprocessing of
    customer internal docs (when not in scope).
15. **Excluded revisions post-approval** — Changes to approved
    deliverables require a Change Request.
16. **Catch-all** — "Any additions, enhancements, or modifications
    without a formally approved Change Request."
17. **Excluded service-level commitments** — **MANDATORY regardless of
    project type or funding (DAF/PSF).** Any guarantee of uptime,
    availability, or service-level agreements (SLAs) for production
    workloads. The solution is architected to support the reliability
    targets informed during discovery, but sustained production
    availability remains the Customer's responsibility after handover.

### Approved phrasings for Category 17 (the uptime denial)

- "Any guaranteed uptime, availability percentage, or service-level
  agreement (SLA) for production workloads. The solution is architected
  to support the reliability patterns described in the Non-Functional
  Requirements; sustained production availability remains the
  Customer's responsibility after handover."
- "Service-level commitments for production environments, including but
  not limited to uptime percentages, mean-time-to-recovery (MTTR), and
  incident-response SLAs. The Customer assumes operational ownership of
  these targets after the knowledge-transfer milestone."

Pick the phrasing that fits the project's tone; do not invent a third
phrasing weaker than these two.

## Anti-patterns

| Anti-pattern | Why it fails | Fix |
|---|---|---|
| OOS list with < 15 items for a 12-week implementation | Most categories are missing — the contractual surface is leaky | Walk all 17 categories explicitly |
| Category 17 (uptime/SLA denial) missing | This is the most common contractual exposure; mandatory regardless of project | Add Category 17 with one of the approved phrasings |
| OOS item that contradicts an in-scope FR without disambiguation | Reader cannot tell which side of the line the capability sits on | Add disambiguation per the "Disambiguation rule" above |
| Generic OOS catch-all ("anything not listed") replacing real categories | Reader cannot tell what specifically is excluded | Use both: specific categories AND the explicit catch-all (Category 16) |
| OOS phrased softly ("the partner may not deliver X") | Soft phrasing leaves room for interpretation | Use definitive scope-boundary language: "explicitly excluded", "not in scope", "the engagement is strictly limited to" |
