"""``apply_sow_global_patch`` — restricted writer for the revision agent.

Replaces the reviser's previous ``stage_sow`` (whole-dict rewrite)
contract. The new tool only writes ONE flat-SOW field at a time and
rejects any field that:

1. Is owned by a section bundle (anything in
   :data:`app.sub_agents.quality_loop.agent._FIELD_TO_SECTION`). These
   fields are now patched by the per-section ``apply_<section>_patch``
   tool; touching them from the reviser would re-introduce the
   "two writers" divergence that the quality-loop refactor exists to
   eliminate.
2. Is derived from the extraction manifest (anything in
   :data:`app.tools.sow.assemble_payload._PROJECT_METADATA_KEYS`).
   Changing ``partner_name``, ``customer_name``, dates, etc., requires
   re-discovery, not a mid-loop patch — and the production incident
   where the reviser flipped ``pt-BR → en`` via stage_sow is precisely
   the kind of cross-cutting metadata mutation this block prevents.

That covers every key the assembler currently writes into the flat
``sow_data`` payload. The tool therefore degrades the reviser to
"nearly noop" by construction — exactly the post-Phase 3 goal: every
structural finding routes to a section repair agent, and the reviser
exists only as the textual/global safety net for the rare future
field that does not fit either category.

Side-effect contract:

- The tool mutates ``ctx.state[STATE_SOW][field]`` in place. It does
  NOT touch ``STATE_STAGE``, the revision log, or any other state key.
- It does NOT clear ``STATE_REVISION_LOG`` or reset round counters —
  ``stage_sow`` used to perform those re-stage side effects, but the
  loop never re-stages mid-run (same-stage repair only). Removing the
  tool from the reviser is what makes that invariant enforceable.
- Every rejected call logs ``reviser_blocked_structural_field`` with
  the field name, the responsible section (or ``manifest`` for
  metadata), and a short value preview so the diagnostic survives a
  long production trace.
"""

# NOTE: deliberately NOT using ``from __future__ import annotations``.
# Same constraint as the other safe_tool-decorated tools in this
# package — ADK's tool-schema introspection resolves type hints
# through ``typing.get_type_hints(wrapper_func)``, and string-based
# annotations evaluated against safe_tool's globals raise NameError
# on names like ``ToolContext`` or ``Literal``.

from typing import Any

import structlog
from google.adk.tools import ToolContext

from ...shared.errors import safe_tool
from ...shared.types import ToolError, ToolSuccess

logger = structlog.get_logger()


def _bundle_owned_fields() -> dict[str, str]:
    """Return the live ``field → section`` mapping.

    Imported lazily so the tool module stays loadable in test
    environments that stub out ``quality_loop``. Lazy import also
    sidesteps the import cycle that would form if ``quality_loop``
    later started importing from ``tools.sow.apply_sow_global_patch``.
    """
    from ...sub_agents.quality_loop.agent import _FIELD_TO_SECTION

    return dict(_FIELD_TO_SECTION)


def _manifest_derived_fields() -> tuple[str, ...]:
    """Return the live tuple of manifest-derived flat-SOW keys."""
    from .assemble_payload import _PROJECT_METADATA_KEYS

    return _PROJECT_METADATA_KEYS


def _value_preview(value: Any, max_chars: int = 120) -> str:
    """Short string preview of a rejected value, safe for log lines.

    Strings are truncated; non-strings are repr'd. Empty / None
    becomes the literal ``'<empty>'`` so the log line never contains
    a misleading blank.
    """
    if value is None or value == '' or value == [] or value == {}:
        return '<empty>'
    text = value if isinstance(value, str) else repr(value)
    return text[:max_chars] + ('…' if len(text) > max_chars else '')


@safe_tool
async def apply_sow_global_patch(
    field: str,
    value: Any,
    tool_context: ToolContext = None,
) -> dict[str, Any]:
    """Patch a single non-structural field on the flat staged SOW.

    Args:
        field: Top-level key of ``state[app:sow:current]`` to overwrite.
            Must NOT be a section-bundle-owned field (those are
            patched via ``apply_<section>_patch``) and must NOT be a
            manifest-derived field (those require re-discovery).
        value: New value for the field. Replaces the current contents
            verbatim — there is no merge logic, no list-append shortcut.

    Returns:
        ``ToolSuccess`` with ``data={field, status: 'applied'}`` on
        success. ``ToolError`` listing the responsible section / origin
        on rejection — the reviser's prompt explains how to react
        (do not retry; the finding belongs to a section agent and the
        router should have caught it).
    """
    if tool_context is None:
        return ToolError(
            status='error',
            error='tool_context is required.',
            retryable=False,
            tool='apply_sow_global_patch',
            suggestion=(
                'Call this tool from within an ADK runtime; tool_context '
                'is injected automatically.'
            ),
        )

    if not isinstance(field, str) or not field.strip():
        return ToolError(
            status='error',
            error='field must be a non-empty string.',
            retryable=True,
            tool='apply_sow_global_patch',
            suggestion='Pass the top-level key you want to overwrite.',
        )

    bundle_map = _bundle_owned_fields()
    manifest_keys = set(_manifest_derived_fields())

    if field in bundle_map:
        owning_section = bundle_map[field]
        logger.warning(
            'reviser_blocked_structural_field',
            field=field,
            owner=owning_section,
            origin='bundle',
            value_preview=_value_preview(value),
        )
        return ToolError(
            status='error',
            error=(
                f"Field '{field}' is owned by the '{owning_section}' "
                'section bundle and cannot be patched by the reviser.'
            ),
            retryable=False,
            tool='apply_sow_global_patch',
            suggestion=(
                f'Findings touching `{field}` are routed to '
                f'`{owning_section}_repair_agent` by the quality loop. '
                'If you are seeing this rejection, the finding was '
                'mis-routed — surface a diagnostic message and stop, '
                'do not retry with a different field.'
            ),
        )

    if field in manifest_keys:
        logger.warning(
            'reviser_blocked_structural_field',
            field=field,
            owner='manifest',
            origin='manifest',
            value_preview=_value_preview(value),
        )
        return ToolError(
            status='error',
            error=(
                f"Field '{field}' is derived from the extraction manifest "
                'and cannot be patched by the reviser. Re-discovery owns '
                'these values.'
            ),
            retryable=False,
            tool='apply_sow_global_patch',
            suggestion=(
                'Project-metadata changes require re-running sow-discovery '
                'with corrected inputs. Surface a diagnostic and stop.'
            ),
        )

    # Read flat SOW lazily so the import cycle stays clean.
    from ..sow._sow_helpers import sow_data_hash
    from ...sub_agents.validation.schema import STATE_SOW

    sow = tool_context.state.get(STATE_SOW)
    if not isinstance(sow, dict):
        return ToolError(
            status='error',
            error=(
                f'state[{STATE_SOW!r}] is not a dict — cannot patch '
                'a flat-SOW field before the assembler has run.'
            ),
            retryable=False,
            tool='apply_sow_global_patch',
            suggestion='The orchestrator must stage the SOW before the reviser runs.',
        )

    if field not in sow:
        # Refuse to invent new top-level keys. Every legitimate flat-SOW
        # field is created by the assembler from bundles + manifest;
        # adding one here would silently expand the docx template
        # surface and almost certainly break rendering.
        return ToolError(
            status='error',
            error=(
                f"Field '{field}' does not exist on the staged SOW. "
                'Cannot create new top-level keys via this tool.'
            ),
            retryable=False,
            tool='apply_sow_global_patch',
            suggestion=(
                'Allowed fields are the existing top-level keys of '
                'state[app:sow:current], minus the bundle-owned and '
                'manifest-derived sets which are blocked above.'
            ),
        )

    before_hash = sow_data_hash(sow)
    sow[field] = value
    after_hash = sow_data_hash(sow)

    logger.info(
        'reviser_global_patch_applied',
        field=field,
        before_hash=before_hash,
        after_hash=after_hash,
    )

    return ToolSuccess(
        status='success',
        data={
            'field': field,
            'status': 'applied',
            'before_hash': before_hash,
            'after_hash': after_hash,
        },
    )
