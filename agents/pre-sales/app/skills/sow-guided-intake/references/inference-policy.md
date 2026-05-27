# Inference Policy

Some items the user may not be able to answer during a guided intake.
This reference defines, for each interview block:

- which fields are **required** and become ``'[TO BE DEFINED]'``
  when skipped;
- which fields are **inference-eligible** and become
  ``'(inferred)'`` when skipped;
- which four fields cannot be skipped at all (must carry a real value
  — see SKILL.md);
- the rules for signalling optionality to the user.

The agent ALWAYS asks the block question. The difference is what
happens when the user does not have an answer.

## Marker semantics

The two markers carry distinct downstream behaviors and must NOT be
used interchangeably. Mistaking one for the other is a common failure
mode and the reason this reference exists.

- **``'(inferred)'``** — downstream skills WILL fill the field with a
  safe consulting default following the style guide and SOW
  conventions. The user does NOT review again until the existing
  Content Review / Architecture Review gates. Use only for fields
  listed as "inference-eligible" below.
- **``'[TO BE DEFINED]'``** — downstream skills WILL keep the
  placeholder in the SOW and roll the gap into open items /
  assumptions for the user to resolve later. They do NOT invent a
  value. Use only for fields listed as "required" below when the user
  could not answer.

Never write ``'[TO BE DEFINED]'`` as a request for inference. The
correct marker for "skipped, please infer" is ``'(inferred)'``.

## Cannot-skip fields (real value mandatory)

The ``save_sow_intake_summary`` tool rejects markers AND blanks on
these four:

- ``customer_name`` (Block 1)
- ``project_title`` (Block 1)
- ``problem_goal`` (Block 2)
- ``solution_direction`` (Block 2)

If after the follow-up budget any of these is still missing, ask one
targeted question per missing field. A SOW cannot be generated
without them.

## Required items → ``'[TO BE DEFINED]'`` on skip

If the user does not have an answer for one of these, write
``'[TO BE DEFINED]'`` in the persisted field (or
``['[TO BE DEFINED]']`` for list fields) and add the field name to
``open_items``. Do NOT infer.

- **Block 1** — ``funding_type``. Accepts ``'DAF'``, ``'PSF'``, or
  ``'[TO BE DEFINED]'``.
- **Block 3** — ``integrations`` (entire field). Integrations are
  client-specific and never safely inferable.
- **Block 5 #2** — ``nfr_quality_targets``. Commitments to the
  customer.
- **Block 5 #3** — ``timeline``. Commitments to the customer.
- **Block 5 #4 (operational portion)** — ``operational_constraints``:
  network / VPN, GCP organization, security approvals, stakeholder
  availability windows.

## Inference-eligible items → ``'(inferred)'`` on skip

If the user skips an inference-eligible item, write ``'(inferred)'``
in the persisted field (or ``['(inferred)']`` for list fields) and
add the field name to ``inferred_items``. The downstream section
skills will propose a concrete value and the user reviews at the
existing review gates.

- **Block 2** — ``technology_stack``.
- **Block 4** — ``out_of_scope``.
- **Block 4** — ``partner_team`` and ``customer_team``.
- **Block 5 #1** — ``engagement_shape``.
- **Block 5 #4 (regulatory portion)** — ``regulatory_constraints``:
  industry-standard frameworks (LGPD, Bacen, data residency for a
  Brazilian customer, sector-standard frameworks).

## Signalling optionality (mandatory)

When an item is inference-eligible, the user MUST hear about the
inference and review path AT LEAST ONCE before they decide to skip.
Hidden inference is exactly what the Content Review gate protects
against — the user must walk into that gate already knowing some
values were inferred.

Hard rules:

- Signal optionality in the same message that asks the question (or
  at the start of the block when the entire block is
  inference-eligible, like Block 4).
- Phrasing is up to the agent — improvise in the conversation
  language, fit the tone. Do NOT paste a fixed template. Do NOT
  repeat the disclaimer mechanically in every question.
- Never silently mark an item as ``'(inferred)'`` without the user
  knowing that the inference path exists.

Examples (rephrase in the user's language and tone):

- "If you don't have the stack defined yet, that's fine — I can
  propose typical GCP services for this kind of project, and you'll
  review them at the Content Review."
- "Se vocês ainda não definiram time, posso propor uma composição
  típica da GFT para esse tipo de engajamento; você revisa antes de
  fechar."

When a required item is missing, do NOT offer the inference path —
write ``'[TO BE DEFINED]'`` and continue. The user reviews the open
items at the Content Review gate.

## Budget interaction

The follow-up budget (3 × 3) applies the same way to both categories.
The difference is at exit:

- Required items still missing after the budget →
  ``'[TO BE DEFINED]'`` plus ``open_items`` entry.
- Inference-eligible items still missing after the budget →
  ``'(inferred)'`` plus ``inferred_items`` entry.

## UX rules tied to this policy

- Do NOT re-ask the user about timeline, NFR targets, or operational
  constraints after the budget runs out. Persist the
  ``'[TO BE DEFINED]'`` marker and stop. The Content Review gate is
  where the user resolves them.
- Do NOT re-ask the user about ``out_of_scope``, ``technology_stack``,
  ``partner_team``, ``customer_team``, ``engagement_shape``, or
  ``regulatory_constraints`` after the budget runs out. Persist
  ``'(inferred)'`` and stop. The Content Review gate is where the
  user revises the proposed defaults.
- Do NOT echo per-field marker tokens to the user mid-interview. They
  are internal to the persisted summary.
