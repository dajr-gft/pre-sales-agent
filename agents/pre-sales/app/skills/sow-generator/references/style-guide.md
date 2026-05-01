# SOW Style Guide — Google DAF/PSF Template

## General Writing Rules

- Clear, professional English. Active voice.
- Specific and quantifiable — no "up to", "various", "several".
- Exact targets or narrowly defined ranges.
- No marketing language in technical sections.
- Focus on **how**, not **what** or **why**.
- **Professionalize all input.** Rewrite user content in enterprise consulting language. Never echo casual phrasing. Preserve meaning, elevate tone.

---

## Self-sufficiency contract (cross-cutting rule — applies to FRs, NFRs, OOS, Assumptions)

The SOW is a contractual document. It MUST be readable and executable WITHOUT opening any other artifact. Every Functional Requirement, Non-Functional Requirement, Out-of-Scope item, and Assumption is self-contained.

**Rule 1 — No external scope delegation.** No FR, NFR, OOS, or Assumption may delegate its definition to an external document. The document being drafted IS the scope. Phrasings that reference external artifacts to define what is or isn't in scope are FORBIDDEN, including:

- "as listed in [Appendix / Annex / Matrix / Capability Sheet / Attachment]"
- "all capabilities defined in [external doc name]"
- "strictly limited to the items in [external doc]"
- "the scope is bounded by [external doc]"
- Any equivalent phrasing in any language that makes scope dependent on opening a separate file.

When the discovery Manifest references a source document by name (capability matrix, RACI, technical annex, kickoff deck, etc.), that source name is metadata for the agent during generation — it MUST NOT appear in any FR/NFR/OOS/Assumption text in the SOW. Translate the items themselves into the SOW; never translate a pointer to the items.

**Rule 2 — Map Manifest items into requirements with flexible grouping.** Each Manifest item whose category is in `[Briefing, Integrations, NFRs]` MUST appear named literally (by name, feature, or direct description) in at least one FR or NFR. Grouping is allowed when natural, required when not:

- **Group into ONE requirement** when items are instances of the same operation differing only by target/channel/system/parameter (e.g., items "X for A", "X for B", "X for C" → one FR: "The platform shall provide X for A, B, and C") — or when items are synonyms of the same concept (e.g., "STT" and "Speech-to-Text support" → one FR).
- **Keep SEPARATE requirements** when items describe functionally distinct capabilities, even if related by domain. Examples: short-term session-scoped memory vs long-term cross-session memory are different mechanisms — two FRs; model training vs model serving are different lifecycle stages — two FRs.

**Self-sufficiency invariant (non-negotiable):** grouping consolidates operations but NEVER erases names. If you group 4 channels into 1 FR, all 4 channel names appear in that FR's text. Every Manifest item must be findable in the SOW by name.

**Decision test:** would the grouped requirement read naturally as "operation X parameterized by [list]"? If yes, group. If you have to invent a fake parent concept to glue items together (e.g., "STT", "DLP", and "RAG" under "AI capabilities"), separate. When in doubt, separate — one extra FR costs less than an artificial umbrella.

**Rule 3 — Counter ranges are floors when the Manifest is rich.** The targets defined per section below (10-20 FRs, 5+ NFRs, 20-30 OOS, 15-25 Assumptions) are MINIMUM floors and SOFT design targets. They are NEVER hard caps. When the Manifest covers more capabilities than the soft target accommodates, exceed the target. A Manifest with 60 distinct capabilities in `[Briefing, Integrations, NFRs]` produces 50+ FRs/NFRs — that is the correct outcome, not an error to be compressed. Compression that creates an "umbrella requirement" pointing at a Manifest category or external document is a SEVERE failure of this contract.

**Anti-patterns (all rejected, in any language):**

> FR-XX: The solution shall implement all capabilities listed in the customer's capability matrix.
>
> FR-XX: The scope is strictly limited to the items defined in the project's technical annex.
>
> FR-XX: The solution shall comply with all requirements detailed in the reference documentation provided by the Customer.

Each is rejected on three grounds: (a) delegates scope to an external document (Rule 1), (b) collapses N capabilities into one umbrella (Rule 2), (c) makes the SOW non-self-sufficient. The defect is structural — it does not depend on the document's specific name. The correct treatment for any of them is to expand into N individual FRs, one per capability, each naming the capability literally in the FR text — even if N is 50 or more (Rule 3).

---

## Section Rules

### Executive Summary
- Business value first, then technical outcomes.
- Length scales with project magnitude.
- Scope boundary statement early (e.g., "This engagement is strictly limited to...").
- Bullet points for activities and objectives.

### Partner Overview
- With search data: 4-6 lines — Google Cloud certs/specializations, certified engineers count, global presence, awards, industry expertise.
- Without search data: 3-4 lines from Phase 1 context.

### Customer Overview
- With search data: 4-6 lines — history, market position, key metrics (revenue, users, share), competitive positioning, tech context.
- Without search data: 3-4 lines from Phase 1 context.

### Functional Requirements
- "Shall" language. Boundary-setting where applicable.
- Numbered table with unique IDs (FR-01, FR-02...).
- **Target: 10-20 FRs** (floor; exceed when Manifest demands per Self-sufficiency Rule 3).
- Each FR must name specific systems, data flows, APIs, or behaviors — not generic capabilities.
- Self-sufficiency contract applies in full — every Manifest-captured capability must be named literally in some FR.
- Infer implicit requirements: authentication/authorization, error handling, audit logging, data validation, admin monitoring, edge cases.

### Non-Functional Requirements
- Align with GCP WAF pillars: Security, Reliability, Performance, Operational Excellence, Cost Optimization.
- Numbered table with unique IDs (NFR-01, NFR-02...).
- Quantifiable targets. Reference specific standards (TLS 1.3, AES-256).
- Use NFRs to reinforce scope boundaries where applicable.
- Self-sufficiency contract applies in full — every Manifest-captured NFR must be named literally in some NFR.
- **Consultancy scope rule (non-negotiable):** NFRs describe architectural qualities GFT implements during the engagement — they do NOT commit to production uptime or availability percentages. This rule applies strictly to availability/uptime/SLA phrasings; latency, throughput, accuracy, security standards, and other quantitative targets are unaffected by this rule.
    - **FORBIDDEN phrasings:** "shall maintain [N]% uptime", "guaranteed availability of [X]%", "SLA of [Y]% availability", "uptime commitment of [Z]%", or any variant that commits Partner to a production availability percentage.
    - **REQUIRED phrasing for the Reliability pillar:** "The platform shall be architected for high availability using [specific services/patterns — e.g., multi-region Cloud Run deployment, Cloud SQL automatic failover, health checks]. Ongoing availability management remains with the Customer post-handover."

### Architecture Overview
See `references/architecture-guide.md` Parts 1-3. All rules there are binding.

### Architecture Diagram
See `references/architecture-guide.md` Part 2. All rules there are binding.

### Technology Stack Table
See `references/architecture-guide.md` Part 4. All rules there are binding.

### Activities
- Action verbs: describe, investigate, review, document, design, develop, perform, integrate, configure, test, validate, deploy.
- Organize by phases. Meaningful phase names.
- Each task → specific systems, GCP services, and technical approach — not just the action verb.
- **Anti-pattern:** "Set up GCP environment", "Develop and test pipelines" — too shallow, could describe any project.
- **Self-test:** Before presenting each task, ask: *"Could this exact task description appear unchanged in a different project?"* If yes, it is too generic. Rewrite with details unique to THIS project — the specific data being processed, the specific API endpoint being consumed, the specific business rule being implemented, or the specific validation being performed.

### Deliverables
- Measurable or quantifiable. Include format (Presentation, Document, Spreadsheet, Video, Code, Demonstration).
- Map to corresponding activity/phase. Every phase ≥ 1 deliverable.
- Include intermediate deliverables: Test Plan, Data Quality Report, Go-Live Runbook, KT docs.
- **Target: minimum 10 deliverables** for a 10-14 week project with 3-4 phases. If you have fewer, you are missing intermediate artifacts.
- **Preferred structure: Workstreams.** Organize deliverables as numbered workstreams (WS01, WS02...), each with:
  - **Objective**: What the workstream delivers (1-2 sentences).
  - **Subtopics**: Specific bounded activities within the workstream.
  - **Outcomes**: Concrete, verifiable results.
- **Anti-pattern:** A flat table with only deliverable name and format (e.g., "Design Document | .docx | Phase 1"). This lacks the depth to communicate what the team will actually produce.

### Assumptions & Prerequisites

**Target: 15-25 assumptions** (floor; exceed when project demands).

Every customer-dependent assumption MUST include explicit consequence.
Pattern: "[Customer] must [obligation] [by when]. [Consequence if not met]."

**Categories to cover (adapt to project):**

1. **Contractual**: Pricing model, effective dates, scope limitation
2. **Execution model**: Remote/on-site, fixed price, working hours
3. **Platform prerequisites**: GCP services industrialized and available by when. Unavailability = extension + cost. **Link each prerequisite to the specific phase or workstream that depends on it** (e.g., "prior to the start of WS03" — not generic "before kickoff"). Each GCP service should have its own deadline.
4. **Access and credentials**: All credentials/service accounts/VPN provisioned before kickoff. Delays = proportional extension
5. **Stakeholder availability**: Named roles available for workshops/validations. Response SLA (e.g., 3 business days). No feedback within SLA = acceptance
6. **Data and documentation**: What customer provides before each phase. Delays = timeline impact
7. **Workshop cancellation**: < 1 day notice → reschedule. Repeated (2+) → timeline review + costs
8. **Blocker resolution**: Customer blockers resolved within SLA (e.g., 2 business days). Unresolved = extension + cost
9. **Deliverable review and approval**: Feedback within SLA. No feedback = acceptance. Post-approval changes = Change Request
10. **Scope protection**: Scope is fixed. Changes require formal CR with cost/timeline impact
11. **Data quality**: Partner not responsible for quality/completeness of customer data. Data must be sanitized and compliant
12. **GenAI/ML acknowledgment** (when applicable): Non-deterministic behavior acknowledged. No 100% accuracy guarantee. Outputs advisory, subject to human review
13. **GCP service dependency**: Performance depends on GCP services outside Partner control
14. **Partner technical autonomy**: Full autonomy to develop. May use internal tools if no customer data exposed
15. **Partner liability for own delays**: Partner delays completed at no additional cost

### Out-of-Scope

**Target: 20-30 items** (floor; exceed when project demands). Each item: complete, self-contained, unambiguous.
Use "including but not limited to" for broad coverage with named technologies.

**Disambiguation rule:** When OOS item could contradict an in-scope FR, MUST distinguish excluded vs. included with cross-reference. Apply ONLY when both FR and conflicting OOS exist. If FR was removed, write OOS normally.

**FR/OOS pre-generation cross-check:**
- User/Manifest explicitly contains the capability → keep FR, disambiguate OOS item.
- Capability was inferred (not present in Manifest, not in user answers) → remove FR, keep OOS as-is.
- Concrete pattern: if OOS mentions model maintenance/retraining/model ops post go-live → do NOT infer FR for automated retraining unless the Manifest explicitly captures it.

**Categories to cover (adapt to project):**

1. **Excluded functionality**: Any feature/workflow/capability not defined in SOW
2. **Excluded integrations**: Name specific tools not in scope + catch-all for unidentified systems
3. **Excluded API work**: Development/modification/remediation of customer APIs. Partner consumes only
4. **Excluded UI/UX**: Custom UIs, web apps, mobile apps. Name the sole interaction layer
5. **Excluded environments**: Production if not in scope, or beyond DEV/UAT
6. **Excluded infrastructure**: Customer network (VPNs, firewalls, DNS), on-prem, out-of-scope cloud resources
7. **Excluded CI/CD**: Build automation, release orchestration, deployment pipelines (if not in scope)
8. **Excluded data work**: Migration, cleansing, normalization beyond defined scope. Source data quality remediation
9. **Excluded testing types**: Penetration, load, stress, performance, security testing
10. **Excluded post-delivery**: Hypercare, SRE/NOC, ongoing maintenance, evolution after KT
11. **Excluded compliance**: Certifications, regulatory approvals, legal sign-offs, security audits
12. **Excluded training**: End-user training beyond technical KT to project team
13. **Excluded code/project alignment**: Merging with other customer projects, code equalization
14. **Excluded documentation processing**: Ingestion/preprocessing of customer internal docs
15. **Excluded revisions post-approval**: Changes to approved deliverables require CR
16. **Catch-all**: "Any additions, enhancements, or modifications without formally approved Change Request"
17. **Excluded service-level commitments**: Any guarantee of uptime, availability, or service-level agreements (SLAs) for production workloads. The solution is architected to support the reliability targets informed during discovery, but sustained production availability remains the Customer's responsibility after handover. **This category is mandatory regardless of project type or funding (DAF/PSF).**

### Change Request Policy
- Include immediately after Out-of-Scope.
- Must state: no out-of-scope work without approved Change Request signed by both parties.
- Must state: verbal agreements are not binding.
- Must state: Partner reserves right to pause work without formal approval.
- The 7 required CR fields (Date of MSA, Date of CR, Impacted SOW, Description of
  changes, Impact on resources/timeline, Cost change, Effective date) are provided
  by the document template as static text. Do not generate them.

### Risks
- Conditional — omit if user removes during review.
- 3-5 project-specific risks with mitigations.
- Each risk must reference specific systems, technologies, or stakeholders.
- Table: Risk | Mitigation Strategy.

### Success Criteria
- Verifiable and measurable. Tied to specific deliverables or outcomes.
- Each criterion unique — no repetition in different words.
- **Target: minimum 5 unique criteria.**

### Timeline
- Table: Phase | Timeframe | Key Outcomes.
- Week ranges (e.g., "Weeks 1-3") or date ranges.

### Project Roles
- Partner + Customer. Partner must include PM.
- No hours/rates. No Google roles.
- **Format:** Table with 3 columns: Role | Description | Organization.
- Each role description must include 2-3 lines of concrete responsibilities — not just the role title. Example: "Responsible for removing blockers, preparing status reports, and organizing scrum ceremonies" — not just "Project Manager."
- Customer roles should specify decision authority where applicable (e.g., "Final escalation point", "Must have authority to validate and sign off requirements").

### Costs
- Fixed-price. Never include hours, hourly rates, or rate cards.
- Placeholders for manual filling. Milestone structure if applicable.

## Formatting
- Consistent heading hierarchy for TOC generation.
- Tables: clear headers, consistent columns.
- Bullet points for lists. Bold for key terms.