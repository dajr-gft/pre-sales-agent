"""
ADK tools for the Extraction Manifest hand-off between sow-discovery and
sow-generator skills.

Pattern: mirrors the existing `validate_sow_content` design — a tool that
runs Pydantic validation, returns structured errors on failure, and lets the
model apply the incremental editing rule on the same payload until it passes.

Wire these three tools into the LlmAgent's tools list. The skills' SKILL.md
files reference them by name.
"""

import json
from typing import Any

import structlog
from google.adk.tools import ToolContext
from google.genai import types
from pydantic import ValidationError

from ._extraction_manifest import ExtractionManifest

logger = structlog.get_logger()

ARTIFACT_NAME = "extraction_manifest.json"
_ARTIFACT_MIME = "application/json"


def _extract_artifact_bytes(part: types.Part | None) -> bytes | None:
    """Return the raw JSON bytes from an artifact Part, or None if absent.

    Reads `inline_data.data` (current binary format). Falls back to `text` so
    artifacts saved by older versions of the tool still load.
    """
    if part is None:
        return None
    inline = getattr(part, "inline_data", None)
    if inline is not None and getattr(inline, "data", None):
        return inline.data
    text = getattr(part, "text", None)
    if text:
        return text.encode("utf-8")
    return None


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


async def save_extraction_manifest(
    manifest: dict[str, Any],
    tool_context: ToolContext,
) -> dict[str, Any]:
    """
    Validates the manifest against the ExtractionManifest schema and persists
    it as a session artifact named 'extraction_manifest'.

    Use at the end of sow-discovery Phase 4, AFTER running the self-audit in
    your reasoning. If validation fails, the response includes a structured
    `errors` list. Apply the incremental editing rule: start from the EXACT
    payload you just submitted, modify ONLY the fields named in `errors[].loc`,
    and call this tool again. Do NOT regenerate the manifest from scratch —
    that consistently drops fields that were previously correct.

    Args:
        manifest: The complete Extraction Manifest as a dict, matching
            references/manifest-schema.md.
        tool_context: Injected by ADK.

    Returns:
        On success: {status: 'ok', items_count, inventory_count,
            hard_gaps_count, pending_decisions_count, ambiguities_count,
            artifact_saved: True}
        On validation failure: {status: 'error', errors: [...],
            artifact_saved: False, guidance: <instructional string>}
        On save failure: {status: 'save_failed', error: <message>,
            artifact_saved: False}
    """
    try:
        validated = ExtractionManifest.model_validate(manifest)
    except ValidationError as exc:
        return {
            "status": "error",
            "errors": _format_errors(exc),
            "artifact_saved": False,
            "guidance": (
                "Validation failed. Each entry in 'errors' has a 'loc' "
                "(field path) and 'msg' (problem). Correct ONLY the cited "
                "fields and call save_extraction_manifest again with the same "
                "payload otherwise unchanged."
            ),
        }

    json_bytes = validated.model_dump_json(indent=2).encode("utf-8")

    try:
        version = await tool_context.save_artifact(
            ARTIFACT_NAME,
            types.Part.from_bytes(data=json_bytes, mime_type=_ARTIFACT_MIME),
        )
    except Exception as exc:  # noqa: BLE001 — surface whatever the runtime raises
        logger.error(
            'manifest_save_failed',
            error=f'{type(exc).__name__}: {exc}',
        )
        return {
            "status": "save_failed",
            "error": f"{type(exc).__name__}: {exc}",
            "artifact_saved": False,
        }

    logger.info(
        'manifest_artifact_saved',
        filename=ARTIFACT_NAME,
        version=version,
        size_bytes=len(json_bytes),
        items_count=len(validated.extracted_items),
    )

    return {
        "status": "ok",
        "inventory_count": len(validated.inventory),
        "items_count": len(validated.extracted_items),
        "hard_gaps_count": len(validated.gaps.hard_gaps),
        "pending_decisions_count": len(validated.gaps.pending_decisions),
        "ambiguities_count": len(validated.gaps.ambiguities),
        "artifact_saved": True,
    }


async def load_extraction_manifest(tool_context: ToolContext) -> dict[str, Any]:
    """
    Loads the Extraction Manifest from session artifacts.

    Use at the start of sow-generator Phase 1 (Path B) to retrieve the
    project context produced by sow-discovery. If the manifest is not found,
    redirect the user to run sow-discovery first.

    Returns:
        On found: {status: 'ok', manifest: {...}}
        On missing: {status: 'not_found', manifest: null}
        On corrupted: {status: 'corrupted', manifest: null, error: <message>}
    """
    try:
        part = await tool_context.load_artifact(ARTIFACT_NAME)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            'manifest_load_failed',
            error=f'{type(exc).__name__}: {exc}',
        )
        return {
            "status": "load_failed",
            "manifest": None,
            "error": f"{type(exc).__name__}: {exc}",
        }

    raw = _extract_artifact_bytes(part)
    if raw is None:
        logger.info(
            'manifest_not_found',
            part_type=type(part).__name__ if part is not None else 'None',
        )
        return {"status": "not_found", "manifest": None}

    try:
        manifest_dict = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return {
            "status": "corrupted",
            "manifest": None,
            "error": f"Stored manifest is not valid JSON: {exc}",
        }

    return {"status": "ok", "manifest": manifest_dict}


def validate_extraction_manifest(
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
