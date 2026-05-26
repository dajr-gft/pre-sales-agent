"""Pydantic schemas for section sub-agent outputs.

Each section sub-agent (``requirements_agent``, ``delivery_plan_agent``,
``scope_boundaries_agent``, ``architecture_agent``, ``narrative_agent``)
returns one of the ``*Bundle`` models below via ``output_schema=`` and
writes it to ``session.state[<output_key>]``.

The ``assemble_sow_payload`` tool reads these bundles from state and
produces the flat ``sow_data`` dict expected by ``stage_sow`` and
``generate_sow_document``.

Field names mirror the top-level keys of ``sow_data`` exactly — the
assembler does a structural copy, not a translation. Changing a field
name here means changing it in the section skill, the template, and
the assembler in lockstep.

Discovery's ``ExtractionManifest`` schema lives elsewhere (sow-discovery
owns it). The assembler only treats the manifest as the source of
project-level metadata; it is consumed as ``dict[str, Any]`` until the
discovery sub-agent migration formalizes that contract.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


_FORBID = ConfigDict(extra='forbid')


# ---------------------------------------------------------------------------
# Atomic item shapes (mirror the existing sow_data schema)
# ---------------------------------------------------------------------------


class FunctionalRequirement(BaseModel):
    model_config = _FORBID
    number: str = Field(description='Stable id, e.g. "FR-01".')
    description: str


class NonFunctionalRequirement(BaseModel):
    model_config = _FORBID
    number: str = Field(description='Stable id, e.g. "NFR-01".')
    description: str


class ActivityPhase(BaseModel):
    model_config = _FORBID
    name: str
    description: str
    tasks: list[str] = Field(default_factory=list)


class Deliverable(BaseModel):
    model_config = _FORBID
    number: str = Field(description='Stable id, e.g. "WS-01".')
    activity: str
    name: str
    description: str
    format: str


class TimelineRow(BaseModel):
    model_config = _FORBID
    activity: str
    timeframe: str
    outcomes: str


class Role(BaseModel):
    model_config = _FORBID
    role: str
    responsibilities: str


class Risk(BaseModel):
    model_config = _FORBID
    number: str = Field(description='Stable id, e.g. "R-01".')
    description: str
    mitigation: str


class ArchitectureComponent(BaseModel):
    model_config = _FORBID
    name: str
    role: str


class ArchitectureIntegration(BaseModel):
    model_config = _FORBID
    name: str
    description: str


class TechnologyStackEntry(BaseModel):
    model_config = _FORBID
    service: str
    purpose: str


# ---------------------------------------------------------------------------
# Section bundles — one per sub-agent
# ---------------------------------------------------------------------------


class RequirementsBundle(BaseModel):
    """Output of ``requirements_agent`` — Functional + Non-Functional."""

    model_config = _FORBID
    functional_requirements: list[FunctionalRequirement]
    non_functional_requirements: list[NonFunctionalRequirement]


class DeliveryPlanBundle(BaseModel):
    """Output of ``delivery_plan_agent`` — work breakdown + timeline + roles.

    The bundle-level ``model_validator(mode='before')`` injects the
    ``number`` field for any deliverable that lacks one (e.g. a legacy
    bundle written to state before ``Deliverable.number`` became a
    required field, or a first-gen draft from an LLM that forgot to
    emit the id). Numbering is bundle-aware — see
    :func:`app.tools.sow._sow_helpers.ensure_collection_numbers`. The
    validator is the *primary* numbering path; the migration helper in
    ``apply_sow_assembly_to_state`` covers raw-dict reads that never
    pass through Pydantic.
    """

    model_config = _FORBID
    activity_phases: list[ActivityPhase]
    deliverables: list[Deliverable]
    timeline: list[TimelineRow]
    partner_roles: list[Role]
    customer_roles: list[Role]
    success_criteria: list[str]
    objectives: list[str] = Field(default_factory=list)

    @model_validator(mode='before')
    @classmethod
    def _inject_deliverable_numbers(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # Local import: schemas.py is imported very early in the
            # tool/agent graph, so we keep ``_sow_helpers`` lazy to
            # avoid pulling docx-template machinery at module load.
            from ..tools.sow._sow_helpers import ensure_collection_numbers

            ensure_collection_numbers(data, 'deliverables', 'WS')
        return data


class ScopeBoundariesBundle(BaseModel):
    """Output of ``scope_boundaries_agent`` — assumptions, OOS, CR, handover, risks.

    The bundle-level ``model_validator(mode='before')`` injects the
    ``number`` field for any risk that lacks one. See
    :class:`DeliveryPlanBundle` for the rationale and
    :func:`app.tools.sow._sow_helpers.ensure_collection_numbers` for
    the numbering implementation.
    """

    model_config = _FORBID
    assumptions: list[str]
    out_of_scope: list[str]
    risks: list[Risk] = Field(default_factory=list)
    handover_disclaimers: list[str] = Field(default_factory=list)
    change_request_policy_text: str = ''

    @model_validator(mode='before')
    @classmethod
    def _inject_risk_numbers(cls, data: Any) -> Any:
        if isinstance(data, dict):
            from ..tools.sow._sow_helpers import ensure_collection_numbers

            ensure_collection_numbers(data, 'risks', 'R')
        return data


class ArchitectureBundle(BaseModel):
    """Output of ``architecture_agent`` — description + components + stack.

    The diagram PNG itself is produced by the ``generate_architecture_diagram``
    tool and lives as a session artifact; this bundle only carries the
    structured fields that go into ``sow_data``.
    """

    model_config = _FORBID
    architecture_description: str
    architecture_components: list[ArchitectureComponent]
    architecture_integrations: list[ArchitectureIntegration]
    technology_stack: list[TechnologyStackEntry]


class NarrativeBundle(BaseModel):
    """Output of ``narrative_agent`` — executive summary + overviews."""

    model_config = _FORBID
    executive_summary: str
    partner_overview: str
    customer_overview: str
    customer_primary_domain: str | None = None


# ---------------------------------------------------------------------------
# State key contract — single source of truth for assembler and tests
# ---------------------------------------------------------------------------


SOW_BUNDLE_STATE_KEYS: dict[str, str] = {
    # `manifest` deliberately breaks the `app:sow:*` namespace because the
    # manifest tools predate the section sub-agents and persist to
    # ``state['extraction_manifest']`` (see ``manifest_tools.py``). Aligning
    # here keeps a single source of truth for the manifest key — changing
    # the manifest tools would touch ``validation/manifest_prefilter.py``
    # and a wider blast radius for no functional gain.
    'manifest': 'extraction_manifest',
    'requirements': 'app:sow:requirements',
    'delivery_plan': 'app:sow:delivery_plan',
    'scope_boundaries': 'app:sow:scope_boundaries',
    'architecture': 'app:sow:architecture',
    'narrative': 'app:sow:narrative',
}

AssembleStage = Literal['content', 'full']

# Bundles required for each assembly stage. Content-stage assembly runs
# right after Steps A+B+C (requirements / delivery / scope) before
# architecture or narrative exist; full-stage assembly runs after D+E.
CONTENT_STAGE_KEYS: tuple[str, ...] = (
    SOW_BUNDLE_STATE_KEYS['manifest'],
    SOW_BUNDLE_STATE_KEYS['requirements'],
    SOW_BUNDLE_STATE_KEYS['delivery_plan'],
    SOW_BUNDLE_STATE_KEYS['scope_boundaries'],
)
FULL_STAGE_KEYS: tuple[str, ...] = CONTENT_STAGE_KEYS + (
    SOW_BUNDLE_STATE_KEYS['architecture'],
    SOW_BUNDLE_STATE_KEYS['narrative'],
)
