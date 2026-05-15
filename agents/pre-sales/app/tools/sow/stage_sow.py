"""Stage the draft SOW in session state for downstream validation.

This tool **only writes state**. It does NOT run validation; the
``sow_quality_loop`` sub-agent owns the critic → revision dance.

Typical sequence:

    1. agent calls ``assemble_sow_payload(stage=...)`` — returns the SOW
       payload as a dict in ``data.sow_data``.
    2. agent calls ``stage_sow(sow_data=<that dict>, stage=...)`` —
       payload lands in ``state['app:sow:current']``.
    3. agent calls the ``sow_quality_loop`` AgentTool.
    4. agent reads ``state['app:sow:quality_loop_result']`` to decide
       the next step.

The legacy ``validate_sow_content`` helper remains available for
non-agent callers that only need a deterministic structural check.
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
    sow_data: dict[str, Any],
    stage: str = 'full',
    language: str = '',
    tool_context: ToolContext = None,
) -> dict[str, Any]:
    """Stage the SOW payload in session state for downstream validation.

    Accepts the dict returned by ``assemble_sow_payload`` (under its
    ``data.sow_data`` field). The signature is intentionally NOT
    ``Union[str, dict]`` — Gemini's function-calling schema rejects
    ``any_of`` combined with other fields (description), and that
    combination is what an annotated Union produces. Keeping a single
    concrete type sidesteps the API constraint cleanly.

    Args:
        sow_data: SOW payload dict in the schema accepted by
            ``generate_sow_document``. Pass the ``sow_data`` field returned
            by ``assemble_sow_payload``.
        stage: "content" for the Phase 2 content stage (architecture and
            narrative still absent) or "full" for the complete payload.
            Defaults to "full".
        language: Optional language tag (e.g. "pt-BR", "en") so the
            validation summary matches the conversation language.

    Returns:
        Success dict. Validation runs separately via ``sow_quality_loop``;
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

    if not isinstance(sow_data, dict):
        return ToolError(
            status='error',
            error=(
                f"'sow_data' must be a dict, got {type(sow_data).__name__}."
            ),
            retryable=False,
            tool='stage_sow',
            suggestion=(
                "Pass the 'sow_data' field returned by assemble_sow_payload."
            ),
        )

    stage_normalized = (stage or 'full').strip().lower()
    if stage_normalized not in ('content', 'full'):
        stage_normalized = 'full'

    tool_context.state[STATE_SOW] = sow_data
    tool_context.state[STATE_STAGE] = stage_normalized
    if language:
        tool_context.state[_LANGUAGE_STATE_KEY] = language

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

    return ToolSuccess(
        status='success',
        data={
            'stage': stage_normalized,
            'sow_data_hash': sow_hash,
            'next_step': (
                'SOW staged in session state. The caller is responsible '
                'for invoking the next validation step (typically the '
                '`sow_quality_loop` sub-agent).'
            ),
        },
    )
