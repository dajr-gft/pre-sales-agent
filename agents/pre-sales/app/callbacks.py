"""Agent callbacks for guardrails and observability.

before_tool_callback: runs before every tool call — validates inputs,
    blocks unsafe operations, logs invocations.
after_tool_callback: runs after every tool call — logs results,
    tracks tool usage in session state for downstream decisions.
empty_response_guard: after_model_callback that detects terminal empty
    turns (no text and no function call) and re-enters the agent loop
    via the internal _request_continuation tool, capped at
    _MAX_EMPTY_RETRIES consecutive attempts.
"""

from __future__ import annotations

import json
from typing import Any

import structlog
from google.adk.agents.callback_context import CallbackContext
from google.adk.models import LlmResponse
from google.adk.tools import ToolContext
from google.genai import types

from ._genai_patches import THOUGHT_SIGNATURE_BYPASS_BYTES
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

_STATE_EMPTY_RESPONSE_ATTEMPTS = '_empty_response_attempts'
_MAX_EMPTY_RETRIES = 2
_RECOVERY_TOOL_NAME = '_request_continuation'

_RECOVERY_EXHAUSTED_MESSAGE = (
    'Tive dificuldade para gerar a próxima resposta. '
    'Pode me pedir novamente, por favor? / '
    "I had trouble generating the next response. "
    'Could you ask me again, please?'
)


def _is_terminal_empty_response(llm_response: LlmResponse) -> bool:
    """True when the response would render as nothing in the UI.

    A turn is terminal-empty when the model believes it is finished
    (``finish_reason`` is ``STOP`` or absent) AND it produced neither
    non-empty text nor a function call. Function-only turns (``text=None``
    + ``function_call`` present) are the NORMAL tool-calling shape and
    must pass through.

    Responses already carrying ``error_code`` / ``error_message`` are
    handled by the existing error pipeline, not by this guard. Safety
    refusals (``finish_reason=SAFETY``) are owned by ``scope_guardrail``
    and the model's safety settings.
    """
    if getattr(llm_response, 'error_code', None) or getattr(
        llm_response, 'error_message', None
    ):
        return False

    finish_reason = getattr(llm_response, 'finish_reason', None)
    stop_reason = getattr(types.FinishReason, 'STOP', None)
    if finish_reason is not None and stop_reason is not None and finish_reason != stop_reason:
        return False

    content = getattr(llm_response, 'content', None)
    if content is None:
        return True

    parts = getattr(content, 'parts', None) or []
    if not parts:
        return True

    for part in parts:
        text = (getattr(part, 'text', None) or '').strip()
        if text:
            return False
        if getattr(part, 'function_call', None):
            return False

    return True


def _build_recovery_call_response() -> LlmResponse:
    """Synthesize a model turn that triggers the recovery tool.

    Mirrors the synthetic-response pattern in ``guardrails`` but the
    ``Part`` carries a ``function_call`` instead of text. The agent
    loop executes the tool, feeds the result back to the model, and the
    model gets a fresh turn with explicit instructions to resume.

    The ``thought_signature`` carries the Vertex AI documented bypass
    sentinel — see :mod:`app._genai_patches`. Gemini 3.x rejects replayed
    ``functionCall`` parts that lack a signature; client-synthesized calls
    have none, so we opt into the bypass for this single turn. The patch
    in ``_genai_patches`` is what makes the sentinel reach the wire as the
    literal ASCII string the backend expects.
    """
    return LlmResponse(
        content=types.Content(
            role='model',
            parts=[
                types.Part(
                    function_call=types.FunctionCall(
                        name=_RECOVERY_TOOL_NAME,
                        args={},
                    ),
                    thought_signature=THOUGHT_SIGNATURE_BYPASS_BYTES,
                )
            ],
        ),
        custom_metadata={'empty_response_recovery': True},
    )


def _build_exhausted_apology_response() -> LlmResponse:
    """Final, single user-visible message after recovery is exhausted."""
    return LlmResponse(
        content=types.Content(
            role='model',
            parts=[types.Part.from_text(text=_RECOVERY_EXHAUSTED_MESSAGE)],
        ),
        custom_metadata={'empty_response_recovery_exhausted': True},
    )


def empty_response_guard(
    callback_context: CallbackContext,
    llm_response: LlmResponse,
) -> LlmResponse | None:
    """after_model_callback that recovers from terminal empty turns.

    Healthy turns pass through unchanged. Terminal empty turns are
    replaced with a synthetic call to ``_request_continuation`` (up to
    ``_MAX_EMPTY_RETRIES`` consecutive attempts) so the agent loop
    re-prompts the model. Once the cap is reached, the user receives
    a single honest apology and the counter resets.
    """
    state = callback_context.state

    if not _is_terminal_empty_response(llm_response):
        if state.get(_STATE_EMPTY_RESPONSE_ATTEMPTS):
            state[_STATE_EMPTY_RESPONSE_ATTEMPTS] = 0
        return None

    attempts = state.get(_STATE_EMPTY_RESPONSE_ATTEMPTS, 0)
    finish_reason = getattr(llm_response, 'finish_reason', None)

    if attempts < _MAX_EMPTY_RETRIES:
        state[_STATE_EMPTY_RESPONSE_ATTEMPTS] = attempts + 1
        logger.warning(
            'empty_response_recovery_injected',
            attempts=attempts + 1,
            max_retries=_MAX_EMPTY_RETRIES,
            finish_reason=str(finish_reason) if finish_reason else None,
        )
        return _build_recovery_call_response()

    state[_STATE_EMPTY_RESPONSE_ATTEMPTS] = 0
    logger.warning(
        'empty_response_recovery_exhausted',
        attempts=attempts,
        max_retries=_MAX_EMPTY_RETRIES,
        finish_reason=str(finish_reason) if finish_reason else None,
    )
    return _build_exhausted_apology_response()
