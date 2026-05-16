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

# F-11: synthetic action tag used when the revision agent reports a
# round that produced no real patches. The Revision Note composer in
# the root prompt MUST skip entries with this action so silent rounds
# do not surface as user-facing changes; downstream telemetry can still
# count them to detect "the revision agent ran empty when it shouldn't
# have" regressions.
NOOP_ACTION = 'noop'

_REQUIRED_ENTRY_KEYS: frozenset[str] = frozenset(
    {'finding_id', 'skill', 'category', 'action', 'fields_touched'},
)


def _noop_log_entry(round_label: str, reason: str) -> dict[str, Any]:
    """Build the canonical noop entry for a zero-patch revision round.

    Keys mirror the required-key schema so consumers iterating the log
    without knowledge of ``action='noop'`` still see a well-formed dict.
    The differentiator is ``action == NOOP_ACTION`` plus
    ``fields_touched == []``; the human-readable rationale lives in
    ``reason``.
    """
    return {
        'finding_id': f'__noop__::{round_label}',
        'skill': '__system__',
        'category': 'noop_round',
        'action': NOOP_ACTION,
        'fields_touched': [],
        'reason': reason,
        'round_label': round_label,
    }


@safe_tool
async def record_revision_log_entries(
    entries: list[dict[str, Any]],
    noop_reason: str = '',
    round_label: str = '',
    tool_context: ToolContext = None,
) -> dict[str, Any]:
    """Append revision log entries to ``state['app:sow:revision_log']``.

    Each entry should describe one patched finding. Required keys per
    entry: ``finding_id``, ``skill``, ``category``, ``action`` (one of
    ``refinement``, ``addition``, ``removal``), ``fields_touched``
    (list of top-level ``sow_data`` keys mutated). Optional but
    encouraged: ``before_hash``, ``after_hash`` per field touched.

    F-11 — zero-patch rounds: when ``entries`` is empty the revision
    agent MUST pass a ``noop_reason`` explaining why no patches were
    applied (e.g. "no findings matched the patch contract",
    "all findings deferred to human review"). The tool then appends a
    synthetic noop entry with ``action='noop'`` so the log still has
    evidence the round ran. Calling with ``entries=[]`` and an empty
    ``noop_reason`` is rejected — silent empty rounds mask bugs where
    the revision agent runs but does nothing.

    Args:
        entries: One dict per processed finding. May be empty when the
            round legitimately produced no patches, but in that case
            ``noop_reason`` is required.
        noop_reason: Human-readable rationale for an empty round. Only
            consulted when ``entries`` is empty.
        round_label: Optional label tying the noop entry to a specific
            round (e.g. ``"round-3"``) so audit reads can group them.
            Defaults to ``"unknown"`` so the synthetic id stays unique
            even when the caller forgets to provide one.

    Returns:
        ``ToolSuccess`` with ``data={'appended', 'total', 'noop'}`` or
        ``ToolError`` when an entry is malformed or an empty round is
        reported without a reason.
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

    # F-11 — empty-list path: synthesize a noop entry from noop_reason.
    if not entries:
        reason = (noop_reason or '').strip()
        if not reason:
            return ToolError(
                status='error',
                error=(
                    'Empty `entries` requires a `noop_reason` so the log '
                    'records why this revision round produced no patches.'
                ),
                retryable=False,
                tool='record_revision_log_entries',
                suggestion=(
                    'Pass a short reason such as "no findings matched the '
                    'patch contract" or "all findings deferred to human '
                    'review". Silent empty rounds mask bugs where the '
                    'agent ran but did nothing.'
                ),
            )
        entries = [
            _noop_log_entry(
                round_label=(round_label or 'unknown').strip() or 'unknown',
                reason=reason,
            ),
        ]
        is_noop_round = True
    else:
        is_noop_round = False
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
        is_noop_round=is_noop_round,
    )
    return ToolSuccess(
        status='success',
        data={
            'appended': len(entries),
            'total': len(appended),
            'noop': is_noop_round,
        },
    )
