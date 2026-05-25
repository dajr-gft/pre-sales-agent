"""Per-section patch engine — transactional ``apply_<section>_patch``
tool factory.

The engine builds one ADK tool per section bundle
(``apply_requirements_patch``, ``apply_delivery_plan_patch``, …). Each
tool accepts a list of ops (:class:`UpdateItemOp` / :class:`AddItemOp`
/ :class:`RemoveItemOp` / :class:`UpdateFieldOp`), validates them
against the bundle's schema *before* any mutation, applies them
atomically to a deep-copy snapshot, validates the result against the
bundle's Pydantic model, and only then commits the snapshot back to
``ctx.state[bundle_key]``. Any failure rolls back the entire batch.

Design notes:

- Tool arg shape is ``ops: list[dict]`` — Pydantic discriminated
  unions do not survive Gemini's function-call schema (``anyOf`` is
  rejected). The engine parses each raw dict into the right op
  subclass via :func:`parse_patch_op` inside the tool body, so the
  LLM still gets fail-fast feedback.
- The allowlist is derived per-tool at factory time from the bundle
  model + ``_COLLECTION_SPECS``. The bundle's list / non-list fields
  are introspected once; per-call validation is dict lookups.
- ``__name__`` of the returned callable is set to
  ``apply_<section>_patch`` so ADK exposes a distinct function name to
  Gemini for each section — the LLM driving the
  ``delivery_plan_repair_agent`` cannot even *see* the other four
  section's tools.
- :class:`PatchError` is the engine's internal exception; the
  ``@safe_tool`` decorator already produces a generic ToolError, so
  the engine wraps controlled failures in a richer ToolError shape
  inside the try block before re-raising via ``return`` (not raise).

Why these constraints matter: the LLM gets one fast-fail per malformed
op (so iteration converges in 1-2 turns rather than 5), and the
two-writer bug (reviser patching a field a section then overwrites)
is gone by construction — the section agent can only patch ITS bundle
through ITS tool.
"""

# NOTE: deliberately NOT using ``from __future__ import annotations``.
# Same constraint as ``assemble_payload.py`` — see the long comment at
# the top of that file. ADK's tool schema introspection resolves type
# hints through ``typing.get_type_hints(wrapper_func)`` and string
# annotations evaluated against ``safe_tool``'s globals raise
# ``NameError`` on names like ``Literal`` / ``ToolContext``.

import copy
from typing import Any, Callable

import structlog
from google.adk.tools import ToolContext
from pydantic import BaseModel, ValidationError

from ...shared.errors import safe_tool
from ...shared.types import ToolError, ToolSuccess
from ._anchor_utils import diff_anchor_ids, extract_anchor_ids
from ._patch_models import (
    AddItemOp,
    CollectionSpec,
    PatchError,
    RemoveItemOp,
    UpdateFieldOp,
    UpdateItemOp,
    _COLLECTION_SPECS,
    get_collection_spec,
    parse_patch_op,
)
from ._sow_helpers import ensure_collection_numbers, sow_data_hash

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Bundle introspection — derive allowlist once at factory time
# ---------------------------------------------------------------------------


def _derive_bundle_fields(
    bundle_model: type[BaseModel],
) -> tuple[set[str], set[str]]:
    """Return ``(list_fields, non_list_fields)`` of the bundle.

    Items in ``list_fields`` are the only collections ``update_item``
    / ``add_item`` / ``remove_item`` can address (after additionally
    consulting :data:`_COLLECTION_SPECS` for ``supports_item_ops``).
    Items in ``non_list_fields`` are the only fields ``update_field``
    can target.
    """
    list_fields: set[str] = set()
    non_list_fields: set[str] = set()
    for name, info in bundle_model.model_fields.items():
        origin = getattr(info.annotation, '__origin__', None)
        if origin is list:
            list_fields.add(name)
        else:
            non_list_fields.add(name)
    return list_fields, non_list_fields


# ---------------------------------------------------------------------------
# Op application — mutate the snapshot in-place
# ---------------------------------------------------------------------------


def _find_item_index(
    items: list[dict[str, Any]],
    identity_field: str,
    item_id: str,
) -> int | None:
    """Return the index of an item whose ``identity_field`` equals
    ``item_id`` (case-sensitive)."""
    for idx, candidate in enumerate(items):
        if not isinstance(candidate, dict):
            continue
        if candidate.get(identity_field) == item_id:
            return idx
    return None


def _apply_update_item(
    snapshot: dict[str, Any],
    op: UpdateItemOp,
    spec: CollectionSpec,
) -> None:
    items = snapshot.get(op.collection)
    if not isinstance(items, list):
        raise PatchError(
            f"Bundle has no list collection named '{op.collection}'.",
            op_summary=_op_summary(op),
            suggestion=f'Available list collections: {sorted(snapshot.keys())}',
        )
    if spec.identity_field is None:
        # Should be caught earlier by spec.supports_item_ops check,
        # but guard for safety.
        raise PatchError(
            f"Collection '{op.collection}' has no identity field.",
            op_summary=_op_summary(op),
        )
    idx = _find_item_index(items, spec.identity_field, op.item_id)
    if idx is None:
        available = sorted(
            str(i.get(spec.identity_field))
            for i in items
            if isinstance(i, dict) and i.get(spec.identity_field) is not None
        )
        raise PatchError(
            f"item_id '{op.item_id}' not found in '{op.collection}'.",
            op_summary=_op_summary(op),
            suggestion=f'available ids in {op.collection}: {available}',
        )
    items[idx] = {**items[idx], **op.fields}


def _apply_add_item(
    snapshot: dict[str, Any],
    op: AddItemOp,
    spec: CollectionSpec,
) -> None:
    items = snapshot.get(op.collection)
    if not isinstance(items, list):
        raise PatchError(
            f"Bundle has no list collection named '{op.collection}'.",
            op_summary=_op_summary(op),
        )
    new_item = dict(op.item)
    # Auto-assign identity if omitted and the collection supports it.
    if spec.identity_field and spec.id_prefix:
        if not new_item.get(spec.identity_field):
            # Compute next id by reusing the bundle-aware numbering helper:
            # pretend the snapshot is a bundle dict and let
            # ``ensure_collection_numbers`` allocate. We then read back the
            # id and remove the temporary insert so the caller's
            # ``after_item_id`` placement still controls the final order.
            staging = {op.collection: items + [new_item]}
            ensure_collection_numbers(staging, op.collection, spec.id_prefix)
            new_item = staging[op.collection][-1]
    if op.after_item_id is not None and spec.identity_field:
        anchor_idx = _find_item_index(items, spec.identity_field, op.after_item_id)
        if anchor_idx is None:
            raise PatchError(
                f"after_item_id '{op.after_item_id}' not found in "
                f"'{op.collection}'.",
                op_summary=_op_summary(op),
            )
        items.insert(anchor_idx + 1, new_item)
    else:
        items.append(new_item)


def _apply_remove_item(
    snapshot: dict[str, Any],
    op: RemoveItemOp,
    spec: CollectionSpec,
) -> None:
    items = snapshot.get(op.collection)
    if not isinstance(items, list):
        raise PatchError(
            f"Bundle has no list collection named '{op.collection}'.",
            op_summary=_op_summary(op),
        )
    if spec.identity_field is None:
        raise PatchError(
            f"Collection '{op.collection}' has no identity field.",
            op_summary=_op_summary(op),
        )
    idx = _find_item_index(items, spec.identity_field, op.item_id)
    if idx is None:
        available = sorted(
            str(i.get(spec.identity_field))
            for i in items
            if isinstance(i, dict) and i.get(spec.identity_field) is not None
        )
        raise PatchError(
            f"item_id '{op.item_id}' not found in '{op.collection}'.",
            op_summary=_op_summary(op),
            suggestion=f'available ids in {op.collection}: {available}',
        )
    items.pop(idx)


def _apply_update_field(
    snapshot: dict[str, Any],
    op: UpdateFieldOp,
) -> None:
    # Whole-list / scalar replacement. The bundle Pydantic validator
    # is what enforces the value's shape — we just write the slot.
    snapshot[op.field] = op.value


def _op_summary(
    op: UpdateItemOp | AddItemOp | RemoveItemOp | UpdateFieldOp,
) -> dict[str, Any]:
    """Compact summary of an op for log + error context."""
    summary: dict[str, Any] = {
        'op': op.op,
        'finding_id': op.finding_id,
    }
    if isinstance(op, (UpdateItemOp, AddItemOp, RemoveItemOp)):
        summary['collection'] = op.collection
    if isinstance(op, (UpdateItemOp, RemoveItemOp)):
        summary['item_id'] = op.item_id
    if isinstance(op, UpdateFieldOp):
        summary['field'] = op.field
    return summary


# ---------------------------------------------------------------------------
# Pre-application validation — runs against the CURRENT state snapshot
# ---------------------------------------------------------------------------


def _validate_op_against_allowlist(
    op: UpdateItemOp | AddItemOp | RemoveItemOp | UpdateFieldOp,
    *,
    list_fields: set[str],
    non_list_fields: set[str],
    snapshot: dict[str, Any],
) -> None:
    """Run cheap structural checks before the snapshot mutation step.

    Failures here raise :class:`PatchError` so the engine can roll
    back with a precise op index + suggestion. Each check is justified:

    - ``update_item`` / ``add_item`` / ``remove_item`` MUST address a
      list field; non-list field there is a category error.
    - ``update_field`` MUST address a non-list field; pointing it at
      a list would replace the whole collection without identity
      bookkeeping — that's what ``update_field`` does for STRING-LIST
      collections only (``supports_item_ops=False``), but item-typed
      collections must go through ``add_item`` / ``remove_item`` to
      preserve identity invariants.
    - ``update_item.fields`` must:
      a) be a non-empty mapping (a zero-key update is a no-op),
      b) contain only keys that exist in the item model,
      c) not contain any key in the collection's
         ``blocked_identity_fields``.
    """
    if isinstance(op, (UpdateItemOp, AddItemOp, RemoveItemOp)):
        if op.collection not in list_fields:
            raise PatchError(
                f"Collection '{op.collection}' is not a list field on this bundle.",
                op_summary=_op_summary(op),
                suggestion=f'list collections on this bundle: {sorted(list_fields)}',
            )
        spec = get_collection_spec(op.collection)
        if spec is None or not spec.supports_item_ops:
            raise PatchError(
                f"Collection '{op.collection}' does not support item-level ops.",
                op_summary=_op_summary(op),
                suggestion=(
                    'Use update_field with the whole replacement list for '
                    'string-typed collections.'
                ),
            )
        if isinstance(op, UpdateItemOp):
            _validate_update_item_fields(op, spec)
        if isinstance(op, AddItemOp):
            _validate_add_item_payload(op, spec)
    elif isinstance(op, UpdateFieldOp):
        if op.field in list_fields:
            spec = get_collection_spec(op.field)
            if spec is None or spec.supports_item_ops:
                raise PatchError(
                    f"Field '{op.field}' is an item-typed list — use "
                    'update_item / add_item / remove_item.',
                    op_summary=_op_summary(op),
                )
            # Item-less string list: value must be a list.
            if not isinstance(op.value, list):
                raise PatchError(
                    f"update_field on '{op.field}' must pass a list value.",
                    op_summary=_op_summary(op),
                )
        elif op.field not in non_list_fields:
            raise PatchError(
                f"Field '{op.field}' does not exist on this bundle.",
                op_summary=_op_summary(op),
                suggestion=(
                    f'allowed text/scalar fields: {sorted(non_list_fields)}'
                ),
            )


def _validate_update_item_fields(
    op: UpdateItemOp, spec: CollectionSpec,
) -> None:
    if not op.fields:
        raise PatchError(
            'update_item.fields must contain at least one field to change.',
            op_summary=_op_summary(op),
        )
    blocked = set(op.fields).intersection(spec.blocked_identity_fields)
    if blocked:
        raise PatchError(
            (
                f"update_item.fields cannot change identity field(s) "
                f"{sorted(blocked)} of '{op.collection}'. Use remove_item + "
                'add_item to replace the item.'
            ),
            op_summary=_op_summary(op),
            suggestion=(
                f'blocked identity fields for {op.collection}: '
                f'{sorted(spec.blocked_identity_fields)}'
            ),
        )
    if spec.item_model is not None:
        allowed = set(spec.item_model.model_fields.keys())
        unknown = set(op.fields) - allowed
        if unknown:
            raise PatchError(
                (
                    f"update_item.fields contains keys not in the item "
                    f"schema: {sorted(unknown)}."
                ),
                op_summary=_op_summary(op),
                suggestion=f'allowed fields on the item: {sorted(allowed)}',
            )


def _validate_add_item_payload(
    op: AddItemOp, spec: CollectionSpec,
) -> None:
    if not isinstance(op.item, dict) or not op.item:
        raise PatchError(
            'add_item.item must be a non-empty dict.',
            op_summary=_op_summary(op),
        )
    if spec.item_model is not None:
        allowed = set(spec.item_model.model_fields.keys())
        unknown = set(op.item) - allowed
        if unknown:
            raise PatchError(
                (
                    f"add_item.item contains keys not in the item schema: "
                    f"{sorted(unknown)}."
                ),
                op_summary=_op_summary(op),
                suggestion=f'allowed fields on the item: {sorted(allowed)}',
            )


# ---------------------------------------------------------------------------
# Anchor-drop detection on update_item / update_field
# ---------------------------------------------------------------------------

# Set of bundle field names that are list-of-string textual collections
# and should be diffed for anchor-id drops on ``update_field``. Items in
# this set must be present in :data:`_COLLECTION_SPECS` with
# ``supports_item_ops=False``.
_LIST_TEXT_FIELDS_FOR_ANCHOR_DIFF: frozenset[str] = frozenset({
    'success_criteria',
    'objectives',
    'assumptions',
    'out_of_scope',
    'handover_disclaimers',
})


def _detect_anchor_drops(
    op: UpdateItemOp | UpdateFieldOp,
    before_bundle: dict[str, Any],
    after_bundle: dict[str, Any],
) -> list[str]:
    """Return the sorted list of anchor ids that disappeared after the op."""
    if isinstance(op, UpdateItemOp):
        before_ids = extract_anchor_ids(before_bundle.get(op.collection))
        after_ids = extract_anchor_ids(after_bundle.get(op.collection))
    elif (
        isinstance(op, UpdateFieldOp)
        and op.field in _LIST_TEXT_FIELDS_FOR_ANCHOR_DIFF
    ):
        before_ids = extract_anchor_ids(before_bundle.get(op.field))
        after_ids = extract_anchor_ids(after_bundle.get(op.field))
    else:
        return []
    dropped, _ = diff_anchor_ids(before_ids, after_ids)
    return sorted(dropped)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def _build_apply_section_patch(
    *,
    section_name: str,
    bundle_key: str,
    bundle_model: type[BaseModel],
    max_ops_per_call: int = 5,
) -> Callable[..., Any]:
    """Build a section-specific patch tool.

    See module docstring for the why. Each returned callable:
      - is decorated with ``@safe_tool`` (uncaught exceptions become
        ToolError, never crash the agent),
      - has ``__name__`` set to ``apply_<section>_patch`` so ADK
        passes a distinct function name to Gemini,
      - validates ops + applies them transactionally,
      - returns ToolSuccess with structured before/after diff, OR
        ToolError listing the offending op index + suggestion.
    """
    list_fields, non_list_fields = _derive_bundle_fields(bundle_model)

    @safe_tool
    async def apply_section_patch(
        ops: list[dict],
        tool_context: ToolContext = None,
    ) -> dict[str, Any]:
        """Apply a batch of structured patch ops to this section's bundle.

        Args:
            ops: List of op dicts. Each must conform to one of:
                ``{"op": "update_item", "finding_id": ..., "collection": ...,
                "item_id": ..., "fields": {...}}``,
                ``{"op": "add_item", "finding_id": ..., "collection": ...,
                "item": {...}, "after_item_id": null}``,
                ``{"op": "remove_item", "finding_id": ..., "collection": ...,
                "item_id": ..., "coverage_transferred_to": ... | null}``,
                ``{"op": "update_field", "finding_id": ..., "field": ...,
                "value": ...}``.

        Returns:
            ``ToolSuccess`` with structured diff
            (``ops_applied``, ``anchor_drops``, ``before_hash``,
            ``after_hash``, ``status``) on success — ``status`` is
            ``'ok'`` when no anchor drops were detected and
            ``'ok_with_warnings'`` otherwise. ``ToolError`` with the
            offending op index + suggestion on validation failure
            (the bundle in state is untouched).
        """
        if tool_context is None:
            return ToolError(
                status='error',
                error='tool_context is required.',
                retryable=False,
                tool=f'apply_{section_name}_patch',
                suggestion=(
                    'Call this tool from within an ADK runtime; tool_context '
                    'is injected automatically.'
                ),
            )
        if not isinstance(ops, list) or not ops:
            return ToolError(
                status='error',
                error='ops must be a non-empty list.',
                retryable=True,
                tool=f'apply_{section_name}_patch',
                suggestion='Pass at least one op.',
            )
        if len(ops) > max_ops_per_call:
            return ToolError(
                status='error',
                error=(
                    f'Too many ops in a single call: {len(ops)} > '
                    f'{max_ops_per_call}. Split into multiple calls.'
                ),
                retryable=True,
                tool=f'apply_{section_name}_patch',
                suggestion=(
                    f'Cap is {max_ops_per_call} ops per call. Prioritise by '
                    'severity (BLOCKER > MAJOR) then by recommendation '
                    'specificity.'
                ),
            )

        # Step 1 — Pydantic-parse every op. Surface the FIRST malformed
        # op index so the LLM can correct in one shot.
        parsed_ops: list[
            UpdateItemOp | AddItemOp | RemoveItemOp | UpdateFieldOp
        ] = []
        for idx, raw in enumerate(ops):
            if not isinstance(raw, dict):
                return _patch_error_response(
                    section_name,
                    PatchError(
                        f'op at index {idx} is not a dict.',
                        op_index=idx,
                        suggestion='Each op must be a JSON object.',
                    ),
                )
            try:
                parsed_ops.append(parse_patch_op(raw))
            except ValidationError as err:
                return _patch_error_response(
                    section_name,
                    PatchError(
                        f'op at index {idx} failed schema validation.',
                        op_index=idx,
                        op_summary={'raw_op': raw.get('op')},
                        validation_errors=[
                            f'{e.get("loc", ["?"])[-1]}: {e.get("msg", "")}'
                            for e in err.errors()
                        ],
                        suggestion=(
                            'See validation_errors for the exact field that '
                            'failed. Common issues: missing finding_id, wrong '
                            "'op' string, missing collection."
                        ),
                    ),
                )

        # Step 2 — snapshot the bundle. ``ctx.state[bundle_key]`` MAY
        # be ``None`` for first-round repair on a brand-new section; we
        # treat that as an empty bundle and let ``add_item`` populate
        # it (or the bundle validator catch the missing required fields).
        current_bundle = tool_context.state.get(bundle_key)
        if not isinstance(current_bundle, dict):
            return ToolError(
                status='error',
                error=(
                    f"Bundle at state[{bundle_key!r}] is not a dict — "
                    'cannot apply patch.'
                ),
                retryable=False,
                tool=f'apply_{section_name}_patch',
                suggestion=(
                    'Run the first-gen section agent before invoking the '
                    'repair tool; the bundle must already exist in state.'
                ),
            )
        snapshot = copy.deepcopy(current_bundle)

        # Step 3 — validate ops against the bundle structure + apply to
        # snapshot. Any failure raises PatchError and we return without
        # writing to state.
        for idx, op in enumerate(parsed_ops):
            try:
                _validate_op_against_allowlist(
                    op,
                    list_fields=list_fields,
                    non_list_fields=non_list_fields,
                    snapshot=snapshot,
                )
                _apply_op(op, snapshot)
            except PatchError as err:
                err.op_index = idx
                return _patch_error_response(section_name, err)

        # Step 4 — re-validate the assembled snapshot against the
        # bundle model. Catches downstream schema breakage (e.g. an
        # add_item that omitted a required field on the item).
        try:
            validated = bundle_model.model_validate(snapshot)
        except ValidationError as err:
            return _patch_error_response(
                section_name,
                PatchError(
                    'Patched snapshot failed bundle schema validation.',
                    validation_errors=[
                        f'{".".join(str(x) for x in e.get("loc", []))}: '
                        f'{e.get("msg", "")}'
                        for e in err.errors()
                    ],
                    suggestion=(
                        'One of the ops produced an item that is missing a '
                        'required field or has the wrong type. See '
                        'validation_errors.'
                    ),
                ),
            )

        # Step 5 — detect anchor drops (warnings, not errors).
        anchor_drops: list[str] = []
        before_bundle_hash = sow_data_hash(current_bundle)
        after_bundle_dict = validated.model_dump(mode='python')
        after_bundle_hash = sow_data_hash(after_bundle_dict)
        for op in parsed_ops:
            if isinstance(op, (UpdateItemOp, UpdateFieldOp)):
                anchor_drops.extend(
                    _detect_anchor_drops(op, current_bundle, after_bundle_dict)
                )
        anchor_drops = sorted(set(anchor_drops))

        # Step 6 — commit. We write the validated dict (not the raw
        # snapshot) so any Pydantic coercion (e.g. id injection) is
        # reflected in state.
        tool_context.state[bundle_key] = after_bundle_dict

        result: dict[str, Any] = {
            'ops_applied': len(parsed_ops),
            'before_hash': before_bundle_hash,
            'after_hash': after_bundle_hash,
            'anchor_drops': anchor_drops,
            'status': 'ok_with_warnings' if anchor_drops else 'ok',
            'section': section_name,
        }
        if anchor_drops:
            result['recommendation'] = (
                'Confirm that another item still covers these manifest '
                'anchors or the next critic round will flag them as '
                'uncovered. Use update_item to restore the anchor in '
                "the item's description, or document the deliberate "
                'removal.'
            )
            logger.warning(
                'section_patch_anchor_drops',
                section=section_name,
                bundle_key=bundle_key,
                dropped_ids=anchor_drops,
                ops=[_op_summary(op) for op in parsed_ops],
            )

        logger.info(
            'section_patch_applied',
            section=section_name,
            bundle_key=bundle_key,
            ops_applied=len(parsed_ops),
            before_hash=before_bundle_hash,
            after_hash=after_bundle_hash,
            anchor_drops_count=len(anchor_drops),
        )

        return ToolSuccess(status='success', data=result)

    apply_section_patch.__name__ = f'apply_{section_name}_patch'
    apply_section_patch.__qualname__ = apply_section_patch.__name__
    apply_section_patch.__doc__ = (
        apply_section_patch.__doc__ or ''
    ) + f'\n\nBound section: {section_name}; bundle_key: {bundle_key!r}.'
    return apply_section_patch


def _apply_op(
    op: UpdateItemOp | AddItemOp | RemoveItemOp | UpdateFieldOp,
    snapshot: dict[str, Any],
) -> None:
    """Dispatch an op to its applier. Mutates ``snapshot`` in-place."""
    if isinstance(op, UpdateItemOp):
        spec = get_collection_spec(op.collection)
        assert spec is not None  # validated by _validate_op_against_allowlist
        _apply_update_item(snapshot, op, spec)
    elif isinstance(op, AddItemOp):
        spec = get_collection_spec(op.collection)
        assert spec is not None
        _apply_add_item(snapshot, op, spec)
    elif isinstance(op, RemoveItemOp):
        spec = get_collection_spec(op.collection)
        assert spec is not None
        _apply_remove_item(snapshot, op, spec)
    elif isinstance(op, UpdateFieldOp):
        _apply_update_field(snapshot, op)


def _patch_error_response(
    section_name: str, err: PatchError,
) -> ToolError:
    """Wrap a :class:`PatchError` in the ToolError shape the agent sees."""
    logger.warning(
        'section_patch_rejected',
        section=section_name,
        reason=err.reason,
        op_index=err.op_index,
        op_summary=err.op_summary,
        validation_errors=err.validation_errors,
    )
    suggestion = err.suggestion or 'Inspect validation_errors and retry.'
    detail: list[str] = []
    if err.op_index is not None:
        detail.append(f'op_index={err.op_index}')
    if err.op_summary:
        detail.append(f'op={err.op_summary}')
    if err.validation_errors:
        detail.append('validation_errors=' + '; '.join(err.validation_errors))
    full_error = err.reason
    if detail:
        full_error = f'{err.reason} ({" | ".join(detail)})'
    return ToolError(
        status='error',
        error=full_error,
        retryable=True,
        tool=f'apply_{section_name}_patch',
        suggestion=suggestion,
    )


# ---------------------------------------------------------------------------
# Exported instances — one per section
# ---------------------------------------------------------------------------
#
# Imported lazily inside a function so the bundle models stay loadable
# without instantiating the tools (some test environments stub modules
# that import from this file). The Pydantic models in ``schemas`` carry
# all the per-bundle metadata the factory needs.


def _build_all_section_tools() -> dict[str, Callable[..., Any]]:
    """Construct one patch tool per section bundle.

    Returns a mapping ``{section_name: callable}``. Module-level
    instances (``apply_requirements_patch``, …) are derived from this
    mapping so the construction logic lives in one place.
    """
    from ...sub_agents.schemas import (
        ArchitectureBundle,
        DeliveryPlanBundle,
        NarrativeBundle,
        RequirementsBundle,
        ScopeBoundariesBundle,
        SOW_BUNDLE_STATE_KEYS,
    )

    return {
        'requirements': _build_apply_section_patch(
            section_name='requirements',
            bundle_key=SOW_BUNDLE_STATE_KEYS['requirements'],
            bundle_model=RequirementsBundle,
        ),
        'delivery_plan': _build_apply_section_patch(
            section_name='delivery_plan',
            bundle_key=SOW_BUNDLE_STATE_KEYS['delivery_plan'],
            bundle_model=DeliveryPlanBundle,
        ),
        'scope_boundaries': _build_apply_section_patch(
            section_name='scope_boundaries',
            bundle_key=SOW_BUNDLE_STATE_KEYS['scope_boundaries'],
            bundle_model=ScopeBoundariesBundle,
        ),
        'architecture': _build_apply_section_patch(
            section_name='architecture',
            bundle_key=SOW_BUNDLE_STATE_KEYS['architecture'],
            bundle_model=ArchitectureBundle,
        ),
        'narrative': _build_apply_section_patch(
            section_name='narrative',
            bundle_key=SOW_BUNDLE_STATE_KEYS['narrative'],
            bundle_model=NarrativeBundle,
        ),
    }


_SECTION_TOOLS = _build_all_section_tools()

apply_requirements_patch = _SECTION_TOOLS['requirements']
apply_delivery_plan_patch = _SECTION_TOOLS['delivery_plan']
apply_scope_boundaries_patch = _SECTION_TOOLS['scope_boundaries']
apply_architecture_patch = _SECTION_TOOLS['architecture']
apply_narrative_patch = _SECTION_TOOLS['narrative']


# Sanity check: every entry in ``_COLLECTION_SPECS`` must reference a
# field that exists on at least one bundle. Catches a misconfigured
# spec at import time rather than at LLM call time.
def _verify_collection_specs_cover_bundles() -> None:
    from ...sub_agents.schemas import (
        ArchitectureBundle,
        DeliveryPlanBundle,
        NarrativeBundle,
        RequirementsBundle,
        ScopeBoundariesBundle,
    )

    all_list_fields: set[str] = set()
    for bundle in (
        RequirementsBundle,
        DeliveryPlanBundle,
        ScopeBoundariesBundle,
        ArchitectureBundle,
        NarrativeBundle,
    ):
        list_fields, _ = _derive_bundle_fields(bundle)
        all_list_fields |= list_fields

    missing = [
        name for name in _COLLECTION_SPECS
        if name not in all_list_fields
    ]
    if missing:
        raise RuntimeError(
            f'_COLLECTION_SPECS references unknown bundle fields: {missing}',
        )


_verify_collection_specs_cover_bundles()
