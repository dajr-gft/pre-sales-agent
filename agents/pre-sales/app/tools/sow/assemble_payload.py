"""Deterministic assembly of the flat ``sow_data`` payload from bundles.

After the root generates each section (inline, following the section
skill) it persists a typed ``*Bundle`` (see ``app.sub_agents.schemas``)
into a dedicated session-state key. The pure
:func:`build_sow_data_from_state` helper reads those bundles plus the
``app:sow:metadata`` envelope from state and returns the flat
``sow_data`` dict that ``stage_sow`` writes and
``generate_sow_document`` renders.

Why a Python helper instead of letting the root LLM merge the JSON: an
LLM merging five structured payloads silently drops fields, renames
keys, or reorders lists. Python doesn't. The mapping is small enough to
audit at a glance — keep it that way.

The ADK-facing :func:`assemble_sow_payload` tool wraps the pure helper
and is **not registered with the root agent**. The canonical write
path is ``stage_sow``, which calls :func:`build_sow_data_from_state`
internally so the assembled dict never has to round-trip through the
model. The wrapper survives as a dry-run / debug entry point — useful
in tests that want to inspect the assembled shape without mutating
state, and for non-agent callers that need the same error semantics.

The ``stage`` parameter mirrors the one accepted by ``stage_sow``:

- ``"content"`` — called after requirements + delivery_plan +
  scope_boundaries have run (Phase 2 content stage). Architecture and
  narrative are still absent; their keys are intentionally omitted from
  the output.
- ``"full"`` — called after architecture + narrative have also run.
  All section keys present.
"""

# NOTE: deliberately NOT using ``from __future__ import annotations``.
# This module is loaded as an ADK tool via @safe_tool, and ADK resolves
# parameter type hints through ``typing.get_type_hints(wrapper_func)``.
# Because ``functools.wraps`` (used inside safe_tool) copies
# ``__annotations__`` but cannot copy ``__globals__``, string-based
# annotations end up resolved against ``app.shared.errors.__globals__``
# — where ``Literal`` is not imported — and raise ``NameError: name
# 'Literal' is not defined`` the first time the agent calls the tool.
# Evaluating annotations eagerly (the pre-PEP-563 default) embeds the
# resolved ``Literal`` object directly in ``__annotations__``, so the
# wrapper's globals never have to be consulted.

from typing import Any, Literal

import structlog
from google.adk.tools import ToolContext

from ...shared.errors import safe_tool
from ...shared.types import ToolError, ToolSuccess
from ...sub_agents.schemas import (
    CONTENT_STAGE_KEYS,
    FULL_STAGE_KEYS,
    SOW_BUNDLE_STATE_KEYS,
    SOW_METADATA_STATE_KEY,
)
from ...sub_agents.validation.field_vocabulary import (
    MANIFEST_DERIVED_FIELDS_TUPLE,
)

logger = structlog.get_logger()


# Sentinel written by section workers when their declared upstream
# inputs are missing from state. See
# ``app.sub_agents._section_agent._MISSING_INPUTS_FOOTER`` — the worker
# emits a schema-valid empty bundle with this string in required scalar
# fields rather than fabricating content. The assembler short-circuits
# on the sentinel so the downstream quality loop does not burn a critic
# round revalidating a SOW the orchestrator already knows is incomplete.
_MISSING_INPUT_SENTINEL = 'MISSING_INPUT'


def _contains_missing_sentinel(value: Any) -> bool:
    """True when ``value`` (or any nested string) equals the sentinel.

    Walks dicts, lists, and tuples — anything else (int, bool, None) is
    skipped because the sentinel is always emitted as a literal string.
    Cheap recursion; bundles are small.
    """
    if isinstance(value, str):
        return value == _MISSING_INPUT_SENTINEL
    if isinstance(value, dict):
        return any(_contains_missing_sentinel(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_missing_sentinel(v) for v in value)
    return False


# Canonical source: ``validation.field_vocabulary.MANIFEST_DERIVED_FIELDS_TUPLE``.
# The aggregator's field-lint pass and ``apply_sow_global_patch``'s
# blocklist read from the same constant — adding a manifest-derived
# field MUST be a one-line change there so the writer / blocker /
# linter cannot drift.
_PROJECT_METADATA_KEYS: tuple[str, ...] = MANIFEST_DERIVED_FIELDS_TUPLE

# F-07: project-level fields the docx template + validation pipeline
# treat as load-bearing. Empty strings here mean the template renders a
# header that says "Partner: " with nothing after, which the validation
# critic does not currently flag as a deterministic error (it focuses
# on the section content). Reject in the assembler so a metadata
# extraction gap does not silently propagate into the generated SOW.
_REQUIRED_PROJECT_METADATA_KEYS: tuple[str, ...] = (
    'partner_name',
    'customer_name',
    'project_title',
    'funding_type',
)


# Bundle keys required per stage. Project metadata is resolved
# separately (see ``_resolve_project_metadata``) from the
# ``app:sow:metadata`` envelope, so the stage tuples carry only
# bundle keys and ARE the bundle-presence requirement directly.
_CONTENT_BUNDLE_KEYS: tuple[str, ...] = CONTENT_STAGE_KEYS
_FULL_BUNDLE_KEYS: tuple[str, ...] = FULL_STAGE_KEYS


def _resolve_project_metadata(
    state: dict[str, Any],
) -> dict[str, Any] | None:
    """Resolve the 13 project-metadata fields from the metadata envelope.

    Reads ``state['app:sow:metadata']`` (written by ``save_sow_metadata``)
    and coerces it onto the canonical key set. Returns ``None`` when the
    envelope is absent or carries no non-empty value, so the caller can
    raise a clear precondition error.
    """
    envelope = state.get(SOW_METADATA_STATE_KEY)
    if isinstance(envelope, dict) and any(
        isinstance(v, str) and v.strip() for v in envelope.values()
    ):
        return {
            key: (envelope.get(key) or '') for key in _PROJECT_METADATA_KEYS
        }
    return None


class AssemblyError(Exception):
    """Raised by :func:`build_sow_data_from_state` when preconditions fail.

    Carries machine-readable lists so callers (the ADK tool wrapper, the
    QualityLoopAgent helper) can render their own user-facing messages
    without parsing strings.
    """

    def __init__(
        self,
        reason: str,
        *,
        missing_keys: list[str] | None = None,
        sentinel_keys: list[str] | None = None,
        missing_metadata: list[str] | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.missing_keys = missing_keys or []
        self.sentinel_keys = sentinel_keys or []
        self.missing_metadata = missing_metadata or []


def build_sow_data_from_state(
    state: dict[str, Any], stage: str,
) -> dict[str, Any]:
    """Pure assembly — reads bundles from ``state``, returns ``sow_data``.

    Does NOT mutate ``state``. Does NOT depend on a ToolContext. The
    ADK-facing ``assemble_sow_payload`` tool wraps this, and the
    QualityLoopAgent's :func:`apply_sow_assembly_to_state` calls it to
    re-assemble after a section-agent repair without going through the
    tool surface (which would require a synthetic ToolContext).

    Raises :class:`AssemblyError` when preconditions are not met —
    missing bundle keys, MISSING_INPUT sentinel from a aborted section,
    non-dict metadata envelope, or blank required project metadata.
    Each error carries the offending detail in a structured attribute
    so callers render their own messages.
    """
    stage_normalized = (stage or 'content').strip().lower()
    if stage_normalized not in ('content', 'full'):
        raise AssemblyError(
            f"Unknown stage '{stage}'. Expected 'content' or 'full'.",
        )

    required = (
        _CONTENT_BUNDLE_KEYS
        if stage_normalized == 'content'
        else _FULL_BUNDLE_KEYS
    )
    missing = [k for k in required if not state.get(k)]
    if missing:
        raise AssemblyError(
            (
                f'Cannot assemble stage={stage_normalized!r}: missing '
                f'{len(missing)} bundle(s) in session state.'
            ),
            missing_keys=missing,
        )

    sentinel_keys = [
        key for key in required
        if _contains_missing_sentinel(state.get(key))
    ]
    if sentinel_keys:
        raise AssemblyError(
            (
                f'Cannot assemble stage={stage_normalized!r}: '
                f'{len(sentinel_keys)} bundle(s) carry the '
                f'{_MISSING_INPUT_SENTINEL!r} sentinel from an aborted '
                'section worker.'
            ),
            sentinel_keys=sentinel_keys,
        )

    project_metadata = _resolve_project_metadata(state)
    if project_metadata is None:
        raise AssemblyError(
            (
                'No project metadata in state: the '
                f'{SOW_METADATA_STATE_KEY!r} envelope is missing or empty. '
                'Call save_sow_metadata before SOW assembly.'
            ),
            missing_metadata=list(_REQUIRED_PROJECT_METADATA_KEYS),
        )

    missing_metadata = [
        key
        for key in _REQUIRED_PROJECT_METADATA_KEYS
        if not (project_metadata.get(key) or '').strip()
    ]
    if missing_metadata:
        raise AssemblyError(
            (
                f'Cannot assemble stage={stage_normalized!r}: required '
                f'project metadata fields are empty: {missing_metadata}. '
                'save_sow_metadata must populate these before SOW assembly '
                'so the document header is not rendered with blanks.'
            ),
            missing_metadata=missing_metadata,
        )

    requirements = state[SOW_BUNDLE_STATE_KEYS['requirements']]
    delivery_plan = state[SOW_BUNDLE_STATE_KEYS['delivery_plan']]
    scope_boundaries = state[SOW_BUNDLE_STATE_KEYS['scope_boundaries']]

    sow_data: dict[str, Any] = {
        **project_metadata,
        'functional_requirements': requirements.get('functional_requirements', []),
        'non_functional_requirements': requirements.get(
            'non_functional_requirements', []
        ),
        'activity_phases': delivery_plan.get('activity_phases', []),
        'deliverables': delivery_plan.get('deliverables', []),
        'timeline': delivery_plan.get('timeline', []),
        'partner_roles': delivery_plan.get('partner_roles', []),
        'customer_roles': delivery_plan.get('customer_roles', []),
        'success_criteria': delivery_plan.get('success_criteria', []),
        'objectives': delivery_plan.get('objectives', []),
        'assumptions': scope_boundaries.get('assumptions', []),
        'out_of_scope': scope_boundaries.get('out_of_scope', []),
        'risks': scope_boundaries.get('risks', []),
        'handover_disclaimers': scope_boundaries.get('handover_disclaimers', []),
        'change_request_policy_text': scope_boundaries.get(
            'change_request_policy_text', ''
        ),
    }

    if stage_normalized == 'full':
        architecture = state[SOW_BUNDLE_STATE_KEYS['architecture']]
        narrative = state[SOW_BUNDLE_STATE_KEYS['narrative']]
        sow_data.update({
            'architecture_description': architecture.get(
                'architecture_description', ''
            ),
            'architecture_components': architecture.get(
                'architecture_components', []
            ),
            'architecture_integrations': architecture.get(
                'architecture_integrations', []
            ),
            'technology_stack': architecture.get('technology_stack', []),
            'executive_summary': narrative.get('executive_summary', ''),
            'partner_overview': narrative.get('partner_overview', ''),
            'customer_overview': narrative.get('customer_overview', ''),
            'customer_primary_domain': narrative.get('customer_primary_domain') or '',
        })

    return sow_data


@safe_tool
async def assemble_sow_payload(
    stage: Literal['content', 'full'] = 'content',
    tool_context: ToolContext = None,
) -> dict[str, Any]:
    """Assemble the flat sow_data dict from per-section bundles in state.

    Call this tool right before ``stage_sow`` so the staged JSON matches
    the schema expected by the validation critic and the document
    generator. The mapping is structural — bundle fields keep their
    names — so adding a new field is a single-line change here once the
    bundle Pydantic schema gains it.

    Args:
        stage: ``"content"`` for the content review checkpoint (requires
            requirements + delivery_plan + scope_boundaries bundles plus
            the metadata envelope); ``"full"`` for the architecture
            review and final assembly (additionally requires
            architecture + narrative).

    Returns:
        ``ToolSuccess`` with ``data={'stage': ..., 'sow_data': {...}}`` on
        success, or ``ToolError`` listing the missing state keys when one
        or more required bundles have not been produced yet.
    """
    if tool_context is None:
        return ToolError(
            status='error',
            error='tool_context is required.',
            retryable=False,
            tool='assemble_sow_payload',
            suggestion=(
                'Call this tool from within an ADK runtime; tool_context '
                'is injected automatically.'
            ),
        )

    stage_normalized = (stage or 'content').strip().lower()
    try:
        sow_data = build_sow_data_from_state(
            tool_context.state, stage_normalized,
        )
    except AssemblyError as err:
        # Map the structured exception attributes onto the ToolError
        # ``suggestion`` so the LLM gets the same actionable guidance the
        # legacy inline checks used to produce. Each branch matches one
        # of the exception's structured attributes; the order is the
        # order :func:`build_sow_data_from_state` raises them in.
        if err.missing_keys:
            suggestion = (
                'Run the section sub-agents that populate these keys before '
                f'calling assemble_sow_payload. Missing: {err.missing_keys}'
            )
            logger.warning(
                'assemble_sow_payload_missing_bundles',
                stage=stage_normalized,
                missing=err.missing_keys,
            )
        elif err.sentinel_keys:
            suggestion = (
                'A section sub-agent emitted an empty bundle because a '
                'required upstream input was missing from state. Re-invoke '
                'the affected section agent(s) in Phase Step order; the '
                'sentinel will clear once the section runs with all its '
                f'inputs present. Affected bundles: {err.sentinel_keys}.'
            )
            logger.warning(
                'assemble_sow_payload_sentinel_detected',
                stage=stage_normalized,
                sentinel_keys=err.sentinel_keys,
            )
        elif err.missing_metadata:
            suggestion = (
                'Call `save_sow_metadata` with non-empty values for: '
                f'{err.missing_metadata}, then assemble again.'
            )
            logger.warning(
                'assemble_sow_payload_missing_metadata',
                stage=stage_normalized,
                missing=err.missing_metadata,
            )
        else:
            suggestion = (
                "Pass stage='content' before the Content Review, 'full' "
                'after architecture and narrative.'
            )
        return ToolError(
            status='error',
            error=err.reason,
            retryable=False,
            tool='assemble_sow_payload',
            suggestion=suggestion,
        )

    logger.info(
        'sow_payload_assembled',
        stage=stage_normalized,
        top_level_keys=sorted(sow_data.keys()),
    )

    return ToolSuccess(
        status='success',
        data={
            'stage': stage_normalized,
            'sow_data': sow_data,
        },
    )
