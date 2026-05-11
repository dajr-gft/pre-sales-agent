"""ADK tool: validate SOW content before presenting to the user.

The agent should call this tool after generating SOW content and BEFORE
presenting results for human review. It runs deterministic structural
checks (ID formats, cross-references, word counts, row counts) and an
independent semantic reviewer pass (contradictions across sections,
naming drift, semantic gaps), then returns actionable feedback the agent
can fix autonomously.

The semantic reviewer fails open: if it times out, errors, or is
disabled, the tool still returns the mechanical validation results and
the SOW pipeline proceeds.
"""
import json
from typing import Any

import structlog
from google.adk.tools import ToolContext

from ...shared.errors import safe_tool
from ...shared.types import ToolError, ToolSuccess
from ...shared.validators import ContentValidator
from ._semantic_review import semantic_review
from ._sow_helpers import sow_data_hash

logger = structlog.get_logger()

_validator = ContentValidator()

# Soft cap on consecutive validation calls that pass mechanically (errors=0)
# while still surfacing semantic findings. The semantic reviewer is an
# independent LLM pass — it is non-deterministic and may return different
# MAJOR/MINOR findings on each call against the same payload. Without a cap,
# an agent that reads the summary as "fix MAJOR before re-validating" can
# loop indefinitely chasing a moving target. After this many consecutive
# passing calls, the summary switches to a stop-instruction telling the
# agent the findings are advisory and re-validation will not converge.
# Counter is reset by any call where mechanical validation fails (passed=False)
# because that signals real work happened. Tracked per-stage in
# ``tool_context.state`` so 'content' (Phase 2 Step 1.5) and 'full' (Phase 3
# Step 1) cycles are independent.
_MAX_PASSED_ATTEMPTS_PER_STAGE = 3
_PASSED_ATTEMPTS_STATE_KEY = 'validation_passed_attempts'


@safe_tool
async def validate_sow_content(
    sow_data: str,
    funding_type: str = '',
    stage: str = 'full',
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
            content but no architecture yet).
            "full" for Phase 4 validation (complete payload before
            document generation). Default: "full".

    Returns:
        A dictionary with:
        - passed: bool — True if no mechanical errors (warnings and
          semantic findings are advisory and do NOT influence this flag).
        - error_count: int — mechanical errors only.
        - warning_count: int — mechanical warnings only.
        - issues: list of {severity, field, message, suggestion} —
          mechanical issues only.
        - findings: list of {id, severity, category, evidence,
          recommendation, fields} — semantic reviewer findings.
          Severity ∈ {BLOCKER, MAJOR, MINOR}. Empty when the reviewer
          was disabled, the stage is unsupported, or the call failed.
        - review_metadata: {ran, model, latency_ms, fallback_reason,
          severity_counts}.
        - summary: human-readable summary string for the agent to relay.
    """
    raw_hash = sow_data_hash(sow_data)
    logger.info('validate_sow_content_invoked', sow_data_hash=raw_hash)

    try:
        data = json.loads(sow_data)
    except json.JSONDecodeError as e:
        return ToolError(
            status='error',
            error=f'Invalid JSON: {e}',
            retryable=False,
            tool='validate_sow_content',
            suggestion='Fix the JSON syntax and call this tool again.',
        )

    ft = funding_type.strip().upper() if funding_type else None
    stage_normalized = stage.strip().lower() if stage else 'full'
    if stage_normalized not in ('content', 'full'):
        stage_normalized = 'full'

    result = _validator.validate(data, funding_type=ft, stage=stage_normalized)

    review = await semantic_review(
        sow_data=data,
        stage=stage_normalized,
        tool_context=tool_context,
    )
    findings = review['findings']
    review_metadata = review['review_metadata']

    passed_attempts = _track_passed_attempts(
        tool_context=tool_context,
        stage=stage_normalized,
        passed=result.passed,
    )

    logger.info(
        'sow_validation_completed',
        sow_data_hash=raw_hash,
        stage=stage_normalized,
        funding_type=ft,
        passed=result.passed,
        errors=len(result.errors),
        warnings=len(result.warnings),
        error_details=[str(e) for e in result.errors],
        warning_details=[str(w) for w in result.warnings],
        findings_count=len(findings),
        review_ran=review_metadata.get('ran'),
        review_fallback=review_metadata.get('fallback_reason'),
        passed_attempts=passed_attempts,
    )

    result_dict = result.to_dict()
    result_dict['findings'] = findings
    result_dict['review_metadata'] = review_metadata

    result_dict['summary'] = _build_summary(
        result, findings, review_metadata, passed_attempts=passed_attempts
    )

    return ToolSuccess(
        status='success',
        data=result_dict,
    )


def _track_passed_attempts(
    tool_context: ToolContext | None,
    stage: str,
    passed: bool,
) -> int:
    """Increment the per-stage passed-attempts counter and return the new value.

    Counter is reset to zero on a failing mechanical validation, because
    real fixes restart the cycle. Without a tool_context, returns 0 (cap
    enforcement is disabled but the conditional summary still applies).
    """
    if tool_context is None:
        return 0

    counters = tool_context.state.get(_PASSED_ATTEMPTS_STATE_KEY) or {}
    if not isinstance(counters, dict):
        counters = {}

    if not passed:
        counters[stage] = 0
    else:
        counters[stage] = counters.get(stage, 0) + 1

    tool_context.state[_PASSED_ATTEMPTS_STATE_KEY] = counters
    return counters[stage]


def _build_summary(
    result: Any,
    findings: list[dict[str, Any]],
    review_metadata: dict[str, Any],
    passed_attempts: int = 0,
) -> str:
    """Compose the agent-facing summary covering mechanical and semantic layers.

    Mechanical errors come first because they govern ``passed``. Semantic
    findings follow with severity-aware guidance:

    - **BLOCKER present** → instruct the agent to address BLOCKERs before
      re-validating. MAJOR/MINOR follow into the revision tracker.
    - **No BLOCKER, attempts under cap** → instruct the agent that
      MAJOR/MINOR findings are advisory because mechanical passed; warn
      that the semantic reviewer is non-deterministic and chasing
      residual findings will not converge.
    - **No BLOCKER, attempts at or beyond cap** → explicit stop
      instruction. Re-validation has hit the cap defined by
      ``_MAX_PASSED_ATTEMPTS_PER_STAGE``; further calls only burn budget
      without making the diff stable.

    The cap-aware branch exists because the previous unconditional
    "Address BLOCKER and MAJOR before re-validating" line was being read
    by the agent as a permanent instruction and produced 20+ consecutive
    re-validations on a passing payload in production.
    """
    lines: list[str] = []

    if result.passed and not result.warnings:
        lines.append(
            'All structural checks passed — content is ready for user review.'
        )
    elif result.passed:
        lines.append(
            f'No blocking errors. {len(result.warnings)} warning(s) found — '
            'consider fixing before presenting to the user:'
        )
        lines.extend(f'  - {w}' for w in result.warnings)
    else:
        lines.append(
            f'{len(result.errors)} error(s) must be fixed before document generation.'
        )
        lines.extend(f'  - {e}' for e in result.errors)
        if result.warnings:
            lines.append('')
            lines.append(f'Additionally, {len(result.warnings)} warning(s):')
            lines.extend(f'  - {w}' for w in result.warnings)

    blockers = sum(1 for f in findings if f.get('severity') == 'BLOCKER')
    majors = sum(1 for f in findings if f.get('severity') == 'MAJOR')
    minors = sum(1 for f in findings if f.get('severity') == 'MINOR')

    if findings:
        lines.append('')
        severity_summary = (
            f'Semantic reviewer surfaced {len(findings)} finding(s) '
            f'(BLOCKER: {blockers}, MAJOR: {majors}, MINOR: {minors}).'
        )

        if blockers > 0:
            lines.append(
                f'{severity_summary} Address BLOCKER findings before '
                're-validating; MAJOR and MINOR may flow into the Phase 3 '
                'revision tracker.'
            )
        elif passed_attempts >= _MAX_PASSED_ATTEMPTS_PER_STAGE:
            lines.append(
                f'{severity_summary} Maximum re-validation attempts reached '
                f'({passed_attempts}/{_MAX_PASSED_ATTEMPTS_PER_STAGE}). No '
                'BLOCKER is present; remaining findings are advisory and the '
                'semantic reviewer is non-deterministic, so re-validation '
                'will surface different findings each call. Record any '
                'remaining concerns for the Phase 3 revision tracker and '
                'proceed — do NOT call this tool again unless you make '
                'structural changes that warrant fresh mechanical validation.'
            )
        else:
            lines.append(
                f'{severity_summary} No BLOCKER findings — these are '
                'advisory. Mechanical validation passed. The semantic '
                'reviewer is non-deterministic, so chasing residual '
                'MAJOR/MINOR findings can produce different results on each '
                'call; apply the max-2-fix-attempts rule per finding (per '
                'SKILL.md), then proceed without re-validating to chase '
                'residuals.'
            )

        for f in findings:
            evidence = (f.get('evidence') or '').replace('\n', ' ').strip()
            if len(evidence) > 200:
                evidence = evidence[:200].rstrip() + '…'
            lines.append(
                f"  - [{f.get('severity')}] {f.get('id')} "
                f"({f.get('category')}): {evidence}"
            )
    elif review_metadata.get('ran') is False:
        reason = review_metadata.get('fallback_reason') or 'unknown'
        lines.append('')
        lines.append(
            f'Semantic reviewer did not run (reason: {reason}); mechanical '
            'validation above is authoritative.'
        )

    return '\n'.join(lines)
