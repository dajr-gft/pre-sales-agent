"""Pydantic schemas for SOW section bundles + administrative metadata.

Each ``*Bundle`` model is the schema for one SOW section. In the
root-skills variant the root generates each section inline (following
the section skill) and persists it via ``save_<section>_bundle``, which
validates against the matching model and writes it to
``session.state['app:sow:<section>']``. The section *repair* agents
read the same bundles when patching.

The ``assemble_sow_payload`` tool reads these bundles plus the
``SowMetadata`` envelope (``state['app:sow:metadata']``) from state and
produces the flat ``sow_data`` dict expected by ``stage_sow`` and
``generate_sow_document``.

Field names mirror the top-level keys of ``sow_data`` exactly — the
assembler does a structural copy, not a translation. Changing a field
name here means changing it in the section skill, the template, and
the assembler in lockstep.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .validation.field_vocabulary import MANIFEST_DERIVED_FIELDS_TUPLE


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


class IntakeSummary(BaseModel):
    """Structured handoff produced by the ``sow-guided-intake`` skill.

    Path A (guided intake) replaces the project documents with this
    summary as the upstream context the section skills read. Fields
    carry one of three semantic states:

    - A real value extracted from the user's answers.
    - The marker ``'(inferred)'`` — downstream skills must fill the
      value with a safe consulting default following the style guide
      and SOW conventions. Never re-ask the user. Surface the inference
      to the user only at the existing review gates.
    - The marker ``'[TO BE DEFINED]'`` — value is genuinely unknown.
      Downstream skills MUST keep the placeholder and roll the gap into
      the SOW's open items / assumptions; they must NOT invent a value.

    For list-typed fields, the marker convention is a single-element
    list whose only element is the marker string (e.g.
    ``out_of_scope=['(inferred)']``). An empty list means the field is
    irrelevant or absent — the marker, when applicable, is the explicit
    signal.

    ``inferred_items`` and ``open_items`` are the explicit roll-ups the
    root and section skills use to distinguish the two marker types
    quickly without scanning every field.

    The four real-value-required fields — ``customer_name``,
    ``project_title``, ``problem_goal``, ``solution_direction`` —
    cannot carry markers. They are the minimum scope a SOW needs and
    the tool rejects markers there.
    """

    model_config = _FORBID

    # --- Required real values (markers forbidden) ---
    customer_name: str
    project_title: str
    problem_goal: str = Field(
        description='One or two lines on the business problem or objective.'
    )
    solution_direction: str = Field(
        description='One or two lines on the proposed solution direction.'
    )

    # --- Identity (marker-tolerant) ---
    partner_name: str = 'GFT Technologies'
    funding_type: str = '[TO BE DEFINED]'

    # --- Scalar marker-tolerant fields ---
    engagement_shape: str = '(inferred)'
    timeline: str = '[TO BE DEFINED]'

    # --- List fields. Single-element list with a marker string is the
    #     explicit marker convention; empty list = no information. ---
    main_scope: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    integrations: list[str] = Field(default_factory=list)
    technology_stack: list[str] = Field(default_factory=list)
    nfr_quality_targets: list[str] = Field(default_factory=list)
    operational_constraints: list[str] = Field(default_factory=list)
    regulatory_constraints: list[str] = Field(default_factory=list)
    partner_team: list[str] = Field(default_factory=list)
    customer_team: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)

    # --- Explicit roll-ups for downstream marker dispatch ---
    inferred_items: list[str] = Field(default_factory=list)
    open_items: list[str] = Field(default_factory=list)


class SowMetadata(BaseModel):
    """Administrative project metadata for the SOW document header.

    These 13 fields are the deterministic envelope the docx template
    needs and that does not fit in any section bundle. In the
    multi-agent variant they are derived from the Extraction Manifest;
    in the root-skills variant the root extracts them from the loaded
    documents and persists them directly via ``save_sow_metadata``.

    The field set is pinned to
    ``validation.field_vocabulary.MANIFEST_DERIVED_FIELDS_TUPLE`` —
    the same constant the assembler iterates over and the global-patch
    blocklist guards — so the writer, the assembler, and the linter
    cannot drift. ``_assert_fields_match_vocabulary`` below fails import
    if they ever diverge.

    All fields default to ``''`` (not ``None``) so a partial extraction
    still produces a schema-valid envelope; the assembler's
    required-field gate (partner_name, customer_name, project_title,
    funding_type) is what rejects blanks before document assembly.
    """

    model_config = _FORBID
    partner_name: str = ''
    customer_name: str = ''
    partner_short_name: str = ''
    customer_short_name: str = ''
    project_title: str = ''
    date: str = ''
    author: str = ''
    funding_type: str = ''
    funding_type_short: str = ''
    project_start_date: str = ''
    project_end_date: str = ''
    engagement_type: str = ''
    organization_term: str = ''


# ---------------------------------------------------------------------------
# State key contract — single source of truth for assembler and tests
# ---------------------------------------------------------------------------


# Standalone key for the administrative metadata envelope. Kept out of
# ``SOW_BUNDLE_STATE_KEYS`` because metadata is not a section bundle —
# it is validated against the required-field gate, not the bundle
# presence checks the stage-key tuples drive.
SOW_METADATA_STATE_KEY = 'app:sow:metadata'

# Path A guided-intake summary. Produced by ``save_sow_intake_summary``
# at the end of the ``sow-guided-intake`` interview. Read by the root
# (for ``save_sow_metadata`` extraction) and by every section skill as
# upstream project context. Like ``SOW_METADATA_STATE_KEY`` it is not a
# section bundle and not part of ``SOW_BUNDLE_STATE_KEYS``.
SOW_INTAKE_SUMMARY_STATE_KEY = 'app:sow:intake_summary'

# Canonical marker tokens. Code that checks a field's marker semantics
# MUST compare against these literals — typoed comparisons would silently
# fall through to "treat as real value" and corrupt downstream
# inference.
INTAKE_MARKER_INFERRED = '(inferred)'
INTAKE_MARKER_TO_BE_DEFINED = '[TO BE DEFINED]'
INTAKE_MARKER_TOKENS: tuple[str, ...] = (
    INTAKE_MARKER_INFERRED,
    INTAKE_MARKER_TO_BE_DEFINED,
)

# Fields the intake tool refuses to accept as markers — a SOW cannot be
# generated without these.
INTAKE_REQUIRED_REAL_FIELDS: tuple[str, ...] = (
    'customer_name',
    'project_title',
    'problem_goal',
    'solution_direction',
)


def _assert_fields_match_vocabulary() -> None:
    """Fail import if ``SowMetadata`` drifts from the canonical field set."""
    model_fields = tuple(SowMetadata.model_fields.keys())
    if model_fields != MANIFEST_DERIVED_FIELDS_TUPLE:
        raise RuntimeError(
            'SowMetadata fields drifted from '
            'MANIFEST_DERIVED_FIELDS_TUPLE. '
            f'model={model_fields} vocabulary={MANIFEST_DERIVED_FIELDS_TUPLE}'
        )


_assert_fields_match_vocabulary()


SOW_BUNDLE_STATE_KEYS: dict[str, str] = {
    'requirements': 'app:sow:requirements',
    'delivery_plan': 'app:sow:delivery_plan',
    'scope_boundaries': 'app:sow:scope_boundaries',
    'architecture': 'app:sow:architecture',
    'narrative': 'app:sow:narrative',
}

AssembleStage = Literal['content', 'full']

# Bundles required for each assembly stage. Project metadata is resolved
# separately from the ``app:sow:metadata`` envelope (see
# ``assemble_payload``). Content-stage assembly runs right after Steps
# A+B+C (requirements / delivery / scope) before architecture or
# narrative exist; full-stage assembly runs after D+E.
CONTENT_STAGE_KEYS: tuple[str, ...] = (
    SOW_BUNDLE_STATE_KEYS['requirements'],
    SOW_BUNDLE_STATE_KEYS['delivery_plan'],
    SOW_BUNDLE_STATE_KEYS['scope_boundaries'],
)
FULL_STAGE_KEYS: tuple[str, ...] = CONTENT_STAGE_KEYS + (
    SOW_BUNDLE_STATE_KEYS['architecture'],
    SOW_BUNDLE_STATE_KEYS['narrative'],
)
