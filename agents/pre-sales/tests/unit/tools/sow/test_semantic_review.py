"""Unit tests for ``app.tools.sow._semantic_review``.

These tests cover the fail-open posture of the reviewer: every error path
must return the canonical empty shape ``{"findings": [], "review_metadata":
{...}}`` so the wrapping tool can preserve mechanical-validation authority.

The Vertex AI client is replaced with stubs throughout — these tests never
make a real model call.
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.tools.sow import _semantic_review
from app.tools.sow._semantic_review import (
    Finding,
    _build_manifest_summary,
    _coerce_findings,
    _empty_result,
    semantic_review,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeAsyncModels:
    """Stub for ``client.aio.models``. ``generate_content`` is patched per test.

    Captures the last call's kwargs so tests can assert on the config that was
    passed to Gemini (e.g., thinking_config presence and budget).
    """

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
    """Replace ``_get_reviewer_client`` with a factory that returns a stubbed client."""
    fake = _FakeClient(behavior)
    monkeypatch.setattr(
        _semantic_review, '_get_reviewer_client', lambda: fake
    )
    return fake


def _set_config(monkeypatch, **overrides):
    """Override config attributes used by the reviewer for the duration of a test."""
    defaults = {
        'SEMANTIC_REVIEW_ENABLED': True,
        'SEMANTIC_REVIEW_MODEL': 'stub-model',
        'SEMANTIC_REVIEW_TIMEOUT_S': 5.0,
        'SEMANTIC_REVIEW_THINKING_BUDGET': 0,
    }
    defaults.update(overrides)
    for key, value in defaults.items():
        monkeypatch.setattr(_semantic_review.config, key, value, raising=False)


def _build_response(parsed=None, text=''):
    """Mimic the shape google.genai returns from generate_content."""
    response = MagicMock()
    response.parsed = parsed
    response.text = text
    return response


# ---------------------------------------------------------------------------
# Disabled / unsupported stage paths
# ---------------------------------------------------------------------------


class TestEarlyExitPaths:
    async def test_disabled_by_config_returns_empty(
        self, sow_data, monkeypatch
    ):
        _set_config(monkeypatch, SEMANTIC_REVIEW_ENABLED=False)
        result = await semantic_review(sow_data, stage='full')
        assert result['findings'] == []
        assert result['review_metadata']['ran'] is False
        assert (
            result['review_metadata']['fallback_reason']
            == 'disabled_by_config'
        )

    @pytest.mark.parametrize('bad_stage', ['', 'phase-1', 'review', 'all'])
    async def test_unsupported_stage_returns_empty(
        self, sow_data, monkeypatch, bad_stage
    ):
        _set_config(monkeypatch)
        result = await semantic_review(sow_data, stage=bad_stage)
        assert result['findings'] == []
        assert result['review_metadata']['ran'] is False
        assert result['review_metadata']['fallback_reason'].startswith(
            'unsupported_stage:'
        )


# ---------------------------------------------------------------------------
# Failure modes — every error path must fail open
# ---------------------------------------------------------------------------


class TestFailureModes:
    async def test_timeout_returns_empty_with_timeout_reason(
        self, sow_data, monkeypatch
    ):
        _set_config(monkeypatch)

        async def _slow():
            await asyncio.sleep(10)
            return _build_response()

        # asyncio.wait_for inside semantic_review fires the timeout. Use a
        # very small timeout to keep the test fast.
        _set_config(monkeypatch, SEMANTIC_REVIEW_TIMEOUT_S=0.01)
        _patch_client(
            monkeypatch,
            behavior=lambda: asyncio.TimeoutError(),
        )

        # The stub's behavior raises TimeoutError synchronously instead of
        # actually sleeping — same observable result for the caller.
        result = await semantic_review(sow_data, stage='full')
        assert result['findings'] == []
        assert result['review_metadata']['ran'] is False
        # Timeout in semantic_review propagates from asyncio.wait_for OR
        # from the stubbed coroutine raising — both surface as a fallback.
        assert result['review_metadata']['fallback_reason'] in (
            'timeout',
            'TimeoutError',
        )

    async def test_generic_exception_returns_empty(
        self, sow_data, monkeypatch
    ):
        _set_config(monkeypatch)
        _patch_client(
            monkeypatch,
            behavior=lambda: RuntimeError('vertex unavailable'),
        )
        result = await semantic_review(sow_data, stage='full')
        assert result['findings'] == []
        assert result['review_metadata']['ran'] is False
        assert (
            result['review_metadata']['fallback_reason'] == 'RuntimeError'
        )
        assert result['review_metadata']['latency_ms'] >= 0

    async def test_malformed_response_returns_empty(
        self, sow_data, monkeypatch
    ):
        _set_config(monkeypatch)
        _patch_client(
            monkeypatch,
            behavior=lambda: _build_response(parsed=None, text='not-json{'),
        )
        result = await semantic_review(sow_data, stage='full')
        assert result['findings'] == []
        assert result['review_metadata']['ran'] is False
        assert (
            result['review_metadata']['fallback_reason']
            == 'malformed_response'
        )


# ---------------------------------------------------------------------------
# Successful path
# ---------------------------------------------------------------------------


class TestSuccessfulReview:
    async def test_findings_pass_through_with_metadata(
        self, sow_data, monkeypatch
    ):
        _set_config(monkeypatch, SEMANTIC_REVIEW_MODEL='gemini-test')
        finding = Finding(
            id='F-001',
            severity='BLOCKER',
            category='contradiction',
            evidence='FR-04 vs NFR-02 latency conflict.',
            recommendation='Resolve the conflict.',
            fields=['functional_requirements', 'non_functional_requirements'],
        )
        parsed = _semantic_review._ReviewerOutput(findings=[finding])
        _patch_client(
            monkeypatch, behavior=lambda: _build_response(parsed=parsed)
        )

        result = await semantic_review(sow_data, stage='full')
        assert len(result['findings']) == 1
        assert result['findings'][0]['id'] == 'F-001'
        assert result['findings'][0]['severity'] == 'BLOCKER'
        assert result['review_metadata']['ran'] is True
        assert result['review_metadata']['model'] == 'gemini-test'
        assert result['review_metadata']['fallback_reason'] is None
        assert result['review_metadata']['severity_counts'] == {
            'BLOCKER': 1,
            'MAJOR': 0,
            'MINOR': 0,
        }

    async def test_text_fallback_parses_when_parsed_absent(
        self, sow_data, monkeypatch
    ):
        """If the SDK didn't auto-coerce ``parsed``, ``text`` is parsed as JSON."""
        _set_config(monkeypatch)
        _patch_client(
            monkeypatch,
            behavior=lambda: _build_response(
                parsed=None,
                text=(
                    '{"findings": [{"id": "F-001", "severity": "MINOR", '
                    '"category": "semantic", "evidence": "vague", '
                    '"recommendation": "tighten", '
                    '"fields": ["out_of_scope"]}]}'
                ),
            ),
        )
        result = await semantic_review(sow_data, stage='full')
        assert len(result['findings']) == 1
        assert result['findings'][0]['severity'] == 'MINOR'

    async def test_empty_findings_list_is_a_valid_result(
        self, sow_data, monkeypatch
    ):
        _set_config(monkeypatch)
        parsed = _semantic_review._ReviewerOutput(findings=[])
        _patch_client(
            monkeypatch, behavior=lambda: _build_response(parsed=parsed)
        )
        result = await semantic_review(sow_data, stage='full')
        assert result['findings'] == []
        assert result['review_metadata']['ran'] is True


class TestThinkingConfig:
    """Verify thinking mode is honored end-to-end via config."""

    async def test_thinking_config_passed_when_budget_positive(
        self, sow_data, monkeypatch
    ):
        _set_config(monkeypatch, SEMANTIC_REVIEW_THINKING_BUDGET=2048)
        parsed = _semantic_review._ReviewerOutput(findings=[])
        fake = _patch_client(
            monkeypatch, behavior=lambda: _build_response(parsed=parsed)
        )
        await semantic_review(sow_data, stage='full')

        kwargs = fake.aio.models.last_call_kwargs
        assert kwargs is not None
        cfg = kwargs['config']
        assert cfg.thinking_config is not None
        assert cfg.thinking_config.thinking_budget == 2048
        assert cfg.thinking_config.include_thoughts is False

    async def test_thinking_config_omitted_when_budget_zero(
        self, sow_data, monkeypatch
    ):
        _set_config(monkeypatch, SEMANTIC_REVIEW_THINKING_BUDGET=0)
        parsed = _semantic_review._ReviewerOutput(findings=[])
        fake = _patch_client(
            monkeypatch, behavior=lambda: _build_response(parsed=parsed)
        )
        await semantic_review(sow_data, stage='full')

        kwargs = fake.aio.models.last_call_kwargs
        assert kwargs is not None
        cfg = kwargs['config']
        # When budget is 0 the kwarg is never set on GenerateContentConfig,
        # so SDK default applies (no thinking).
        assert cfg.thinking_config is None


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


class TestEmptyResult:
    def test_shape_is_canonical(self):
        result = _empty_result('disabled_by_config')
        assert set(result.keys()) == {'findings', 'review_metadata'}
        meta = result['review_metadata']
        assert meta['ran'] is False
        assert meta['fallback_reason'] == 'disabled_by_config'
        assert meta['latency_ms'] == 0


class TestCoerceFindings:
    def test_caps_findings_at_module_limit(self):
        """Cap is enforced regardless of value — read it from the module."""
        cap = _semantic_review._MAX_FINDINGS
        oversize = [
            Finding(
                id=f'F-{i:03d}',
                severity='MINOR',
                category='semantic',
                evidence='evidence',
                recommendation='rec',
                fields=['out_of_scope'],
            )
            for i in range(1, cap + 10)
        ]
        parsed = _semantic_review._ReviewerOutput(findings=oversize)
        coerced = _coerce_findings(parsed)
        assert len(coerced) == cap

    def test_drops_malformed_dict_entries(self):
        raw = {
            'findings': [
                # valid
                {
                    'id': 'F-001',
                    'severity': 'MINOR',
                    'category': 'semantic',
                    'evidence': 'e',
                    'recommendation': 'r',
                    'fields': ['out_of_scope'],
                },
                # invalid: bad severity
                {
                    'id': 'F-002',
                    'severity': 'CRITICAL',
                    'category': 'semantic',
                    'evidence': 'e',
                    'recommendation': 'r',
                    'fields': ['out_of_scope'],
                },
                # invalid: missing fields
                {'id': 'F-003'},
            ]
        }
        coerced = _coerce_findings(raw)
        assert len(coerced) == 1
        assert coerced[0]['id'] == 'F-001'

    def test_unknown_input_returns_empty(self):
        assert _coerce_findings(None) == []
        assert _coerce_findings('garbage') == []
        assert _coerce_findings(42) == []


class TestManifestSummary:
    def test_empty_or_invalid_returns_empty_string(self):
        assert _build_manifest_summary(None) == ''
        assert _build_manifest_summary({}) == ''
        assert _build_manifest_summary('not-a-dict') == ''

    def test_well_formed_manifest_renders_categories(self):
        manifest = {
            'extracted_items': [
                {
                    'category': 'Briefing',
                    'content': 'Customer wants a data platform.',
                },
                {
                    'category': 'Integrations',
                    'content': 'CRM via batch export.',
                },
                {
                    'category': 'Briefing',
                    'content': 'Project must finish by year-end.',
                },
            ],
            'gaps': {
                'pending_decisions': [
                    {'description': 'Region selection pending.'},
                ]
            },
        }
        summary = _build_manifest_summary(manifest)
        assert '## Briefing' in summary
        assert '## Integrations' in summary
        assert 'Customer wants a data platform.' in summary
        assert 'CRM via batch export.' in summary
        assert 'Pending decisions' in summary
        assert 'Region selection pending.' in summary

    def test_caps_items_per_category(self):
        many = [
            {'category': 'Briefing', 'content': f'item {i}'} for i in range(1, 25)
        ]
        manifest = {'extracted_items': many}
        summary = _build_manifest_summary(manifest)
        # The cap is _MAX_MANIFEST_ITEMS_PER_CATEGORY; we verify the long
        # tail is dropped rather than rendered.
        rendered_count = sum(1 for line in summary.splitlines() if line.startswith('- '))
        assert rendered_count == _semantic_review._MAX_MANIFEST_ITEMS_PER_CATEGORY

    def test_long_entries_get_truncated(self):
        manifest = {
            'extracted_items': [
                {'category': 'Briefing', 'content': 'X' * 1000},
            ]
        }
        summary = _build_manifest_summary(manifest)
        rendered = next(line for line in summary.splitlines() if line.startswith('- '))
        # Ellipsis marker proves truncation happened.
        assert rendered.endswith('…')

    def test_malformed_items_are_skipped(self):
        """Non-dict entries and empty-content entries are dropped; the rest survive.

        Items missing a category fall under the literal "Uncategorized" bucket
        by design — graceful degradation rather than a hard skip. This test
        pins both behaviors so a future refactor doesn't silently change them.
        """
        manifest: dict[str, Any] = {
            'extracted_items': [
                'not a dict',
                {'no_category': True, 'content': 'orphan'},
                {'category': 'Briefing', 'content': ''},
                {'category': 'Briefing', 'content': 'good entry'},
            ]
        }
        summary = _build_manifest_summary(manifest)
        # Valid entry rendered.
        assert 'good entry' in summary
        # Missing-category entry rendered under Uncategorized — does not crash.
        assert '## Uncategorized' in summary
        assert 'orphan' in summary
        # Empty-content rows are dropped (no bullet line for them).
        rendered_bullets = [
            line for line in summary.splitlines() if line.startswith('- ')
        ]
        assert len(rendered_bullets) == 2  # 'good entry' + 'orphan'
        # The literal string 'not a dict' must not appear — it was rejected.
        assert 'not a dict' not in summary


class TestRubricCache:
    def test_load_rubric_returns_string_with_expected_anchors(self):
        # Reset cache to exercise the read path under test.
        _semantic_review._rubric_cache = None
        rubric = _semantic_review._load_rubric()
        assert isinstance(rubric, str)
        # Domain-specific anchors that survive markdown/XML restructuring of
        # the rubric. Checking for severity-name literals and the mandatory
        # completeness protocol header rather than human-readable section
        # titles keeps the test stable across formatting refactors.
        assert 'BLOCKER' in rubric
        assert 'MAJOR' in rubric
        assert 'MINOR' in rubric
        assert 'mandatory_completeness_protocol' in rubric

    def test_load_rubric_is_cached(self, monkeypatch):
        _semantic_review._rubric_cache = 'sentinel-cached'
        # Ensure the file is NOT read again when the cache is populated.
        original_read_text = _semantic_review.Path.read_text

        def _explode(*args, **kwargs):  # noqa: ARG001
            raise AssertionError('read_text should not be called when cached')

        monkeypatch.setattr(_semantic_review.Path, 'read_text', _explode)
        try:
            assert _semantic_review._load_rubric() == 'sentinel-cached'
        finally:
            monkeypatch.setattr(_semantic_review.Path, 'read_text', original_read_text)
            _semantic_review._rubric_cache = None
