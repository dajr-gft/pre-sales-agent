# Intake Summary Format

The structured summary is persisted via
``save_sow_intake_summary(intake_summary=<dict>)``. This reference is
the binding contract for the dict's shape. The tool validates it
against the ``IntakeSummary`` Pydantic model — extra fields are
rejected.

## Top-level shape

Single JSON object, no nesting beyond the lists below.

```json
{
  "customer_name": "...",
  "project_title": "...",
  "partner_name": "GFT Technologies",
  "funding_type": "DAF | PSF | [TO BE DEFINED]",

  "problem_goal": "...",
  "solution_direction": "...",
  "engagement_shape": "greenfield | brownfield | migration | foundation | assessment | (inferred)",
  "timeline": "real value | [TO BE DEFINED] | (inferred)",

  "main_scope": ["item", "..."],
  "out_of_scope": ["item", "..."],
  "integrations": ["..."],
  "technology_stack": ["..."],
  "nfr_quality_targets": ["..."],
  "operational_constraints": ["..."],
  "regulatory_constraints": ["..."],
  "partner_team": ["..."],
  "customer_team": ["..."],
  "assumptions": ["..."],

  "inferred_items": ["field_name_1", "field_name_2"],
  "open_items": ["field_name_3", "field_name_4"]
}
```

## Field semantics

Each field carries exactly one of three states, per the SKILL.md
marker contract:

1. **Real value** — extracted from the user's answers.
2. **``'(inferred)'``** — inference-eligible field the user skipped.
   Downstream fills with a safe default.
3. **``'[TO BE DEFINED]'``** — required field the user could not
   answer. Downstream keeps the placeholder.

For list fields, write the marker as a single-element list whose only
entry is the marker token:

```json
"out_of_scope": ["(inferred)"]
"operational_constraints": ["[TO BE DEFINED]"]
```

An empty list (``[]``) means the field is genuinely empty without a
marker — only use it when neither inference nor open-item routing
applies (rare; most skips should land on one of the two markers).

## Per-field rules

### Required real-value fields (markers forbidden)

The tool rejects markers and blanks on these four:

- ``customer_name`` — the customer organization. Required.
- ``project_title`` — the engagement / project title. Required.
- ``problem_goal`` — one or two sentences describing the customer
  problem or objective. Required.
- ``solution_direction`` — one or two sentences describing the
  proposed direction. Required.

### Marker-tolerant scalar fields

- ``partner_name`` — defaults to ``'GFT Technologies'``. Do not
  rename.
- ``funding_type`` — one of ``'DAF'``, ``'PSF'``, or
  ``'[TO BE DEFINED]'``. Funding is required-but-tolerates-unknown.
- ``engagement_shape`` — one of the five labels or ``'(inferred)'``.
  Engagement shape is inference-eligible.
- ``timeline`` — a real description (duration, dates, hard
  deadlines), ``'[TO BE DEFINED]'`` (required, user did not know), or
  ``'(inferred)'`` only when the project genuinely has no time
  constraints.

### List fields

Each list is either:

- A list of real items (strings). For integrations, write one bullet
  per system, free-form text including the sub-fields the user
  provided (name, direction, protocol, operations). Missing sub-fields
  are simply omitted from the bullet — do NOT write
  ``protocol: not stated`` or ``operations: not stated`` for the user
  to see; that detail lives inside your bullet text.
- A single-element list with the marker token, per the convention
  above.
- An empty list when the field is genuinely empty (rare).

List fields and the marker convention to use when skipped:

| Field | Default marker on skip | Why |
|---|---|---|
| ``main_scope`` | ``['(inferred)']`` | Inference-eligible — solution direction implies scope. |
| ``out_of_scope`` | ``['(inferred)']`` | Inference-eligible — propose typical exclusions. |
| ``integrations`` | ``['[TO BE DEFINED]']`` | Required — client-specific, cannot be inferred. |
| ``technology_stack`` | ``['(inferred)']`` | Inference-eligible — derive from problem/goal. |
| ``nfr_quality_targets`` | ``['[TO BE DEFINED]']`` | Required — commitments to the customer. |
| ``operational_constraints`` | ``['[TO BE DEFINED]']`` | Required — VPN, GCP organization, etc. |
| ``regulatory_constraints`` | ``['(inferred)']`` | Inference-eligible — industry standard frameworks. |
| ``partner_team`` | ``['(inferred)']`` | Inference-eligible — propose typical GFT team. |
| ``customer_team`` | ``['(inferred)']`` | Inference-eligible — propose typical customer roles. |
| ``assumptions`` | ``[]`` | No marker — assumptions surface from constraints downstream. |

### Roll-up fields

- ``inferred_items`` — list of field names (strings, matching the
  JSON keys above) whose value resolves to ``'(inferred)'``. The root
  uses this list to dispatch downstream inference behavior.
- ``open_items`` — list of field names whose value resolves to
  ``'[TO BE DEFINED]'``. The root and section skills use this to
  decide which gaps go into the SOW's open items.

Both lists are bookkeeping; downstream code MAY rebuild them by
walking the rest of the dict if it does not trust them. Keep them
accurate.

## Anti-patterns

- Do NOT pass markers on ``customer_name``, ``project_title``,
  ``problem_goal``, or ``solution_direction``. The tool returns
  ``ToolError`` and the LLM must ask the user one targeted question
  per missing field.
- Do NOT mix a marker token and a real item in the same list (e.g.
  ``['Phase 1', '(inferred)']``). Either the list has real items or
  it is the single-element marker form. Mixed lists are ambiguous and
  downstream parsing will treat the marker as a real item.
- Do NOT translate the field labels. The keys above are the persisted
  contract; the values are written in the user's language when the
  user provided them in that language.
- Do NOT emit the structured summary as text to the user. The skill
  calls ``save_sow_intake_summary`` and replies briefly. The root and
  section skills read the persisted state directly.
- Do NOT include fields that are not in the schema above. The tool
  rejects extras.
