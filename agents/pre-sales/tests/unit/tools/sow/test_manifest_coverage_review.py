"""Unit tests for ``app.tools.sow._manifest_coverage_review``.

The pass mirrors the fail-open shape of ``_semantic_review`` — every error
path returns ``{"findings": [], "review_metadata": {...}}`` so the wrapping
tool can preserve mechanical-validation authority. The Vertex client is
stubbed throughout; tests never make a real model call.
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.tools.sow import _manifest_coverage_review
from app.tools.sow._manifest_coverage_review import (
    CoverageFinding,
    _coerce_findings,
    _empty_result,
    _serialize_manifest_for_prompt,
    manifest_coverage_review,
)


# ---------------------------------------------------------------------------
# Helpers (parallel to test_semantic_review.py)
# ---------------------------------------------------------------------------


class _FakeAsyncModels:
    def __init__(self, behavior):
        self._behavior = behavior
        self.last_call_kwargs: dict | None = None

    async def generate_content(self, **kwargs):
        self.last_call_kwargs = kwargs
        result = self._behavior()
        if isinstance(result, BaseException):
            raise result
        return result


class _FakeAio:
    def __init__(self, models):
        self.models = models


class _FakeClient:
    def __init__(self, behavior):
        self.aio = _FakeAio(_FakeAsyncModels(behavior))


def _patch_client(monkeypatch, behavior):
    fake = _FakeClient(behavior)
    monkeypatch.setattr(
        _manifest_coverage_review, '_get_reviewer_client', lambda: fake
    )
    return fake


def _set_config(monkeypatch, **overrides):
    defaults = {
        'COVERAGE_REVIEW_ENABLED': True,
        'COVERAGE_REVIEW_MODEL': 'stub-model',
        'COVERAGE_REVIEW_TIMEOUT_S': 5.0,
        'COVERAGE_REVIEW_THINKING_BUDGET': 0,
    }
    defaults.update(overrides)
    for key, value in defaults.items():
        monkeypatch.setattr(
            _manifest_coverage_review.config, key, value, raising=False
        )


def _build_response(parsed=None, text=''):
    response = MagicMock()
    response.parsed = parsed
    response.text = text
    return response


def _ctx_with_manifest(manifest: dict | None) -> MagicMock:
    """Return a mock ToolContext whose state holds the given manifest."""
    ctx = MagicMock(name='ToolContext')
    ctx.state = {'extraction_manifest': manifest} if manifest else {}
    return ctx


# A minimal but realistic manifest for tests that need one.
_SAMPLE_MANIFEST: dict[str, Any] = {
    'manifest_version': '1.0',
    'extracted_items': [
        {
            'id': 'I-001',
            'category': 'NFRs',
            'value': 'Sub-second p95 latency',
            'value_detail': 'Customer requires p95 latency under 1 second.',
        },
        {
            'id': 'I-002',
            'category': 'Integrations',
            'value': 'Internal CRM',
            'value_detail': 'Read product master via REST.',
        },
    ],
    'gaps': {
        'hard_gaps': [],
        'pending_decisions': [],
        'ambiguities': [],
        'to_be_defined': [],
    },
}


# ---------------------------------------------------------------------------
# Early-exit paths
# ---------------------------------------------------------------------------


class TestEarlyExitPaths:
    async def test_disabled_by_config_returns_empty(self, sow_data, monkeypatch):
        _set_config(monkeypatch, COVERAGE_REVIEW_ENABLED=False)
        result = await manifest_coverage_review(sow_data, stage='full')
        assert result['findings'] == []
        assert result['review_metadata']['ran'] is False
        assert result['review_metadata']['fallback_reason'] == 'disabled_by_config'

    @pytest.mark.parametrize('bad_stage', ['', 'phase-1', 'review', 'all'])
    async def test_unsupported_stage_returns_empty(
        self, sow_data, monkeypatch, bad_stage
    ):
        _set_config(monkeypatch)
        result = await manifest_coverage_review(sow_data, stage=bad_stage)
        assert result['findings'] == []
        assert result['review_metadata']['ran'] is False
        assert result['review_metadata']['fallback_reason'].startswith(
            'unsupported_stage:'
        )

    async def test_missing_manifest_returns_empty_with_reason(
        self, sow_data, monkeypatch
    ):
        """Without a manifest, the pass has nothing to compare — short-circuit."""
        _set_config(monkeypatch)
        ctx = _ctx_with_manifest(None)
        result = await manifest_coverage_review(
            sow_data, stage='full', tool_context=ctx
        )
        assert result['findings'] == []
        assert result['review_metadata']['ran'] is False
        assert (
            result['review_metadata']['fallback_reason']
            == 'no_manifest_available'
        )

    async def test_no_tool_context_returns_no_manifest_available(
        self, sow_data, monkeypatch
    ):
        _set_config(monkeypatch)
        result = await manifest_coverage_review(sow_data, stage='full')
        assert result['review_metadata']['fallback_reason'] == 'no_manifest_available'


# ---------------------------------------------------------------------------
# Failure modes — every error path must fail open
# ---------------------------------------------------------------------------


class TestFailureModes:
    async def test_timeout_returns_empty_with_timeout_reason(
        self, sow_data, monkeypatch
    ):
        _set_config(monkeypatch, COVERAGE_REVIEW_TIMEOUT_S=0.01)
        _patch_client(monkeypatch, behavior=lambda: asyncio.TimeoutError())
        ctx = _ctx_with_manifest(_SAMPLE_MANIFEST)
        result = await manifest_coverage_review(
            sow_data, stage='full', tool_context=ctx
        )
        assert result['findings'] == []
        assert result['review_metadata']['ran'] is False
        assert result['review_metadata']['fallback_reason'] in (
            'timeout',
            'TimeoutError',
        )

    async def test_generic_exception_returns_empty(
        self, sow_data, monkeypatch
    ):
        _set_config(monkeypatch)
        _patch_client(monkeypatch, behavior=lambda: RuntimeError('vertex down'))
        ctx = _ctx_with_manifest(_SAMPLE_MANIFEST)
        result = await manifest_coverage_review(
            sow_data, stage='full', tool_context=ctx
        )
        assert result['findings'] == []
        assert result['review_metadata']['fallback_reason'] == 'RuntimeError'

    async def test_malformed_response_returns_empty(self, sow_data, monkeypatch):
        _set_config(monkeypatch)
        _patch_client(
            monkeypatch,
            behavior=lambda: _build_response(parsed=None, text='not-json{'),
        )
        ctx = _ctx_with_manifest(_SAMPLE_MANIFEST)
        result = await manifest_coverage_review(
            sow_data, stage='full', tool_context=ctx
        )
        assert result['findings'] == []
        assert (
            result['review_metadata']['fallback_reason'] == 'malformed_response'
        )


# ---------------------------------------------------------------------------
# Successful path
# ---------------------------------------------------------------------------


class TestSuccessfulReview:
    async def test_findings_pass_through_with_metadata(
        self, sow_data, monkeypatch
    ):
        _set_config(monkeypatch, COVERAGE_REVIEW_MODEL='gemini-test')
        finding = CoverageFinding(
            id='F-001',
            severity='MAJOR',
            category='coverage',
            evidence='Manifest item I-001 has no SOW anchor.',
            recommendation='Add an FR or success criterion.',
            fields=['functional_requirements', 'success_criteria'],
        )
        parsed = _manifest_coverage_review._CoverageOutput(findings=[finding])
        _patch_client(monkeypatch, behavior=lambda: _build_response(parsed=parsed))
        ctx = _ctx_with_manifest(_SAMPLE_MANIFEST)

        result = await manifest_coverage_review(
            sow_data, stage='full', tool_context=ctx
        )
        assert len(result['findings']) == 1
        assert result['findings'][0]['category'] == 'coverage'
        assert result['findings'][0]['severity'] == 'MAJOR'
        assert result['review_metadata']['ran'] is True
        assert result['review_metadata']['model'] == 'gemini-test'
        assert result['review_metadata']['severity_counts'] == {
            'BLOCKER': 0,
            'MAJOR': 1,
            'MINOR': 0,
        }

    async def test_text_fallback_parses_when_parsed_absent(
        self, sow_data, monkeypatch
    ):
        _set_config(monkeypatch)
        _patch_client(
            monkeypatch,
            behavior=lambda: _build_response(
                parsed=None,
                text=(
                    '{"findings": [{"id": "F-001", "severity": "MAJOR", '
                    '"category": "coverage", "evidence": "missing anchor", '
                    '"recommendation": "add deliverable", '
                    '"fields": ["deliverables"]}]}'
                ),
            ),
        )
        ctx = _ctx_with_manifest(_SAMPLE_MANIFEST)
        result = await manifest_coverage_review(
            sow_data, stage='full', tool_context=ctx
        )
        assert len(result['findings']) == 1
        assert result['findings'][0]['severity'] == 'MAJOR'

    async def test_empty_findings_list_is_a_valid_result(
        self, sow_data, monkeypatch
    ):
        _set_config(monkeypatch)
        parsed = _manifest_coverage_review._CoverageOutput(findings=[])
        _patch_client(monkeypatch, behavior=lambda: _build_response(parsed=parsed))
        ctx = _ctx_with_manifest(_SAMPLE_MANIFEST)
        result = await manifest_coverage_review(
            sow_data, stage='full', tool_context=ctx
        )
        assert result['findings'] == []
        assert result['review_metadata']['ran'] is True


class TestThinkingConfig:
    async def test_thinking_config_passed_when_budget_positive(
        self, sow_data, monkeypatch
    ):
        _set_config(monkeypatch, COVERAGE_REVIEW_THINKING_BUDGET=2048)
        parsed = _manifest_coverage_review._CoverageOutput(findings=[])
        fake = _patch_client(
            monkeypatch, behavior=lambda: _build_response(parsed=parsed)
        )
        ctx = _ctx_with_manifest(_SAMPLE_MANIFEST)
        await manifest_coverage_review(sow_data, stage='full', tool_context=ctx)

        kwargs = fake.aio.models.last_call_kwargs
        assert kwargs is not None
        cfg = kwargs['config']
        assert cfg.thinking_config is not None
        assert cfg.thinking_config.thinking_budget == 2048

    async def test_thinking_config_omitted_when_budget_zero(
        self, sow_data, monkeypatch
    ):
        _set_config(monkeypatch, COVERAGE_REVIEW_THINKING_BUDGET=0)
        parsed = _manifest_coverage_review._CoverageOutput(findings=[])
        fake = _patch_client(
            monkeypatch, behavior=lambda: _build_response(parsed=parsed)
        )
        ctx = _ctx_with_manifest(_SAMPLE_MANIFEST)
        await manifest_coverage_review(sow_data, stage='full', tool_context=ctx)

        kwargs = fake.aio.models.last_call_kwargs
        cfg = kwargs['config']
        assert cfg.thinking_config is None


class TestEmptyResult:
    def test_shape_is_canonical(self):
        result = _empty_result('disabled_by_config')
        assert set(result.keys()) == {'findings', 'review_metadata'}
        meta = result['review_metadata']
        assert meta['ran'] is False
        assert meta['fallback_reason'] == 'disabled_by_config'
        assert meta['latency_ms'] == 0


class TestCoerceFindings:
    def test_caps_at_eight_findings(self):
        oversize = [
            CoverageFinding(
                id=f'F-{i:03d}',
                severity='MINOR',
                category='coverage',
                evidence='evidence',
                recommendation='rec',
                fields=['out_of_scope'],
            )
            for i in range(1, 20)
        ]
        parsed = _manifest_coverage_review._CoverageOutput(findings=oversize)
        coerced = _coerce_findings(parsed)
        assert len(coerced) == 8

    def test_drops_malformed_dict_entries(self):
        raw = {
            'findings': [
                {
                    'id': 'F-001',
                    'severity': 'MAJOR',
                    'category': 'coverage',
                    'evidence': 'e',
                    'recommendation': 'r',
                    'fields': ['out_of_scope'],
                },
                # Bad category — pass produces only "coverage"
                {
                    'id': 'F-002',
                    'severity': 'MAJOR',
                    'category': 'contradiction',
                    'evidence': 'e',
                    'recommendation': 'r',
                    'fields': ['out_of_scope'],
                },
                {'id': 'F-003'},  # missing fields
            ]
        }
        coerced = _coerce_findings(raw)
        assert len(coerced) == 1
        assert coerced[0]['id'] == 'F-001'

    def test_unknown_input_returns_empty(self):
        assert _coerce_findings(None) == []
        assert _coerce_findings('garbage') == []
        assert _coerce_findings(42) == []


class TestSerializeManifest:
    def test_none_returns_none(self):
        assert _serialize_manifest_for_prompt(None) is None

    def test_non_dict_returns_none(self):
        assert _serialize_manifest_for_prompt('not-a-dict') is None
        assert _serialize_manifest_for_prompt([]) is None

    def test_dict_serialized_to_pretty_json(self):
        out = _serialize_manifest_for_prompt({'a': 1, 'b': [2, 3]})
        assert isinstance(out, str)
        # JSON shape is preserved verbatim — model needs the full structure.
        assert '"a": 1' in out
        assert '"b"' in out

    def test_unicode_preserved_for_pt_br_content(self):
        out = _serialize_manifest_for_prompt(
            {'value': 'Implementação não-funcional'}
        )
        assert 'Implementação não-funcional' in out
