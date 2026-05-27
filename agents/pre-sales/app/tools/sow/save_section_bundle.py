"""Deterministic tools: persist a section bundle produced by the root.

In the root-skills architecture variant the root agent loads a section
skill (e.g. ``sow-requirements``), generates the section content inline
following the SKILL.md, then calls the matching ``save_<section>_bundle``
tool to persist it. No LLM runs inside these tools — each validates the
bundle against the section's existing Pydantic schema (the same one the
section sub-agent uses as ``output_schema``) and writes it to the
canonical ``app:sow:<section>`` state key the assembler reads.

The five tools are produced by a single factory so the validate-then-
persist contract cannot drift between sections. Their public names
(``save_requirements_bundle`` etc.) are what the root prompt references.
"""

# NOTE: deliberately NOT using ``from __future__ import annotations`` —
# see the analogous comment in ``assemble_payload.py``. ADK resolves the
# generated tools' parameter type hints via ``typing.get_type_hints``
# against the ``safe_tool`` wrapper globals; eager (real-object)
# annotations on ``bundle: dict`` / ``tool_context: ToolContext``
# sidestep the string-resolution failure.

from typing import Any, Callable

import structlog
from google.adk.tools import ToolContext
from pydantic import BaseModel, ValidationError

from ...shared.errors import safe_tool
from ...shared.types import ToolError, ToolSuccess
from ...sub_agents.schemas import (
    ArchitectureBundle,
    DeliveryPlanBundle,
    NarrativeBundle,
    RequirementsBundle,
    ScopeBoundariesBundle,
    SOW_BUNDLE_STATE_KEYS,
)

logger = structlog.get_logger()


def _summarize_bundle(persisted: dict[str, Any]) -> dict[str, int]:
    """Per-list item counts, for the persisted-bundle telemetry."""
    return {
        key: len(value)
        for key, value in persisted.items()
        if isinstance(value, list)
    }


def _make_save_bundle_tool(
    *,
    section: str,
    state_key: str,
    schema: type[BaseModel],
) -> Callable:
    """Build one ``save_<section>_bundle`` ADK tool.

    The returned coroutine validates ``bundle`` against ``schema`` and
    writes ``schema(...).model_dump()`` to ``state[state_key]``.
    """
    tool_name = f'save_{section}_bundle'

    async def _save_bundle(
        bundle: dict,
        tool_context: ToolContext = None,
    ) -> dict[str, Any]:
        if tool_context is None:
            return ToolError(
                status='error',
                error='tool_context is required.',
                retryable=False,
                tool=tool_name,
                suggestion=(
                    'Call this tool from within an ADK runtime; '
                    'tool_context is injected automatically.'
                ),
            )

        if not isinstance(bundle, dict):
            return ToolError(
                status='error',
                error=(
                    f"'bundle' must be a JSON object, got "
                    f'{type(bundle).__name__}.'
                ),
                retryable=True,
                tool=tool_name,
                suggestion=(
                    'Pass the section content as a single JSON object '
                    'matching the schema described in the loaded skill.'
                ),
            )

        try:
            model = schema(**bundle)
        except ValidationError as err:
            logger.warning(
                'save_bundle_validation_failed',
                section=section,
                errors=err.errors(),
            )
            return ToolError(
                status='error',
                error=f'Bundle failed schema validation: {err}',
                retryable=True,
                tool=tool_name,
                suggestion=(
                    'Fix the fields flagged above and call the tool again. '
                    'The schema is the one documented in the loaded skill; '
                    'extra fields are rejected.'
                ),
            )

        persisted = model.model_dump()
        tool_context.state[state_key] = persisted

        counts = _summarize_bundle(persisted)
        logger.info(
            'section_bundle_saved',
            section=section,
            state_key=state_key,
            item_counts=counts,
        )

        return ToolSuccess(
            status='success',
            data={
                'section': section,
                'state_key': state_key,
                'item_counts': counts,
            },
        )

    _save_bundle.__name__ = tool_name
    _save_bundle.__qualname__ = tool_name
    _save_bundle.__doc__ = (
        f'Validate and persist the {section} section bundle to '
        f'``state[{state_key!r}]``.\n\n'
        'Call this after generating the section content inline by '
        'following the loaded skill. Pass the entire section as a single '
        "JSON object in ``bundle``; it is validated against the section's "
        'schema (extra fields rejected) and written to state for the '
        'assembler to read. Returns ToolSuccess with per-list item counts, '
        'or ToolError when validation fails.\n\n'
        'Args:\n'
        '    bundle: The section content as a JSON object matching the '
        'schema in the loaded skill.\n'
        '    tool_context: Injected by ADK.'
    )

    return safe_tool(_save_bundle)


save_requirements_bundle = _make_save_bundle_tool(
    section='requirements',
    state_key=SOW_BUNDLE_STATE_KEYS['requirements'],
    schema=RequirementsBundle,
)
save_delivery_plan_bundle = _make_save_bundle_tool(
    section='delivery_plan',
    state_key=SOW_BUNDLE_STATE_KEYS['delivery_plan'],
    schema=DeliveryPlanBundle,
)
save_scope_boundaries_bundle = _make_save_bundle_tool(
    section='scope_boundaries',
    state_key=SOW_BUNDLE_STATE_KEYS['scope_boundaries'],
    schema=ScopeBoundariesBundle,
)
save_architecture_bundle = _make_save_bundle_tool(
    section='architecture',
    state_key=SOW_BUNDLE_STATE_KEYS['architecture'],
    schema=ArchitectureBundle,
)
save_narrative_bundle = _make_save_bundle_tool(
    section='narrative',
    state_key=SOW_BUNDLE_STATE_KEYS['narrative'],
    schema=NarrativeBundle,
)


SAVE_BUNDLE_TOOLS = (
    save_requirements_bundle,
    save_delivery_plan_bundle,
    save_scope_boundaries_bundle,
    save_architecture_bundle,
    save_narrative_bundle,
)
