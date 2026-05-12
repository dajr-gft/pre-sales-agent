"""Unit tests for ``app.tools.sow.validate_sow_content`` (the ADK tool)."""
from __future__ import annotations

import json

import pytest

from app.tools.sow.validate_sow_content import (
    _FINDINGS_SEEN_STATE_KEY,
    _MAX_PASSED_ATTEMPTS_PER_STAGE,
    _PASSED_ATTEMPTS_STATE_KEY,
    _finding_fingerprint,
    validate_sow_content,
)


def _disabled_metadata(reason: str = 'disabled_in_unit_tests') -> dict:
    return {
        'ran': False,
        'model': None,
        'latency_ms': 0,
        'fallback_reason': reason,
    }


@pytest.fixture(autouse=True)
def mock_reviewers(monkeypatch):
    """Default both reviewer passes to a disabled-shape stub.

    Unit tests must never reach Vertex AI. Tests that exercise reviewer
    behavior re-patch via ``_patch_semantic_review`` / ``_patch_coverage_review``.
    """
    async def _semantic_stub(sow_data, stage, tool_context=None):
        return {'findings': [], 'review_metadata': _disabled_metadata()}

    async def _coverage_stub(sow_data, stage, tool_context=None):
        return {'findings': [], 'review_metadata': _disabled_metadata()}

    monkeypatch.setattr(
        'app.tools.sow.validate_sow_content.semantic_review', _semantic_stub
    )
    monkeypatch.setattr(
        'app.tools.sow.validate_sow_content.manifest_coverage_review',
        _coverage_stub,
    )


def _make_stub(findings, ran, fallback_reason):
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
    return _stub


def _patch_semantic_review(
    monkeypatch, *, findings=None, ran=True, fallback_reason=None
):
    """Install a deterministic stub for the semantic reviewer pass."""
    monkeypatch.setattr(
        'app.tools.sow.validate_sow_content.semantic_review',
        _make_stub(findings, ran, fallback_reason),
    )


def _patch_coverage_review(
    monkeypatch, *, findings=None, ran=True, fallback_reason=None
):
    """Install a deterministic stub for the manifest coverage reviewer pass."""
    monkeypatch.setattr(
        'app.tools.sow.validate_sow_content.manifest_coverage_review',
        _make_stub(findings, ran, fallback_reason),
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
        # The mechanical-pass line is phrased neutrally to avoid the
        # production failure where agents read "No blocking errors" as a
        # green light and skipped the SKILL.md fix loop on BLOCKER findings.
        assert 'Mechanical validation passed' in summary
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
    """Cover the contract between the tool and the reviewer passes.

    The reviewers are mocked out entirely — we never invoke Vertex AI in
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
        """When both reviewers fail open with ran=False, mechanical results stand."""
        _patch_semantic_review(
            monkeypatch,
            findings=[],
            ran=False,
            fallback_reason='timeout',
        )
        # coverage stub from autouse fixture is already ran=False
        result = await validate_sow_content(
            sow_data=json.dumps(sow_data),
            tool_context=mock_tool_context,
        )
        data = result['data']
        assert data['passed'] is True
        assert data['findings'] == []
        assert data['review_metadata']['semantic']['ran'] is False
        assert data['review_metadata']['semantic']['fallback_reason'] == 'timeout'
        # Summary names the failed pass so the agent knows which signal is missing.
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


class TestCoverageReviewIntegration:
    """Cover the manifest coverage pass integration with validate_sow_content."""

    async def test_coverage_findings_merge_with_semantic_findings(
        self, sow_data, mock_tool_context, monkeypatch
    ):
        """Both reviewers contributing — findings concatenate in returned list."""
        _patch_semantic_review(
            monkeypatch,
            findings=[
                {
                    'id': 'F-001',
                    'severity': 'MAJOR',
                    'category': 'contradiction',
                    'evidence': 'FR vs NFR mismatch.',
                    'recommendation': 'Resolve.',
                    'fields': ['functional_requirements'],
                },
            ],
        )
        _patch_coverage_review(
            monkeypatch,
            findings=[
                {
                    'id': 'F-001',
                    'severity': 'MAJOR',
                    'category': 'coverage',
                    'evidence': 'Manifest item I-007 has no SOW anchor.',
                    'recommendation': 'Add an FR or success criterion.',
                    'fields': ['functional_requirements', 'success_criteria'],
                },
            ],
        )
        result = await validate_sow_content(
            sow_data=json.dumps(sow_data),
            tool_context=mock_tool_context,
        )
        findings = result['data']['findings']
        assert len(findings) == 2
        categories = {f['category'] for f in findings}
        assert categories == {'contradiction', 'coverage'}

    async def test_coverage_only_findings_when_semantic_disabled(
        self, sow_data, mock_tool_context, monkeypatch
    ):
        """Coverage runs independently of semantic — its findings still surface."""
        _patch_coverage_review(
            monkeypatch,
            findings=[
                {
                    'id': 'F-001',
                    'severity': 'MAJOR',
                    'category': 'coverage',
                    'evidence': 'Manifest item I-003 has no anchor.',
                    'recommendation': 'Add a deliverable.',
                    'fields': ['deliverables'],
                },
            ],
        )
        # semantic stub from autouse fixture stays disabled (ran=False, [])
        result = await validate_sow_content(
            sow_data=json.dumps(sow_data),
            tool_context=mock_tool_context,
        )
        findings = result['data']['findings']
        assert len(findings) == 1
        assert findings[0]['category'] == 'coverage'

    async def test_metadata_carries_both_pass_results_separately(
        self, sow_data, mock_tool_context, monkeypatch
    ):
        _patch_semantic_review(monkeypatch, findings=[])
        _patch_coverage_review(monkeypatch, findings=[], ran=False, fallback_reason='no_manifest_available')
        result = await validate_sow_content(
            sow_data=json.dumps(sow_data),
            tool_context=mock_tool_context,
        )
        meta = result['data']['review_metadata']
        assert set(meta.keys()) == {'semantic', 'coverage'}
        assert meta['semantic']['ran'] is True
        assert meta['coverage']['ran'] is False
        assert meta['coverage']['fallback_reason'] == 'no_manifest_available'

    async def test_coverage_failure_does_not_block_semantic_findings(
        self, sow_data, mock_tool_context, monkeypatch
    ):
        """If coverage reviewer fails open, semantic findings still flow through."""
        _patch_semantic_review(
            monkeypatch,
            findings=[
                {
                    'id': 'F-001',
                    'severity': 'MAJOR',
                    'category': 'semantic',
                    'evidence': 'Vague language.',
                    'recommendation': 'Tighten.',
                    'fields': ['out_of_scope'],
                },
            ],
        )
        _patch_coverage_review(
            monkeypatch, findings=[], ran=False, fallback_reason='timeout'
        )
        result = await validate_sow_content(
            sow_data=json.dumps(sow_data),
            tool_context=mock_tool_context,
        )
        assert len(result['data']['findings']) == 1
        assert result['data']['findings'][0]['category'] == 'semantic'
        assert 'Coverage reviewer did not run' in result['data']['summary']


class TestPassedAttemptsCap:
    """Cap on consecutive passing validations with semantic findings.

    Production logs showed 20+ consecutive validate_sow_content calls on a
    payload that already passed mechanically — the agent treated the
    "Address BLOCKER and MAJOR before re-validating" line as a permanent
    instruction and looped against the non-deterministic semantic
    reviewer. The counter + cap-aware summary block this loop without
    relying on the agent to remember the SKILL.md max-4-fix-attempts rule
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


class TestFindingFingerprint:
    """Fingerprint construction — should be stable on identical findings and
    different on findings that diverge in any of (category, severity, fields,
    operative evidence text)."""

    def _finding(self, **overrides):
        base = {
            'id': 'F-001',
            'severity': 'MAJOR',
            'category': 'semantic',
            'evidence': 'A-03 references undefined GFT templates.',
            'recommendation': 'Name the standards inline.',
            'fields': ['assumptions'],
        }
        base.update(overrides)
        return base

    def test_identical_findings_share_fingerprint(self):
        a = self._finding()
        b = self._finding()
        assert _finding_fingerprint(a) == _finding_fingerprint(b)

    def test_id_does_not_affect_fingerprint(self):
        """Reviewers renumber findings between calls — IDs must not factor in."""
        a = self._finding(id='F-001')
        b = self._finding(id='F-042')
        assert _finding_fingerprint(a) == _finding_fingerprint(b)

    def test_recommendation_does_not_affect_fingerprint(self):
        """Same defect, paraphrased recommendation, still the same fingerprint."""
        a = self._finding(recommendation='Rewrite the obligation inline.')
        b = self._finding(recommendation='Name the standards in A-03 directly.')
        assert _finding_fingerprint(a) == _finding_fingerprint(b)

    def test_fields_order_does_not_affect_fingerprint(self):
        a = self._finding(fields=['assumptions', 'functional_requirements'])
        b = self._finding(fields=['functional_requirements', 'assumptions'])
        assert _finding_fingerprint(a) == _finding_fingerprint(b)

    def test_severity_change_changes_fingerprint(self):
        a = self._finding(severity='MAJOR')
        b = self._finding(severity='MINOR')
        assert _finding_fingerprint(a) != _finding_fingerprint(b)

    def test_category_change_changes_fingerprint(self):
        a = self._finding(category='semantic')
        b = self._finding(category='coverage')
        assert _finding_fingerprint(a) != _finding_fingerprint(b)

    def test_fields_change_changes_fingerprint(self):
        a = self._finding(fields=['assumptions'])
        b = self._finding(fields=['out_of_scope'])
        assert _finding_fingerprint(a) != _finding_fingerprint(b)

    def test_evidence_change_changes_fingerprint(self):
        a = self._finding(evidence='A-03 references undefined templates.')
        b = self._finding(evidence='B-07 references undefined registry.')
        assert _finding_fingerprint(a) != _finding_fingerprint(b)

    def test_whitespace_and_case_normalized(self):
        a = self._finding(
            evidence='A-03 REFERENCES   undefined GFT templates.'
        )
        b = self._finding(
            evidence='a-03 references undefined gft templates.'
        )
        assert _finding_fingerprint(a) == _finding_fingerprint(b)


class TestFindingPersistence:
    """Cross-call persistence tracking on the tool surface.

    The same fingerprint appearing in two consecutive calls means the
    generator already had a chance to fix it — the summary tags it
    [persistent] and the response carries ``persistent=True`` so the agent
    can degrade it to MINOR for the revision tracker without retrying.
    """

    @staticmethod
    def _finding(**overrides):
        base = {
            'id': 'F-001',
            'severity': 'MAJOR',
            'category': 'self_sufficiency',
            'evidence': 'A-03 references undefined GFT templates.',
            'recommendation': 'Name the templates inline.',
            'fields': ['assumptions'],
        }
        base.update(overrides)
        return base

    async def test_first_call_no_findings_are_persistent(
        self, sow_data, mock_tool_context, monkeypatch
    ):
        """Empty prior state → nothing can be persistent."""
        _patch_semantic_review(monkeypatch, findings=[self._finding()])
        result = await validate_sow_content(
            sow_data=json.dumps(sow_data),
            tool_context=mock_tool_context,
        )
        findings = result['data']['findings']
        assert len(findings) == 1
        assert findings[0]['persistent'] is False

    async def test_repeated_finding_marked_persistent_on_second_call(
        self, sow_data, mock_tool_context, monkeypatch
    ):
        finding = self._finding()
        _patch_semantic_review(monkeypatch, findings=[finding])

        await validate_sow_content(
            sow_data=json.dumps(sow_data),
            tool_context=mock_tool_context,
        )
        result = await validate_sow_content(
            sow_data=json.dumps(sow_data),
            tool_context=mock_tool_context,
        )
        findings = result['data']['findings']
        assert len(findings) == 1
        assert findings[0]['persistent'] is True

    async def test_renumbered_finding_still_marked_persistent(
        self, sow_data, mock_tool_context, monkeypatch
    ):
        """Reviewers re-number findings each call; persistence must survive that."""
        _patch_semantic_review(
            monkeypatch, findings=[self._finding(id='F-001')]
        )
        await validate_sow_content(
            sow_data=json.dumps(sow_data),
            tool_context=mock_tool_context,
        )
        _patch_semantic_review(
            monkeypatch, findings=[self._finding(id='F-007')]
        )
        result = await validate_sow_content(
            sow_data=json.dumps(sow_data),
            tool_context=mock_tool_context,
        )
        assert result['data']['findings'][0]['persistent'] is True

    async def test_new_finding_in_second_call_not_persistent(
        self, sow_data, mock_tool_context, monkeypatch
    ):
        _patch_semantic_review(monkeypatch, findings=[self._finding()])
        await validate_sow_content(
            sow_data=json.dumps(sow_data),
            tool_context=mock_tool_context,
        )
        new_finding = self._finding(
            evidence='Different defect: NFR-02 contradicts FR-04 latency.',
            fields=['non_functional_requirements'],
        )
        _patch_semantic_review(monkeypatch, findings=[new_finding])
        result = await validate_sow_content(
            sow_data=json.dumps(sow_data),
            tool_context=mock_tool_context,
        )
        assert result['data']['findings'][0]['persistent'] is False

    async def test_mixed_persistent_and_fresh_findings(
        self, sow_data, mock_tool_context, monkeypatch
    ):
        repeat = self._finding()
        _patch_semantic_review(monkeypatch, findings=[repeat])
        await validate_sow_content(
            sow_data=json.dumps(sow_data),
            tool_context=mock_tool_context,
        )

        fresh = self._finding(
            evidence='Different defect entirely about a different field.',
            fields=['out_of_scope'],
        )
        _patch_semantic_review(monkeypatch, findings=[repeat, fresh])
        result = await validate_sow_content(
            sow_data=json.dumps(sow_data),
            tool_context=mock_tool_context,
        )
        by_evidence = {
            f['evidence']: f['persistent'] for f in result['data']['findings']
        }
        assert by_evidence[repeat['evidence']] is True
        assert by_evidence[fresh['evidence']] is False

    async def test_persistence_is_per_stage(
        self, sow_data, mock_tool_context, monkeypatch
    ):
        """'content' stage's seen findings must not contaminate 'full' stage."""
        _patch_semantic_review(monkeypatch, findings=[self._finding()])
        await validate_sow_content(
            sow_data=json.dumps(sow_data),
            stage='content',
            tool_context=mock_tool_context,
        )
        result = await validate_sow_content(
            sow_data=json.dumps(sow_data),
            stage='full',
            tool_context=mock_tool_context,
        )
        assert result['data']['findings'][0]['persistent'] is False

    async def test_state_replaced_each_call_not_accumulated(
        self, sow_data, mock_tool_context, monkeypatch
    ):
        """State tracks immediately-previous call only — a finding that
        disappeared and re-appears in the third call is NOT persistent."""
        first = self._finding(evidence='First defect about A-03 templates.')
        _patch_semantic_review(monkeypatch, findings=[first])
        await validate_sow_content(
            sow_data=json.dumps(sow_data),
            tool_context=mock_tool_context,
        )
        interlude = self._finding(
            evidence='Entirely different intermediate defect about NFRs.',
            fields=['non_functional_requirements'],
        )
        _patch_semantic_review(monkeypatch, findings=[interlude])
        await validate_sow_content(
            sow_data=json.dumps(sow_data),
            tool_context=mock_tool_context,
        )
        _patch_semantic_review(monkeypatch, findings=[first])
        result = await validate_sow_content(
            sow_data=json.dumps(sow_data),
            tool_context=mock_tool_context,
        )
        # ``first`` was last seen 2 calls ago — state only carries the
        # previous call's fingerprints, so persistent must be False.
        assert result['data']['findings'][0]['persistent'] is False

    async def test_persistent_tag_appears_in_summary(
        self, sow_data, mock_tool_context, monkeypatch
    ):
        _patch_semantic_review(monkeypatch, findings=[self._finding()])
        await validate_sow_content(
            sow_data=json.dumps(sow_data),
            tool_context=mock_tool_context,
        )
        result = await validate_sow_content(
            sow_data=json.dumps(sow_data),
            tool_context=mock_tool_context,
        )
        summary = result['data']['summary']
        assert '[persistent]' in summary
        assert 're-appeared from the previous call' in summary

    async def test_no_persistent_note_when_nothing_persists(
        self, sow_data, mock_tool_context, monkeypatch
    ):
        _patch_semantic_review(monkeypatch, findings=[self._finding()])
        result = await validate_sow_content(
            sow_data=json.dumps(sow_data),
            tool_context=mock_tool_context,
        )
        summary = result['data']['summary']
        assert '[persistent]' not in summary
        assert 're-appeared from the previous call' not in summary

    async def test_state_stores_current_call_fingerprints(
        self, sow_data, mock_tool_context, monkeypatch
    ):
        finding = self._finding()
        _patch_semantic_review(monkeypatch, findings=[finding])
        await validate_sow_content(
            sow_data=json.dumps(sow_data),
            tool_context=mock_tool_context,
        )
        seen = mock_tool_context.state[_FINDINGS_SEEN_STATE_KEY]
        assert 'full' in seen
        assert seen['full'] == [_finding_fingerprint(finding)]


class TestBlockerStructuralSignals:
    """The agent must NOT need to parse summary text to detect that a BLOCKER
    is present. Two structural signals make the check mechanical:

    1. ``has_blocker_findings: bool`` on the returned data dict.
    2. A top-of-summary ``STOP — N BLOCKER finding(s) present...`` directive
       that appears BEFORE the mechanical pass line, so the leading sentence
       of the summary can never read as a green light when a BLOCKER exists.

    Production observation: agents parsed "No blocking errors" (the legacy
    mechanical-pass phrasing) as authoritative and skipped the SKILL.md
    BLOCKER fix loop. The structural signals close that failure mode.
    """

    @staticmethod
    def _blocker_finding(**overrides):
        base = {
            'id': 'F-001',
            'severity': 'BLOCKER',
            'category': 'contradiction',
            'evidence': 'FR-04 vs NFR-02 contradiction.',
            'recommendation': 'Resolve.',
            'fields': ['functional_requirements', 'non_functional_requirements'],
        }
        base.update(overrides)
        return base

    async def test_has_blocker_findings_true_when_blocker_present(
        self, sow_data, mock_tool_context, monkeypatch
    ):
        _patch_semantic_review(
            monkeypatch, findings=[self._blocker_finding()]
        )
        result = await validate_sow_content(
            sow_data=json.dumps(sow_data),
            tool_context=mock_tool_context,
        )
        assert result['data']['has_blocker_findings'] is True

    async def test_has_blocker_findings_false_when_only_majors_minors(
        self, sow_data, mock_tool_context, monkeypatch
    ):
        _patch_semantic_review(
            monkeypatch,
            findings=[
                self._blocker_finding(severity='MAJOR'),
                self._blocker_finding(severity='MINOR'),
            ],
        )
        result = await validate_sow_content(
            sow_data=json.dumps(sow_data),
            tool_context=mock_tool_context,
        )
        assert result['data']['has_blocker_findings'] is False

    async def test_has_blocker_findings_false_when_no_findings(
        self, sow_data, mock_tool_context
    ):
        """Empty findings list → flag must be False, not missing."""
        result = await validate_sow_content(
            sow_data=json.dumps(sow_data),
            tool_context=mock_tool_context,
        )
        assert result['data']['has_blocker_findings'] is False

    async def test_summary_leads_with_stop_when_blocker_present(
        self, sow_data, mock_tool_context, monkeypatch
    ):
        """The STOP directive must appear BEFORE the mechanical-pass line."""
        _patch_semantic_review(
            monkeypatch, findings=[self._blocker_finding()]
        )
        result = await validate_sow_content(
            sow_data=json.dumps(sow_data),
            tool_context=mock_tool_context,
        )
        summary = result['data']['summary']
        stop_idx = summary.find('STOP')
        mech_idx = summary.find('Mechanical validation passed')
        assert stop_idx != -1, 'STOP directive missing'
        assert mech_idx != -1, 'Mechanical-pass line missing'
        assert stop_idx < mech_idx, (
            'STOP directive must come BEFORE the mechanical-pass line '
            'so the agent does not read the mechanical pass as a green light.'
        )

    async def test_summary_stop_references_skill_md_protocol(
        self, sow_data, mock_tool_context, monkeypatch
    ):
        """The standard STOP message must name the SKILL.md sections so the
        agent has a concrete protocol pointer."""
        _patch_semantic_review(
            monkeypatch, findings=[self._blocker_finding()]
        )
        result = await validate_sow_content(
            sow_data=json.dumps(sow_data),
            tool_context=mock_tool_context,
        )
        summary = result['data']['summary']
        assert 'SKILL.md' in summary
        assert 'incremental-edit rule' in summary

    async def test_summary_no_stop_when_no_blocker(
        self, sow_data, mock_tool_context, monkeypatch
    ):
        """STOP directive must NOT appear when there are no BLOCKER findings,
        even with MAJOR/MINOR present — those route through the standard
        advisory message instead."""
        _patch_semantic_review(
            monkeypatch,
            findings=[
                self._blocker_finding(severity='MAJOR'),
                self._blocker_finding(severity='MINOR'),
            ],
        )
        result = await validate_sow_content(
            sow_data=json.dumps(sow_data),
            tool_context=mock_tool_context,
        )
        summary = result['data']['summary']
        # Use 'STOP — ' (with em-dash and space) to avoid matching incidental
        # uses of "stop" elsewhere in the summary text.
        assert 'STOP —' not in summary

    async def test_mechanical_pass_line_acknowledges_blocker_when_present(
        self, sow_data, mock_tool_context, monkeypatch
    ):
        """When mechanical passes AND a BLOCKER exists, the mechanical line
        must explicitly say the BLOCKER findings 'still require fix' — so the
        agent does not parse the mechanical line in isolation as a green light."""
        _patch_semantic_review(
            monkeypatch, findings=[self._blocker_finding()]
        )
        result = await validate_sow_content(
            sow_data=json.dumps(sow_data),
            tool_context=mock_tool_context,
        )
        summary = result['data']['summary']
        # The phrasing differs between "0 warnings" and "N warnings" but
        # both branches include this anchor when a BLOCKER is present.
        assert 'BLOCKER findings above still require fix' in summary


class TestPersistentBlockerAtCapSummary:
    """When every BLOCKER finding re-appears after the maximum fix attempts,
    the agent received two contradictory instructions from the legacy summary
    code: "Address BLOCKER findings before re-validating" AND "do NOT attempt
    to fix again" (from the persistent note). The dedicated branch resolves
    this by emitting a single coherent calibration-error message that points
    at the disambiguation pattern most likely to be the root cause.
    """

    @staticmethod
    def _blocker_finding(**overrides):
        base = {
            'id': 'F-001',
            'severity': 'BLOCKER',
            'category': 'contradiction',
            'evidence': (
                "OOS-7 excludes 'Terraform configurations of any kind'. "
                "Deliverable WS05 'BigQuery Infrastructure as Code' provides "
                'Terraform scripts.'
            ),
            'recommendation': 'Resolve the contradiction.',
            'fields': ['out_of_scope', 'deliverables'],
        }
        base.update(overrides)
        return base

    async def test_calibration_message_when_all_blockers_persistent_at_cap(
        self, sow_data, mock_tool_context, monkeypatch
    ):
        """Seed the persistent-findings state AND the passed-attempts counter
        so the upcoming call sees a re-appearing BLOCKER at exactly the cap."""
        finding = self._blocker_finding()
        mock_tool_context.state[_FINDINGS_SEEN_STATE_KEY] = {
            'full': [_finding_fingerprint(finding)],
        }
        mock_tool_context.state[_PASSED_ATTEMPTS_STATE_KEY] = {
            'full': _MAX_PASSED_ATTEMPTS_PER_STAGE - 1,
        }
        _patch_semantic_review(monkeypatch, findings=[finding])

        result = await validate_sow_content(
            sow_data=json.dumps(sow_data),
            tool_context=mock_tool_context,
        )
        summary = result['data']['summary']
        assert 're-appeared after the maximum fix attempts' in summary
        assert 'reviewer calibration error' in summary
        assert 'disambiguation clause' in summary
        # The contradictory advice must NOT appear in this branch.
        assert 'Address BLOCKER findings before re-validating' not in summary

    async def test_standard_blocker_message_when_blocker_is_fresh_at_cap(
        self, sow_data, mock_tool_context, monkeypatch
    ):
        """Cap reached but the BLOCKER is brand new — the agent has not yet
        had a chance to fix it. Standard 'Address BLOCKER' message must fire."""
        mock_tool_context.state[_PASSED_ATTEMPTS_STATE_KEY] = {
            'full': _MAX_PASSED_ATTEMPTS_PER_STAGE - 1,
        }
        _patch_semantic_review(
            monkeypatch, findings=[self._blocker_finding()]
        )

        result = await validate_sow_content(
            sow_data=json.dumps(sow_data),
            tool_context=mock_tool_context,
        )
        summary = result['data']['summary']
        assert 'Address BLOCKER findings' in summary
        assert 're-appeared after the maximum fix attempts' not in summary

    async def test_standard_blocker_message_when_persistent_but_under_cap(
        self, sow_data, mock_tool_context, monkeypatch
    ):
        """Persistent BLOCKER but cap not yet reached — give the agent one
        more chance to attempt the fix."""
        finding = self._blocker_finding()
        mock_tool_context.state[_FINDINGS_SEEN_STATE_KEY] = {
            'full': [_finding_fingerprint(finding)],
        }
        # Counter will increment to 1, still below cap of 2.
        _patch_semantic_review(monkeypatch, findings=[finding])

        result = await validate_sow_content(
            sow_data=json.dumps(sow_data),
            tool_context=mock_tool_context,
        )
        summary = result['data']['summary']
        assert 'Address BLOCKER findings' in summary
        assert 're-appeared after the maximum fix attempts' not in summary

    async def test_standard_blocker_message_when_some_blockers_are_fresh(
        self, sow_data, mock_tool_context, monkeypatch
    ):
        """Mixed BLOCKERs (one persistent, one new) at cap — do NOT silence
        the new BLOCKER. Falls through to the standard message so the agent
        attempts the fresh one."""
        persistent = self._blocker_finding(
            evidence='Persistent BLOCKER about OOS-7 vs WS05.',
        )
        fresh = self._blocker_finding(
            evidence='Fresh BLOCKER about something different entirely.',
        )
        mock_tool_context.state[_FINDINGS_SEEN_STATE_KEY] = {
            'full': [_finding_fingerprint(persistent)],
        }
        mock_tool_context.state[_PASSED_ATTEMPTS_STATE_KEY] = {
            'full': _MAX_PASSED_ATTEMPTS_PER_STAGE - 1,
        }
        _patch_semantic_review(monkeypatch, findings=[persistent, fresh])

        result = await validate_sow_content(
            sow_data=json.dumps(sow_data),
            tool_context=mock_tool_context,
        )
        summary = result['data']['summary']
        assert 'Address BLOCKER findings' in summary
        assert 're-appeared after the maximum fix attempts' not in summary
