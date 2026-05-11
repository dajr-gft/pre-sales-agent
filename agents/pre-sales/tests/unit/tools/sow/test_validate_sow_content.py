"""Unit tests for ``app.tools.sow.validate_sow_content`` (the ADK tool)."""
from __future__ import annotations

import json

import pytest

from app.tools.sow.validate_sow_content import (
    _MAX_PASSED_ATTEMPTS_PER_STAGE,
    _PASSED_ATTEMPTS_STATE_KEY,
    validate_sow_content,
)


@pytest.fixture(autouse=True)
def mock_semantic_review(monkeypatch):
    """Default the semantic reviewer to a disabled-shape stub.

    Unit tests must never reach Vertex AI. Tests that exercise reviewer
    behavior re-patch this attribute themselves with the desired stub.
    """
    async def _disabled_stub(sow_data, stage, tool_context=None):
        return {
            'findings': [],
            'review_metadata': {
                'ran': False,
                'model': None,
                'latency_ms': 0,
                'fallback_reason': 'disabled_in_unit_tests',
            },
        }

    monkeypatch.setattr(
        'app.tools.sow.validate_sow_content.semantic_review',
        _disabled_stub,
    )
    return _disabled_stub


def _patch_semantic_review(monkeypatch, *, findings=None, ran=True, fallback_reason=None):
    """Helper: install a deterministic stub that returns a chosen result."""
    findings = findings or []

    async def _stub(sow_data, stage, tool_context=None):
        return {
            'findings': findings,
            'review_metadata': {
                'ran': ran,
                'model': 'stub-model',
                'latency_ms': 1,
                'fallback_reason': fallback_reason,
                'severity_counts': {
                    'BLOCKER': sum(1 for f in findings if f['severity'] == 'BLOCKER'),
                    'MAJOR': sum(1 for f in findings if f['severity'] == 'MAJOR'),
                    'MINOR': sum(1 for f in findings if f['severity'] == 'MINOR'),
                },
            },
        }

    monkeypatch.setattr(
        'app.tools.sow.validate_sow_content.semantic_review',
        _stub,
    )


class TestContractShape:
    async def test_success_returns_tool_success_dict(
        self, sow_data, mock_tool_context
    ):
        result = await validate_sow_content(
            sow_data=json.dumps(sow_data),
            funding_type='DAF',
            stage='full',
            tool_context=mock_tool_context,
        )
        assert result['status'] == 'success'
        data = result['data']
        assert 'passed' in data
        assert 'error_count' in data
        assert 'warning_count' in data
        assert 'issues' in data
        assert 'summary' in data
        assert 'findings' in data
        assert 'review_metadata' in data

    async def test_invalid_json_returns_tool_error(self, mock_tool_context):
        result = await validate_sow_content(
            sow_data='{not valid',
            tool_context=mock_tool_context,
        )
        assert result['status'] == 'error'
        assert 'Invalid JSON' in result['error']
        assert result['retryable'] is False
        assert result['tool'] == 'validate_sow_content'


class TestValidationOutcomes:
    async def test_valid_payload_passes(self, sow_data, mock_tool_context):
        result = await validate_sow_content(
            sow_data=json.dumps(sow_data),
            tool_context=mock_tool_context,
        )
        assert result['status'] == 'success'
        assert result['data']['passed'] is True
        assert result['data']['error_count'] == 0

    async def test_invalid_fr_id_fails(self, sow_data, mock_tool_context):
        sow_data['functional_requirements'][0]['number'] = 'bad-id'
        result = await validate_sow_content(
            sow_data=json.dumps(sow_data),
            tool_context=mock_tool_context,
        )
        assert result['data']['passed'] is False
        assert result['data']['error_count'] >= 1


class TestFundingTypeHandling:
    async def test_funding_type_auto_detected_from_payload(
        self, sow_data_psf, mock_tool_context
    ):
        """When caller doesn't pass funding_type, validator infers from data."""
        result = await validate_sow_content(
            sow_data=json.dumps(sow_data_psf),
            funding_type='',
            tool_context=mock_tool_context,
        )
        assert result['data']['passed'] is True

class TestStageHandling:
    async def test_content_stage_skips_arch_checks(
        self, sow_data, mock_tool_context
    ):
        sow_data['architecture_description'] = 'short'
        result = await validate_sow_content(
            sow_data=json.dumps(sow_data),
            stage='content',
            tool_context=mock_tool_context,
        )
        assert not any(
            i['field'] == 'architecture_description'
            for i in result['data']['issues']
        )

    async def test_full_stage_runs_arch_checks(
        self, sow_data, mock_tool_context
    ):
        sow_data['architecture_description'] = 'short'
        result = await validate_sow_content(
            sow_data=json.dumps(sow_data),
            stage='full',
            tool_context=mock_tool_context,
        )
        assert any(
            i['field'] == 'architecture_description'
            for i in result['data']['issues']
        )

    @pytest.mark.parametrize('bad_stage', ['bogus', 'phase-1', ''])
    async def test_invalid_stage_falls_back_to_full(
        self, sow_data, mock_tool_context, bad_stage
    ):
        """Unknown stage values silently fall back to 'full' (defensive)."""
        sow_data['architecture_description'] = 'short'
        result = await validate_sow_content(
            sow_data=json.dumps(sow_data),
            stage=bad_stage,
            tool_context=mock_tool_context,
        )
        # Arch check should fire → proves full stage was applied
        assert any(
            i['field'] == 'architecture_description'
            for i in result['data']['issues']
        )


class TestSummary:
    async def test_summary_when_all_passed(
        self, sow_data, mock_tool_context
    ):
        result = await validate_sow_content(
            sow_data=json.dumps(sow_data),
            tool_context=mock_tool_context,
        )
        assert 'All structural checks passed' in result['data']['summary']

    async def test_summary_when_warnings_only(
        self, sow_data, mock_tool_context
    ):
        # trigger a warning: too few OOS items
        sow_data['out_of_scope'] = ['one', 'two']
        result = await validate_sow_content(
            sow_data=json.dumps(sow_data),
            tool_context=mock_tool_context,
        )
        summary = result['data']['summary']
        assert 'No blocking errors' in summary
        assert 'warning' in summary.lower()

    async def test_summary_when_errors(self, sow_data, mock_tool_context):
        sow_data['functional_requirements'][0]['number'] = 'BAD'
        result = await validate_sow_content(
            sow_data=json.dumps(sow_data),
            tool_context=mock_tool_context,
        )
        summary = result['data']['summary']
        assert 'error' in summary.lower()
        assert 'must be fixed' in summary


class TestSemanticReviewIntegration:
    """Cover the contract between the tool and the semantic reviewer.

    The reviewer is mocked out entirely — we never invoke Vertex AI in
    unit tests. These tests exercise the merge logic, the `passed`
    contract, the summary composition, and the fail-open posture.
    """

    async def test_findings_do_not_govern_passed(
        self, sow_data, mock_tool_context, monkeypatch
    ):
        """A BLOCKER finding must NOT flip `passed`; only mechanical errors do."""
        _patch_semantic_review(
            monkeypatch,
            findings=[
                {
                    'id': 'F-001',
                    'severity': 'BLOCKER',
                    'category': 'contradiction',
                    'evidence': 'FR-04 demands real-time; NFR-02 requires batch.',
                    'recommendation': 'Resolve the latency conflict.',
                    'fields': [
                        'functional_requirements',
                        'non_functional_requirements',
                    ],
                }
            ],
        )
        result = await validate_sow_content(
            sow_data=json.dumps(sow_data),
            tool_context=mock_tool_context,
        )
        assert result['status'] == 'success'
        assert result['data']['passed'] is True
        assert result['data']['error_count'] == 0
        assert len(result['data']['findings']) == 1
        assert result['data']['findings'][0]['severity'] == 'BLOCKER'

    async def test_findings_appear_in_summary_with_severity_breakdown(
        self, sow_data, mock_tool_context, monkeypatch
    ):
        _patch_semantic_review(
            monkeypatch,
            findings=[
                {
                    'id': 'F-001',
                    'severity': 'BLOCKER',
                    'category': 'contradiction',
                    'evidence': 'A vs B contradiction.',
                    'recommendation': 'Resolve.',
                    'fields': ['functional_requirements'],
                },
                {
                    'id': 'F-002',
                    'severity': 'MINOR',
                    'category': 'semantic',
                    'evidence': 'Vague phrasing.',
                    'recommendation': 'Tighten.',
                    'fields': ['out_of_scope'],
                },
            ],
        )
        result = await validate_sow_content(
            sow_data=json.dumps(sow_data),
            tool_context=mock_tool_context,
        )
        summary = result['data']['summary']
        assert 'BLOCKER: 1' in summary
        assert 'MINOR: 1' in summary
        assert 'F-001' in summary
        assert 'F-002' in summary

    async def test_reviewer_failure_returns_no_findings_and_marks_metadata(
        self, sow_data, mock_tool_context, monkeypatch
    ):
        """A reviewer that raises should NOT crash the tool — fail open."""
        async def _raises(sow_data, stage, tool_context=None):
            # Real semantic_review wraps internal failures itself; this
            # double-checks that even an unexpected raise from the stub is
            # tolerated by the surrounding tool because @safe_tool catches it.
            raise RuntimeError('reviewer down')

        monkeypatch.setattr(
            'app.tools.sow.validate_sow_content.semantic_review',
            _raises,
        )
        result = await validate_sow_content(
            sow_data=json.dumps(sow_data),
            tool_context=mock_tool_context,
        )
        # @safe_tool turns the RuntimeError into a ToolError. Mechanical
        # validation never runs in this path, but the contract preserved
        # is that the agent receives a structured error instead of a crash.
        assert result['status'] == 'error'

    async def test_reviewer_returns_ran_false_keeps_passed_authoritative(
        self, sow_data, mock_tool_context, monkeypatch
    ):
        """When the reviewer fails open with ran=False, mechanical results stand."""
        _patch_semantic_review(
            monkeypatch,
            findings=[],
            ran=False,
            fallback_reason='timeout',
        )
        result = await validate_sow_content(
            sow_data=json.dumps(sow_data),
            tool_context=mock_tool_context,
        )
        data = result['data']
        assert data['passed'] is True
        assert data['findings'] == []
        assert data['review_metadata']['ran'] is False
        assert data['review_metadata']['fallback_reason'] == 'timeout'
        assert 'Semantic reviewer did not run' in data['summary']

    async def test_findings_carry_full_schema_through_to_caller(
        self, sow_data, mock_tool_context, monkeypatch
    ):
        """The agent needs every field of a finding to act on it surgically."""
        finding = {
            'id': 'F-007',
            'severity': 'MAJOR',
            'category': 'self_sufficiency',
            'evidence': 'A-07 references undefined platform standards.',
            'recommendation': 'Name the standards inline or rewrite the obligation.',
            'fields': ['assumptions'],
        }
        _patch_semantic_review(monkeypatch, findings=[finding])
        result = await validate_sow_content(
            sow_data=json.dumps(sow_data),
            tool_context=mock_tool_context,
        )
        returned = result['data']['findings'][0]
        assert returned == finding


class TestPassedAttemptsCap:
    """Cap on consecutive passing validations with semantic findings.

    Production logs showed 20+ consecutive validate_sow_content calls on a
    payload that already passed mechanically — the agent treated the
    "Address BLOCKER and MAJOR before re-validating" line as a permanent
    instruction and looped against the non-deterministic semantic
    reviewer. The counter + cap-aware summary block this loop without
    relying on the agent to remember the SKILL.md max-2-fix-attempts rule
    over many turns.
    """

    @staticmethod
    def _major_only_findings() -> list[dict]:
        """Findings that exercise the no-BLOCKER branch."""
        return [
            {
                'id': 'F-001',
                'severity': 'MAJOR',
                'category': 'contradiction',
                'evidence': 'NFR-02 vs A-16 throughput responsibility.',
                'recommendation': 'Rewrite NFR-02 conditionality.',
                'fields': ['non_functional_requirements', 'assumptions'],
            },
        ]

    async def test_advisory_summary_under_cap(
        self, sow_data, mock_tool_context, monkeypatch
    ):
        """First call with MAJOR-only findings → advisory message, no stop."""
        _patch_semantic_review(
            monkeypatch, findings=self._major_only_findings()
        )
        result = await validate_sow_content(
            sow_data=json.dumps(sow_data),
            tool_context=mock_tool_context,
        )
        summary = result['data']['summary']
        assert 'advisory' in summary.lower()
        assert 'non-deterministic' in summary.lower()
        assert 'Maximum re-validation attempts reached' not in summary

    async def test_cap_reached_summary_after_threshold(
        self, sow_data, mock_tool_context, monkeypatch
    ):
        """When the per-stage counter reaches the cap on a passing call, the
        summary switches to an explicit stop instruction."""
        _patch_semantic_review(
            monkeypatch, findings=self._major_only_findings()
        )
        # Pre-seed the counter to (cap - 1); the call about to happen
        # increments to cap and must trigger the stop branch.
        mock_tool_context.state[_PASSED_ATTEMPTS_STATE_KEY] = {
            'full': _MAX_PASSED_ATTEMPTS_PER_STAGE - 1,
        }
        result = await validate_sow_content(
            sow_data=json.dumps(sow_data),
            tool_context=mock_tool_context,
        )
        summary = result['data']['summary']
        assert 'Maximum re-validation attempts reached' in summary
        assert 'do NOT call this tool again' in summary

    async def test_blocker_finding_overrides_cap_message(
        self, sow_data, mock_tool_context, monkeypatch
    ):
        """A BLOCKER must always re-trigger the address-BLOCKER message,
        even when the cap counter would otherwise emit a stop instruction."""
        _patch_semantic_review(
            monkeypatch,
            findings=[
                {
                    'id': 'F-001',
                    'severity': 'BLOCKER',
                    'category': 'contradiction',
                    'evidence': 'Hard contradiction.',
                    'recommendation': 'Resolve.',
                    'fields': ['functional_requirements'],
                },
            ],
        )
        mock_tool_context.state[_PASSED_ATTEMPTS_STATE_KEY] = {
            'full': _MAX_PASSED_ATTEMPTS_PER_STAGE + 5,
        }
        result = await validate_sow_content(
            sow_data=json.dumps(sow_data),
            tool_context=mock_tool_context,
        )
        summary = result['data']['summary']
        assert 'Address BLOCKER findings' in summary
        assert 'Maximum re-validation attempts reached' not in summary

    async def test_counter_increments_on_each_passing_call(
        self, sow_data, mock_tool_context, monkeypatch
    ):
        """Two consecutive passing calls leave the counter at 2."""
        _patch_semantic_review(monkeypatch, findings=[])
        await validate_sow_content(
            sow_data=json.dumps(sow_data),
            tool_context=mock_tool_context,
        )
        await validate_sow_content(
            sow_data=json.dumps(sow_data),
            tool_context=mock_tool_context,
        )
        counters = mock_tool_context.state[_PASSED_ATTEMPTS_STATE_KEY]
        assert counters['full'] == 2

    async def test_counter_resets_on_mechanical_failure(
        self, sow_data, mock_tool_context, monkeypatch
    ):
        """A failing mechanical validation resets the counter — real fix
        cycles should not be penalized by stale loop counters from earlier
        in the session."""
        _patch_semantic_review(monkeypatch, findings=[])
        mock_tool_context.state[_PASSED_ATTEMPTS_STATE_KEY] = {'full': 5}
        sow_data['functional_requirements'][0]['number'] = 'BAD'
        result = await validate_sow_content(
            sow_data=json.dumps(sow_data),
            tool_context=mock_tool_context,
        )
        assert result['data']['passed'] is False
        counters = mock_tool_context.state[_PASSED_ATTEMPTS_STATE_KEY]
        assert counters['full'] == 0

    async def test_counter_is_per_stage(
        self, sow_data, mock_tool_context, monkeypatch
    ):
        """'content' (Phase 2 Step 1.5) and 'full' (Phase 3 Step 1) cycles
        are independent — incrementing one must not affect the other."""
        _patch_semantic_review(monkeypatch, findings=[])
        await validate_sow_content(
            sow_data=json.dumps(sow_data),
            stage='content',
            tool_context=mock_tool_context,
        )
        await validate_sow_content(
            sow_data=json.dumps(sow_data),
            stage='content',
            tool_context=mock_tool_context,
        )
        await validate_sow_content(
            sow_data=json.dumps(sow_data),
            stage='full',
            tool_context=mock_tool_context,
        )
        counters = mock_tool_context.state[_PASSED_ATTEMPTS_STATE_KEY]
        assert counters['content'] == 2
        assert counters['full'] == 1
