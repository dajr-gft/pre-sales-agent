"""ADK tool: validate SOW content before presenting to the user.

The agent should call this tool after generating SOW content and BEFORE
presenting results for human review. It runs deterministic structural
checks (ID formats, cross-references, word counts, row counts) and two
independent reviewer passes in parallel:

- ``semantic_review`` — contradictions across sections, naming drift,
  semantic gaps.
- ``manifest_coverage_review`` — every Extraction Manifest item should
  have at least one substantive anchor in the SOW; unanchored items
  surface as ``category="coverage"`` findings.

The reviewers fail open: if either times out, errors, or is disabled,
the tool still returns the mechanical validation results plus whatever
findings the surviving pass produced, and the SOW pipeline proceeds.
"""
import asyncio
import hashlib
import json
from typing import Any

import structlog
from google.adk.tools import ToolContext

from ...shared.errors import safe_tool
from ...shared.types import ToolError, ToolSuccess
from ...shared.validators import ContentValidator
from ._manifest_coverage_review import manifest_coverage_review
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
#
# The cap is 5, which gives the agent exactly 4 actual correction rounds
# between calls — matching the SKILL.md "Maximum 4 correction rounds"
# protocol. The arithmetic: call 1 is the initial read (no fix yet);
# calls 2-5 are after fix attempts 1-4; call 5 is the cap. Earlier values
# (2 and 3) under-counted SKILL.md's allowance and cut the agent off
# mid-protocol on non-deterministic semantic findings.
_MAX_PASSED_ATTEMPTS_PER_STAGE = 5
_PASSED_ATTEMPTS_STATE_KEY = 'validation_passed_attempts'

# Per-stage record of finding fingerprints from the immediately previous call.
# Used to detect findings the reviewer re-surfaced verbatim — strong signal
# that the agent already tried to fix them and the issue is residual reviewer
# noise (or a genuine gap the agent cannot resolve in this stage). Surfacing
# persistence in the summary lets the agent stop retrying specific findings
# instead of relying only on the global attempts cap.
_FINDINGS_SEEN_STATE_KEY = 'validation_findings_seen'

# Cap on the evidence-text window used to compute a finding's fingerprint.
# Reviewers paraphrase across calls; comparing the first ~200 normalized
# characters catches deterministic re-emissions without over-matching loosely
# related findings that share only the leading words.
_FINGERPRINT_EVIDENCE_WINDOW = 200


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
          recommendation, fields, persistent} — semantic reviewer + manifest
          coverage findings, concatenated. Severity ∈ {BLOCKER, MAJOR, MINOR}.
          ``persistent`` is True when the finding's fingerprint matched a
          finding from the immediately previous call against the same stage
          (signal that the generator already had a chance to fix it).
          Empty when both passes were disabled / unsupported / failed.
        - has_blocker_findings: bool — convenience flag derived from
          ``findings``. True iff any finding has severity ``BLOCKER``. The
          agent should treat this as a hard gate: do NOT present content,
          do NOT call other tools, and do NOT re-validate until the BLOCKER
          findings have been addressed per SKILL.md Phase 2 Step 1.5 /
          Phase 3 Step 1.
        - review_metadata: {semantic: {...}, coverage: {...}} — each
          sub-dict carries {ran, model, latency_ms, fallback_reason,
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

    semantic_pass, coverage_pass = await asyncio.gather(
        semantic_review(
            sow_data=data,
            stage=stage_normalized,
            tool_context=tool_context,
        ),
        manifest_coverage_review(
            sow_data=data,
            stage=stage_normalized,
            tool_context=tool_context,
        ),
    )
    findings = [*semantic_pass['findings'], *coverage_pass['findings']]
    review_metadata = {
        'semantic': semantic_pass['review_metadata'],
        'coverage': coverage_pass['review_metadata'],
    }

    passed_attempts = _track_passed_attempts(
        tool_context=tool_context,
        stage=stage_normalized,
        passed=result.passed,
    )

    persistent_count = _annotate_persistent_findings(
        tool_context=tool_context,
        stage=stage_normalized,
        findings=findings,
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
        persistent_findings_count=persistent_count,
        semantic_ran=review_metadata['semantic'].get('ran'),
        semantic_fallback=review_metadata['semantic'].get('fallback_reason'),
        coverage_ran=review_metadata['coverage'].get('ran'),
        coverage_fallback=review_metadata['coverage'].get('fallback_reason'),
        passed_attempts=passed_attempts,
    )

    result_dict = result.to_dict()
    result_dict['findings'] = findings
    result_dict['review_metadata'] = review_metadata
    # Programmatic flag so the agent does not need to parse the summary text
    # to detect BLOCKER findings. Production observation: agents misread the
    # leading mechanical-pass line as a green light and skipped the SKILL.md
    # fix loop. A structured boolean removes that failure mode.
    result_dict['has_blocker_findings'] = any(
        f.get('severity') == 'BLOCKER' for f in findings
    )

    result_dict['summary'] = _build_summary(
        result, findings, review_metadata, passed_attempts=passed_attempts
    )

    return ToolSuccess(
        status='success',
        data=result_dict,
    )


def _finding_fingerprint(finding: dict[str, Any]) -> str:
    """Return a short stable hash identifying a finding across calls.

    The fingerprint combines the dimensions that should remain stable when a
    reviewer re-surfaces the same defect: category, severity, the sorted set
    of fields the recommendation touches, and a normalized window of the
    evidence text. ID is intentionally excluded — reviewers renumber findings
    each call (F-001, F-002 ...) and matching by ID would never persist.

    Evidence text is lowercased, whitespace-collapsed, and truncated to a
    fixed window before hashing so light paraphrase ("Assumption A-03 refers
    to ... GFT templates" vs. "A-03 references ... GFT templates") still
    collides on the operative phrase. The window is intentionally short — a
    longer window would let trailing reviewer commentary defeat the match.
    """
    category = str(finding.get('category') or '').strip().lower()
    severity = str(finding.get('severity') or '').strip().upper()
    fields_value = finding.get('fields') or []
    fields_norm = '/'.join(
        sorted(str(f).strip().lower() for f in fields_value if str(f).strip())
    )
    raw_evidence = str(finding.get('evidence') or '').strip().lower()
    normalized_evidence = ' '.join(raw_evidence.split())[
        :_FINGERPRINT_EVIDENCE_WINDOW
    ]
    payload = f'{category}|{severity}|{fields_norm}|{normalized_evidence}'
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]


def _annotate_persistent_findings(
    tool_context: ToolContext | None,
    stage: str,
    findings: list[dict[str, Any]],
) -> int:
    """Mark findings whose fingerprint matched the previous call's set.

    Mutates each entry of ``findings`` in place to add a boolean
    ``persistent`` key — ``True`` when this finding's fingerprint appeared in
    the immediately previous call against the same stage, ``False`` otherwise.
    Always writes the current call's fingerprints back to state, replacing
    the previous set so the next call only compares against this run.

    Without a ``tool_context``, persistence cannot be tracked across calls —
    every finding is annotated ``persistent=False`` and the function returns
    zero. The returned int is the count of persistent findings, used for
    logging and for the summary's severity-line tag.
    """
    current_fingerprints = [_finding_fingerprint(f) for f in findings]

    if tool_context is None:
        for f in findings:
            f['persistent'] = False
        return 0

    seen_state = tool_context.state.get(_FINDINGS_SEEN_STATE_KEY) or {}
    if not isinstance(seen_state, dict):
        seen_state = {}
    previous = set(seen_state.get(stage) or [])

    persistent_count = 0
    for f, fp in zip(findings, current_fingerprints):
        is_persistent = fp in previous
        f['persistent'] = is_persistent
        if is_persistent:
            persistent_count += 1

    seen_state[stage] = sorted(set(current_fingerprints))
    tool_context.state[_FINDINGS_SEEN_STATE_KEY] = seen_state
    return persistent_count


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

    Mechanical errors come first because they govern ``passed``. Reviewer
    findings (semantic + coverage) follow with severity-aware guidance:

    - **BLOCKER present** → instruct the agent to address BLOCKERs before
      re-validating. MAJOR/MINOR follow into the revision tracker.
    - **No BLOCKER, attempts under cap** → instruct the agent that
      MAJOR/MINOR findings are advisory because mechanical passed; warn
      that the reviewers are non-deterministic and chasing residual
      findings will not converge.
    - **No BLOCKER, attempts at or beyond cap** → explicit stop
      instruction. Re-validation has hit the cap defined by
      ``_MAX_PASSED_ATTEMPTS_PER_STAGE``; further calls only burn budget
      without making the diff stable.

    The cap-aware branch exists because the previous unconditional
    "Address BLOCKER and MAJOR before re-validating" line was being read
    by the agent as a permanent instruction and produced 20+ consecutive
    re-validations on a passing payload in production.

    ``review_metadata`` arrives as ``{'semantic': {...}, 'coverage': {...}}``
    after the parallel-pass refactor. The "did not run" branch reports
    each pass independently so the agent can see which signal is missing.
    """
    lines: list[str] = []

    blockers = sum(1 for f in findings if f.get('severity') == 'BLOCKER')
    majors = sum(1 for f in findings if f.get('severity') == 'MAJOR')
    minors = sum(1 for f in findings if f.get('severity') == 'MINOR')
    persistent_count = sum(1 for f in findings if f.get('persistent'))
    persistent_blockers = sum(
        1
        for f in findings
        if f.get('severity') == 'BLOCKER' and f.get('persistent')
    )
    all_blockers_persistent_at_cap = (
        blockers > 0
        and persistent_blockers == blockers
        and passed_attempts >= _MAX_PASSED_ATTEMPTS_PER_STAGE
    )

    # When semantic BLOCKER findings exist, the summary MUST lead with a
    # STOP directive — otherwise the mechanical pass message ("Mechanical
    # validation passed...") is read by the agent as a green light and the
    # SKILL.md fix loop is skipped. Production runs have shown agents parsing
    # the leading sentence as authoritative and ignoring the BLOCKER section
    # further down. The directive is split into two variants so the persistent-
    # at-cap calibration-error case (where retrying is the wrong action) does
    # NOT tell the agent to fix.
    if blockers > 0:
        if all_blockers_persistent_at_cap:
            lines.append(
                f'STOP — {blockers} BLOCKER finding(s) re-appeared after the '
                f'maximum fix attempts '
                f'({passed_attempts}/{_MAX_PASSED_ATTEMPTS_PER_STAGE}). This '
                'is almost always reviewer calibration error rather than a '
                'real defect: BLOCKER severity assigned to a finding whose '
                'cited anchors actually contain a disambiguation clause '
                '("except for [in-scope item]" / "exceto" / "salvo") that '
                'the reviewer ignored. Verify each cited OOS or in-scope '
                'item LITERALLY in the payload — read every anchor to its '
                'last word. If a disambiguation clause is present, degrade '
                'the finding to MAJOR for the Phase 3 revision tracker. Do '
                'NOT retry the fix loop.'
            )
        else:
            lines.append(
                f'STOP — {blockers} BLOCKER finding(s) present. Per '
                'SKILL.md Phase 2 Step 1.5 / Phase 3 Step 1, you MUST apply '
                'the incremental-edit rule to fix every BLOCKER finding '
                'before: (a) re-validating, (b) calling any other tool, '
                '(c) presenting any content to the user. Mechanical '
                'validation passing does NOT override semantic BLOCKER '
                'findings. See [BLOCKER] entries below for the specific '
                'anchors to address.'
            )
        lines.append('')

    if result.passed and not result.warnings:
        if blockers > 0:
            lines.append(
                'Mechanical validation passed (0 errors, 0 warnings) — '
                'BLOCKER findings above still require fix.'
            )
        else:
            lines.append(
                'All structural checks passed — content is ready for user review.'
            )
    elif result.passed:
        warning_lead = (
            f'Mechanical validation passed (0 errors, {len(result.warnings)} '
            'warning(s)) — BLOCKER findings above still require fix. Warning '
            'details:'
            if blockers > 0
            else (
                f'Mechanical validation passed (0 errors, {len(result.warnings)} '
                'warning(s)) — consider fixing the warnings before '
                'presenting to the user:'
            )
        )
        lines.append(warning_lead)
        lines.extend(f'  - {w}' for w in result.warnings)
    else:
        lines.append(
            f'{len(result.errors)} mechanical error(s) must be fixed before '
            'document generation.'
        )
        lines.extend(f'  - {e}' for e in result.errors)
        if result.warnings:
            lines.append('')
            lines.append(f'Additionally, {len(result.warnings)} warning(s):')
            lines.extend(f'  - {w}' for w in result.warnings)

    if findings:
        lines.append('')
        severity_summary = (
            f'Reviewers surfaced {len(findings)} finding(s) '
            f'(BLOCKER: {blockers}, MAJOR: {majors}, MINOR: {minors}).'
        )

        persistent_note = (
            f' {persistent_count} re-appeared from the previous call '
            '(marked [persistent] below). For each one the generator '
            'already had a chance to fix it; treat as residual reviewer '
            'noise — degrade to MINOR for the Phase 3 revision tracker '
            'and do NOT attempt to fix again.'
            if persistent_count > 0
            else ''
        )

        # The top-of-summary STOP directive already covered the BLOCKER
        # routing (standard vs persistent-at-cap calibration error). The
        # branches below provide the severity counts, persistent-finding
        # context, and the routing advice for the non-BLOCKER cases.
        # ``all_blockers_persistent_at_cap`` was computed at the top of the
        # function and is reused here.
        if all_blockers_persistent_at_cap:
            lines.append(
                f'{severity_summary} All {blockers} BLOCKER finding(s) '
                're-appeared after the maximum fix attempts '
                f'({passed_attempts}/{_MAX_PASSED_ATTEMPTS_PER_STAGE}). The '
                'generator could not resolve them in repeated tries, which '
                'most commonly indicates reviewer calibration error — '
                'BLOCKER severity assigned to findings whose cited anchors '
                'actually contain a disambiguation clause '
                '("except for [in-scope item]" / "exceto" / "salvo") the '
                'reviewer overlooked. Verify the cited OOS items literally '
                'in the payload; if a disambiguation clause is present, '
                'degrade the finding to MAJOR for the Phase 3 revision '
                'tracker and proceed — do NOT retry. MAJOR and MINOR may '
                'also flow into the revision tracker.'
            )
        elif blockers > 0:
            lines.append(
                f'{severity_summary}{persistent_note} Address BLOCKER '
                'findings before re-validating; MAJOR and MINOR may flow '
                'into the Phase 3 revision tracker.'
            )
        elif passed_attempts >= _MAX_PASSED_ATTEMPTS_PER_STAGE:
            lines.append(
                f'{severity_summary}{persistent_note} Maximum '
                're-validation attempts reached '
                f'({passed_attempts}/{_MAX_PASSED_ATTEMPTS_PER_STAGE}). No '
                'BLOCKER is present; remaining findings are advisory and the '
                'reviewers are non-deterministic, so re-validation will '
                'surface different findings each call. Record any remaining '
                'concerns for the Phase 3 revision tracker and proceed — do '
                'NOT call this tool again unless you make structural changes '
                'that warrant fresh mechanical validation.'
            )
        else:
            lines.append(
                f'{severity_summary}{persistent_note} No BLOCKER findings — '
                'these are advisory. Mechanical validation passed. The '
                'reviewers are non-deterministic, so chasing residual '
                'MAJOR/MINOR findings can produce different results on each '
                'call; apply the max-4-fix-attempts rule per finding (per '
                'SKILL.md), then proceed without re-validating to chase '
                'residuals.'
            )

        for f in findings:
            evidence = (f.get('evidence') or '').replace('\n', ' ').strip()
            if len(evidence) > 200:
                evidence = evidence[:200].rstrip() + '…'
            persistent_tag = ' [persistent]' if f.get('persistent') else ''
            lines.append(
                f"  - [{f.get('severity')}]{persistent_tag} {f.get('id')} "
                f"({f.get('category')}): {evidence}"
            )

    # Always surface a reviewer that didn't run, regardless of findings from
    # the surviving pass. The agent needs to know which signal is missing so
    # it can decide whether to retry validation later.
    for pass_name in ('semantic', 'coverage'):
        meta = review_metadata.get(pass_name) or {}
        if meta.get('ran') is False:
            reason = meta.get('fallback_reason') or 'unknown'
            lines.append('')
            lines.append(
                f'{pass_name.capitalize()} reviewer did not run '
                f'(reason: {reason}); mechanical validation above is '
                'authoritative for this pass.'
            )

    return '\n'.join(lines)
