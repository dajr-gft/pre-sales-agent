"""Step 2 of validation_critic — four semantic skills running in parallel.

Each skill is an `LlmAgent` whose ``instruction`` is the body of its
`SKILL.md` plus a runtime payload (SOW + stage) resolved per invocation.
Every skill writes its findings into a **dedicated** state key —
`app:skill_findings:{name}` — so the `ParallelAgent` runs without any
chance of a write race.

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
from .schema import (
    ROUND_MODE_VERIFICATION,
    SKILL_NAMES,
    STATE_CHANGED_SECTIONS,
    STATE_ROUND_MODE,
    STATE_SOW,
    STATE_STAGE,
    SkillFindings,
    skill_findings_state_key,
)

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
  sections are populated, alongside project metadata. The assembler
  invoked by ``stage_sow`` intentionally omits architecture and
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

At ``Stage: full`` the inverse holds for the CONTENT sections —
requirements, delivery_plan, and scope_boundaries. The user already
validated and approved them at the Content Review, and no section
bundle changes between that approval and the full-stage run. Treat the
following content-owned ``sow_data`` keys as READ-ONLY context — you
may read them to judge architecture and narrative, but you MUST NOT
emit a finding that would route a repair back into one of them:

- ``functional_requirements``
- ``non_functional_requirements``
- ``activity_phases``
- ``deliverables``
- ``timeline``
- ``partner_roles``
- ``customer_roles``
- ``success_criteria``
- ``objectives``
- ``assumptions``
- ``out_of_scope``
- ``risks``
- ``handover_disclaimers``
- ``change_request_policy_text``

By default, do NOT emit a finding whose ``fields`` target one of those
content keys when ``Stage: full``. Re-litigating approved content only
adds churn and risks editing text the user already signed off on; that
content was the content-stage critic's job and it passed. This is NOT a
re-run of the content checks — only the two cross-section cases below
may touch content at all, and both require a GENUINE, high-confidence
conflict between architecture/narrative and content (a real
contradiction, not a stylistic or coverage preference).

Case 1 — the fix belongs in architecture/narrative (the common case).
The approved content is the source of truth and architecture/narrative
must align to it. Emit the finding ``auto_fixable`` and name ONLY
architecture / narrative / technology_stack keys in ``fields`` so the
repair routes to the architecture or narrative section. Cite the
content key in your evidence text, never in ``fields``. Example: a
service named in the architecture description is absent from
``technology_stack`` → fix the architecture side.

Case 2 — the conflict can ONLY be resolved correctly by changing the
approved content (architecture/narrative is right and the defect is in
requirements / delivery_plan / scope_boundaries). Do NOT auto-fix and
do NOT silently drop it. Emit ONE finding with
``resolution_mode: "decision_required"`` (NEVER ``auto_fixable``), name
the offending content key(s) in ``fields``, and state in the
``recommendation`` that resolving it requires REOPENING the Content
Review for that section. The aggregator promotes any
``decision_required`` finding to ``needs_human_review``, so the loop
stops and the orchestrator surfaces it to the user instead of rewriting
approved content. ``decision_required`` is the ONLY mode under which a
content key may appear in ``fields`` at ``Stage: full``. Use this
sparingly — it interrupts the user; reserve it for a true conflict
where aligning the architecture would itself be wrong.

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


# Injected into a skill's instruction ONLY in verification mode (rounds
# 2+), in addition to ``_RESOLUTION_MODE_GUIDE``. Mirrors that block's
# pattern: one centralized rubric, not duplicated per ``SKILL.md``.
#
# Discovery (round 1) audits a freshly staged document broadly. Rounds
# 2+ run AFTER a repair, and the failure mode the loop kept hitting is an
# "infinite audit": the critic re-audits the whole document every round
# and opens new lateral fronts, so the residue never reaches zero even
# while net progress is real. This block narrows a verification round to
# "confirm the repair + catch regressions" WITHOUT re-mining the whole
# document, while explicitly refusing to let a genuine critical BLOCKER
# disappear. The rule is prompt-level (soft); a deterministic backstop in
# the aggregator can mirror it later (PR-5) if measurement shows leakage.
#
# ``{changed_sections}`` is filled per round by the instruction provider
# from ``STATE_CHANGED_SECTIONS``.
_VERIFICATION_MODE_GUIDE = """\

---

# Verification mode (this round is a repair check, NOT a fresh audit)

This is a VERIFICATION round, not a discovery round. A previous round
already audited this document and an automatic repair was applied. Your
job now is to confirm that repair landed and to catch regressions it may
have introduced — NOT to open new, unrelated audit fronts. Re-auditing
the whole document every round is what prevents the loop from
converging.

Sections changed by the previous repair: {changed_sections}.

Finding-admission rule for verification mode:

1. UNRESOLVED / RECURRING issue — if a defect you would have flagged
   before is STILL present in the SOW, flag it again. Confirming whether
   prior issues were actually fixed is the whole point of this round;
   never suppress one just because it is not new.

2. REGRESSION RELATED TO THE REPAIR — a NEW issue is in scope when it is
   directly connected to the repair: a BLOCKER or MAJOR defect in one of
   the changed sections listed above, OR a new instance of the same
   problem family (same ``skill`` + ``category``) as an issue from a
   prior round. These are exactly the failure modes a repair introduces;
   report them normally.

3. NEW, FULLY-LATERAL issue of MAJOR or MINOR severity — an issue
   unrelated to the repair (not in a changed section, not the same
   family) that is not a critical defect: do NOT raise it this round. It
   belongs to a later discovery pass, not this verification loop.
   Omitting it here is deliberate and it is not lost — a future
   discovery round will surface it.

4. NEW, FULLY-LATERAL issue of BLOCKER severity — do NOT go looking for
   these; verification mode is not an audit. But if you nonetheless
   identify an unambiguous, critical, brand-new lateral BLOCKER, you MUST
   still report it — never silently drop it. Label it clearly as a
   critical lateral exception in the ``recommendation`` and keep your
   ``confidence`` honest. A genuine BLOCKER is never suppressed just
   because it is lateral.

In short: confirm the repair, catch regressions and unresolved issues,
do NOT mine for new lateral MAJOR/MINOR problems, and never hide a
genuine critical BLOCKER.
"""


def _make_instruction_provider(skill_name: str, skill_body: str):
    """Closure that resolves SKILL.md body + runtime payload from state."""

    def _provider(ctx: ReadonlyContext) -> str:
        state = ctx.state
        sow = state.get(STATE_SOW) or {}
        stage = state.get(STATE_STAGE) or 'full'

        # Verification-mode binding (PR-3). Round 1 (discovery) gets the
        # base rubric; rounds 2+ also get the verification block so the
        # critic confirms the repair instead of re-auditing the whole
        # document. ``STATE_ROUND_MODE`` / ``STATE_CHANGED_SECTIONS`` are
        # written by the QualityLoopAgent before each critic run (PR-2);
        # absent or 'discovery' -> no block, identical to prior behavior.
        verification_block = ''
        if state.get(STATE_ROUND_MODE) == ROUND_MODE_VERIFICATION:
            changed_sections = state.get(STATE_CHANGED_SECTIONS) or []
            changed_label = (
                ', '.join(str(s) for s in changed_sections)
                if changed_sections
                else '(none recorded for the previous repair)'
            )
            verification_block = _VERIFICATION_MODE_GUIDE.format(
                changed_sections=changed_label,
            )

        payload = (
            '\n\n---\n\n'
            '# Runtime payload\n\n'
            f'Stage: `{stage}`.\n\n'
            '<sow_data>\n'
            f'{json.dumps(sow, ensure_ascii=False, indent=2)}\n'
            '</sow_data>\n\n'
            'Return ONLY a JSON object matching the schema: '
            f'`{{"findings": [Finding, ...]}}` with `skill="{skill_name}"` '
            'on every finding. Each finding MUST include a '
            '`resolution_mode` field (defaulting to `auto_fixable`). '
            'Return `{"findings": []}` if nothing applies.'
        )
        return (
            skill_body + _RESOLUTION_MODE_GUIDE + verification_block + payload
        )

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


contradictions_skill_agent = _make_skill_agent('contradictions')
contractual_exposure_skill_agent = _make_skill_agent('contractual_exposure')
disclosures_skill_agent = _make_skill_agent('disclosures')
semantic_quality_skill_agent = _make_skill_agent('semantic_quality')


semantic_skills_parallel = ParallelAgent(
    name='semantic_skills',
    description=(
        'Runs the four semantic validation skills concurrently. Each skill '
        'writes into its own state key — no race condition possible.'
    ),
    sub_agents=[
        contradictions_skill_agent,
        contractual_exposure_skill_agent,
        disclosures_skill_agent,
        semantic_quality_skill_agent,
    ],
)


__all__ = [
    'contradictions_skill_agent',
    'contractual_exposure_skill_agent',
    'disclosures_skill_agent',
    'semantic_quality_skill_agent',
    'semantic_skills_parallel',
    'SKILL_NAMES',
]
