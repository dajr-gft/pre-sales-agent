"""SOW revision specialist — surgical patches driven by validation findings.

Wrapped in ``AgentTool`` by ``QualityLoopAgent`` so the root never holds
this agent's instruction in its own context. The agent needs the staged
SOW and the latest ``ValidationReport`` to do its job, but with
``include_contents='none'`` it cannot see them via conversation history,
and no tool returns them either. We therefore build the instruction
through a provider that reads the relevant state keys at every turn and
injects them as labelled XML blocks (``<staged_sow>``,
``<validation_report>``, ``<revision_log>``). When either of the two
required inputs is missing the provider switches to a STOP footer so the
LLM never invents a patch from training data.

The agent intentionally has no ``output_schema``: its outputs are side
effects on session state (``app:sow:current`` updated by ``stage_sow``;
``app:sow:revision_log`` appended by ``record_revision_log_entries``).
That keeps it away from the known ``output_schema + tools`` loop hazard
described in the ADK docs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from google.adk.agents import Agent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.models import Gemini
from google.adk.skills import load_skill_from_dir
from google.genai import types

from ...config import config
from ...shared.safety import build_safety_settings
from ...tools.sow.stage_sow import stage_sow
from ..validation.schema import STATE_SOW, STATE_VALIDATION_RESULT
from .log_tools import REVISION_LOG_STATE_KEY, record_revision_log_entries
from .tools import load_sow_reference

_SKILL_DIR = Path(__file__).parents[2] / 'skills' / 'sow-revision'
_skill = load_skill_from_dir(_SKILL_DIR)


_MISSING_INPUTS_FOOTER = (
    '\n\n---\n\n'
    '# Runtime inputs — MISSING (do not invent patches)\n\n'
    'The following inputs the patch contract depends on are NOT present '
    'in session state on this turn:\n{missing_list}\n\n'
    '**STOP.** Do NOT call `stage_sow`. Do NOT call '
    '`record_revision_log_entries`. Do NOT fabricate a patched SOW from '
    'prior training, general knowledge, or earlier turns. The required '
    'upstream state has not been written yet — the QualityLoopAgent '
    'invoked this agent out of order or the staged SOW was cleared '
    'between rounds.\n\n'
    'End your turn with a single short diagnostic message naming which '
    'state key(s) are missing, in English, so the orchestrator can '
    'recover. Do not produce any tool calls.\n'
)


_INPUTS_PRESENT_FOOTER = (
    '\n\n---\n\n'
    '# Runtime inputs\n\n'
    '{rendered_inputs}\n\n'
    'Use ONLY the data above plus the references loaded via '
    '`load_sow_reference`. Apply the three anti-regeneration contracts '
    'from the SKILL above: touch only the top-level keys listed in '
    '`finding.fields`; preserve every untouched field byte-for-byte; '
    'preserve every existing id. Persist patches via `stage_sow` and '
    'append one entry per processed finding via '
    '`record_revision_log_entries`.\n'
)


def _serialize_state_value(value: Any) -> str:
    """Compact JSON for the prompt block; ``repr`` fallback for oddities.

    Compact encoding keeps the worker prompt lean — a full SOW already
    occupies the bulk of the budget. ``ensure_ascii=False`` preserves
    Portuguese accents in customer/vendor names captured by discovery.
    """
    try:
        return json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    except (TypeError, ValueError):
        return repr(value)


def _is_present(value: Any) -> bool:
    """Mirror ``_section_agent._is_present``: empty containers count as missing.

    The patcher needs substantive content. An empty dict in
    ``state['app:sow:current']`` is just as useless as ``None`` — it
    would still leave the LLM with nothing to patch.
    """
    if value is None:
        return False
    if isinstance(value, (dict, list, str, tuple, set)) and not value:
        return False
    return True


def _make_revision_instruction_provider(skill_body: str):
    """Build the instruction provider that injects SOW + report + log.

    Captures only the static ``skill_body`` string; reads ``ctx.state``
    every turn so each round of the QualityLoopAgent sees the latest
    patched SOW and the freshest critic report.

    Args:
        skill_body: ``sow-revision/SKILL.md`` body (already stripped of
            frontmatter by ``load_skill_from_dir``). Treated as opaque
            text — the provider only appends a footer.

    Returns:
        A callable accepted by ADK's ``LlmAgent(instruction=...)``.
    """

    def _provider(ctx: ReadonlyContext) -> str:
        state = ctx.state
        sow = state.get(STATE_SOW)
        report = state.get(STATE_VALIDATION_RESULT)
        # revision_log is optional — round 1 starts with an empty log and
        # that is the correct initial state, not a missing input.
        revision_log = state.get(REVISION_LOG_STATE_KEY) or []

        missing: list[str] = []
        if not _is_present(sow):
            missing.append(
                f'- `<staged_sow>` (state[{STATE_SOW!r}])'
            )
        if not _is_present(report):
            missing.append(
                f'- `<validation_report>` (state[{STATE_VALIDATION_RESULT!r}])'
            )

        if missing:
            return skill_body + _MISSING_INPUTS_FOOTER.format(
                missing_list='\n'.join(missing)
            )

        rendered = (
            f'<staged_sow>\n{_serialize_state_value(sow)}\n</staged_sow>\n\n'
            f'<validation_report>\n{_serialize_state_value(report)}\n'
            '</validation_report>\n\n'
            f'<revision_log>\n{_serialize_state_value(revision_log)}\n'
            '</revision_log>'
        )
        return skill_body + _INPUTS_PRESENT_FOOTER.format(
            rendered_inputs=rendered
        )

    return _provider


revision_agent = Agent(
    name='revision_agent',
    description=(
        'Applies minimum-change patches to the staged SOW based on a '
        'ValidationReport. Loads section references via load_sow_reference '
        '(allowlist-protected), patches affected fields only, then calls '
        'stage_sow and record_revision_log_entries. Never regenerates '
        'whole sections.'
    ),
    model=Gemini(
        model=config.GEMINI_MODEL,
        retry_options=types.HttpRetryOptions(attempts=config.MAX_RETRIES),
    ),
    instruction=_make_revision_instruction_provider(_skill.instructions),
    # Real context isolation: the revision agent must operate solely on
    # its instruction (SKILL.md + injected staged SOW + report + log) and
    # the references loaded via `load_sow_reference`. Letting it inherit
    # the root's conversation history would re-introduce the monolithic-
    # context problem the decomposition was designed to fix: revision
    # rounds that have already seen the user discussing "make the timeline
    # shorter" would bias the patcher toward content changes outside
    # `finding.fields`, violating Contract 1.
    include_contents='none',
    tools=[
        load_sow_reference,
        stage_sow,
        record_revision_log_entries,
    ],
    # The agent has its work fully scoped by the staged SOW and the
    # ValidationReport; there is no legitimate reason for it to escalate
    # to the QualityLoopAgent parent or transfer to a sibling. Pinning
    # these two flags keeps the loop's stop conditions authoritative.
    disallow_transfer_to_parent=True,
    disallow_transfer_to_peers=True,
    generate_content_config=types.GenerateContentConfig(
        temperature=config.TEMPERATURE,
        safety_settings=build_safety_settings(),
        thinking_config=types.ThinkingConfig(
            include_thoughts=False,
            thinking_budget=config.THINKING_BUDGET,
        ),
    ),
)
