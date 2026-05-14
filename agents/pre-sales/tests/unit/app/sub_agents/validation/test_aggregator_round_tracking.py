"""Tests for round tracking in ``ValidationAggregatorAgent``.

The aggregator owns three round-tracking responsibilities and these tests
exercise each one in isolation, plus the cross-round interaction that the
root prompt relies on for loop-convergence decisions:

- ``STATE_ROUND_COUNT`` increments on every run.
- ``STATE_PRIOR_BLOCKING_FINGERPRINTS`` round-trips between runs.
- Each ``Finding`` whose fingerprint reappears as blocking carries
  ``persistent=True``; severity migration (BLOCKER -> MAJOR) is tolerated.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from app.sub_agents.validation.aggregator import (
    _fingerprint,
    _is_blocking_finding,
    validation_aggregator_agent,
)
from app.sub_agents.validation.schema import (
    PRIOR_FINGERPRINTS_CAP,
    STATE_DET_RESULT,
    STATE_PRIOR_BLOCKING_FINGERPRINTS,
    STATE_REPORT_PARTIAL,
    STATE_ROUND_COUNT,
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
    fields: tuple[str, ...] = ('functional_requirements', 'non_functional_requirements'),
) -> dict[str, Any]:
    """Build a serialized finding dict ready to be stored in state."""
    return {
        'id': fid,
        'skill': skill,
        'category': category,
        'severity': severity,
        'confidence': confidence,
        'evidence': evidence,
        'recommendation': 'Align FR-01 and NFR-02.',
        'fields': list(fields),
        'manifest_item_id': None,
        'persistent': False,
        'requires_human_review': False,
        'model_used': 'test-model',
    }


def _make_ctx(state: dict[str, Any]) -> MagicMock:
    """Mock the ADK ``InvocationContext`` surface used by the aggregator."""
    ctx = MagicMock(name='InvocationContext')
    ctx.session.state = state
    ctx.invocation_id = 'inv-test'
    ctx.branch = 'test'
    return ctx


def _base_state(
    skill_payloads: dict[str, list[dict[str, Any]]] | None = None,
    *,
    det_result: dict[str, Any] | None = None,
    prior_fps: list[str] | None = None,
    round_count: int | None = None,
    stage: str = 'full',
) -> dict[str, Any]:
    """Build a state dict the aggregator can consume.

    All five skill keys are seeded (empty by default) so the critical-skill
    failure path is not triggered accidentally — that path is tested
    elsewhere and would otherwise mask round-tracking assertions.
    """
    state: dict[str, Any] = {
        STATE_DET_RESULT: det_result
        or DeterministicResult(passed=True, error_count=0).model_dump(),
        STATE_STAGE: stage,
    }
    payloads = skill_payloads or {}
    for name in (
        'coverage',
        'contradictions',
        'contractual_exposure',
        'disclosures',
        'semantic_quality',
    ):
        state[skill_findings_state_key(name)] = {
            'findings': payloads.get(name, [])
        }
    if prior_fps is not None:
        state[STATE_PRIOR_BLOCKING_FINGERPRINTS] = prior_fps
    if round_count is not None:
        state[STATE_ROUND_COUNT] = round_count
    return state


async def _run_aggregator(state: dict[str, Any]) -> ValidationReport:
    """Drive the aggregator and return the parsed partial report."""
    ctx = _make_ctx(state)
    async for _ in validation_aggregator_agent._run_async_impl(ctx):
        pass
    return ValidationReport.model_validate(state[STATE_REPORT_PARTIAL])


# ---------------------------------------------------------------------------
# _is_blocking_finding helper
# ---------------------------------------------------------------------------


def test_is_blocking_finding_includes_major_not_just_blocker():
    det = DeterministicResult(passed=True, error_count=0)
    blocker = Finding(**_finding(severity='BLOCKER'))
    major = Finding(**_finding(severity='MAJOR'))
    minor = Finding(**_finding(severity='MINOR'))

    assert _is_blocking_finding(blocker, det) is True
    assert _is_blocking_finding(major, det) is True
    assert _is_blocking_finding(minor, det) is False


# ---------------------------------------------------------------------------
# Round 1 — no prior state
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_first_round_initializes_counters_and_marks_nothing_persistent():
    state = _base_state(
        {'contradictions': [_finding(severity='BLOCKER')]}
    )
    report = await _run_aggregator(state)

    assert report.round_count == 1
    assert report.persistent_blocking_finding_count == 0
    assert report.new_blocking_finding_count == 1
    assert report.resolved_blocking_finding_count == 0
    assert all(f.persistent is False for f in report.findings)
    assert state[STATE_ROUND_COUNT] == 1
    assert len(state[STATE_PRIOR_BLOCKING_FINGERPRINTS]) == 1


@pytest.mark.unit
async def test_first_round_with_passed_status_writes_empty_prior_set():
    state = _base_state({'semantic_quality': [_finding(severity='MINOR')]})
    report = await _run_aggregator(state)

    assert report.overall_status == 'passed'
    assert report.round_count == 1
    assert state[STATE_PRIOR_BLOCKING_FINGERPRINTS] == []


# ---------------------------------------------------------------------------
# Round N+1 — prior fingerprints feed the persistence flag
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_second_round_marks_identical_finding_as_persistent():
    finding_payload = _finding(severity='BLOCKER')
    expected_fp = _fingerprint(Finding(**finding_payload))

    state = _base_state(
        {'contradictions': [finding_payload]},
        prior_fps=[expected_fp],
        round_count=1,
    )
    report = await _run_aggregator(state)

    assert report.round_count == 2
    assert report.persistent_blocking_finding_count == 1
    assert report.new_blocking_finding_count == 0
    assert report.resolved_blocking_finding_count == 0
    assert len(report.findings) == 1
    assert report.findings[0].persistent is True


@pytest.mark.unit
async def test_severity_downgrade_blocker_to_major_still_counts_as_persistent():
    """The fingerprint excludes severity, so a BLOCKER that the aggregator
    downgrades to MAJOR (low confidence) is the *same* finding across rounds.
    Persistence tracking must follow the fingerprint, not the severity label.
    """
    # Round 1: emitted as BLOCKER, downgraded to MAJOR by the calibrator
    # (confidence below floor triggers the downgrade).
    round1_payload = _finding(severity='BLOCKER', confidence=0.5)
    # The fingerprint is computed on the un-calibrated finding because the
    # calibrator only mutates severity (excluded from the fingerprint).
    fp = _fingerprint(Finding(**round1_payload))

    state = _base_state(
        {'contradictions': [round1_payload]},
        prior_fps=[fp],
        round_count=1,
    )
    report = await _run_aggregator(state)

    # Confirm the downgrade happened (this is the precondition for the test).
    assert report.findings[0].severity == 'MAJOR'
    # And the persistence flag survived the severity change.
    assert report.findings[0].persistent is True
    assert report.persistent_blocking_finding_count == 1


@pytest.mark.unit
async def test_resolved_finding_drops_from_prior_set_and_counter():
    """A finding present in round 1 but absent in round 2 must be counted as
    resolved, must not appear in the report, and must be evicted from state.
    """
    stale_payload = _finding(fid='contradictions-001', severity='BLOCKER')
    stale_fp = _fingerprint(Finding(**stale_payload))

    fresh_payload = _finding(
        fid='contradictions-002',
        category='scope_vs_oos',
        severity='BLOCKER',
        evidence="OOS-03 contradicts 'FR-07' which lists the same scope item",
        fields=('out_of_scope', 'functional_requirements'),
    )

    state = _base_state(
        {'contradictions': [fresh_payload]},
        prior_fps=[stale_fp],
        round_count=1,
    )
    report = await _run_aggregator(state)

    assert report.round_count == 2
    assert report.persistent_blocking_finding_count == 0
    assert report.new_blocking_finding_count == 1
    assert report.resolved_blocking_finding_count == 1
    assert report.findings[0].id == 'contradictions-002'
    assert report.findings[0].persistent is False
    # Prior set should now hold only the new fingerprint.
    assert state[STATE_PRIOR_BLOCKING_FINGERPRINTS] == [
        _fingerprint(Finding(**fresh_payload))
    ]


@pytest.mark.unit
async def test_minor_findings_never_enter_prior_blocking_set():
    state = _base_state({'semantic_quality': [_finding(severity='MINOR')]})
    await _run_aggregator(state)

    assert state[STATE_PRIOR_BLOCKING_FINGERPRINTS] == []


@pytest.mark.unit
async def test_round_count_increments_monotonically_across_runs():
    """Same state dict, two consecutive runs: counter goes 1 -> 2."""
    state = _base_state({'contradictions': [_finding(severity='MAJOR')]})

    report_round1 = await _run_aggregator(state)
    # Refresh the skill payload — without it, the aggregator would see the
    # same findings (real callers re-run skills between rounds).
    state[skill_findings_state_key('contradictions')] = {
        'findings': [_finding(severity='MAJOR')]
    }
    report_round2 = await _run_aggregator(state)

    assert report_round1.round_count == 1
    assert report_round2.round_count == 2
    # Same finding appeared in both rounds -> persistent on round 2.
    assert report_round2.persistent_blocking_finding_count == 1
    assert report_round2.findings[0].persistent is True


@pytest.mark.unit
async def test_prior_blocking_fingerprints_capped_at_limit():
    """Pathological case: aggregator must not write more than the cap."""
    many_findings = [
        _finding(
            fid=f'contradictions-{i:03d}',
            evidence=f"FR-{i:02d} and NFR-{i:02d} conflict over scope item {i}",
            fields=('functional_requirements', f'sentinel_{i}'),
        )
        for i in range(PRIOR_FINGERPRINTS_CAP + 5)
    ]
    state = _base_state({'contradictions': many_findings})
    await _run_aggregator(state)

    assert (
        len(state[STATE_PRIOR_BLOCKING_FINGERPRINTS]) == PRIOR_FINGERPRINTS_CAP
    )
