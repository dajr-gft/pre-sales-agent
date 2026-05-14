"""Stage the draft SOW in session state for the Validation Critic.

This tool **only writes state**. It does NOT invoke the
`validation_critic` sub-agent — that would re-introduce the "tool
orchestrating agent" anti-pattern the new architecture was designed
to eliminate.

Use this tool right before transferring control to `validation_critic`:

    1. agent calls `stage_sow(sow_data, stage)` — SOW lands in state.
    2. agent transfers to `validation_critic` (native ADK mechanism).
    3. validation_critic reads state, runs pipeline, escalates back.
    4. agent reads `state['app:validation_result']`.

The legacy `validate_sow_content` tool remains available for callers
that only need a deterministic check without involving the critic.
"""

import json
from typing import Any

import structlog
from google.adk.tools import ToolContext

from ...shared.errors import safe_tool
from ...shared.types import ToolError, ToolSuccess
from ...sub_agents.validation.schema import STATE_SOW, STATE_STAGE
from ._sow_helpers import sow_data_hash

logger = structlog.get_logger()

_LANGUAGE_STATE_KEY = 'app:language'


@safe_tool
async def stage_sow(
    sow_data: str,
    stage: str = 'full',
    language: str = '',
    tool_context: ToolContext = None,
) -> dict[str, Any]:
    """Stage the SOW JSON in session state so the Validation Critic can read it.

    Call this tool **before** transferring control to `validation_critic`.
    After this returns, your next step is to transfer to
    `validation_critic`. The result will arrive in
    `state['app:validation_result']` once the critic escalates back.

    Args:
        sow_data: The SOW JSON string. Same schema as
            `generate_sow_document`.
        stage: "content" for Phase 2 Step 1.5 (no architecture yet) or
            "full" for Phase 4 (full payload). Defaults to "full".
        language: Optional language tag (e.g. "pt-BR", "en") so the
            validation summary matches the conversation language.

    Returns:
        A success dict. The validation result is NOT returned by this
        tool — read it from `state['app:validation_result']` after
        transferring to `validation_critic` and getting control back.
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

    try:
        data = json.loads(sow_data)
    except json.JSONDecodeError as exc:
        return ToolError(
            status='error',
            error=f'Invalid JSON: {exc}',
            retryable=False,
            tool='stage_sow',
            suggestion='Fix the JSON syntax and call this tool again.',
        )

    stage_normalized = (stage or 'full').strip().lower()
    if stage_normalized not in ('content', 'full'):
        stage_normalized = 'full'

    tool_context.state[STATE_SOW] = data
    tool_context.state[STATE_STAGE] = stage_normalized
    if language:
        tool_context.state[_LANGUAGE_STATE_KEY] = language

    sow_hash = sow_data_hash(sow_data)
    logger.info(
        'sow_staged_for_validation',
        sow_data_hash=sow_hash,
        stage=stage_normalized,
        language=language or None,
    )

    return ToolSuccess(
        status='success',
        data={
            'stage': stage_normalized,
            'sow_data_hash': sow_hash,
            'next_step': (
                'Transfer to the `validation_critic` sub-agent. The '
                'final ValidationReport will land in '
                'state["app:validation_result"].'
            ),
        },
    )
