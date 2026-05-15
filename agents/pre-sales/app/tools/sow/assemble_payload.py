"""Deterministic assembly of the flat ``sow_data`` payload from bundles.

After each section sub-agent runs, it writes a typed ``*Bundle`` (see
``app.sub_agents.schemas``) into a dedicated session-state key. This
tool reads those bundles and the Extraction Manifest from state and
returns the flat ``sow_data`` dict that ``stage_sow`` and
``generate_sow_document`` expect.

Why a Python tool instead of letting the root LLM merge the JSON: an
LLM merging five structured payloads silently drops fields, renames
keys, or reorders lists. Python doesn't. The mapping is small enough to
audit at a glance — keep it that way.

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


_PROJECT_METADATA_KEYS: tuple[str, ...] = (
    'partner_name',
    'customer_name',
    'partner_short_name',
    'customer_short_name',
    'project_title',
    'date',
    'author',
    'funding_type',
    'funding_type_short',
    'project_start_date',
    'project_end_date',
    'engagement_type',
    'organization_term',
)


def _extract_project_metadata(manifest: dict[str, Any]) -> dict[str, Any]:
    """Pull project-level fields from the manifest in a shape-tolerant way.

    The Extraction Manifest may store project metadata either flat at the
    top level (``manifest['project_title']``) or nested under a
    ``project`` sub-dict (``manifest['project']['title']``). We accept
    both and produce the flat shape ``generate_sow_document`` expects.
    Missing keys are emitted as empty strings rather than dropped, so the
    docx template never KeyErrors at render time — the validation critic
    is responsible for catching missing required project fields.
    """
    project_nested = manifest.get('project') if isinstance(manifest, dict) else None
    project_nested = project_nested if isinstance(project_nested, dict) else {}

    nested_aliases: dict[str, tuple[str, ...]] = {
        'project_title': ('title', 'project_title'),
        'customer_name': ('customer_name',),
        'partner_name': ('partner_name',),
        'partner_short_name': ('partner_short_name',),
        'customer_short_name': ('customer_short_name',),
        'date': ('date',),
        'author': ('author',),
        'funding_type': ('funding_type',),
        'funding_type_short': ('funding_type_short',),
        'project_start_date': ('start_date', 'project_start_date'),
        'project_end_date': ('end_date', 'project_end_date'),
        'engagement_type': ('engagement_type',),
        'organization_term': ('organization_term',),
    }

    out: dict[str, Any] = {}
    for key in _PROJECT_METADATA_KEYS:
        if key in manifest:
            out[key] = manifest[key]
            continue
        for alias in nested_aliases.get(key, ()):
            if alias in project_nested:
                out[key] = project_nested[alias]
                break
        else:
            out[key] = ''
    return out


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
            manifest + requirements + delivery_plan + scope_boundaries);
            ``"full"`` for the architecture review and final assembly
            (additionally requires architecture + narrative).

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
    if stage_normalized not in ('content', 'full'):
        return ToolError(
            status='error',
            error=f"Unknown stage '{stage}'. Expected 'content' or 'full'.",
            retryable=False,
            tool='assemble_sow_payload',
            suggestion="Pass stage='content' before the Content Review, 'full' after architecture and narrative.",
        )

    required = CONTENT_STAGE_KEYS if stage_normalized == 'content' else FULL_STAGE_KEYS
    missing = [k for k in required if not tool_context.state.get(k)]
    if missing:
        logger.warning(
            'assemble_sow_payload_missing_bundles',
            stage=stage_normalized,
            missing=missing,
        )
        return ToolError(
            status='error',
            error=(
                f'Cannot assemble stage={stage_normalized!r}: missing '
                f'{len(missing)} bundle(s) in session state.'
            ),
            retryable=False,
            tool='assemble_sow_payload',
            suggestion=(
                'Run the section sub-agents that populate these keys before '
                f'calling assemble_sow_payload. Missing: {missing}'
            ),
        )

    # ----- MISSING_INPUT sentinel detection (stage-aware) ----------------
    # A section worker aborts and emits ``MISSING_INPUT`` in its scalar
    # fields when one of its declared upstream state inputs was empty
    # at run time (see _section_agent._make_worker_instruction_provider).
    # We check ONLY the bundles required for the current stage so a
    # content-stage assembly does not get blocked by an absent
    # architecture / narrative — those keys are not part of
    # ``CONTENT_STAGE_KEYS``.
    sentinel_keys = [
        key for key in required
        if _contains_missing_sentinel(tool_context.state.get(key))
    ]
    if sentinel_keys:
        logger.warning(
            'assemble_sow_payload_sentinel_detected',
            stage=stage_normalized,
            sentinel_keys=sentinel_keys,
        )
        return ToolError(
            status='error',
            error=(
                f'Cannot assemble stage={stage_normalized!r}: '
                f'{len(sentinel_keys)} bundle(s) carry the '
                f'{_MISSING_INPUT_SENTINEL!r} sentinel from an aborted '
                'section worker.'
            ),
            retryable=False,
            tool='assemble_sow_payload',
            suggestion=(
                'A section sub-agent emitted an empty bundle because a '
                'required upstream input was missing from state. Re-invoke '
                'the affected section agent(s) in Phase Step order; the '
                'sentinel will clear once the section runs with all its '
                f'inputs present. Affected bundles: {sentinel_keys}.'
            ),
        )
    # ---------------------------------------------------------------------

    manifest = tool_context.state[SOW_BUNDLE_STATE_KEYS['manifest']]
    if not isinstance(manifest, dict):
        return ToolError(
            status='error',
            error=(
                'Manifest in state is not a dict; cannot extract project '
                f"metadata (got {type(manifest).__name__})."
            ),
            retryable=False,
            tool='assemble_sow_payload',
            suggestion=(
                'Re-run sow-discovery so the manifest is written as a dict.'
            ),
        )

    requirements = tool_context.state[SOW_BUNDLE_STATE_KEYS['requirements']]
    delivery_plan = tool_context.state[SOW_BUNDLE_STATE_KEYS['delivery_plan']]
    scope_boundaries = tool_context.state[SOW_BUNDLE_STATE_KEYS['scope_boundaries']]

    sow_data: dict[str, Any] = {
        **_extract_project_metadata(manifest),
        # Requirements bundle
        'functional_requirements': requirements.get('functional_requirements', []),
        'non_functional_requirements': requirements.get(
            'non_functional_requirements', []
        ),
        # Delivery plan bundle
        'activity_phases': delivery_plan.get('activity_phases', []),
        'deliverables': delivery_plan.get('deliverables', []),
        'timeline': delivery_plan.get('timeline', []),
        'partner_roles': delivery_plan.get('partner_roles', []),
        'customer_roles': delivery_plan.get('customer_roles', []),
        'success_criteria': delivery_plan.get('success_criteria', []),
        'objectives': delivery_plan.get('objectives', []),
        # Scope-boundaries bundle
        'assumptions': scope_boundaries.get('assumptions', []),
        'out_of_scope': scope_boundaries.get('out_of_scope', []),
        'risks': scope_boundaries.get('risks', []),
        'handover_disclaimers': scope_boundaries.get('handover_disclaimers', []),
        'change_request_policy_text': scope_boundaries.get(
            'change_request_policy_text', ''
        ),
    }

    if stage_normalized == 'full':
        architecture = tool_context.state[SOW_BUNDLE_STATE_KEYS['architecture']]
        narrative = tool_context.state[SOW_BUNDLE_STATE_KEYS['narrative']]
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
