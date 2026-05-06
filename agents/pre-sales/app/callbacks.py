"""Agent callbacks for guardrails and observability.

before_tool_callback: runs before every tool call — validates inputs,
    blocks unsafe operations, logs invocations.
after_tool_callback: runs after every tool call — logs results,
    tracks tool usage in session state for downstream decisions.
"""

from __future__ import annotations

import json
from typing import Any

import structlog
from google.adk.tools import ToolContext

from .tools.sow.confirm_phase import is_architecture_review_approved

logger = structlog.get_logger()

# Maximum JSON input size (chars) to prevent oversized payloads
_MAX_SOW_DATA_CHARS = 500_000

# Tools whose execution requires the user to have approved the
# Architecture Review (Phase 2 Step 4). validate_sow_content is gated
# only when stage='full' (or when stage is omitted — 'full' is the
# default); stage='content' is used in Phase 2 Step 1.5 BEFORE
# architecture exists and must remain unblocked.
_ARCH_REVIEW_GATED_TOOLS = frozenset(
    {'validate_sow_content', 'generate_sow_document'}
)


def _arch_review_gate_blocks(
    tool_name: str,
    args: dict[str, Any],
    tool_context: ToolContext,
) -> dict | None:
    """Return a ToolError dict when the architecture-review gate blocks the call.

    Returns None when the gate passes (call proceeds normally). The dict
    return short-circuits the tool call; ADK uses it as the tool's
    response.
    """
    if tool_name not in _ARCH_REVIEW_GATED_TOOLS:
        return None

    # validate_sow_content with stage='content' is used pre-architecture
    # in Phase 2 Step 1.5 and must NOT be gated.
    if tool_name == 'validate_sow_content':
        stage = (args.get('stage') or 'full').strip().lower()
        if stage != 'full':
            return None

    if is_architecture_review_approved(tool_context.state):
        return None

    logger.warning(
        'arch_review_gate_blocked',
        tool=tool_name,
        stage=args.get('stage') if tool_name == 'validate_sow_content' else None,
    )
    return {
        'status': 'error',
        'error': (
            'Cannot proceed with final document generation: the '
            'Architecture Review phase has not been confirmed. '
            'Required steps:\n'
            '1. Present the Architecture Review to the user in the '
            'conversation language.\n'
            "2. Wait for the user's explicit approval.\n"
            "3. Call `confirm_phase_completion(phase_key="
            "'architecture_review_approved')`.\n"
            '4. Then retry this tool.'
        ),
        'retryable': True,
        'tool': tool_name,
        'suggestion': (
            'Return to Phase 2 Step 4, present the Architecture Review, '
            'wait for approval, and stamp the phase via '
            "confirm_phase_completion before retrying."
        ),
    }


def before_tool_callback(
    tool,
    args: dict[str, Any],
    tool_context: ToolContext,
) -> dict | None:
    """Pre-execution guardrails.

    - Validates sow_data size to prevent OOM on large payloads.
    - Logs every tool invocation for audit trail.
    """
    tool_name = getattr(tool, 'name', str(tool))
    log = logger.bind(tool=tool_name)
    log.info('tool_invoked', args_keys=list(args.keys()))

    # Guard: architecture-review approval is a precondition for
    # final-document tools. Runs FIRST so blocked calls don't pay the
    # cost of the JSON-size and JSON-parse guards below.
    gate_block = _arch_review_gate_blocks(tool_name, args, tool_context)
    if gate_block is not None:
        return gate_block

    # Guard: reject oversized sow_data payloads
    sow_data = args.get('sow_data')
    if isinstance(sow_data, str) and len(sow_data) > _MAX_SOW_DATA_CHARS:
        log.warning(
            'sow_data_too_large',
            size=len(sow_data),
            limit=_MAX_SOW_DATA_CHARS,
        )
        return {
            'status': 'error',
            'error': (
                f'sow_data exceeds maximum size ({len(sow_data):,} chars, '
                f'limit: {_MAX_SOW_DATA_CHARS:,}). Reduce content and retry.'
            ),
        }

    # Guard: validate sow_data is parseable JSON before expensive tools run
    if sow_data and tool_name in (
        'generate_sow_document',
        'validate_sow_content',
    ):
        try:
            json.loads(sow_data)
        except json.JSONDecodeError as e:
            log.warning('sow_data_invalid_json', error=str(e))
            return {
                'status': 'error',
                'error': f'sow_data is not valid JSON: {e}',
            }

    return None


def after_tool_callback(
    tool,
    args: dict[str, Any],
    tool_context: ToolContext,
    tool_response: dict,
) -> dict | None:
    """Post-execution observability and state tracking.

    - Logs tool result status.
    - Tracks which tools have been called in session state so the agent
      (and SKILL.md instructions) can make informed decisions about
      pipeline stage.
    """
    tool_name = getattr(tool, 'name', str(tool))
    status = 'unknown'
    if isinstance(tool_response, dict):
        status = tool_response.get('status', 'unknown')

    logger.info('tool_completed_callback', tool=tool_name, status=status)

    # Track tool call history in session state
    tool_history: list = tool_context.state.get('tool_call_history', [])
    tool_history.append(
        {
            'tool': tool_name,
            'status': status,
        }
    )
    tool_context.state['tool_call_history'] = tool_history

    # Track validation state for pipeline awareness
    if tool_name == 'validate_sow_content' and isinstance(tool_response, dict):
        data = tool_response.get('data', {})
        tool_context.state['last_validation_passed'] = data.get(
            'passed', False
        )
        tool_context.state['last_validation_error_count'] = data.get(
            'error_count', 0
        )

    return None
