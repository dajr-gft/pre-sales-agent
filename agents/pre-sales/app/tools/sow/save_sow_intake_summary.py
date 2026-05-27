"""Deterministic tool: persist the guided-intake summary.

In Path A (no project documents) the ``sow-guided-intake`` skill drives
a short interview and calls this tool to commit the structured summary
to session state. The summary then takes the role the project documents
play in Path B — every section skill reads it as upstream context.

The tool enforces the marker contract described in
``app.sub_agents.schemas.IntakeSummary``:

- ``customer_name``, ``project_title``, ``problem_goal``,
  ``solution_direction`` cannot carry the ``(inferred)`` or
  ``[TO BE DEFINED]`` markers — a SOW with any of these unknown is not
  generatable.
- All other fields may carry either marker as their value; the tool
  reflects this back to the caller in ``inferred_fields`` /
  ``open_fields`` so downstream code can dispatch on it without
  rescanning every field.

No LLM runs inside the tool. The body is pure validation + state write.
"""

# NOTE: deliberately NOT using ``from __future__ import annotations`` —
# see the analogous comment in ``save_sow_metadata.py``. ADK resolves
# parameter type hints via ``typing.get_type_hints`` against the
# ``safe_tool`` wrapper globals; eager (real-object) annotations on
# ``intake_summary: dict`` / ``tool_context: ToolContext`` sidestep the
# string-resolution failure.

from typing import Any

import structlog
from google.adk.tools import ToolContext
from pydantic import ValidationError

from ...shared.errors import safe_tool
from ...shared.types import ToolError, ToolSuccess
from ...sub_agents.schemas import (
    INTAKE_MARKER_INFERRED,
    INTAKE_MARKER_TO_BE_DEFINED,
    INTAKE_MARKER_TOKENS,
    INTAKE_REQUIRED_REAL_FIELDS,
    IntakeSummary,
    SOW_INTAKE_SUMMARY_STATE_KEY,
)

logger = structlog.get_logger()


def _field_marker(value: Any) -> str | None:
    """Return the marker carried by ``value``, or ``None`` if not marked.

    Marker convention:
      - For scalar fields, the entire string is one of the marker tokens.
      - For list fields, the list is exactly one element and that
        element is a marker token.
    Any other shape (real value, multiple items, empty list) returns
    ``None``.
    """
    if isinstance(value, str):
        stripped = value.strip()
        return stripped if stripped in INTAKE_MARKER_TOKENS else None
    if isinstance(value, list) and len(value) == 1 and isinstance(value[0], str):
        only = value[0].strip()
        return only if only in INTAKE_MARKER_TOKENS else None
    return None


def _classify_fields(
    persisted: dict[str, Any],
) -> tuple[list[str], list[str], list[str]]:
    """Walk every field of the persisted intake and split into three
    buckets used by the downstream marker dispatch:

      - ``populated``: field carries a real value (string with content,
        or list with at least one non-marker item).
      - ``inferred_fields``: field's value is exactly ``(inferred)``.
      - ``open_fields``: field's value is exactly ``[TO BE DEFINED]``.

    The bookkeeping fields (``inferred_items`` / ``open_items``) are
    never classified — they ARE the roll-up.
    """
    populated: list[str] = []
    inferred_fields: list[str] = []
    open_fields: list[str] = []

    for field, value in persisted.items():
        if field in ('inferred_items', 'open_items'):
            continue
        marker = _field_marker(value)
        if marker == INTAKE_MARKER_INFERRED:
            inferred_fields.append(field)
        elif marker == INTAKE_MARKER_TO_BE_DEFINED:
            open_fields.append(field)
        elif isinstance(value, str) and value.strip():
            populated.append(field)
        elif isinstance(value, list) and value:
            populated.append(field)

    return populated, inferred_fields, open_fields


@safe_tool
async def save_sow_intake_summary(
    intake_summary: dict,
    tool_context: ToolContext = None,
) -> dict[str, Any]:
    """Persist the guided-intake summary to session state.

    Call this ONCE at the end of the ``sow-guided-intake`` interview,
    passing every captured field as a single JSON object matching
    :class:`app.sub_agents.schemas.IntakeSummary`. The tool validates
    the shape, enforces the marker contract (real values required for
    ``customer_name``, ``project_title``, ``problem_goal``,
    ``solution_direction``), and writes the persisted dict to
    ``state['app:sow:intake_summary']``.

    Marker semantics in the value space:
      - ``'(inferred)'`` — downstream skills fill with a safe default.
        Never re-ask the user.
      - ``'[TO BE DEFINED]'`` — downstream skills keep the placeholder
        and roll the gap into the SOW's open items.

    For list fields, mark by passing a single-element list whose only
    element is the marker token, e.g.
    ``out_of_scope=['(inferred)']``.

    Args:
        intake_summary: The structured summary as a JSON object. Schema
            is :class:`IntakeSummary`. Extra fields are rejected.
        tool_context: Injected by ADK.

    Returns:
        ``ToolSuccess`` with the persisted field summary plus
        ``inferred_fields`` and ``open_fields`` so the caller can route
        downstream behavior without re-walking the dict, OR a
        ``ToolError`` when validation fails / required fields are blank
        / required fields carry markers.
    """
    if tool_context is None:
        return ToolError(
            status='error',
            error='tool_context is required.',
            retryable=False,
            tool='save_sow_intake_summary',
            suggestion=(
                'Call this tool from within an ADK runtime; tool_context '
                'is injected automatically.'
            ),
        )

    if not isinstance(intake_summary, dict):
        return ToolError(
            status='error',
            error=(
                f"'intake_summary' must be a JSON object, got "
                f'{type(intake_summary).__name__}.'
            ),
            retryable=True,
            tool='save_sow_intake_summary',
            suggestion=(
                'Pass the guided-intake content as a single JSON object '
                'matching the IntakeSummary schema documented in the '
                'sow-guided-intake skill.'
            ),
        )

    try:
        model = IntakeSummary(**intake_summary)
    except ValidationError as err:
        logger.warning(
            'save_sow_intake_summary_validation_failed', errors=err.errors()
        )
        return ToolError(
            status='error',
            error=f'Intake summary failed schema validation: {err}',
            retryable=True,
            tool='save_sow_intake_summary',
            suggestion=(
                'Match the IntakeSummary schema exactly. Required string '
                'fields are customer_name, project_title, problem_goal, '
                'solution_direction. All list fields default to empty; '
                "mark them with ['(inferred)'] or ['[TO BE DEFINED]'] when "
                'applicable.'
            ),
        )

    persisted = model.model_dump()

    # Reject markers on the four real-value-required fields. The
    # IntakeSummary model treats them as strings, so the user can
    # technically pass a marker there — guard at the tool level.
    marker_violations = [
        field
        for field in INTAKE_REQUIRED_REAL_FIELDS
        if _field_marker(persisted.get(field)) is not None
    ]
    blank_required = [
        field
        for field in INTAKE_REQUIRED_REAL_FIELDS
        if not (persisted.get(field) or '').strip()
    ]
    if marker_violations or blank_required:
        offending = sorted(set(marker_violations + blank_required))
        return ToolError(
            status='error',
            error=(
                'These intake fields must carry a real value (no markers, '
                f'no blanks): {offending}. A SOW cannot be generated '
                'without customer, project title, problem/goal, and '
                'solution direction.'
            ),
            retryable=True,
            tool='save_sow_intake_summary',
            suggestion=(
                'Ask the user once for the missing fields and call '
                'save_sow_intake_summary again with real values. '
                'Do NOT pass "[TO BE DEFINED]" or "(inferred)" for these.'
            ),
        )

    populated, inferred_fields, open_fields = _classify_fields(persisted)

    tool_context.state[SOW_INTAKE_SUMMARY_STATE_KEY] = persisted

    logger.info(
        'sow_intake_summary_saved',
        populated_count=len(populated),
        inferred_count=len(inferred_fields),
        open_count=len(open_fields),
        inferred_fields=inferred_fields,
        open_fields=open_fields,
    )

    return ToolSuccess(
        status='success',
        data={
            'state_key': SOW_INTAKE_SUMMARY_STATE_KEY,
            'populated_fields': populated,
            'inferred_fields': inferred_fields,
            'open_fields': open_fields,
        },
    )
