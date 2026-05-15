"""Gate-decision tests for the ``resolution_mode`` recalibration.

The aggregator must split the legacy binary ``requires_human_review``
flag into a four-valued ``resolution_mode`` taxonomy and use it as the
sole human-review trigger. Severity (BLOCKER / MAJOR / MINOR) is no
longer allowed to escalate a finding to ``needs_human_review`` — the
QualityLoopAgent depends on this so it can invoke ``revision_agent``
on auto-fixable BLOCKERs.

Each test below maps to one of the failure classes the reviewer
enumerated:

- Out-of-source drift (a vendor / customer / tech not in the manifest).
- Missing manifest coverage (a manifest item not anchored in the SOW).
- Generic OOS conflicting with an explicitly included item.
- Real commercial / performance trade-off (legitimate escalation).
- Source conflict between two equally authoritative inputs.

The fixtures speak in abstract identifiers (``Vendor-A``, ``System-X``)
so no project-specific name (JBS, Salesforce, ...) is ever hardcoded;
the rule is "out-of-source-vs-manifest", not "vendor X is special".
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from app.sub_agents.validation.aggregator import (
    _calibrate,
    _decide_status,
    validation_aggregator_agent,
)
from app.sub_agents.validation.schema import (
    STATE_DET_RESULT,
    STATE_REPORT_PARTIAL,
    STATE_STAGE,
    DeterministicResult,
    Finding,
    ValidationReport,
    skill_findings_state_key,
)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _finding(
    *,
    fid: str = 'contradictions-001',
    skill: str = 'contradictions',
    category: str = 'fr_vs_nfr',
    severity: str = 'BLOCKER',
    confidence: float = 0.9,
    evidence: str = "FR-01 says 'X' but NFR-02 requires 'Y' contradicting it",
    fields: tuple[str, ...] = (
        'functional_requirements',
        'non_functional_requirements',
    ),
    resolution_mode: str = 'auto_fixable',
    requires_human_review: bool = False,
) -> dict[str, Any]:
    """Serialised Finding dict ready for state seeding."""
    return {
        'id': fid,
        'skill': skill,
        'category': category,
        'severity': severity,
        'confidence': confidence,
        'evidence': evidence,
        'recommendation': 'Apply the calibrated fix.',
        'fields': list(fields),
        'manifest_item_id': None,
        'persistent': False,
        'resolution_mode': resolution_mode,
        'requires_human_review': requires_human_review,
        'model_used': 'test-model',
    }


def _make_ctx(state: dict[str, Any]) -> MagicMock:
    ctx = MagicMock(name='InvocationContext')
    ctx.session.state = state
    ctx.invocation_id = 'inv-test'
    ctx.branch = 'test'
    return ctx


def _state_with(skill_findings: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    state: dict[str, Any] = {
        STATE_DET_RESULT: DeterministicResult(
            passed=True, error_count=0
        ).model_dump(),
        STATE_STAGE: 'full',
    }
    for name in (
        'coverage',
        'contradictions',
        'contractual_exposure',
        'disclosures',
        'semantic_quality',
    ):
        state[skill_findings_state_key(name)] = {
            'findings': skill_findings.get(name, [])
        }
    return state


async def _run(state: dict[str, Any]) -> ValidationReport:
    ctx = _make_ctx(state)
    async for _ in validation_aggregator_agent._run_async_impl(ctx):
        pass
    return ValidationReport.model_validate(state[STATE_REPORT_PARTIAL])


# ---------------------------------------------------------------------------
# 1. BLOCKER + auto_fixable → blocked (NOT needs_human_review)
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_blocker_auto_fixable_routes_to_blocked():
    """A confidently-quoted BLOCKER whose fix is a rewrite must reach the
    revision_agent — that is the whole reason the calibration exists.
    """
    state = _state_with(
        {
            'contradictions': [
                _finding(
                    severity='BLOCKER',
                    confidence=0.95,
                    resolution_mode='auto_fixable',
                ),
            ]
        }
    )
    report = await _run(state)

    assert report.overall_status == 'blocked'
    assert report.requires_human_review is False
    assert report.findings[0].severity == 'BLOCKER'
    assert report.findings[0].resolution_mode == 'auto_fixable'


# ---------------------------------------------------------------------------
# 2. MAJOR + auto_fixable → blocked
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_major_auto_fixable_does_not_escalate_to_human_review():
    state = _state_with(
        {
            'contractual_exposure': [
                _finding(
                    fid='contractual_exposure-001',
                    skill='contractual_exposure',
                    category='missing_change_request_gate',
                    severity='MAJOR',
                    resolution_mode='auto_fixable',
                ),
            ]
        }
    )
    report = await _run(state)

    assert report.overall_status == 'blocked'
    assert report.requires_human_review is False


# ---------------------------------------------------------------------------
# 3. decision_required → needs_human_review
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_decision_required_routes_to_needs_human_review():
    state = _state_with(
        {
            'contractual_exposure': [
                _finding(
                    fid='contractual_exposure-002',
                    skill='contractual_exposure',
                    severity='MAJOR',
                    category='subjective_nfr_target',
                    resolution_mode='decision_required',
                    requires_human_review=True,
                ),
            ]
        }
    )
    report = await _run(state)

    assert report.overall_status == 'needs_human_review'
    assert report.requires_human_review is True


# ---------------------------------------------------------------------------
# 4. source_conflict → needs_human_review
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_source_conflict_routes_to_needs_human_review():
    state = _state_with(
        {
            'contradictions': [
                _finding(
                    severity='BLOCKER',
                    resolution_mode='source_conflict',
                    requires_human_review=True,
                ),
            ]
        }
    )
    report = await _run(state)

    assert report.overall_status == 'needs_human_review'


@pytest.mark.unit
async def test_not_fixable_by_agent_routes_to_needs_human_review():
    state = _state_with(
        {
            'coverage': [
                _finding(
                    fid='coverage-001',
                    skill='coverage',
                    category='manifest_item_uncovered',
                    severity='MAJOR',
                    resolution_mode='not_fixable_by_agent',
                    requires_human_review=True,
                ),
            ]
        }
    )
    report = await _run(state)

    assert report.overall_status == 'needs_human_review'


# ---------------------------------------------------------------------------
# 5. Out-of-source entity / drift → blocked + auto_fixable
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_out_of_source_drift_is_auto_fixable_blocked():
    """A SOW mentions Vendor-A but Vendor-A is not in the manifest.
    The revision_agent's contract is "remove anything not in the
    manifest", so this must be ``blocked`` and ``auto_fixable``.
    """
    state = _state_with(
        {
            'semantic_quality': [
                _finding(
                    fid='semantic_quality-001',
                    skill='semantic_quality',
                    category='naming_drift',
                    severity='MAJOR',
                    evidence=(
                        "FR-04 mentions 'Vendor-A' as a partner system. "
                        "Vendor-A is not present in <manifest_residual>."
                    ),
                    resolution_mode='auto_fixable',
                ),
            ]
        }
    )
    report = await _run(state)

    assert report.overall_status == 'blocked'
    assert report.findings[0].resolution_mode == 'auto_fixable'


# ---------------------------------------------------------------------------
# 6. Missing manifest coverage → blocked + auto_fixable
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_missing_manifest_coverage_is_auto_fixable_blocked():
    """A manifest item (System-X) has no SOW anchor. Restore it = auto-fix."""
    state = _state_with(
        {
            'coverage': [
                _finding(
                    fid='coverage-002',
                    skill='coverage',
                    category='manifest_item_uncovered',
                    severity='MAJOR',
                    evidence=(
                        "Manifest item I-07: 'System-X integration'. No "
                        "FR/NFR/Architecture row references it."
                    ),
                    resolution_mode='auto_fixable',
                ),
            ]
        }
    )
    report = await _run(state)

    assert report.overall_status == 'blocked'
    assert report.findings[0].resolution_mode == 'auto_fixable'


# ---------------------------------------------------------------------------
# 7. Generic OOS vs explicitly-included scope → blocked + auto_fixable
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_generic_oos_conflicting_with_explicit_scope_is_auto_fixable():
    """OOS-03 says "any third-party System-Y"; FR-09 explicitly includes
    System-Y. Narrowing the OOS = auto-fixable.
    """
    state = _state_with(
        {
            'contradictions': [
                _finding(
                    fid='contradictions-001',
                    skill='contradictions',
                    category='scope_vs_oos',
                    severity='BLOCKER',
                    confidence=0.92,
                    evidence=(
                        "FR-09: 'integrate with System-Y'. "
                        "OOS-03: 'any third-party System-Y' (no exception)."
                    ),
                    resolution_mode='auto_fixable',
                ),
            ]
        }
    )
    report = await _run(state)

    assert report.overall_status == 'blocked'
    assert report.findings[0].resolution_mode == 'auto_fixable'


# ---------------------------------------------------------------------------
# 8. Real cost/performance trade-off → needs_human_review
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_real_cost_performance_tradeoff_is_decision_required():
    """A genuine NFR vs cost trade-off that requires customer approval is
    the ONLY legitimate path to ``needs_human_review``.
    """
    state = _state_with(
        {
            'contractual_exposure': [
                _finding(
                    fid='contractual_exposure-003',
                    skill='contractual_exposure',
                    category='subjective_nfr_target',
                    severity='MAJOR',
                    evidence=(
                        "NFR-02 commits to 'P95 < 2s' end-to-end; the "
                        "architecture uses Cloud Run with min_instances=0, "
                        "which cannot meet that target under cold starts. "
                        "Holding the target requires changing the "
                        "infrastructure cost profile."
                    ),
                    resolution_mode='decision_required',
                    requires_human_review=True,
                ),
            ]
        }
    )
    report = await _run(state)

    assert report.overall_status == 'needs_human_review'


# ---------------------------------------------------------------------------
# 9. Reconciliation: legacy requires_human_review=True is overridden
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_calibrate_forces_human_review_false_when_auto_fixable():
    """An LLM that emits ``auto_fixable`` + ``requires_human_review=True``
    by accident must NOT leak into the gate. ``auto_fixable`` wins.
    """
    raw = Finding(
        **_finding(
            severity='MAJOR',
            confidence=0.9,
            resolution_mode='auto_fixable',
            requires_human_review=True,  # mismatched on purpose
        )
    )

    calibrated = _calibrate([raw])

    assert len(calibrated) == 1
    assert calibrated[0].resolution_mode == 'auto_fixable'
    assert calibrated[0].requires_human_review is False


@pytest.mark.unit
def test_calibrate_forces_human_review_true_for_decision_required():
    """Symmetric: ``decision_required`` always materialises
    ``requires_human_review=True`` so the legacy channel stays consistent.
    """
    raw = Finding(
        **_finding(
            severity='MAJOR',
            confidence=0.9,
            resolution_mode='decision_required',
            requires_human_review=False,  # mismatched on purpose
        )
    )

    calibrated = _calibrate([raw])

    assert calibrated[0].resolution_mode == 'decision_required'
    assert calibrated[0].requires_human_review is True


@pytest.mark.unit
def test_decide_status_ignores_legacy_human_review_flag_when_auto_fixable():
    """Stronger invariant: even at the gate, a finding marked
    ``auto_fixable`` cannot reach ``needs_human_review`` no matter what
    the legacy flag claims. The aggregator first reconciles via
    ``_calibrate`` and then evaluates ``_decide_status``; this test
    exercises both layers end-to-end.
    """
    findings = _calibrate(
        [
            Finding(
                **_finding(
                    severity='BLOCKER',
                    confidence=0.95,
                    resolution_mode='auto_fixable',
                    requires_human_review=True,  # stale True
                )
            )
        ]
    )

    status, requires_human = _decide_status(
        DeterministicResult(passed=True, error_count=0),
        findings,
        skills_failed_critical=False,
    )

    assert status == 'blocked'
    assert requires_human is False


# ---------------------------------------------------------------------------
# Safe-inference guardrail — Manifest silence alone is NOT a reason to
# escalate. Gaps that the revision_agent can fill from style guides,
# architecture references, or standard consulting practice must reach
# the gate as ``blocked`` so the loop can apply the canonical fix.
# These tests cover the three reviewer-mandated canonical cases plus the
# inverse: ungrounded content (drift) being removed is also auto_fixable.
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_missing_handover_boundary_with_silent_manifest_is_auto_fixable():
    """Production deployment without an explicit handover exclusion. The
    Manifest does not need to spell out the consultancy-standard
    boundary; the style guide does. Fix = insert the canonical phrasing
    via the revision_agent, never ask the user.
    """
    state = _state_with(
        {
            'contractual_exposure': [
                _finding(
                    fid='contractual_exposure-001',
                    skill='contractual_exposure',
                    category='missing_handover_boundary',
                    severity='MAJOR',
                    evidence=(
                        "FRs commit to production deployment; no Assumption "
                        "or OOS item names ongoing-operations as Customer "
                        "responsibility post-handover."
                    ),
                    resolution_mode='auto_fixable',
                ),
            ]
        }
    )
    report = await _run(state)

    assert report.overall_status == 'blocked'
    assert report.requires_human_review is False
    assert report.findings[0].resolution_mode == 'auto_fixable'


@pytest.mark.unit
async def test_missing_change_request_policy_is_auto_fixable():
    """CR policy is mandated by the style guide for every SOW. Not in the
    Manifest is irrelevant — the revision_agent inserts the canonical
    clause from the references.
    """
    state = _state_with(
        {
            'contractual_exposure': [
                _finding(
                    fid='contractual_exposure-002',
                    skill='contractual_exposure',
                    category='missing_change_request_gate',
                    severity='MAJOR',
                    evidence=(
                        'No Assumption or Customer-roles entry contains '
                        'the Change Request gate language.'
                    ),
                    resolution_mode='auto_fixable',
                ),
            ]
        }
    )
    report = await _run(state)

    assert report.overall_status == 'blocked'
    assert report.findings[0].resolution_mode == 'auto_fixable'


@pytest.mark.unit
async def test_missing_consequence_clause_is_auto_fixable():
    """Style-guide-mandated consequence sentence on a customer obligation.
    Manifest silence on the consequence does not justify a user question.
    """
    state = _state_with(
        {
            'contractual_exposure': [
                _finding(
                    fid='contractual_exposure-003',
                    skill='contractual_exposure',
                    category='missing_consequence_clause',
                    severity='MAJOR',
                    evidence=(
                        "Assumption A-04: 'Customer must provide VPN access'. "
                        'No consequence sentence follows; canonical pattern '
                        'requires timeline-extension / cost / scope language.'
                    ),
                    resolution_mode='auto_fixable',
                ),
            ]
        }
    )
    report = await _run(state)

    assert report.overall_status == 'blocked'


@pytest.mark.unit
async def test_missing_ai_nondeterminism_disclosure_is_auto_fixable():
    """Style guide mandates the AI/ML non-determinism Assumption whenever
    the architecture mentions an AI service. No Manifest entry required.
    """
    state = _state_with(
        {
            'disclosures': [
                _finding(
                    fid='disclosures-001',
                    skill='disclosures',
                    category='missing_ai_nondeterminism_disclosure',
                    severity='MAJOR',
                    evidence=(
                        'Architecture mentions Vertex AI; Assumptions / '
                        'Customer roles / OOS do not contain the AI '
                        'non-determinism acknowledgment.'
                    ),
                    resolution_mode='auto_fixable',
                ),
            ]
        }
    )
    report = await _run(state)

    assert report.overall_status == 'blocked'


# ---------------------------------------------------------------------------
# Drift (inverse of safe inference) — ungrounded content must be REMOVED
# by the revision_agent, never escalated to a user question.
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_new_integration_without_source_is_auto_fixable_for_removal():
    """An Integrations row mentions System-Z, which is in neither the
    Manifest nor the architecture references. The correct fix is to
    drop the row (auto_fixable), not to ask the user whether System-Z
    is real.
    """
    state = _state_with(
        {
            'contradictions': [
                _finding(
                    fid='contradictions-001',
                    skill='contradictions',
                    category='architecture_vs_stack',
                    severity='BLOCKER',
                    confidence=0.93,
                    evidence=(
                        "architecture_integrations row I-04 references "
                        "'System-Z'. No FR, NFR, manifest item, or "
                        "architecture reference mentions System-Z."
                    ),
                    resolution_mode='auto_fixable',
                ),
            ]
        }
    )
    report = await _run(state)

    assert report.overall_status == 'blocked'
    assert report.findings[0].resolution_mode == 'auto_fixable'
    assert report.requires_human_review is False


@pytest.mark.unit
async def test_invented_quantitative_commitment_must_be_decision_required():
    """The inverse guardrail: when the SOW invents a commitment the
    revision_agent CANNOT safely confirm or invent away (e.g. an SLA
    target the Manifest never set), removing or keeping it is a
    commercial decision. This is decision_required, not auto_fixable.
    """
    state = _state_with(
        {
            'contractual_exposure': [
                _finding(
                    fid='contractual_exposure-004',
                    skill='contractual_exposure',
                    category='subjective_nfr_target',
                    severity='MAJOR',
                    evidence=(
                        "NFR-03 commits to '99.95% uptime'; the Manifest "
                        'is silent on availability targets and removing '
                        'the commitment narrows scope vs the customer '
                        'expectation set during discovery.'
                    ),
                    resolution_mode='decision_required',
                    requires_human_review=True,
                ),
            ]
        }
    )
    report = await _run(state)

    assert report.overall_status == 'needs_human_review'


# ---------------------------------------------------------------------------
# 10. Mixed batch — one decision_required is enough to escalate, but the
#     rest of the auto-fixable BLOCKERs must still be tracked in the report.
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_mixed_batch_escalates_but_preserves_auto_fixable_findings():
    state = _state_with(
        {
            'contradictions': [
                _finding(
                    fid='contradictions-001',
                    severity='BLOCKER',
                    confidence=0.92,
                    resolution_mode='auto_fixable',
                ),
            ],
            'contractual_exposure': [
                _finding(
                    fid='contractual_exposure-001',
                    skill='contractual_exposure',
                    category='subjective_nfr_target',
                    severity='MAJOR',
                    resolution_mode='decision_required',
                    requires_human_review=True,
                ),
            ],
        }
    )
    report = await _run(state)

    # The single decision_required finding wins the gate decision...
    assert report.overall_status == 'needs_human_review'
    # ...but the auto_fixable BLOCKER still appears in the findings list
    # so the user can see the full picture.
    auto_fixable = [
        f for f in report.findings if f.resolution_mode == 'auto_fixable'
    ]
    assert len(auto_fixable) == 1
    assert auto_fixable[0].severity == 'BLOCKER'
