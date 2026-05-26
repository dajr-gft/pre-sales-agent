# Partner Overview / Customer Overview — rules (binding)

The Partner Overview introduces the delivery partner; the Customer
Overview introduces the customer organization. Both sections are short
narrative paragraphs that contextualize the engagement for any reviewer
who is not on the project's core team. Stored as single strings:

- `sow_data['partner_overview']`
- `sow_data['customer_overview']`

Plus the customer's primary domain captured in
`sow_data['customer_primary_domain']` — used by the document template to
render the customer logo.

## Partner Overview

### With reliable web search data

- 4-6 lines. Cover:
  - Google Cloud certifications and specializations.
  - Certified engineer count (when published).
  - Global presence (regions, countries, offices when published).
  - Awards / recognition.
  - Industry expertise relevant to the engagement.

### Without reliable web search data

- 3-4 lines drawn from Phase 1 context (the Manifest's Identity category).
  Stick to facts present in the Manifest; do not invent metrics.

### Anti-patterns

| Anti-pattern | Why it fails | Fix |
|---|---|---|
| Marketing puffery ("leading", "world-class", "transformational") | Not consulting tone | Replace with concrete certifications, services, or experience the partner brings |
| Generic "the partner has extensive experience in cloud" | Could apply to any partner | Name the specific specializations (Data Analytics, ML, Infrastructure, …) |
| Numeric claims without web-search backing (engineer count, ranking) | Unverified data is a contractual exposure | Either omit, or anchor to a web-search result captured in this turn |

## Customer Overview

### With reliable web search data

- 4-6 lines. Cover:
  - History (founding year, milestones — when published).
  - Market position (sector, segment).
  - Key metrics (revenue, employees, users, market share — when
    published).
  - Competitive positioning when relevant.
  - Tech context (existing GCP/AWS/Azure footprint when known).

### Without reliable web search data

- 3-4 lines from the Manifest's Identity + Briefing categories. Stick to
  facts present in the Manifest.

### Anti-patterns

| Anti-pattern | Why it fails | Fix |
|---|---|---|
| Numeric claims without web-search backing (revenue, employees) | Unverified data is a contractual exposure | Either omit, or anchor to a web-search result captured in this turn |
| Generic "the customer is a leader in their industry" | Could apply to any customer | Name the actual sector, segment, region |
| Customer overview that duplicates Executive Summary content | Overlapping sections waste the reader's time | The Customer Overview is about the organization; the Executive Summary is about the engagement |

## Web search queries (4 queries, sequential)

When generating the narrative section, run the following 4 queries in
order. Use the results as the sole source of any numeric or factual
claim that is not in the Manifest:

1. `"<Partner Name>" Google Cloud partner specialization` → results feed
   `partner_overview`.
2. `"<Customer Name>" <sector from Manifest> company overview` → results
   feed `customer_overview`.
3. `"<Customer Name>" <sector from Manifest> market share competitors` →
   results enrich `customer_overview` with competitive positioning.
4. `"<Customer Name>" official homepage` → results feed EXCLUSIVELY
   `customer_primary_domain`. Do NOT use this query's results to enrich
   the prose overview.

### Domain capture rules (from the 4th query only)

The domain must come from the **URL field** of a result you actually
observed in this turn's tool calls — not from snippet text, not from
prior knowledge, not constructed from the customer's name.

The official homepage is typically the top organic result for the
homepage query, with the company's brand name in the domain. Skip
aggregators and third-party pages: Wikipedia, LinkedIn, Crunchbase, news
portals, review sites, job boards, and similar directories.

If no result returned an official homepage, **leave
`customer_primary_domain` unset**. A visible logo placeholder is the
correct outcome for unknown domains — preferable to a silently wrong
logo.

### Domain format

- Strip the URL scheme (`https://`, `http://`).
- Strip the path (everything after the host).
- Strip the leading `www.` if present.
- Keep the public-suffix TLD (`.com`, `.co.uk`, `.com.br`, ...).
- Lowercase.

Pattern: input `https://www.AcmeCorp.com/about-us` → stored as `acmecorp.com`.

## When web search is unavailable

If the web search tool is unavailable or returns no usable results:

- Generate `partner_overview` and `customer_overview` from Manifest
  context only (Identity + Briefing categories).
- Use the shorter line range (3-4 lines).
- Leave `customer_primary_domain` unset.
- Add an inferred-content marker to any factual claim that is not
  literally in the Manifest, per
  `sow-shared/references/language-rules.md` → "Inferred-content marker".
