"""
ADK tools for the Extraction Manifest hand-off between sow-discovery and
sow-generator skills.

Incremental construction flow:
   `initialize_extraction_buffer` → many `append_extraction_items` calls →
   `finalize_extraction_manifest`. Items are persisted to session state as
   they are extracted, eliminating the failure mode where the model declares
   "extracted N items" without ever producing the structured records.

`load_extraction_manifest` retrieves the finalized Manifest from session
state for the sow-generator skill. `validate_extraction_manifest` runs the
full schema validation against an arbitrary manifest dict without persisting
— useful for mid-construction self-checks.

Wire all five tools into the LlmAgent's tools list. The skills' SKILL.md
files reference them by name.
"""

from datetime import datetime, timezone
from typing import Any

import structlog
from google.adk.tools import ToolContext
from pydantic import ValidationError

from ._extraction_manifest import (
    ExtractedItem,
    ExtractionManifest,
    InventoryEntry,
)

logger = structlog.get_logger()

_MANIFEST_STATE_KEY = "extraction_manifest"
_BUFFER_STATE_KEY = "extraction_buffer"


def _format_errors(exc: ValidationError) -> list[dict[str, Any]]:
    """Convert Pydantic errors into a compact, model-friendly shape."""
    out: list[dict[str, Any]] = []
    for err in exc.errors(include_url=False):
        out.append(
            {
                "loc": ".".join(str(x) for x in err.get("loc", [])),
                "msg": err.get("msg", ""),
                "type": err.get("type", ""),
            }
        )
    return out

async def initialize_extraction_buffer(
    conversation_language: str,
    inventory: list[dict[str, Any]],
    tool_context: ToolContext,
) -> dict[str, Any]:
    """
    Initializes the extraction buffer in session state, seeding it with the
    artifact inventory confirmed in Phase 0.5.

    Call ONCE, at the end of Phase 0.5 (after the user confirms the triage
    order). This locks in the inventory; subsequent `append_extraction_items`
    calls will validate that every item's `source.artifact_id` exists in the
    inventory you pass here.

    If the buffer already exists in session state, this call OVERWRITES it
    and returns a warning. That covers the case where the user restarts
    discovery mid-session — no items from the previous run leak into the
    new buffer.

    Args:
        conversation_language: Language code of the conversation between
            user and agent (e.g., 'pt-BR', 'en', 'es'). Will be written to
            the manifest at finalize time.
        inventory: List of artifact dicts, each matching the InventoryEntry
            schema (id, name, type, phase_0_hypothesis, source_language,
            optionally uploaded_at and notes). Tier ordering from triage is
            implicit in the list order — Primary first, Secondary next,
            Context last.
        tool_context: Injected by ADK.

    Returns:
        On success: {status: 'ok', inventory_count, buffer_initialized: True,
            warnings: [...] (empty unless overwriting)}
        On validation failure: {status: 'error', errors: [...],
            buffer_initialized: False, guidance: <instructional string>}
    """
    validated_entries: list[dict[str, Any]] = []
    per_entry_errors: list[dict[str, Any]] = []

    seen_ids: set[str] = set()
    for idx, entry_dict in enumerate(inventory):
        try:
            entry = InventoryEntry.model_validate(entry_dict)
        except ValidationError as exc:
            per_entry_errors.append(
                {
                    "inventory_index": idx,
                    "raw_id": entry_dict.get("id", "<missing>"),
                    "errors": _format_errors(exc),
                }
            )
            continue

        if entry.id in seen_ids:
            per_entry_errors.append(
                {
                    "inventory_index": idx,
                    "raw_id": entry.id,
                    "errors": [
                        {
                            "loc": "id",
                            "msg": (
                                f"Duplicate inventory id '{entry.id}'. "
                                f"Inventory IDs must be unique within the buffer."
                            ),
                            "type": "duplicate_id",
                        }
                    ],
                }
            )
            continue

        seen_ids.add(entry.id)
        validated_entries.append(entry.model_dump())

    if per_entry_errors:
        return {
            "status": "error",
            "errors": per_entry_errors,
            "buffer_initialized": False,
            "guidance": (
                "One or more inventory entries failed validation. Fix the "
                "fields named in `errors[].errors[].loc` for each entry and "
                "call initialize_extraction_buffer again with the corrected "
                "inventory list."
            ),
        }

    warnings: list[str] = []
    prior = tool_context.state.get(_BUFFER_STATE_KEY)
    if prior is not None:
        prior_items = len(prior.get("extracted_items", []))
        warnings.append(
            f"Overwrote existing buffer with {prior_items} extracted_items. "
            f"All previously appended items have been discarded."
        )
        logger.warning(
            "extraction_buffer_overwritten",
            prior_items_count=prior_items,
            prior_inventory_count=len(prior.get("inventory", [])),
        )

    tool_context.state[_BUFFER_STATE_KEY] = {
        "initialized_at": datetime.now(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        ),
        "conversation_language": conversation_language,
        "inventory": validated_entries,
        "extracted_items": [],
    }

    logger.info(
        "extraction_buffer_initialized",
        inventory_count=len(validated_entries),
        conversation_language=conversation_language,
    )

    return {
        "status": "ok",
        "inventory_count": len(validated_entries),
        "buffer_initialized": True,
        "warnings": warnings,
    }


async def append_extraction_items(
    items: list[dict[str, Any]],
    tool_context: ToolContext,
) -> dict[str, Any]:
    """
    Appends one or more extracted_item dicts to the in-progress buffer.

    Call this DURING Phase 1, ideally once per artifact processed. Each item
    is validated individually against the ExtractedItem schema; valid items
    are appended to the buffer, invalid items are returned in `errors_per_item`
    so the model can correct them and resubmit.

    Per-item validation enforced here:
        - All ExtractedItem field validators (extra="forbid", min_length on
          source, controlled vocabularies for category/confidence).
        - Every `source.artifact_id` must exist in the inventory established
          by initialize_extraction_buffer.
        - `id` must be unique against all items already in the buffer AND
          against other items in this same call.

    Cross-reference validation that requires the complete manifest (cross_refs
    pointing to other items, engagement_shape requirement) is deferred to
    finalize_extraction_manifest. This is intentional: items are added one
    artifact at a time, so cross_refs may point to items not yet appended.

    If the buffer has not been initialized, this call returns an error
    instructing the model to call initialize_extraction_buffer first.

    Args:
        items: List of extracted_item dicts. Each dict must match the
            ExtractedItem schema in references/manifest-schema.md.
        tool_context: Injected by ADK.

    Returns:
        Always: {
            status: 'ok' | 'partial' | 'error',
            items_appended_this_call: int,
            total_items_in_buffer: int,
            errors_per_item: [{item_index, raw_id, errors: [...]}, ...]
        }
        - 'ok': all items in this call were appended successfully.
        - 'partial': some items appended, some failed. Successful items are
          persisted; failed items must be corrected and resubmitted in a
          new call.
        - 'error': no items appended (e.g., buffer not initialized, or all
          items failed validation).
    """
    buffer = tool_context.state.get(_BUFFER_STATE_KEY)
    if buffer is None:
        return {
            "status": "error",
            "items_appended_this_call": 0,
            "total_items_in_buffer": 0,
            "errors_per_item": [
                {
                    "item_index": -1,
                    "raw_id": "<n/a>",
                    "errors": [
                        {
                            "loc": "buffer",
                            "msg": (
                                "Extraction buffer is not initialized. Call "
                                "initialize_extraction_buffer with the "
                                "confirmed inventory before appending items."
                            ),
                            "type": "buffer_not_initialized",
                        }
                    ],
                }
            ],
        }

    inventory_ids: set[str] = {entry["id"] for entry in buffer["inventory"]}
    existing_item_ids: set[str] = {item["id"] for item in buffer["extracted_items"]}

    appended: list[dict[str, Any]] = []
    errors_per_item: list[dict[str, Any]] = []
    ids_in_this_call: set[str] = set()

    for idx, item_dict in enumerate(items):
        try:
            item = ExtractedItem.model_validate(item_dict)
        except ValidationError as exc:
            errors_per_item.append(
                {
                    "item_index": idx,
                    "raw_id": item_dict.get("id", "<missing>"),
                    "errors": _format_errors(exc),
                }
            )
            continue

        unknown_refs = [
            src.artifact_id
            for src in item.source
            if src.artifact_id not in inventory_ids
        ]
        if unknown_refs:
            errors_per_item.append(
                {
                    "item_index": idx,
                    "raw_id": item.id,
                    "errors": [
                        {
                            "loc": "source",
                            "msg": (
                                f"source references artifact(s) not in "
                                f"inventory: {sorted(set(unknown_refs))}. "
                                f"Known artifact IDs: {sorted(inventory_ids)}."
                            ),
                            "type": "unknown_artifact",
                        }
                    ],
                }
            )
            continue

        if item.id in existing_item_ids:
            errors_per_item.append(
                {
                    "item_index": idx,
                    "raw_id": item.id,
                    "errors": [
                        {
                            "loc": "id",
                            "msg": (
                                f"Item id '{item.id}' already exists in the "
                                f"buffer. IDs must be unique across all "
                                f"appended items. Use the next sequential ID "
                                f"(buffer currently holds "
                                f"{len(existing_item_ids)} items)."
                            ),
                            "type": "duplicate_id",
                        }
                    ],
                }
            )
            continue

        if item.id in ids_in_this_call:
            errors_per_item.append(
                {
                    "item_index": idx,
                    "raw_id": item.id,
                    "errors": [
                        {
                            "loc": "id",
                            "msg": (
                                f"Item id '{item.id}' is duplicated within "
                                f"this same append call."
                            ),
                            "type": "duplicate_id_in_call",
                        }
                    ],
                }
            )
            continue

        ids_in_this_call.add(item.id)
        appended.append(item.model_dump())

    buffer["extracted_items"].extend(appended)
    tool_context.state[_BUFFER_STATE_KEY] = buffer

    if not appended and errors_per_item:
        status = "error"
    elif errors_per_item:
        status = "partial"
    else:
        status = "ok"

    logger.info(
        "extraction_items_appended",
        items_in_call=len(items),
        appended=len(appended),
        rejected=len(errors_per_item),
        total_in_buffer=len(buffer["extracted_items"]),
        status=status,
    )

    return {
        "status": status,
        "items_appended_this_call": len(appended),
        "total_items_in_buffer": len(buffer["extracted_items"]),
        "errors_per_item": errors_per_item,
    }


async def finalize_extraction_manifest(
    gaps: dict[str, Any],
    self_audit: dict[str, Any],
    tool_context: ToolContext,
) -> dict[str, Any]:
    """
    Finalizes the manifest by combining the in-progress buffer with the gaps
    and self_audit collected during Phase 3 and 4, runs full Pydantic
    validation (including all cross-reference validators), and persists the
    result in session state under the `extraction_manifest` key.

    Call ONCE at the end of Phase 4. After successful persistence, the
    buffer is cleared from session state.

    Validation runs the complete `ExtractionManifest.model_validate()` chain:
        - All ExtractedItem field validators (already enforced at append).
        - Unique IDs across inventory, extracted_items, and gaps.
        - source.artifact_id references resolve to inventory entries.
        - cross_refs entries point to existing extracted_items IDs.
        - to_be_defined entries link to existing HardGap or Ambiguity IDs.
        - Every inventory entry contributed at least one item, OR has a
          justifying notes field.
        - At least one Identity item has primitives.engagement_shape set
          (or a hard_gap blocks_sow_generation=true marks it as missing).
        - inventory[].items_extracted and categories_found are auto-populated
          based on the actual extracted_items contents.

    If validation fails, the response includes a structured `errors` list and
    the manifest is NOT persisted. The buffer remains intact so the model can
    correct gaps/self_audit (or append more items via append_extraction_items)
    and call finalize again.

    Args:
        gaps: dict matching the Gaps schema (hard_gaps, pending_decisions,
            ambiguities, to_be_defined). Pass empty lists for sections with
            no entries.
        self_audit: dict matching the SelfAudit schema (all_artifacts_contributed,
            all_required_categories_covered, contradictions_resolved_or_flagged,
            user_interview_turns).
        tool_context: Injected by ADK.

    Returns:
        On success: {status: 'ok', items_count, inventory_count,
            hard_gaps_count, pending_decisions_count, ambiguities_count,
            manifest_persisted: True}
        On validation failure: {status: 'error', errors: [...],
            manifest_persisted: False, guidance: <instructional string>}
        On missing buffer: {status: 'error', errors: [...],
            manifest_persisted: False, guidance: <instructional string>}
    """
    buffer = tool_context.state.get(_BUFFER_STATE_KEY)
    if buffer is None:
        return {
            "status": "error",
            "errors": [
                {
                    "loc": "buffer",
                    "msg": (
                        "Extraction buffer is not initialized. The manifest "
                        "cannot be finalized without a buffer. This typically "
                        "means initialize_extraction_buffer was never called, "
                        "or the buffer was cleared by a prior finalize."
                    ),
                    "type": "buffer_not_initialized",
                }
            ],
            "manifest_persisted": False,
            "guidance": (
                "Call initialize_extraction_buffer with the confirmed "
                "inventory, then append_extraction_items per artifact, "
                "then finalize_extraction_manifest."
            ),
        }

    manifest_dict = {
        "manifest_version": "1.0",
        "created_at": datetime.now(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        ),
        "conversation_language": buffer["conversation_language"],
        "inventory": buffer["inventory"],
        "extracted_items": buffer["extracted_items"],
        "gaps": gaps,
        "self_audit": self_audit,
    }

    try:
        validated = ExtractionManifest.model_validate(manifest_dict)
    except ValidationError as exc:
        return {
            "status": "error",
            "errors": _format_errors(exc),
            "manifest_persisted": False,
            "guidance": (
                "Final validation failed. Each entry in 'errors' has a 'loc' "
                "(field path) and 'msg' (problem). Fields under "
                "'extracted_items' indicate items already in the buffer that "
                "fail cross-validation — you may need to append corrected "
                "items or fix gaps/self_audit. Fields under 'gaps' or "
                "'self_audit' indicate problems with this finalize call's "
                "payload — call finalize_extraction_manifest again with the "
                "corrected gaps/self_audit (the buffer is preserved)."
            ),
        }

    manifest_dict = validated.model_dump(mode="json")
    tool_context.state[_MANIFEST_STATE_KEY] = manifest_dict
    tool_context.state[_BUFFER_STATE_KEY] = None

    logger.info(
        "manifest_persisted",
        storage="session_state",
        state_key=_MANIFEST_STATE_KEY,
        items_count=len(validated.extracted_items),
        flow="incremental",
    )

    return {
        "status": "ok",
        "inventory_count": len(validated.inventory),
        "items_count": len(validated.extracted_items),
        "hard_gaps_count": len(validated.gaps.hard_gaps),
        "pending_decisions_count": len(validated.gaps.pending_decisions),
        "ambiguities_count": len(validated.gaps.ambiguities),
        "manifest_persisted": True,
    }

async def load_extraction_manifest(tool_context: ToolContext) -> dict[str, Any]:
    """
    Loads the Extraction Manifest from session state.

    Use at the start of sow-generator Phase 1 (Path B) to retrieve the
    project context produced by sow-discovery. If the manifest is not found
    in state, redirect the user to run sow-discovery first.

    Returns:
        On found: {status: 'ok', manifest: {...}}
        On missing: {status: 'not_found', manifest: null}
    """
    manifest_dict = tool_context.state.get(_MANIFEST_STATE_KEY)
    if manifest_dict is None:
        logger.info("manifest_not_found", state_key=_MANIFEST_STATE_KEY)
        return {"status": "not_found", "manifest": None}
    return {"status": "ok", "manifest": manifest_dict}


async def validate_extraction_manifest(
    manifest: dict[str, Any],
) -> dict[str, Any]:
    """
    Validates a manifest dict against the ExtractionManifest schema WITHOUT
    saving it.

    Use this for mid-construction self-checks on large artifact sets — for
    example, after building the inventory and extracted_items but before
    adding gaps. Catches structural mistakes early, before they accumulate.
    Runs the same validation as save_extraction_manifest but persists nothing.

    Args:
        manifest: The manifest to validate.

    Returns:
        On valid: {valid: True, errors: []}
        On invalid: {valid: False, errors: [...]}
    """
    try:
        ExtractionManifest.model_validate(manifest)
        return {"valid": True, "errors": []}
    except ValidationError as exc:
        return {
            "valid": False,
            "errors": _format_errors(exc),
        }
