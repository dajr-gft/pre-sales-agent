"""ADK tool that records workflow phase confirmations.

The model presents each review (Inference Summary, Content Review,
Architecture Review) as free-form text in the conversation language,
then — after explicit user approval — calls
``confirm_phase_completion`` once per phase to stamp the runtime
state. Phases must be confirmed in workflow order; the
``architecture_review_approved`` stamp is what unlocks the Phase 3
final-document tools via ``before_tool_callback``.
"""

from typing import Any

import structlog
from google.adk.tools import ToolContext

from ...shared.errors import safe_tool
from ...shared.types import ToolError, ToolSuccess

logger = structlog.get_logger()

PHASE_KEYS: tuple[str, ...] = (
    'inference_summary_confirmed',
    'content_review_approved',
    'architecture_review_approved',
)

PHASE_PREREQUISITES: dict[str, tuple[str, ...]] = {
    'inference_summary_confirmed': (),
    'content_review_approved': ('inference_summary_confirmed',),
    'architecture_review_approved': ('content_review_approved',),
}

PHASE_STATE_PREFIX = 'phase.'


def _phase_state_key(phase_key: str) -> str:
    return f'{PHASE_STATE_PREFIX}{phase_key}'


@safe_tool
async def confirm_phase_completion(
    phase_key: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """
    Records that a workflow phase was completed and approved by the
    user. Call this tool ONCE per phase, AFTER the user has explicitly
    approved the review for that phase.

    Valid phase_key values, in workflow order:

    - 'inference_summary_confirmed': after the user confirms the
      Inference Summary in Phase 1.
    - 'content_review_approved': after the user approves the Content
      Review in Phase 2 Step 2.
    - 'architecture_review_approved': after the user approves the
      Architecture Review in Phase 2 Step 4. Setting this key unlocks
      the Phase 3 tools (validate_sow_content stage='full' and
      generate_sow_document).

    Each key requires its predecessor to be confirmed first. Calling
    out of order returns a ToolError instructing the model to confirm
    the missing predecessor.

    Args:
        phase_key: One of the values listed in PHASE_KEYS.
        tool_context: Injected by ADK.

    Returns:
        On success: ``{status, data: {phase_confirmed, all_phases_confirmed}}``.
        On invalid key or missing prerequisite: ``ToolError``.
    """
    if phase_key not in PHASE_KEYS:
        logger.warning(
            'confirm_phase_completion_invalid_key',
            phase_key=phase_key,
        )
        return ToolError(
            status='error',
            error=(
                f"Unknown phase_key '{phase_key}'. Valid values, in "
                f"workflow order, are: {', '.join(PHASE_KEYS)}."
            ),
            retryable=False,
            tool='confirm_phase_completion',
            suggestion=(
                'Pass one of the documented phase_key values matching '
                'the review the user just approved.'
            ),
        )

    state = tool_context.state

    for prereq in PHASE_PREREQUISITES[phase_key]:
        if not state.get(_phase_state_key(prereq), False):
            logger.warning(
                'confirm_phase_completion_missing_prerequisite',
                phase_key=phase_key,
                missing_prerequisite=prereq,
            )
            return ToolError(
                status='error',
                error=(
                    f"Cannot confirm '{phase_key}' because the prior "
                    f"phase '{prereq}' has not been confirmed yet. "
                    'Phases must be confirmed in workflow order.'
                ),
                retryable=True,
                tool='confirm_phase_completion',
                suggestion=(
                    f'Present the review for the prior phase, obtain '
                    f'explicit user approval, and call '
                    f"confirm_phase_completion(phase_key='{prereq}') "
                    'first.'
                ),
            )

    state[_phase_state_key(phase_key)] = True

    all_confirmed = all(
        state.get(_phase_state_key(k), False) for k in PHASE_KEYS
    )

    logger.info(
        'phase_confirmed',
        phase_key=phase_key,
        all_phases_confirmed=all_confirmed,
    )

    return ToolSuccess(
        status='success',
        data={
            'phase_confirmed': phase_key,
            'all_phases_confirmed': all_confirmed,
        },
    )


def is_architecture_review_approved(state: dict[str, Any]) -> bool:
    """Boolean accessor used by the before_tool_callback gate.

    Reads the new phase-based state key. Returns True only when the
    Architecture Review phase has been explicitly confirmed via
    confirm_phase_completion.
    """
    return bool(state.get(_phase_state_key('architecture_review_approved')))
