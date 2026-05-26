# Risks — quality calibration examples

Patterns from real SOWs approved by Google for DAF/PSF funding. Each risk
names a specific system / technology / stakeholder and an actionable
mitigation the partner team can execute.


## Risks Patterns

Each risk names a specific system, technology, or stakeholder. Mitigation is actionable.

**Data platform example:**
> "Risk: Source system data quality from legacy SAP ERP may contain inconsistencies,
> missing fields, or undocumented business rules that are only discovered during
> pipeline development.
> Mitigation: Allocate a dedicated data validation sprint in Phase 2. Implement
> automated data quality checks at ingestion. Escalate critical data quality issues
> to the customer's Data Architect for resolution within 3 business days."

**Access provisioning example:**
> "Risk: Delays in customer provisioning of access credentials, VPN tunnels, or
> service accounts for on-premise source systems may block pipeline development.
> Mitigation: Deliver a pre-kickoff access checklist to the customer with specific
> deadlines. Flag access blockers in weekly status reports. Reserve 1 week of buffer
> in the timeline for access-related delays."

**ML accuracy example:**
> "Risk: The demand forecasting model may not achieve the target MAPE < 15% on the
> first training iteration due to insufficient or noisy historical data.
> Mitigation: Plan for 2-3 model iteration cycles within the Phase 3 timeline.
> Define a minimum viable accuracy threshold (e.g., MAPE < 20%) for initial
> deployment, with a roadmap for improvement post-engagement."

**AI/agent example:**
> "Risk: Gemini model responses may not meet business accuracy expectations for
> complex, domain-specific queries without extensive prompt engineering.
> Mitigation: Include a dedicated prompt tuning and evaluation phase. Define
> acceptance criteria for response quality with concrete test cases before UAT."

**Integration example:**
> "Risk: Integration with customer's existing Confluence workspace may be
> constrained by permission models or API rate limits not identified during
> the assessment phase.
> Mitigation: Conduct a technical spike on Confluence API integration in the
> first week of development. Document API constraints and escalate blockers early."

