"""Side-effect tool: append entries to the revision log in session state.

The revision SKILL.md instructs the agent to record one log entry per
processed finding (id, skill, category, action, fields touched, hash
deltas) so the root can compose a localized Revision Note after the
quality loop converges. Since ADK sub-agents cannot mutate state
directly outside of a tool call, this small wrapper exists to make that
write explicit and auditable.

The log is append-only across rounds: a new invocation extends the
existing list rather than replacing it. This preserves the patch
history the root needs to explain "what changed since the last review".
"""

# NOTE: deliberately NOT using ``from __future__ import annotations``.
# This module exposes an ADK tool wrapped with @safe_tool. ADK introspects
# the wrapper via ``typing.get_type_hints(wrapper)`` to build the function
# declaration sent to Gemini, but ``functools.wraps`` cannot copy
# ``__globals__`` — so string annotations end up resolved against
# ``app.shared.errors.__globals__`` (where ``ToolContext`` is not imported)
# and raise ``NameError: name 'ToolContext' is not defined`` the first
# time the agent invokes the tool. Same root cause as the equivalent
# comment in ``app/tools/sow/assemble_payload.py``.

from typing import Any

import structlog
from google.adk.tools import ToolContext

from ...shared.errors import safe_tool
from ...shared.types import ToolError, ToolSuccess

logger = structlog.get_logger()

REVISION_LOG_STATE_KEY = 'app:sow:revision_log'

_REQUIRED_ENTRY_KEYS: frozenset[str] = frozenset(
    {'finding_id', 'skill', 'category', 'action', 'fields_touched'},
)


@safe_tool
async def record_revision_log_entries(
    entries: list[dict[str, Any]],
    tool_context: ToolContext = None,
) -> dict[str, Any]:
    """Append revision log entries to ``state['app:sow:revision_log']``.

    Each entry should describe one patched finding. Required keys per
    entry: ``finding_id``, ``skill``, ``category``, ``action`` (one of
    ``refinement``, ``addition``, ``removal``), ``fields_touched``
    (list of top-level ``sow_data`` keys mutated). Optional but
    encouraged: ``before_hash``, ``after_hash`` per field touched.

    Args:
        entries: One dict per processed finding. May be empty if a
            round produced no patches (still valid — records the
            intent).

    Returns:
        ``ToolSuccess`` with ``data={'appended', 'total'}`` or
        ``ToolError`` when an entry is malformed.
    """
    if tool_context is None:
        return ToolError(
            status='error',
            error='tool_context is required.',
            retryable=False,
            tool='record_revision_log_entries',
            suggestion='Call from within the ADK runtime.',
        )

    if not isinstance(entries, list):
        return ToolError(
            status='error',
            error=f"'entries' must be a list, got {type(entries).__name__}.",
            retryable=False,
            tool='record_revision_log_entries',
            suggestion='Pass a list of entry dicts, even if empty.',
        )

    for idx, entry in enumerate(entries):
        if not isinstance(entry, dict):
            return ToolError(
                status='error',
                error=(
                    f'Entry at index {idx} must be a dict, got '
                    f'{type(entry).__name__}.'
                ),
                retryable=False,
                tool='record_revision_log_entries',
                suggestion='Each entry must be a JSON object.',
            )
        missing = _REQUIRED_ENTRY_KEYS - entry.keys()
        if missing:
            return ToolError(
                status='error',
                error=(
                    f'Entry at index {idx} is missing required keys: '
                    f'{sorted(missing)}.'
                ),
                retryable=False,
                tool='record_revision_log_entries',
                suggestion=(
                    'Each entry needs at least: finding_id, skill, '
                    'category, action, fields_touched.'
                ),
            )

    current = tool_context.state.get(REVISION_LOG_STATE_KEY) or []
    if not isinstance(current, list):
        # Defensive: someone wrote a non-list into our key. Start fresh
        # and log the discard so the test can catch the regression.
        logger.warning(
            'revision_log_state_was_not_list',
            existing_type=type(current).__name__,
        )
        current = []

    appended = list(current) + list(entries)
    tool_context.state[REVISION_LOG_STATE_KEY] = appended

    logger.info(
        'revision_log_appended',
        appended_count=len(entries),
        total_count=len(appended),
    )
    return ToolSuccess(
        status='success',
        data={
            'appended': len(entries),
            'total': len(appended),
        },
    )
