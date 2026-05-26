"""Step 3 of validation_critic — five semantic skills running in parallel.

Each skill is an `LlmAgent` whose ``instruction`` is the body of its
`SKILL.md` plus a runtime payload (SOW + manifest residual + stage)
resolved per invocation. Every skill writes its findings into a
**dedicated** state key — `app:skill_findings:{name}` — so the
`ParallelAgent` runs without any chance of a write race.

Anti-monolith gates (`SKILL.md` ≤ 200 lines) are enforced at startup so
a bloated prompt cannot quietly regress the architecture.
"""

from __future__ import annotations

import json
from pathlib import Path

import structlog
from google.adk.agents import LlmAgent, ParallelAgent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.models import Gemini
from google.genai import types

from ...config import config
from ...shared.placeholders import collect_approved_deferrals
from .schema import (
    SKILL_NAMES,
    STATE_MANIFEST_RESIDUAL,
    STATE_SOW,
    STATE_STAGE,
    SkillFindings,
    skill_findings_state_key,
)

# Manifest is written to state by ``load_extraction_manifest`` under
# this key (see ``app.tools.sow.manifest_tools``). Reading the raw
# manifest — not just the prefiltered residual — gives the validator
# access to ``gaps.to_be_defined`` and the non-blocking ``hard_gaps``
# entries that authorise specific placeholder fields in the SOW.
_MANIFEST_STATE_KEY = 'extraction_manifest'

logger = structlog.get_logger()

_SKILLS_DIR = Path(__file__).parent / 'skills'
_MAX_SKILL_LINES = 200


def _read_with_gate(path: Path) -> str:
    """Read a SKILL.md and fail fast if it breaches the size gate.

    The gate is an architectural constraint from the MVP plan: each skill
    must stay focused. Crash at startup so the issue surfaces in CI, not
    in production telemetry.
    """
    if not path.exists():
        raise FileNotFoundError(f'Required skill file not found: {path}')
    text = path.read_text(encoding='utf-8')
    line_count = text.count('\n') + 1
    if line_count > _MAX_SKILL_LINES:
        raise RuntimeError(
            f'Anti-monolith gate breached: {path} has {line_count} '
            f'lines (limit: {_MAX_SKILL_LINES}). Trim the SKILL.md.'
        )
    return text


def _trim_manifest(items: list[dict] | None) -> list[dict]:
    """Keep the residual lean so each skill's prompt stays focused."""
    out: list[dict] = []
    for item in items or []:
        out.append(
            {
                'item_id': item.get('item_id'),
                'category': item.get('category'),
                'priority': item.get('priority'),
                'value': item.get('value'),
                'value_detail': item.get('value_detail'),
            }
        )
    return out


# Shared block injected into every skill's instruction. Centralised here
# (rather than duplicated across each ``SKILL.md``) so the taxonomy stays
# in sync, and so the per-skill size gate (200 lines per ``SKILL.md``)
# is not pushed by repeating the same rubric five times. The aggregator
# is the only place that consumes ``resolution_mode``; the wording below
# is what teaches the LLM which mode to emit.
_RESOLUTION_MODE_GUIDE = """\

---

# Resolution mode (REQUIRED on every finding)

Every finding MUST carry a ``resolution_mode`` field. This controls
whether the validation_critic surfaces the issue to a human or hands it
off to the revision_agent for an automatic patch. Severity (BLOCKER /
MAJOR / MINOR) is independent — a BLOCKER can still be ``auto_fixable``;
a MINOR can still be ``decision_required``.

Allowed values:

- ``auto_fixable`` — the revision_agent can apply the fix from the SOW,
  the manifest, and your recommendation alone. **Default and the most
  common case.** Use it whenever the fix is a rewrite, deletion,
  addition, or rewording the model can compose without external input.
  Examples (illustrative, not exhaustive):
    * SOW mentions an entity / vendor / technology / customer / integration
      that is NOT in the manifest or references — the agent must drop it
      (out-of-source drift / hallucination).
    * A concrete manifest item disappeared from the SOW — restore it.
    * A generic OOS clause conflicts with something explicitly included
      in the manifest — narrow the OOS or remove the conflict.
    * Ambiguous requirement can be clarified from manifest context.
    * Standard contractual clause is missing (MSA reference,
      AI non-determinism disclosure, handover boundary, CR gate,
      consequence clause, customer-responsibility shift).
    * Quantitative NFR from the manifest was dropped or weakened —
      restore the original target.
    * Deliverables not linked to schedule phases — link them.
    * Naming inconsistency between sections — pick one canonical name.

- ``decision_required`` — the fix needs a real business, commercial,
  legal, or scope decision that is NOT in the SOW / manifest / references.
  Use sparingly. Examples (illustrative):
    * A genuine cost / performance / scope trade-off — e.g. keeping a
      strict latency target requires more expensive infrastructure that
      the customer has not approved.
    * Choosing a price, payment milestone, governing law, region, or
      data-residency rule that the manifest does not state.
    * A scope decision that changes commercial risk and that only the
      customer or account team can make.

- ``source_conflict`` — two equally authoritative sources (manifest vs
  reference doc, two distinct customer statements, etc.) disagree and
  the SOW cannot pick one safely.

- ``not_fixable_by_agent`` — fix needs information that cannot be
  found or safely inferred from the Manifest, references, current SOW,
  style guides, architecture guides, or standard consulting practice.
  **Manifest silence alone is not enough.**

## Safe inference vs invention

When the Manifest is silent on a topic but the gap can be filled by
safe inference from the style guide, architecture references, section
references, the current SOW context, or standard consulting practice,
the finding is ``auto_fixable``. Safe inference is part of the
revision_agent's job.

Manifest silence alone is **not** a reason to set
``decision_required`` or ``not_fixable_by_agent``.

Escalate only when the fix requires a real external decision, a
business / commercial / legal choice, a trade-off not resolved by the
sources, or information that cannot be inferred from the Manifest,
references, current SOW, style guides, architecture guides, or
standard consulting practice.

Safe inference **MAY** add:

- Standard contractual clauses (MSA reference, CR policy, consequence
  clauses, parent-contract reference).
- Responsibility boundaries (Customer-responsibility shifts, handover
  language, ongoing-operations exclusions).
- Disclosure language (AI non-determinism, external-API dependency,
  PII Customer-responsibility, production-handover boundary).
- Style corrections (naming consistency, verb tense, register,
  language hygiene, structural alignment between sections).
- Details that are consistent with the architecture already in the
  SOW (cross-cutting services the architecture contract requires,
  edge labels for IAM/TLS, etc.).

Safe inference **MUST NOT** invent:

- New vendors, customers, systems, integrations, or technologies not
  grounded in the Manifest or references.
- Dates, milestones, durations, prices, costs, payment terms.
- SLAs, uptime targets, latency budgets, or other quantitative
  commitments not present in the sources.
- New scope commitments, deliverables, or customer responsibilities.
- Business facts (org structure, governance, regional choices) not
  grounded in the sources.

When the SOW contains such ungrounded content, the correct fix is to
**REMOVE** it (``auto_fixable``) — never to ask the user to confirm
the invention.

Default to ``auto_fixable`` whenever the recommendation is a concrete
rewrite, removal, or canonical insertion. Severity is NOT a reason to
escalate: do not mark ``decision_required`` just because a finding is
BLOCKER or MAJOR.

When emitting a finding, set both ``resolution_mode`` and (for
backwards compatibility) ``requires_human_review`` consistently:
``auto_fixable`` ⇒ ``requires_human_review=false``; the other three
modes ⇒ ``requires_human_review=true``. The aggregator will
reconcile mismatches by trusting ``resolution_mode``.

---

# Stage-awareness gate (binding)

You receive ``Stage`` as either ``content`` or ``full``. The staged
SOW differs between the two:

- ``content`` — only requirements, delivery_plan, and scope_boundaries
  sections are populated, alongside project metadata. The
  ``assemble_sow_payload`` tool intentionally omits architecture and
  narrative keys at this stage.
- ``full`` — every field is populated, including architecture and
  narrative.

At ``Stage: content``, the following ``sow_data`` keys are EMPTY by
design and the full-stage critic catches gaps in them in a later
round:

- ``architecture_description``
- ``architecture_components``
- ``architecture_integrations``
- ``technology_stack``
- ``executive_summary``
- ``partner_overview``
- ``customer_overview``
- ``customer_primary_domain``

Do NOT emit any finding whose ``fields`` references one of those keys
when ``Stage: content``. Examples of disallowed content-stage findings:
"architecture description is missing", "a service named in an FR is
not present in technology_stack", "executive summary too short".
Their absence at content stage is the contract, not a defect — wait
for the full-stage run.

This rule applies on top of any stage-specific guidance in your
skill — if your skill already gates a check to ``stage == "full"``,
keep doing so; this block adds a baseline gate for every skill.

---

# Approved placeholders (binding)

A SOW field may legitimately carry a placeholder literal such as
``[TO BE DEFINED]``, ``[TBD]``, ``[A DEFINIR]``, ``[A SER DEFINIDO]``,
``[POR DEFINIR]``, or ``[INSERT X]``. Placeholders are *approved* when:

- The runtime payload's ``<approved_deferrals>`` block lists a
  description that matches the field's topic — these come from
  ``manifest.gaps.to_be_defined`` and from ``manifest.gaps.hard_gaps``
  with ``blocks_sow_generation: false``, i.e. the discovery agent or
  the user explicitly recorded the field as deferred.
- The placeholder appears in a contractual field the SOW template
  treats as fill-on-signature (MSA / parent contract reference,
  governing law, customer counterpart, sponsor names).

For approved placeholders: do NOT emit a finding asking the user to
fill the field. The deferral is sanctioned; the value will be supplied
in a later phase or at signature time. The only time you flag an
approved placeholder is when it creates a downstream inconsistency
(e.g. another SOW field asserts a concrete value that contradicts the
deferral).

For UNAPPROVED placeholders (the placeholder marker is present but no
``<approved_deferrals>`` entry covers the field, AND it is not one of
the contractual fill-on-signature fields): emit one finding with
``resolution_mode: "auto_fixable"`` recommending the revision_agent
either populate the field from manifest evidence or remove the field
if no manifest evidence exists. This catches a model that drops a
placeholder into a non-deferred field to hide a real coverage gap —
the revision_agent will replace it with grounded content or surface
the underlying gap. NEVER set ``decision_required`` on an unapproved
placeholder — the fix is mechanical (populate or remove); only the
underlying coverage gap might be decision-required, and the coverage
skill emits that finding separately.

---

# Inference patterns (binding)

The following patterns describe classes of findings — not specific
incidents — where the resolution is inferable from sources already in
the SOW, the manifest, or standard consulting practice. When a finding
matches one of these classes, set ``resolution_mode: "auto_fixable"``
and let the revision_agent apply the canonical fix. The escape
hatches at the end of each pattern preserve genuine escalation when
the inference is unsafe (guardrail #4 of the audit: never mask a real
external-data need or a real trade-off).

## Pattern A — Responsibility ambiguity resolved by adjacent scope

Trigger: a contradiction or ambiguity about which party (Partner or
Customer) is responsible for an action, AND the SOW already contains
at least one assumption, OOS item, or scope statement that names one
party's responsibility for adjacent work.

Resolution: refine the contradicting item to follow the canonical
split implied by the engagement scope — the Partner is responsible
for the artifacts and systems being delivered under this SOW; the
Customer is responsible for their own environment, owned systems, and
any artifacts they bring to the engagement that the Partner is
consuming or integrating with. Mirror the language already used by
the adjacent assumption / scope statement.

Resolution mode: ``auto_fixable``.

Escape hatch — escalate to ``decision_required`` ONLY when no adjacent
assumption or scope statement names either party, AND the choice
between the two parties has clear commercial consequences (different
cost envelope, different timeline, different liability surface).

## Pattern B — External Customer artifact referenced without attachment

Trigger: a SOW item references an external artifact owned by the
Customer (a standard, template, document, policy, capability matrix,
integration spec, governance framework, etc.) that is not in the
manifest's inventory and is not attached to the SOW.

Resolution: the SOW does not need to contain the external artifact
itself. Insert the canonical consulting pattern: (1) an assumption
stating the Customer must provide the artifact by a defined milestone
(kickoff or the start of the relevant phase), and (2) a Change
Request clause stating that any post-delivery changes to the artifact
require a CR with potential timeline / cost impact.

Resolution mode: ``auto_fixable``.

Escape hatch — escalate to ``decision_required`` ONLY when the
artifact's content would materially change the engagement's scope
(e.g. it might mandate a technology choice that conflicts with the
current architecture, or expand responsibility beyond the Partner's
delivery surface).

## Pattern C — Identifying information about Customer participants

Trigger: the SOW lists Customer roles by function (Sponsor, SME,
Engineer, etc.) but specific names are not in the manifest and not
in the conversation history.

Resolution: keep the role + responsibility text as authored. Specific
names belong in a placeholder field per the "Approved placeholders"
rule above; the downstream signature workflow fills them. Do not
remove the role and do not ask the user to supply the names during
validation.

Resolution mode: ``auto_fixable``.

Escape hatch — escalate to ``decision_required`` ONLY when the
manifest's ``gaps.hard_gaps`` explicitly flagged the missing name
with ``blocks_sow_generation: true`` (signalling that discovery
already escalated the gap and the SOW cannot proceed without
resolution).
"""


def _make_instruction_provider(skill_name: str, skill_body: str):
    """Closure that resolves SKILL.md body + runtime payload from state."""

    def _provider(ctx: ReadonlyContext) -> str:
        state = ctx.state
        sow = state.get(STATE_SOW) or {}
        residual = _trim_manifest(state.get(STATE_MANIFEST_RESIDUAL) or [])
        stage = state.get(STATE_STAGE) or 'full'

        # Approved placeholders block — sourced from manifest.gaps so
        # the LLM has a concrete list of deferral descriptions to
        # match against any ``[TO BE DEFINED]`` / ``[A DEFINIR]``
        # literal it finds in the SOW. Empty list is fine: when no
        # deferrals are approved, every placeholder in the SOW is by
        # definition unapproved (and the "Approved placeholders" block
        # in the GUIDE tells the skill to emit an auto_fixable finding
        # asking the revision_agent to populate or remove the field).
        manifest = state.get(_MANIFEST_STATE_KEY) or {}
        approved_deferrals = collect_approved_deferrals(manifest)

        payload = (
            '\n\n---\n\n'
            '# Runtime payload\n\n'
            f'Stage: `{stage}`.\n\n'
            '<sow_data>\n'
            f'{json.dumps(sow, ensure_ascii=False, indent=2)}\n'
            '</sow_data>\n\n'
            '<manifest_residual>\n'
            f'{json.dumps(residual, ensure_ascii=False, indent=2)}\n'
            '</manifest_residual>\n\n'
            '<approved_deferrals>\n'
            f'{json.dumps(approved_deferrals, ensure_ascii=False, indent=2)}\n'
            '</approved_deferrals>\n\n'
            'Return ONLY a JSON object matching the schema: '
            f'`{{"findings": [Finding, ...]}}` with `skill="{skill_name}"` '
            'on every finding. Each finding MUST include a '
            '`resolution_mode` field (defaulting to `auto_fixable`). '
            'Return `{"findings": []}` if nothing applies.'
        )
        return skill_body + _RESOLUTION_MODE_GUIDE + payload

    return _provider


def _make_model() -> Gemini:
    return Gemini(
        model=config.VALIDATION_SKILL_MODEL,
        retry_options=types.HttpRetryOptions(attempts=config.MAX_RETRIES),
    )


def _make_skill_agent(skill_name: str) -> LlmAgent:
    """Build the LlmAgent that runs one semantic skill."""
    skill_md = _read_with_gate(_SKILLS_DIR / skill_name / 'SKILL.md')
    return LlmAgent(
        name=f'{skill_name}_skill_agent',
        description=f'Semantic validation skill: {skill_name}.',
        model=_make_model(),
        instruction=_make_instruction_provider(skill_name, skill_md),
        output_schema=SkillFindings,
        output_key=skill_findings_state_key(skill_name),
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
        generate_content_config=types.GenerateContentConfig(
            temperature=config.TEMPERATURE,
            thinking_config=types.ThinkingConfig(
                include_thoughts=False,
                thinking_budget=config.VALIDATION_SKILL_THINKING_BUDGET,
            ),
        ),
        include_contents="none"
    )


coverage_skill_agent = _make_skill_agent('coverage')
contradictions_skill_agent = _make_skill_agent('contradictions')
contractual_exposure_skill_agent = _make_skill_agent('contractual_exposure')
disclosures_skill_agent = _make_skill_agent('disclosures')
semantic_quality_skill_agent = _make_skill_agent('semantic_quality')


semantic_skills_parallel = ParallelAgent(
    name='semantic_skills',
    description=(
        'Runs the five semantic validation skills concurrently. Each skill '
        'writes into its own state key — no race condition possible.'
    ),
    sub_agents=[
        coverage_skill_agent,
        contradictions_skill_agent,
        contractual_exposure_skill_agent,
        disclosures_skill_agent,
        semantic_quality_skill_agent,
    ],
)


__all__ = [
    'coverage_skill_agent',
    'contradictions_skill_agent',
    'contractual_exposure_skill_agent',
    'disclosures_skill_agent',
    'semantic_quality_skill_agent',
    'semantic_skills_parallel',
    'SKILL_NAMES',
]
