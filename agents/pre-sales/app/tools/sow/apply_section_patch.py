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
from ...shared.logging_config import is_verbose_sow_logging
from ._anchor_utils import ANCHOR_ID_PATTERN, diff_anchor_ids, extract_anchor_ids
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


# Section-ownership of anchor-id prefixes. The owning section is the one
# whose bundle carries the *item* that holds the id — not every place
# the id might be mentioned in prose. This distinction matters for the
# net-drop enforcement (see ``_classify_anchor_owner``):
#
# - Owned drop  → REJECT the patch. The worker is taking out the item
#   that owns the manifest coverage, and that demands an explicit
#   ``remove_item`` with ``coverage_transferred_to``.
# - Cross-section drop → WARN. The worker rewrote some prose and lost
#   a textual cross-reference to an item that lives in ANOTHER bundle.
#   The item itself is intact in its owning section, so this is at
#   most a coverage-recall concern surfaced to the critic in the next
#   round, not a structural break of the SOW.
#
# Architecture / narrative own no anchored items today (they hold free-
# form prose). They are still listed for documentation / future use.
_SECTION_OWNED_ANCHOR_PREFIXES: dict[str, frozenset[str]] = {
    'requirements': frozenset({'FR', 'NFR'}),
    'delivery_plan': frozenset({'WS'}),
    'scope_boundaries': frozenset({'R'}),
    'architecture': frozenset(),
    'narrative': frozenset(),
}


def _anchor_prefix(anchor_id: str) -> str:
    """Extract the canonical alphabetic prefix from an anchor id.

    Handles both the dashed form (``FR-01`` → ``FR``) and the dotted
    deliverable form (``WS01.1`` → ``WS``). Returns the empty string
    if the id has no leading alpha run.
    """
    leading: list[str] = []
    for ch in anchor_id:
        if ch.isalpha():
            leading.append(ch.upper())
            continue
        break
    return ''.join(leading)


def _classify_anchor_owner(
    anchor_id: str, section_name: str,
) -> tuple[bool, str | None]:
    """Return ``(is_owned_here, owning_section_name)``.

    The first element is True when ``section_name`` is the canonical
    owner of the anchor (i.e. an item with this id lives in this
    bundle). The second element names the owning section if known,
    regardless of whether it matches ``section_name`` — useful for the
    cross-section warning ("FR-15 is owned by the requirements
    section, not by this one"). ``None`` means no owning section is
    declared for this prefix (the prefix may be reserved but no schema
    consumes it yet).
    """
    prefix = _anchor_prefix(anchor_id)
    if not prefix:
        return False, None
    owning_section: str | None = None
    for section, owned in _SECTION_OWNED_ANCHOR_PREFIXES.items():
        if prefix in owned:
            owning_section = section
            break
    is_owned_here = (
        owning_section is not None
        and owning_section == section_name
    )
    return is_owned_here, owning_section


def _detect_anchor_drops(
    op: UpdateItemOp | UpdateFieldOp,
    before_bundle: dict[str, Any],
    after_bundle: dict[str, Any],
) -> list[str]:
    """Return the sorted list of anchor ids that disappeared after the op.

    Narrow scope: only ``UpdateItemOp`` and ``UpdateFieldOp`` on list-text
    fields. ``RemoveItemOp`` is intentionally ignored here because
    removing an item is the op's whole purpose — the broader
    :func:`_classify_op_anchor_impact` is the place that surfaces
    ``RemoveItemOp`` drops as a separate observability signal so an
    intentional remove still shows up when the dropped id matches the
    manifest anchor pattern (FR/NFR/WS/R/etc.).
    """
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


def _find_removed_item_payload(
    bundle: dict[str, Any], collection: str, item_id: str | None,
) -> Any:
    """Return the item dict from ``bundle[collection]`` whose identity
    field matches ``item_id``, or ``None`` if not found.

    Tries every plausible identity key (``number``, ``role``, ``name``,
    ``id``) so the helper works across schemas that use different
    discriminators. Returning ``None`` is safe — the caller treats a
    missing payload as "no collateral anchors to authorise" and falls
    back to the item_id-only path.
    """
    if not item_id:
        return None
    items = bundle.get(collection)
    if not isinstance(items, list):
        return None
    target = item_id.strip()
    if not target:
        return None
    for candidate in items:
        if not isinstance(candidate, dict):
            continue
        for identity_field in ('number', 'role', 'name', 'id'):
            value = candidate.get(identity_field)
            if isinstance(value, str) and value.strip() == target:
                return candidate
    return None


def _classify_op_anchor_impact(
    op: UpdateItemOp | AddItemOp | RemoveItemOp | UpdateFieldOp,
    before_bundle: dict[str, Any],
    after_bundle: dict[str, Any],
) -> dict[str, Any]:
    """Rich per-op anchor diagnosis for the ``section_patch_ops_executed`` log.

    Always returns a dict so the log shape is stable regardless of op
    type. Fields:

    - ``op_kind`` — ``update_item`` / ``add_item`` / ``remove_item`` /
      ``update_field``.
    - ``finding_id`` — finding the worker claimed motivates this op.
    - ``collection`` / ``item_id`` / ``field`` — op target (None when
      not applicable).
    - ``dropped_anchors`` — anchor ids (FR-NN, NFR-NN, WS-NN, R-NN, …)
      that disappeared from the touched container as a side effect. For
      ``RemoveItemOp`` whose ``item_id`` itself matches the anchor
      pattern, the item_id is reported here; for the other op types we
      reuse the narrow detector.
    - ``removes_manifest_anchored_item`` — True iff this is a
      ``RemoveItemOp`` whose ``item_id`` matches the anchor pattern.
      That single boolean is the structural signal we need to decide
      whether to enforce later: a remove on FR-15 deserves a different
      treatment than a remove on an unanchored OOS bullet.
    """
    info: dict[str, Any] = {
        'op_kind': op.op,
        'finding_id': op.finding_id,
        'collection': None,
        'item_id': None,
        'field': None,
        'payload': None,
        'dropped_anchors': [],
        'removes_manifest_anchored_item': False,
    }
    if isinstance(op, (UpdateItemOp, AddItemOp, RemoveItemOp)):
        info['collection'] = op.collection
    if isinstance(op, (UpdateItemOp, RemoveItemOp)):
        info['item_id'] = op.item_id
    if isinstance(op, UpdateFieldOp):
        info['field'] = op.field

    # ``payload`` carries the actual content the worker is writing —
    # the field shape matches the op kind. This is the smoking-gun
    # diagnostic when a finding persists across rounds: we can compare
    # the worker's value against the manifest item it was supposed to
    # anchor and see whether the patch was substantive, off-target, or
    # too generic to count as an anchor.
    #
    # Gated by ``SOW_VERBOSE_LOGGING`` because the payload can be large
    # (an ``add_item`` for a deliverable carries the full item dict).
    # In default mode ``payload`` stays ``None`` so the log shape is
    # stable while keeping the structural columns (op_kind, finding_id,
    # collection, item_id, dropped_anchors, removes_manifest_anchored_item)
    # available for everyday triage.
    if is_verbose_sow_logging():
        if isinstance(op, UpdateItemOp):
            info['payload'] = {'fields': op.fields}
        elif isinstance(op, AddItemOp):
            info['payload'] = {
                'item': op.item,
                'after_item_id': op.after_item_id,
            }
        elif isinstance(op, RemoveItemOp):
            info['payload'] = {
                'coverage_transferred_to': op.coverage_transferred_to,
            }
        elif isinstance(op, UpdateFieldOp):
            info['payload'] = {'value': op.value}

    if isinstance(op, RemoveItemOp):
        # An intentional removal — but if the item carried a manifest-
        # anchor id, the next critic round will flag the manifest item
        # as uncovered. Surface it explicitly so the loop can correlate.
        if op.item_id and ANCHOR_ID_PATTERN.fullmatch(op.item_id.strip()):
            info['removes_manifest_anchored_item'] = True
            info['dropped_anchors'] = [op.item_id.upper()]
    elif isinstance(op, (UpdateItemOp, UpdateFieldOp)):
        info['dropped_anchors'] = _detect_anchor_drops(
            op, before_bundle, after_bundle,
        )
    return info


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def _build_apply_section_patch(
    *,
    section_name: str,
    bundle_key: str,
    bundle_model: type[BaseModel],
    max_ops_per_call: int = 15,
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

        # Rich per-op classification — runs over ALL ops (including
        # RemoveItemOp / AddItemOp) so the executed-ops log carries a
        # uniform anchor-impact column. Distinct from the narrow
        # ``anchor_drops`` set above, which preserves the pre-existing
        # "side-effect-only" semantics for the ``section_patch_applied``
        # event (and downstream consumers / tests that key off it).
        op_impacts = [
            _classify_op_anchor_impact(op, current_bundle, after_bundle_dict)
            for op in parsed_ops
        ]
        manifest_anchored_removes = sorted({
            anchor
            for impact in op_impacts
            if impact['removes_manifest_anchored_item']
            for anchor in impact['dropped_anchors']
        })

        # Step 5b — ownership-scoped anchor enforcement. Compare the
        # bundle's anchor-id set before/after applying every op, then
        # PARTITION the missing anchors by who owns them:
        #
        # - Owned drop (this section IS the canonical owner of the
        #   anchor prefix — e.g. ``requirements`` owns ``FR-NN`` /
        #   ``NFR-NN``): refuse the patch unless the drop is
        #   authorised by an explicit ``remove_item`` with
        #   ``coverage_transferred_to``. Taking out an item that owns
        #   manifest coverage is the operation that must be declared.
        # - Cross-section drop (anchor prefix is owned by ANOTHER
        #   section — e.g. ``requirements`` dropping ``WS03.7`` from
        #   FR-15's description): emit a structured warning. The item
        #   itself remains intact in its owning bundle, so this is at
        #   most a cross-reference change the critic may flag next
        #   round; it is NOT a structural break and refusing it would
        #   block legitimate prose edits (the false-positive failure
        #   mode observed in run 55-59 of the production trace).
        #
        # ``coverage_transferred_to`` semantics for ``remove_item``:
        # when the removed item id matches the anchor pattern AND the
        # section owns it, ``coverage_transferred_to`` MUST be non-empty.
        # ``None`` used to be the "no coverage at stake" escape hatch;
        # that claim is incoherent for an item whose anchor IS the
        # manifest coverage.
        before_anchor_set = extract_anchor_ids(current_bundle)
        after_anchor_set = extract_anchor_ids(after_bundle_dict)
        net_dropped_anchors = before_anchor_set - after_anchor_set

        authorized_drop_ids: set[str] = set()
        anchored_remove_violations: list[dict[str, Any]] = []
        for op in parsed_ops:
            if not isinstance(op, RemoveItemOp):
                continue
            normalised_item_id = (op.item_id or '').strip()
            if not normalised_item_id:
                continue
            if not ANCHOR_ID_PATTERN.fullmatch(normalised_item_id):
                # Unanchored items keep the legacy contract — None
                # ``coverage_transferred_to`` is still allowed because
                # there is no manifest anchor at stake.
                continue
            anchor_upper = normalised_item_id.upper()
            is_owned_here, _ = _classify_anchor_owner(
                anchor_upper, section_name,
            )
            if not is_owned_here:
                # Removing a cross-section anchor by id is unusual
                # (the worker shouldn't be doing that — its own
                # collections only carry its own ids) but we let it
                # through here: the owned-vs-cross partition below
                # will flag the drop as cross-section warning.
                continue
            # Layer 2 — anchored removes must declare coverage. ``None``
            # / empty value is incoherent for a manifest-anchored item.
            if not op.coverage_transferred_to:
                anchored_remove_violations.append({
                    'finding_id': op.finding_id,
                    'item_id': anchor_upper,
                    'collection': op.collection,
                })
                continue
            # Authorised: the explicit item id PLUS every anchor that
            # lived inside the removed item's content. Without the
            # collateral expansion, an item whose ``description`` quoted
            # other anchors (e.g. WS-01.description = "FR-01 spec.")
            # would trip the per-bundle drop check on the collateral
            # anchor even when the worker has done its homework.
            authorized_drop_ids.add(anchor_upper)
            removed_item = _find_removed_item_payload(
                current_bundle, op.collection, op.item_id,
            )
            if removed_item is not None:
                authorized_drop_ids.update(extract_anchor_ids(removed_item))

        unauthorized_drops = net_dropped_anchors - authorized_drop_ids

        # Partition unauthorised drops by ownership.
        unauthorized_owned: list[str] = []
        cross_section_drops: list[dict[str, str | None]] = []
        for anchor in sorted(unauthorized_drops):
            is_owned_here, owning_section = _classify_anchor_owner(
                anchor, section_name,
            )
            if is_owned_here:
                unauthorized_owned.append(anchor)
            else:
                cross_section_drops.append({
                    'anchor': anchor,
                    'owning_section': owning_section,
                })

        # Cross-section drops never refuse — they just warn so the
        # observability trail captures the change without preventing
        # legitimate prose edits. The next critic round is the right
        # place to flag coverage recall if any.
        if cross_section_drops:
            logger.warning(
                'section_patch_cross_section_anchor_dropped',
                section=section_name,
                bundle_key=bundle_key,
                cross_section_drops=cross_section_drops,
                ops=op_impacts,
            )

        if anchored_remove_violations or unauthorized_owned:
            logger.warning(
                'section_patch_refused_anchor_loss',
                section=section_name,
                bundle_key=bundle_key,
                unauthorized_drops=unauthorized_owned,
                anchored_removes_without_coverage=anchored_remove_violations,
                cross_section_drops=cross_section_drops,
                ops=op_impacts,
            )
            error_parts: list[str] = []
            if unauthorized_owned:
                error_parts.append(
                    f'This section owns anchor id(s) {unauthorized_owned} '
                    f'and the patch removes them from the bundle without '
                    f'an explicit `remove_item`. Preserve the id in '
                    f'another item or emit a `remove_item` that names '
                    f'`coverage_transferred_to`.'
                )
            if anchored_remove_violations:
                violation_ids = sorted({
                    v['item_id'] for v in anchored_remove_violations
                })
                error_parts.append(
                    f'`remove_item` on manifest-anchored item(s) '
                    f'{violation_ids} must declare '
                    f'`coverage_transferred_to` naming the item(s) that '
                    f'absorb the manifest coverage (`None` is not a '
                    f'valid acknowledgement for anchored items in the '
                    f'owning section).'
                )
            return ToolError(
                status='error',
                error=' '.join(error_parts),
                retryable=True,
                tool=f'apply_{section_name}_patch',
                suggestion=(
                    'Either (a) restore the dropped anchor by adding/'
                    'updating another item in the same batch to '
                    'reference it, (b) emit a `remove_item` op whose '
                    '`item_id` is the dropped anchor and whose '
                    '`coverage_transferred_to` lists the item(s) that '
                    'now cover the manifest entry, or (c) drop the '
                    'offending op and reformulate the fix to preserve '
                    'the anchor. Cross-section anchors (anchors owned '
                    'by another section, e.g. WS-IDs cited inside a '
                    'requirements FR) are NOT enforced here and may be '
                    'edited freely; they appear as `cross_section_drops` '
                    'in the warning log when removed.'
                ),
                data={
                    'unauthorized_drops': unauthorized_owned,
                    'anchored_removes_without_coverage': anchored_remove_violations,
                    'cross_section_drops': cross_section_drops,
                },
            )

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
            manifest_anchored_removes_count=len(manifest_anchored_removes),
        )

        # Always-on full ops log — one structured event per patch call
        # carrying every op + finding_id + per-op anchor impact. This is
        # the trail we follow post-mortem to answer "which finding made
        # the worker emit which op that dropped which anchor?". The
        # legacy ``section_patch_anchor_drops`` warning above stays put;
        # it fires only when the narrow detector sees a drop and is what
        # downstream consumers / dashboards already key off.
        logger.info(
            'section_patch_ops_executed',
            section=section_name,
            bundle_key=bundle_key,
            before_hash=before_bundle_hash,
            after_hash=after_bundle_hash,
            ops=op_impacts,
            anchor_drops=anchor_drops,
            manifest_anchored_removes=manifest_anchored_removes,
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
