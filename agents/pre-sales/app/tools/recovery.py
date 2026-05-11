"""Internal recovery tool — invoked by the empty-response guard only.

When the model emits a terminal empty turn (no text, no function call),
``empty_response_guard`` in ``app.callbacks`` injects a synthetic call
to this tool. Its response is fed back to the model so the next turn
produces visible output for the active phase.

The model must NOT call this tool directly. The root prompt instructs
the model to ignore it; it lives in the tool list solely so the ADK
runtime can dispatch the synthetic call the guard emits.
"""
from __future__ import annotations

import structlog
from google.adk.tools import ToolContext

logger = structlog.get_logger()

_CONTINUATION_INSTRUCTION = (
    'Your previous turn ended without producing any user-visible text and '
    'without calling another tool, so the user saw nothing. Resume the '
    'response the current phase requires, in the conversation language, '
    'following the instructions already in your system prompt and any '
    'active skill. Do not call this tool directly — it exists only for '
    'internal recovery and is invoked automatically when needed.'
)


def _request_continuation(tool_context: ToolContext) -> dict:
    """Signal the model to finish the response the current phase requires.

    Returns a stable ``status='continue'`` payload plus a phase-agnostic
    instruction. The instruction never names a specific skill, phase, or
    section — recovery must work for any place in the agent where this
    failure mode can occur.
    """
    attempts = tool_context.state.get('_empty_response_attempts', 0)
    logger.warning('empty_response_recovery_invoked', attempts=attempts)
    return {
        'status': 'continue',
        'instruction': _CONTINUATION_INSTRUCTION,
    }
