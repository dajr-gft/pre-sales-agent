"""Legacy `validate_sow_content` — degraded to a CI-only helper.

Validation moved to the `validation_critic` sub-agent. The agent flow no
longer registers this function as an ADK tool, so the LLM cannot call it.

The helper survives only for callers outside the agent flow (CI, ad-hoc
scripts) that need to run the deterministic ``ContentValidator`` without
involving the critic. The payload shape mirrors the legacy tool output:

    {
        "passed": bool,
        "error_count": int,
        "warning_count": int,
        "issues": list[{severity, field, message, suggestion}],
        "summary": str,
    }

Scheduled for removal alongside MVP 2 (when ``validation_loop`` replaces
``validation_critic`` as the root sub_agent). Do not re-add this as a
tool — the architecture explicitly forbids tools that orchestrate the
validation agent.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from ...shared.validators import ContentValidator, ValidationResult

_validator = ContentValidator()


def _build_summary(result: ValidationResult) -> str:
    """Mirror the human-readable summary the legacy tool produced."""
    if result.passed and not result.warnings:
        return 'All structural checks passed — content is ready for user review.'
    if result.passed:
        body = '\n'.join(f'  - {w}' for w in result.warnings)
        return (
            f'No blocking errors. {len(result.warnings)} warning(s) found — '
            f'consider fixing before presenting to the user:\n{body}'
        )
    errors_body = '\n'.join(f'  - {e}' for e in result.errors)
    summary = (
        f'{len(result.errors)} error(s) must be fixed before document '
        f'generation.\n{errors_body}'
    )
    if result.warnings:
        warn_body = '\n'.join(f'  - {w}' for w in result.warnings)
        summary += (
            f'\n\nAdditionally, {len(result.warnings)} warning(s):\n{warn_body}'
        )
    return summary


def validate_sow_content(
    sow_data: str,
    funding_type: str = '',
    stage: Literal['content', 'full'] = 'full',
) -> dict[str, Any]:
    """Run the deterministic structural validator on a SOW payload.

    This is a Python-only helper. It does NOT invoke any LLM, does NOT
    write session state, and is NOT registered as an ADK tool. Use the
    `validation_critic` sub-agent for the full pipeline.

    Args:
        sow_data: SOW JSON string.
        funding_type: "PSF" or "DAF". Auto-detected when empty.
        stage: "content" or "full".

    Returns:
        Dict with keys: ``passed``, ``error_count``, ``warning_count``,
        ``issues``, ``summary``. Raises ``ValueError`` on invalid JSON.
    """
    try:
        data = json.loads(sow_data)
    except json.JSONDecodeError as exc:
        raise ValueError(f'Invalid SOW JSON: {exc}') from exc

    ft = funding_type.strip().upper() if funding_type else None
    stage_normalized: Literal['content', 'full'] = (
        'content' if (stage or '').strip().lower() == 'content' else 'full'
    )

    result = _validator.validate(data, funding_type=ft, stage=stage_normalized)
    payload = result.to_dict()
    payload['summary'] = _build_summary(result)
    return payload
