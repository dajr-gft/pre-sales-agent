"""Tests for the deterministic-issue → finding bridge in the aggregator.

Deterministic issues normally only gate pass/fail (``det.error_count``)
and are never repaired, because the quality loop routes ``findings`` to
the section agents and deterministic issues are not findings. The
timeline cross-reference check is the scoped exception: the aggregator
synthesises a routable ``Finding`` for each of its issues so the repair
can act on them.

These tests pin: (1) only the two timeline-reference categories are
bridged; (2) the synthesised finding is auto_fixable, MAJOR, skilled
``deterministic``, and anchored on ``fields=['timeline']`` so it routes
to delivery_plan; (3) plain deterministic issues are NOT bridged.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from app.sub_agents.quality_loop.agent import _partition_findings
from app.sub_agents.validation.aggregator import validation_aggregator_agent
from app.sub_agents.validation.schema import (
    STATE_DET_RESULT,
    STATE_REPORT_PARTIAL,
    STATE_STAGE,
    DeterministicIssue,
    DeterministicResult,
    ValidationReport,
    skill_findings_state_key,
)

pytestmark = pytest.mark.asyncio


def _make_ctx(state: dict[str, Any]) -> MagicMock:
    ctx = MagicMock(name='InvocationContext')
    ctx.session.state = state
    ctx.invocation_id = 'inv-test'
    ctx.branch = 'test'
    return ctx


def _state_with_det_issues(issues: list[DeterministicIssue]) -> dict[str, Any]:
    """State with NO LLM-skill findings and a deterministic result carrying
    ``issues`` — isolates the bridge."""
    det = DeterministicResult(
        passed=True, error_count=0, warning_count=len(issues), issues=issues,
    )
    state: dict[str, Any] = {
        STATE_DET_RESULT: det.model_dump(),
        STATE_STAGE: 'content',
    }
    for name in (
        'coverage', 'contradictions', 'contractual_exposure',
        'disclosures', 'semantic_quality',
    ):
        state[skill_findings_state_key(name)] = {'findings': []}
    return state


async def _run(state: dict[str, Any]) -> ValidationReport:
    async for _ in validation_aggregator_agent._run_async_impl(_make_ctx(state)):
        pass
    return ValidationReport.model_validate(state[STATE_REPORT_PARTIAL])


def _orphan_issue() -> DeterministicIssue:
    return DeterministicIssue(
        severity='warning', field='timeline',
        category='orphan_timeline_reference',
        message="Timeline outcome for 'Phase 1' references 'WS99', "
                'which is not a deliverable id in this SOW.',
        suggestion='Reference an existing deliverable id or remove the tag.',
    )


def _noncanonical_issue() -> DeterministicIssue:
    return DeterministicIssue(
        severity='warning', field='timeline',
        category='noncanonical_timeline_reference',
        message="Timeline outcome for 'Phase 1' references 'WS01', "
                "which is not the canonical deliverable id 'WS-01'.",
        suggestion="Use the exact deliverable id 'WS-01'.",
    )


async def test_timeline_ref_issue_becomes_routable_finding():
    report = await _run(_state_with_det_issues([_orphan_issue()]))

    det_findings = [f for f in report.findings if f.skill == 'deterministic']
    assert len(det_findings) == 1
    f = det_findings[0]
    assert f.category == 'orphan_timeline_reference'
    assert f.severity == 'MAJOR'
    assert f.resolution_mode == 'auto_fixable'
    assert f.fields == ['timeline']
    # A routable auto-fixable significant finding gates as blocked (so the
    # loop runs a repair round on it) rather than passing.
    assert report.overall_status == 'blocked'


async def test_both_timeline_categories_are_bridged():
    report = await _run(
        _state_with_det_issues([_orphan_issue(), _noncanonical_issue()])
    )
    cats = sorted(
        f.category for f in report.findings if f.skill == 'deterministic'
    )
    assert cats == [
        'noncanonical_timeline_reference', 'orphan_timeline_reference',
    ]


async def test_plain_deterministic_issue_is_not_bridged():
    plain = DeterministicIssue(
        severity='warning', field='assumptions',
        message='Assumption 3 has no consequence clause.',
        # no category -> not routable
    )
    report = await _run(_state_with_det_issues([plain]))
    assert [f for f in report.findings if f.skill == 'deterministic'] == []


async def test_bridged_finding_routes_to_delivery_plan():
    report = await _run(_state_with_det_issues([_orphan_issue()]))
    det_findings = [
        f.model_dump() for f in report.findings if f.skill == 'deterministic'
    ]
    by_section, mechanical = _partition_findings(
        det_findings, available_sections={'delivery_plan'},
    )
    assert 'delivery_plan' in by_section
    assert mechanical == []
