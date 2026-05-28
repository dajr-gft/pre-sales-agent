"""Assemble + stage the draft SOW in session state for downstream validation.

This tool reads the per-section bundles plus the metadata envelope
already written to session state, assembles the flat ``sow_data``
payload internally (via :func:`build_sow_data_from_state`), and
persists it under ``state['app:sow:current']`` together with the stage
cursor. The model never re-emits the payload — that closes the
round-trip where drift between what the assembler produced and what
the LLM passed back as ``sow_data`` could corrupt the staged SOW.

Typical sequence:

    1. The root has saved every per-section bundle (`save_<section>_bundle`)
       and the metadata envelope (`save_sow_metadata`) into state.
    2. The root calls ``stage_sow(stage=..., language=...)`` — the
       assembly + staging happen together.
    3. The root calls the ``sow_quality_loop`` AgentTool.
    4. The root reads ``state['app:sow:quality_loop_result']`` to
       decide the next step.

The pure :func:`build_sow_data_from_state` helper and the ADK-tool
``assemble_sow_payload`` (in ``tools.sow.assemble_payload``) remain
available as dry-run / debug entry points that return the assembled
dict without mutating state. The canonical write path is this tool.
"""

import json
from typing import Any

import structlog
from google.adk.tools import ToolContext

from ...shared.errors import safe_tool
from ...shared.types import ToolError, ToolSuccess
from ...sub_agents.validation.schema import (
    STATE_PRIOR_BLOCKING_FINGERPRINTS,
    STATE_ROUND_COUNT,
    STATE_SOW,
    STATE_STAGE,
)
from ._sow_helpers import sow_data_hash
from .assemble_payload import AssemblyError, build_sow_data_from_state

logger = structlog.get_logger()

_LANGUAGE_STATE_KEY = 'app:language'


_VALID_STAGES: frozenset[str] = frozenset({'content', 'full'})


@safe_tool
async def stage_sow(
    stage: str,
    language: str = '',
    tool_context: ToolContext = None,
) -> dict[str, Any]:
    """Assemble and stage the SOW payload from session-state bundles.

    The flat ``sow_data`` dict is built deterministically by
    :func:`build_sow_data_from_state` from the bundles and metadata
    envelope already in state — the caller does NOT pass it. Removing
    the round-trip closes the window where the model could drop fields,
    rename keys, or pass a stale snapshot between the assembler and the
    staging write.

    Args:
        stage: ``"content"`` for the Phase 2 content stage (architecture
            and narrative still absent) or ``"full"`` for the complete
            payload. **Required** — there is no safe default. The
            previous behaviour silently fell back to ``"full"`` when the
            argument was missing or invalid, which let the
            revision_agent promote a content-stage SOW to full mid-loop.
            That reset ``STATE_ROUND_COUNT`` /
            ``STATE_PRIOR_BLOCKING_FINGERPRINTS`` and re-introduced
            architecture / narrative findings that the content-stage
            validation correctly ignored.
        language: language tag (e.g. "pt-BR", "en") so the validation
            summary matches the conversation language.

    Returns:
        Success dict with the stage value and a hash of the staged
        payload. Validation runs separately via ``sow_quality_loop``;
        read its outcome from ``state['app:sow:quality_loop_result']``.
    """
    if tool_context is None:
        return ToolError(
            status='error',
            error='tool_context is required.',
            retryable=False,
            tool='stage_sow',
            suggestion=(
                'Call this tool from within an ADK runtime; tool_context '
                'is injected automatically.'
            ),
        )

    stage_normalized = (stage or '').strip().lower()
    if stage_normalized not in _VALID_STAGES:
        previous_stage = tool_context.state.get(STATE_STAGE)
        return ToolError(
            status='error',
            error=(
                f"'stage' must be 'content' or 'full', got {stage!r}."
            ),
            retryable=False,
            tool='stage_sow',
            suggestion=(
                'Pass the stage explicitly. The revision_agent must copy '
                f"the current stage value (currently {previous_stage!r}) "
                'from `<current_stage>` in its prompt; the orchestrator '
                'passes `content` during Phase 2 content review and `full` '
                'for the architecture review and Phase 3. There is no '
                'safe default — silently promoting the stage resets the '
                "QualityLoopAgent's round tracking."
            ),
        )

    # Build the flat payload from state-resident bundles. Any precondition
    # failure (missing bundle, MISSING_INPUT sentinel from an aborted
    # section worker, blank required metadata) surfaces as a structured
    # AssemblyError that we translate into a ToolError with an actionable
    # suggestion — the same surface assemble_sow_payload used to expose.
    # State is NOT mutated when assembly fails: the writes below only run
    # past a clean assembly.
    try:
        sow_data = build_sow_data_from_state(
            tool_context.state, stage_normalized,
        )
    except AssemblyError as err:
        if err.missing_keys:
            suggestion = (
                'Run the affected section skill(s) and save their bundles '
                f'before calling stage_sow. Missing: {err.missing_keys}'
            )
            logger.warning(
                'stage_sow_missing_bundles',
                stage=stage_normalized,
                missing=err.missing_keys,
            )
        elif err.sentinel_keys:
            suggestion = (
                'A section bundle contains the MISSING_INPUT sentinel '
                'because a required upstream input was missing when the '
                'section ran. Re-load the affected section skill, regenerate '
                'the section, and call the matching save_<section>_bundle '
                'tool again — the sentinel clears once the bundle is rewritten '
                f'with all its inputs present. Affected bundles: '
                f'{err.sentinel_keys}.'
            )
            logger.warning(
                'stage_sow_sentinel_detected',
                stage=stage_normalized,
                sentinel_keys=err.sentinel_keys,
            )
        elif err.missing_metadata:
            suggestion = (
                'Call `save_sow_metadata` with non-empty values for: '
                f'{err.missing_metadata}, then call stage_sow again.'
            )
            logger.warning(
                'stage_sow_missing_metadata',
                stage=stage_normalized,
                missing=err.missing_metadata,
            )
        else:
            suggestion = (
                "Pass stage='content' before the Content Review, 'full' "
                'after architecture and narrative.'
            )
        return ToolError(
            status='error',
            error=err.reason,
            retryable=False,
            tool='stage_sow',
            suggestion=suggestion,
        )

    # Detect stage transitions BEFORE writing the new stage. The aggregator
    # increments STATE_ROUND_COUNT monotonically across critic runs within a
    # single staged payload — that signal is meaningful only while the staged
    # SOW (and its `stage`) is unchanged. The moment we re-stage with a
    # different stage value (typically `content` -> `full` after the user
    # approves the Content Review and the architecture / narrative bundles
    # join the payload), the previous round_count and the persistent
    # fingerprint set refer to a SOW that no longer exists. Carrying them
    # forward poisons persistence detection on the new payload and inflates
    # the round counter in telemetry. Reset both keys here so the next
    # `sow_quality_loop` run starts from a clean budget.
    previous_stage = tool_context.state.get(STATE_STAGE)
    stage_changed = previous_stage is not None and previous_stage != stage_normalized

    tool_context.state[STATE_SOW] = sow_data
    tool_context.state[STATE_STAGE] = stage_normalized
    if stage_changed:
        tool_context.state[STATE_ROUND_COUNT] = 0
        tool_context.state[STATE_PRIOR_BLOCKING_FINGERPRINTS] = []
        logger.info(
            'stage_sow_reset_round_state',
            previous_stage=previous_stage,
            new_stage=stage_normalized,
        )
    # Language is owned by the orchestrator (the root sets it from the
    # user's first turn) — sub-agents that re-stage during the quality
    # loop must NOT clobber it. Production incident: the revision_agent
    # called stage_sow(..., language='en') between rounds and overrode
    # the user's 'pt-BR', flipping the root's subsequent responses to
    # English. The rule: set only when the slot is empty; ignore any
    # non-empty argument that disagrees with the value already in state.
    if language:
        existing_language = tool_context.state.get(_LANGUAGE_STATE_KEY)
        if not existing_language:
            tool_context.state[_LANGUAGE_STATE_KEY] = language
        elif existing_language != language:
            logger.info(
                'stage_sow_language_override_ignored',
                existing_language=existing_language,
                attempted_language=language,
            )

    # Stable serialization for the hash regardless of dict ordering.
    sow_hash = sow_data_hash(
        json.dumps(sow_data, sort_keys=True, ensure_ascii=False),
    )
    logger.info(
        'sow_staged_for_validation',
        sow_data_hash=sow_hash,
        stage=stage_normalized,
        language=language or None,
    )

    # ``sow_data`` is returned so the caller has the freshly assembled
    # payload visible in its conversation context — this is what the root
    # uses to render the Content / Architecture Review gates with the
    # current content, including any patches the quality loop's repair
    # agents applied after the previous stage. The caller MUST NOT
    # re-emit this dict as an argument to any tool: stage_sow takes no
    # ``sow_data`` parameter on purpose (closing the round-trip drift
    # window), and generate_sow_document reads ``state['app:sow:current']``
    # directly. Treat the field as read-only situational awareness for the
    # turn that presents the review.
    return ToolSuccess(
        status='success',
        data={
            'stage': stage_normalized,
            'sow_data_hash': sow_hash,
            'sow_data': sow_data,
            'next_step': (
                'SOW assembled from state and staged. The caller is '
                'responsible for invoking the next validation step '
                '(typically the `sow_quality_loop` sub-agent).'
            ),
        },
    )
