"""ADK tool: validate SOW content before presenting to the user.

The agent should call this tool after generating SOW content and BEFORE
presenting results for human review. It runs deterministic structural
checks (ID formats, cross-references, word counts, row counts) and
returns actionable feedback the agent can fix autonomously.
"""

from __future__ import annotations

import json
from typing import Any

import structlog
from google.adk.tools import ToolContext

from ...shared.errors import safe_tool
from ...shared.types import ToolError, ToolSuccess
from ...shared.validators import ContentValidator

logger = structlog.get_logger()

_validator = ContentValidator()


@safe_tool
async def validate_sow_content(
    sow_data: str,
    funding_type: str = "",
    stage: str = "full",
    tool_context: ToolContext = None,
) -> dict[str, Any]:
    """
    Validates the structural quality of SOW content before presenting
    to the user or generating the final document.

    Call this tool AFTER assembling the SOW JSON and BEFORE asking the
    user to review. It catches formatting errors, missing cross-references,
    and content gaps that can be fixed automatically.

    Args:
        sow_data: A JSON string containing the SOW sections to validate.
            Accepts the same schema as generate_sow_document.
        funding_type: "PSF" or "DAF". If empty, auto-detected from
            sow_data fields (funding_type_short or funding_type).
        stage: "content" for Phase 2 Step 1.5 validation (payload has
            content but no architecture or consumption plan yet).
            "full" for Phase 4 validation (complete payload before
            document generation). Default: "full".

    Returns:
        A dictionary with:
        - passed: bool — True if no errors (warnings are OK).
        - error_count: int
        - warning_count: int
        - issues: list of {severity, field, message, suggestion}
        - summary: human-readable summary string for the agent to relay.
    """
    try:
        data = json.loads(sow_data)
    except json.JSONDecodeError as e:
        return ToolError(
            status="error",
            error=f"Invalid JSON: {e}",
            retryable=False,
            tool="validate_sow_content",
            suggestion="Fix the JSON syntax and call this tool again.",
        )

    ft = funding_type.strip().upper() if funding_type else None
    stage_normalized = stage.strip().lower() if stage else "full"
    if stage_normalized not in ("content", "full"):
        stage_normalized = "full"

    result = _validator.validate(data, funding_type=ft, stage=stage_normalized)

    logger.info(
        "sow_validation_completed",
        passed=result.passed,
        errors=len(result.errors),
        warnings=len(result.warnings),
    )

    result_dict = result.to_dict()

    # Build a concise summary the agent can include in its response.
    if result.passed and not result.warnings:
        summary = "All structural checks passed — content is ready for user review."
    elif result.passed:
        summary = (
            f"No blocking errors. {len(result.warnings)} warning(s) found — "
            "consider fixing before presenting to the user:\n"
            + "\n".join(f"  - {w}" for w in result.warnings)
        )
    else:
        summary = (
            f"{len(result.errors)} error(s) must be fixed before document generation.\n"
            + "\n".join(f"  - {e}" for e in result.errors)
        )
        if result.warnings:
            summary += (
                f"\n\nAdditionally, {len(result.warnings)} warning(s):\n"
                + "\n".join(f"  - {w}" for w in result.warnings)
            )

    result_dict["summary"] = summary

    return ToolSuccess(
        status="success",
        data=result_dict,
    )
