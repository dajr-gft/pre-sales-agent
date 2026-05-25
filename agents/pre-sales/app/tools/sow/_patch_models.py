"""PatchOp Pydantic models + per-collection identity specs.

This module is the contract the per-section patch engine
(:mod:`apply_section_patch`) builds on. It has two layers:

1. ``CollectionSpec`` + ``_COLLECTION_SPECS`` — declarative table of
   every list field a bundle exposes, the Pydantic ``item_model`` for
   each item, the field that acts as the stable id, the prefix used to
   auto-generate new ids, and the set of fields that
   :class:`UpdateItemOp` MUST refuse to touch (the item's identity, FK
   targets, or natural keys referenced from other items). Each entry
   in the table is justified by a comment so future contributors do
   not have to re-discover the rationale when extending the schema.

   String-list collections (``success_criteria``, ``assumptions``, …)
   have ``supports_item_ops=False`` because they have no per-item
   identity available — the patch engine still allows
   :class:`UpdateFieldOp` to replace the whole list, but ``update_item``
   / ``add_item`` / ``remove_item`` are statically rejected.

2. ``PatchOp`` discriminated union (added in Phase 1) — the actual
   tool argument shape. Phase 0.5 ships only the table above; the op
   models land alongside the engine.

The table mirrors the audit produced in the implementation plan
(``velvety-zooming-fog.md`` §0.5). Treat it as the single source of
truth: every consumer (the patch engine, lint tests, prompt footers)
must derive its allowlist / blocklist from this dict, never reinvent
them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated, Any, Literal, Union

from pydantic import BaseModel as _PydanticBaseModel, Field as _PydanticField

if TYPE_CHECKING:  # pragma: no cover — type-only imports
    from pydantic import BaseModel


@dataclass(frozen=True)
class CollectionSpec:
    """Per-collection identity and ops contract for the patch engine.

    Attributes:
        item_model: Pydantic model for items in the collection, or
            ``None`` for plain ``list[str]`` collections.
        identity_field: Name of the field that stably identifies an
            item across patches. ``None`` for ``list[str]`` collections.
            Identity field appears in :attr:`blocked_identity_fields`
            by convention.
        id_prefix: Prefix used when auto-generating new ids
            (e.g. ``'WS'`` → ``'WS-01'``). ``None`` for collections
            that do not auto-generate ids.
        supports_item_ops: Whether the collection accepts
            ``update_item`` / ``add_item`` / ``remove_item``. Plain
            string lists set this to ``False`` and accept only
            ``update_field``.
        blocked_identity_fields: Fields that :class:`UpdateItemOp`
            must reject. Always a superset of ``{identity_field}``;
            may include additional FK targets or natural keys.
    """

    item_model: type['BaseModel'] | None
    identity_field: str | None
    id_prefix: str | None
    supports_item_ops: bool
    blocked_identity_fields: frozenset[str] = field(
        default_factory=frozenset,
    )


def _build_collection_specs() -> dict[str, CollectionSpec]:
    """Construct the collection-spec table.

    Wrapped in a function so the Pydantic imports stay local — the
    bundle schema module imports ``ensure_collection_numbers`` from
    ``_sow_helpers``, which already pulls heavy machinery; we keep
    ``_patch_models.py`` import-light at module load time.
    """
    from ...sub_agents.schemas import (
        ActivityPhase,
        ArchitectureComponent,
        ArchitectureIntegration,
        Deliverable,
        FunctionalRequirement,
        NonFunctionalRequirement,
        Risk,
        Role,
        TechnologyStackEntry,
        TimelineRow,
    )

    return {
        # Requirements bundle ----------------------------------------------
        'functional_requirements': CollectionSpec(
            item_model=FunctionalRequirement,
            identity_field='number',
            id_prefix='FR',
            supports_item_ops=True,
            # ``number`` (FR-NN) is the manifest-anchor id quoted by the
            # critic in evidence — renaming it via update_item would
            # silently break cross-references.
            blocked_identity_fields=frozenset({'number'}),
        ),
        'non_functional_requirements': CollectionSpec(
            item_model=NonFunctionalRequirement,
            identity_field='number',
            id_prefix='NFR',
            supports_item_ops=True,
            blocked_identity_fields=frozenset({'number'}),
        ),
        # Delivery plan bundle ---------------------------------------------
        'activity_phases': CollectionSpec(
            item_model=ActivityPhase,
            identity_field='name',
            id_prefix=None,
            supports_item_ops=True,
            # ``name`` is the FK target referenced by
            # ``Deliverable.activity`` and ``TimelineRow.activity`` —
            # renaming it would orphan dependent rows without a cascade.
            blocked_identity_fields=frozenset({'name'}),
        ),
        'deliverables': CollectionSpec(
            item_model=Deliverable,
            identity_field='number',
            id_prefix='WS',
            supports_item_ops=True,
            # WS-NN is the convention the critic already uses in evidence;
            # ``Deliverable.number`` was added in Phase 0.5 specifically
            # so the patch engine has a stable id to address by.
            blocked_identity_fields=frozenset({'number'}),
        ),
        'timeline': CollectionSpec(
            item_model=TimelineRow,
            identity_field='activity',
            id_prefix=None,
            supports_item_ops=True,
            # 1:1 FK to ``ActivityPhase.name``; mutating ``activity``
            # silently desynchronises the timeline from the phases.
            blocked_identity_fields=frozenset({'activity'}),
        ),
        'partner_roles': CollectionSpec(
            item_model=Role,
            identity_field='role',
            id_prefix=None,
            supports_item_ops=True,
            # ``role`` is the natural key; replace = remove + add.
            blocked_identity_fields=frozenset({'role'}),
        ),
        'customer_roles': CollectionSpec(
            item_model=Role,
            identity_field='role',
            id_prefix=None,
            supports_item_ops=True,
            blocked_identity_fields=frozenset({'role'}),
        ),
        'success_criteria': CollectionSpec(
            item_model=None,
            identity_field=None,
            id_prefix=None,
            supports_item_ops=False,
        ),
        'objectives': CollectionSpec(
            item_model=None,
            identity_field=None,
            id_prefix=None,
            supports_item_ops=False,
        ),
        # Scope boundaries bundle ------------------------------------------
        'assumptions': CollectionSpec(
            item_model=None,
            identity_field=None,
            id_prefix=None,
            supports_item_ops=False,
        ),
        'out_of_scope': CollectionSpec(
            item_model=None,
            identity_field=None,
            id_prefix=None,
            supports_item_ops=False,
        ),
        'risks': CollectionSpec(
            item_model=Risk,
            identity_field='number',
            id_prefix='R',
            supports_item_ops=True,
            # R-NN — added in Phase 0.5 to align with the anchor
            # convention the critic already recognises.
            blocked_identity_fields=frozenset({'number'}),
        ),
        'handover_disclaimers': CollectionSpec(
            item_model=None,
            identity_field=None,
            id_prefix=None,
            supports_item_ops=False,
        ),
        # Architecture bundle ----------------------------------------------
        'architecture_components': CollectionSpec(
            item_model=ArchitectureComponent,
            identity_field='name',
            id_prefix=None,
            supports_item_ops=True,
            # ``name`` is the natural key; integrations reference it.
            blocked_identity_fields=frozenset({'name'}),
        ),
        'architecture_integrations': CollectionSpec(
            item_model=ArchitectureIntegration,
            identity_field='name',
            id_prefix=None,
            supports_item_ops=True,
            blocked_identity_fields=frozenset({'name'}),
        ),
        'technology_stack': CollectionSpec(
            item_model=TechnologyStackEntry,
            identity_field='service',
            id_prefix=None,
            supports_item_ops=True,
            blocked_identity_fields=frozenset({'service'}),
        ),
    }


_COLLECTION_SPECS: dict[str, CollectionSpec] = _build_collection_specs()


def get_collection_spec(collection: str) -> CollectionSpec | None:
    """Return the :class:`CollectionSpec` for ``collection`` or ``None``.

    Returning ``None`` for an unknown collection (as opposed to
    raising) lets the patch engine convert it into a
    ``ToolError(error="unknown collection ...", suggestion=...)``
    that the LLM can recover from in the next turn.
    """
    return _COLLECTION_SPECS.get(collection)


# ---------------------------------------------------------------------------
# PatchOp discriminated union — tool argument shape
# ---------------------------------------------------------------------------
#
# The engine accepts ``ops: list[dict]`` at the ADK boundary (Pydantic
# discriminator unions do not round-trip cleanly through Gemini's
# function-call schema; ``anyOf`` is rejected). Inside the tool the
# raw dicts are validated against :data:`PatchOp` so the LLM still
# gets fail-fast feedback before any mutation.
#
# Every op carries ``finding_id`` — the patch engine enforces 1:1
# rastreio between findings dispatched to the section agent and the
# ops it emits. No anonymous patches.


_FORBID_EXTRA = {'extra': 'forbid'}


class _OpBase(_PydanticBaseModel):
    """Base for every patch op — pinning a forbid-extra model_config
    on every subclass without repetition."""

    model_config = _FORBID_EXTRA  # type: ignore[assignment]


class UpdateItemOp(_OpBase):
    """Update one or more fields of an existing item inside a
    list-typed bundle field (a ``collection``).

    The patch engine rejects:
      - ``collection`` not present in the bundle as a list field,
      - ``item_id`` not found in the named collection's identity field,
      - ``fields`` containing any key in the collection's
        ``blocked_identity_fields`` (renaming the identity is the
        ``remove_item`` + ``add_item`` workflow),
      - ``fields`` containing keys outside the item model's schema.
    """

    op: Literal['update_item']
    finding_id: str = _PydanticField(
        description='Id of the finding that motivates this op.',
        min_length=1,
    )
    collection: str = _PydanticField(min_length=1)
    item_id: str = _PydanticField(min_length=1)
    fields: dict[str, Any] = _PydanticField(
        description='Item fields to overwrite. Identity field disallowed.',
    )


class AddItemOp(_OpBase):
    """Append a new item to a list-typed bundle field.

    If ``item`` omits the identity field, the patch engine auto-assigns
    the next sequential id based on the collection's ``id_prefix`` and
    the items already present.
    """

    op: Literal['add_item']
    finding_id: str = _PydanticField(min_length=1)
    collection: str = _PydanticField(min_length=1)
    item: dict[str, Any]
    after_item_id: str | None = None


class RemoveItemOp(_OpBase):
    """Remove an item from a list-typed bundle field.

    ``coverage_transferred_to`` is REQUIRED on the schema: the LLM
    must decide before removal whether any manifest anchor riding on
    this item now belongs to another item, OR explicitly acknowledge
    that no coverage was at stake (``None``). The engine does not
    validate the redirected item still exists — that responsibility
    sits with the LLM and the next critic round.
    """

    op: Literal['remove_item']
    finding_id: str = _PydanticField(min_length=1)
    collection: str = _PydanticField(min_length=1)
    item_id: str = _PydanticField(min_length=1)
    coverage_transferred_to: str | None


class UpdateFieldOp(_OpBase):
    """Overwrite a non-list top-level field of the bundle.

    Used for scalar / text fields (``executive_summary``,
    ``change_request_policy_text``, ``architecture_description``) and
    for string-list collections that disabled item ops
    (``assumptions``, ``out_of_scope``, …) — the LLM passes the full
    replacement list as ``value``.
    """

    op: Literal['update_field']
    finding_id: str = _PydanticField(min_length=1)
    field: str = _PydanticField(min_length=1)
    value: Any


PatchOp = Annotated[
    Union[UpdateItemOp, AddItemOp, RemoveItemOp, UpdateFieldOp],
    _PydanticField(discriminator='op'),
]


class _PatchOpEnvelope(_PydanticBaseModel):
    """Internal helper — Pydantic v2 needs a model to host the
    discriminated union when validating a raw dict at runtime.

    Tool callers pass ``list[dict]``; the engine wraps each dict in
    this envelope so Pydantic dispatches to the right op subclass.
    """

    op_value: PatchOp


def parse_patch_op(raw: dict[str, Any]) -> UpdateItemOp | AddItemOp | RemoveItemOp | UpdateFieldOp:
    """Validate ``raw`` and return the concrete op subclass.

    Raises :class:`pydantic.ValidationError` on schema mismatch.
    Caller (the patch engine) converts that into a
    ``ToolError(error=..., suggestion=...)`` for the LLM.
    """
    envelope = _PatchOpEnvelope.model_validate({'op_value': raw})
    return envelope.op_value  # type: ignore[return-value]


class PatchError(Exception):
    """Raised by :mod:`apply_section_patch` when an op batch cannot
    be applied. Carries structured detail so the tool wrapper can
    render a ToolError without parsing the message.

    Attributes:
        reason: Short human-readable cause.
        op_index: 0-based index of the offending op inside the batch.
            ``None`` if the failure is not op-specific (e.g. batch-wide
            bundle validation).
        op_summary: Short dict describing the offending op (``op``
            kind, ``finding_id``, ``collection``) — enough for the
            log without dumping the full payload.
        validation_errors: List of Pydantic / collection error
            messages.
        suggestion: Hint the LLM can act on without re-reading docs.
    """

    def __init__(
        self,
        reason: str,
        *,
        op_index: int | None = None,
        op_summary: dict[str, Any] | None = None,
        validation_errors: list[str] | None = None,
        suggestion: str | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.op_index = op_index
        self.op_summary = op_summary or {}
        self.validation_errors = validation_errors or []
        self.suggestion = suggestion
