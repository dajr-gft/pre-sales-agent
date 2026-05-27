"""Deterministic tool: persist the SOW administrative metadata envelope.

In the root-skills architecture variant the root agent extracts the 13
administrative fields (partner/customer identity, dates, funding type,
engagement type, ...) from the loaded project documents and calls this
tool to persist them under ``state['app:sow:metadata']``. No LLM runs
inside the tool — it only validates the shape with the shared
``SowMetadata`` Pydantic model and writes state.

The assembler reads this key first (falling back to the legacy
Extraction Manifest) when building ``sow_data``, so the field names
here are pinned to the same canonical vocabulary the assembler
iterates over — see ``app.sub_agents.schemas.SowMetadata``.
"""

# NOTE: deliberately NOT using ``from __future__ import annotations`` —
# see the analogous comment in ``assemble_payload.py``. ADK resolves
# this tool's parameter type hints via ``typing.get_type_hints`` against
# the ``safe_tool`` wrapper's globals, where string annotations would
# fail to resolve. Eager (real-object) annotations sidestep that.

from typing import Any

import structlog
from google.adk.tools import ToolContext
from pydantic import ValidationError

from ...shared.errors import safe_tool
from ...shared.types import ToolError, ToolSuccess
from ...sub_agents.schemas import SOW_METADATA_STATE_KEY, SowMetadata

logger = structlog.get_logger()

_REQUIRED_FIELDS = ('partner_name', 'customer_name', 'project_title', 'funding_type')


@safe_tool
async def save_sow_metadata(
    partner_name: str = '',
    customer_name: str = '',
    partner_short_name: str = '',
    customer_short_name: str = '',
    project_title: str = '',
    date: str = '',
    author: str = '',
    funding_type: str = '',
    funding_type_short: str = '',
    project_start_date: str = '',
    project_end_date: str = '',
    engagement_type: str = '',
    organization_term: str = '',
    tool_context: ToolContext = None,
) -> dict[str, Any]:
    """Persist the SOW administrative metadata to session state.

    Call this once, after ``load_artifacts``, having extracted the
    project's administrative facts from the loaded documents. Pass the
    fields you found; leave the rest blank. The four required fields
    (``partner_name``, ``customer_name``, ``project_title``,
    ``funding_type``) must be non-blank — the document header renders
    blanks otherwise and SOW assembly will reject the payload.

    Args:
        partner_name: Full legal/brand name of the delivering partner.
        customer_name: Full legal/brand name of the customer.
        partner_short_name: Short label for the partner (headers/tables).
        customer_short_name: Short label for the customer.
        project_title: The engagement / project title.
        date: SOW issue date, as it should appear on the cover.
        author: Author / owner of the SOW.
        funding_type: Funding mechanism (full label).
        funding_type_short: Short label for the funding mechanism.
        project_start_date: Planned engagement start.
        project_end_date: Planned engagement end.
        engagement_type: Type of engagement (e.g. fixed scope, T&M).
        organization_term: Term used to refer to the delivering org.
        tool_context: Injected by ADK.

    Returns:
        ``ToolSuccess`` with the persisted field summary, or
        ``ToolError`` when validation fails or required fields are blank.
    """
    if tool_context is None:
        return ToolError(
            status='error',
            error='tool_context is required.',
            retryable=False,
            tool='save_sow_metadata',
            suggestion=(
                'Call this tool from within an ADK runtime; tool_context '
                'is injected automatically.'
            ),
        )

    raw = {
        'partner_name': partner_name,
        'customer_name': customer_name,
        'partner_short_name': partner_short_name,
        'customer_short_name': customer_short_name,
        'project_title': project_title,
        'date': date,
        'author': author,
        'funding_type': funding_type,
        'funding_type_short': funding_type_short,
        'project_start_date': project_start_date,
        'project_end_date': project_end_date,
        'engagement_type': engagement_type,
        'organization_term': organization_term,
    }

    try:
        metadata = SowMetadata(**raw)
    except ValidationError as err:
        logger.warning('save_sow_metadata_validation_failed', errors=err.errors())
        return ToolError(
            status='error',
            error=f'Metadata failed schema validation: {err}',
            retryable=True,
            tool='save_sow_metadata',
            suggestion=(
                'Pass each field as a plain string. Do not nest objects '
                'or pass non-string values.'
            ),
        )

    persisted = metadata.model_dump()

    missing_required = [
        key for key in _REQUIRED_FIELDS if not (persisted.get(key) or '').strip()
    ]
    if missing_required:
        return ToolError(
            status='error',
            error=(
                'Required metadata fields are blank: '
                f'{missing_required}. The SOW header cannot render with '
                'these empty.'
            ),
            retryable=True,
            tool='save_sow_metadata',
            suggestion=(
                'Re-read the project documents for the missing fields and '
                'call save_sow_metadata again with all four required '
                'fields populated.'
            ),
        )

    tool_context.state[SOW_METADATA_STATE_KEY] = persisted

    populated = sorted(k for k, v in persisted.items() if (v or '').strip())
    logger.info(
        'sow_metadata_saved',
        populated_fields=populated,
        populated_count=len(populated),
    )

    return ToolSuccess(
        status='success',
        data={
            'state_key': SOW_METADATA_STATE_KEY,
            'populated_fields': populated,
            'populated_count': len(populated),
        },
    )
