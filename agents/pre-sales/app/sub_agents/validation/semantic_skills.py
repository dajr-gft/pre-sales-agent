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
from .schema import (
    SKILL_NAMES,
    STATE_MANIFEST_RESIDUAL,
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
"""


def _make_instruction_provider(skill_name: str, skill_body: str):
    """Closure that resolves SKILL.md body + runtime payload from state."""

    def _provider(ctx: ReadonlyContext) -> str:
        state = ctx.state
        sow = state.get(STATE_SOW) or {}
        residual = _trim_manifest(state.get(STATE_MANIFEST_RESIDUAL) or [])
        stage = state.get(STATE_STAGE) or 'full'

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
