"""Step 4 of validation_critic — Python-only gate decision.

Reads ``state[STATE_DET_RESULT]`` and the five per-skill keys
(``state[app:skill_findings:{name}]``), deduplicates findings, calibrates
severity, decides ``overall_status``, and writes a partial
``ValidationReport`` (no summary, no next_action) to
``state[STATE_REPORT_PARTIAL]``. The summary skill never alters anything
produced here — it only fills the two text fields.

Severity calibration rules (executed in order, all deterministic):
- BLOCKER with `confidence < 0.7`                 → downgrade to MAJOR.
- BLOCKER without ≥ 2 quoted anchors in evidence  → downgrade to MAJOR.
Gate rules (strict, order matters):
- Critical skill failed                           → `needs_human_review`.
- Any finding flagged `requires_human_review`     → `needs_human_review`.
- Any deterministic error                         → `blocked`.
- Any BLOCKER (post-calibration)                  → `blocked`.
- Any MAJOR (post-calibration)                    → `blocked`.
- Otherwise                                       → `passed`.

`blocked` means the root/reviser may attempt an automatic correction.
`needs_human_review` means the system should not decide or correct without
human guidance.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from typing import AsyncGenerator, ClassVar, Iterable

import structlog
from google.adk.agents import BaseAgent
from google.adk.agents.base_agent_config import BaseAgentConfig
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions

from .schema import (
    SKILL_NAMES,
    STATE_DET_RESULT,
    STATE_REPORT_PARTIAL,
    STATE_STAGE,
    DeterministicResult,
    Finding,
    SkillRunMetadata,
    Status,
    ValidationReport,
    skill_findings_state_key,
)

logger = structlog.get_logger()

_SEVERITY_ORDER = {'BLOCKER': 0, 'MAJOR': 1, 'MINOR': 2}
_BLOCKER_CONFIDENCE_FLOOR = 0.7
_CRITICAL_SKILLS = frozenset({'coverage', 'contradictions'})


def _fingerprint(f: Finding) -> str:
    """Stable identity used to deduplicate findings across rounds/skills."""
    key = (
        f.skill,
        f.category,
        (f.evidence or '')[:240].strip().lower(),
        tuple(sorted(f.fields or [])),
        f.manifest_item_id or '',
    )
    return hashlib.sha256(repr(key).encode('utf-8')).hexdigest()[:16]


def _has_two_anchors(evidence: str) -> bool:
    """Heuristic for the BLOCKER evidence bar — count quoted SOW items."""
    if not evidence:
        return False
    anchors = sum(1 for token in ('FR-', 'NFR-', 'OOS-', 'A-', 'I-') if token in evidence)
    return anchors >= 2 or evidence.count("'") >= 4 or evidence.count('"') >= 4


def _normalize_findings(raw: Iterable[dict] | None) -> list[Finding]:
    """Drop malformed entries (wrong schema) rather than crashing the gate."""
    out: list[Finding] = []
    if not raw:
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            out.append(Finding.model_validate(item))
        except Exception as exc:
            logger.warning(
                'invalid_finding_dropped',
                error=str(exc),
                evidence_excerpt=(item.get('evidence') or '')[:120],
            )
    return out


def _calibrate(findings: list[Finding]) -> list[Finding]:
    """Apply severity downgrades in Python. Human review is explicit per finding."""
    calibrated: list[Finding] = []
    for f in findings:
        if f.severity == 'BLOCKER':
            if f.confidence < _BLOCKER_CONFIDENCE_FLOOR or not _has_two_anchors(
                f.evidence
            ):
                f = f.model_copy(update={'severity': 'MAJOR'})
        calibrated.append(f)
    return calibrated


def _dedupe(findings: list[Finding]) -> list[Finding]:
    seen: dict[str, Finding] = {}
    for f in findings:
        fp = _fingerprint(f)
        existing = seen.get(fp)
        if existing is None or f.confidence > existing.confidence:
            seen[fp] = f
    return sorted(
        seen.values(),
        key=lambda x: (_SEVERITY_ORDER.get(x.severity, 9), -x.confidence),
    )


def _decide_status(
    det: DeterministicResult,
    findings: list[Finding],
    skills_failed_critical: bool,
) -> tuple[Status, bool]:
    """Return (overall_status, requires_human_review). LLM never touches this.

    Order matters: human-review conditions are evaluated before blocked,
    because `blocked` invites automated correction while human-review means
    the system should stop and ask for guidance.
    """
    if skills_failed_critical:
        return 'needs_human_review', True
    if any(f.requires_human_review for f in findings):
        return 'needs_human_review', True
    if det.error_count > 0:
        return 'blocked', False
    if any(f.severity == 'BLOCKER' for f in findings):
        return 'blocked', False
    if any(f.severity == 'MAJOR' for f in findings):
        return 'blocked', False
    return 'passed', False


def _overall_score(det: DeterministicResult, findings: list[Finding]) -> float:
    """Heuristic 0..1 score for telemetry — never the gate."""
    score = 1.0
    score -= 0.4 * min(det.error_count, 2)
    score -= 0.05 * min(det.warning_count, 4)
    for f in findings:
        weight = {'BLOCKER': 0.4, 'MAJOR': 0.15, 'MINOR': 0.05}.get(
            f.severity, 0.0
        )
        score -= weight
    return max(0.0, min(1.0, score))


class ValidationAggregatorAgent(BaseAgent):
    """Python-only gate. Consumes deterministic + 5 skill outputs from state."""

    config_type: ClassVar[type[BaseAgentConfig]] = BaseAgentConfig

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        det_raw = state.get(STATE_DET_RESULT) or {}
        det = (
            DeterministicResult.model_validate(det_raw)
            if det_raw
            else DeterministicResult(passed=False, error_count=1)
        )

        all_findings: list[Finding] = []
        skills_run: list[SkillRunMetadata] = []
        skills_not_run: list[str] = []
        skills_failed_critical = False

        for name in SKILL_NAMES:
            payload = state.get(skill_findings_state_key(name)) or {}
            ran = bool(payload)
            raw_findings = (
                payload.get('findings')
                if isinstance(payload, dict)
                else None
            )
            findings = _normalize_findings(raw_findings)
            all_findings.extend(findings)

            if ran:
                skills_run.append(
                    SkillRunMetadata(
                        skill=name,
                        ran=True,
                        finding_count=len(findings),
                    )
                )
            else:
                skills_not_run.append(name)
                if name in _CRITICAL_SKILLS:
                    skills_failed_critical = True

        findings = _dedupe(_calibrate(all_findings))
        overall_status, requires_human = _decide_status(
            det, findings, skills_failed_critical
        )

        sev_counts = Counter(f.severity for f in findings)
        skill_counts = Counter(f.skill for f in findings)

        report = ValidationReport(
            overall_status=overall_status,
            overall_score=_overall_score(det, findings),
            requires_human_review=requires_human,
            deterministic=det,
            findings=findings,
            skills_run=skills_run,
            skills_not_run=skills_not_run,
            stage=state.get(STATE_STAGE) or 'full',
            blocker_count=sev_counts.get('BLOCKER', 0),
            major_count=sev_counts.get('MAJOR', 0),
            minor_count=sev_counts.get('MINOR', 0),
            findings_by_skill=dict(skill_counts),
        )

        partial = report.model_dump()
        state[STATE_REPORT_PARTIAL] = partial

        logger.info(
            'validation_aggregated',
            overall_status=overall_status,
            requires_human_review=requires_human,
            blocker=report.blocker_count,
            major=report.major_count,
            minor=report.minor_count,
            findings_by_skill=report.findings_by_skill,
            skills_not_run=skills_not_run,
        )

        # State-only event. Telemetry already in Cloud Logging via logger.info.
        # No Content so the gate decision does not surface to the chat — the
        # root agent reads `state[STATE_VALIDATION_RESULT]` after the
        # assembler runs and produces the user-facing reply.
        yield Event(
            invocation_id=ctx.invocation_id,
            author=self.name,
            branch=ctx.branch,
            actions=EventActions(state_delta={STATE_REPORT_PARTIAL: partial}),
        )


validation_aggregator_agent = ValidationAggregatorAgent(
    name='validation_aggregator_agent',
    description=(
        'Python-only gate that dedupes findings from the 5 skills, '
        'calibrates severity, decides overall_status, and writes the '
        'partial ValidationReport.'
    ),
)
