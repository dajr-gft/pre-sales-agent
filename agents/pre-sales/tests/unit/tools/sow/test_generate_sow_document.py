"""Unit tests for ``app.tools.sow.generate_sow_document``.

Focus on the pure preprocessing functions (``_apply_defaults``,
``_auto_derive_fields``, ``_infer_project_type``) and on the error paths of
the public tool that don't require docx rendering (invalid JSON, missing
required fields, quality gate failures, structural failures, missing
template). Actual .docx rendering lives in tests/integration.
"""
from __future__ import annotations

import json
from copy import deepcopy
from unittest.mock import patch

import pytest

from docxtpl import InlineImage, Listing, RichText

from app.tools.sow.generate_sow_document import (
    _apply_defaults,
    _auto_derive_fields,
    _infer_project_type,
    _normalize_multiline_string,
    _normalize_text_fields,
    generate_sow_document,
)


class TestApplyDefaults:
    def test_sets_default_organization_term(self):
        data = {}
        _apply_defaults(data)
        assert data['organization_term'] == 'phases'

    def test_long_organization_term_resets_to_phases(self):
        data = {'organization_term': 'invalid multi word term'}
        _apply_defaults(data)
        assert data['organization_term'] == 'phases'

    def test_valid_engagement_preserved(self):
        data = {'engagement_type': 'pilot'}
        _apply_defaults(data)
        assert data['engagement_type'] == 'pilot'

    def test_invalid_engagement_reset_to_project(self):
        data = {'engagement_type': 'weekend hackathon'}
        _apply_defaults(data)
        assert data['engagement_type'] == 'project'

    def test_engagement_case_insensitive_match(self):
        data = {'engagement_type': 'PILOT'}
        _apply_defaults(data)
        # Case preserved for whatever the template needs
        assert data['engagement_type'] == 'PILOT'

    @pytest.mark.parametrize(
        'key,default',
        [
            ('taxes_included', True),
            ('non_commit_psf', False),
            ('milestones', []),
            ('risks', []),
            ('architecture_diagram', ''),
        ],
    )
    def test_optional_defaults(self, key, default):
        data = {}
        _apply_defaults(data)
        assert data[key] == default

    def test_existing_values_not_overwritten(self):
        data = {
            'taxes_included': False,
            'milestones': [{'name': 'M1', 'deliverables': 'd', 'estimated_completion': 'w', 'payment': 'p'}],
            'risks': [{'description': 'r', 'mitigation': 'm'}],
        }
        _apply_defaults(data)
        assert data['taxes_included'] is False
        assert len(data['milestones']) == 1
        assert len(data['risks']) == 1


class TestAutoDeriveFields:
    def test_activities_derived_from_phases_when_missing(self):
        data = {
            'activity_phases': [
                {'name': 'Phase 1', 'description': 'x', 'tasks': []},
                {'name': 'Phase 2', 'description': 'y', 'tasks': []},
            ]
        }
        _auto_derive_fields(data)
        assert data['activities'] == ['Phase 1', 'Phase 2']

    def test_existing_activities_not_overwritten(self):
        data = {
            'activities': ['Custom 1', 'Custom 2'],
            'activity_phases': [{'name': 'Ignored', 'description': '', 'tasks': []}],
        }
        _auto_derive_fields(data)
        assert data['activities'] == ['Custom 1', 'Custom 2']

    @pytest.mark.parametrize(
        'funding_type,short',
        [
            ('Google PSF Partner Sponsored', 'PSF'),
            ('PARTNER Funding', 'PSF'),
            ('Google DAF', 'DAF'),
            ('Deal Acceleration Fund', 'DAF'),
            ('Unknown', 'DAF'),  # default
        ],
    )
    def test_funding_type_short_inferred(self, funding_type, short):
        data = {'funding_type': funding_type}
        _auto_derive_fields(data)
        assert data['funding_type_short'] == short

    def test_funding_short_preserved_when_already_set(self):
        data = {'funding_type': 'Anything', 'funding_type_short': 'PSF'}
        _auto_derive_fields(data)
        assert data['funding_type_short'] == 'PSF'

class TestInferProjectType:
    def test_genai_detected_from_vertex_ai(self):
        data = {
            'architecture_components': [
                {'name': 'Vertex AI', 'role': 'model serving'}
            ],
            'architecture_description': '',
            'executive_summary': '',
        }
        assert _infer_project_type(data) == 'genai'

    def test_genai_detected_from_description(self):
        data = {
            'architecture_description': 'uses Gemini for summarization',
            'executive_summary': '',
        }
        assert _infer_project_type(data) == 'genai'

    def test_genai_detected_from_executive_summary(self):
        data = {
            'executive_summary': 'The project relies on Vertex AI Search for RAG.',
        }
        assert _infer_project_type(data) == 'genai'

    def test_ml_detected_from_automl(self):
        data = {
            'architecture_components': [
                {'name': 'AutoML', 'role': 'Training'}
            ],
            'architecture_description': '',
            'executive_summary': '',
        }
        assert _infer_project_type(data) == 'ml'

    def test_ml_detected_from_tensorflow(self):
        data = {
            'architecture_description': 'Uses TensorFlow models deployed on GKE.',
            'executive_summary': '',
        }
        assert _infer_project_type(data) == 'ml'

    def test_genai_takes_precedence_over_ml(self):
        data = {
            'architecture_components': [
                {'name': 'Vertex AI', 'role': 'Gemini'},
                {'name': 'AutoML', 'role': 'Classic ML'},
            ],
            'architecture_description': 'Uses Gemini and AutoML.',
            'executive_summary': '',
        }
        assert _infer_project_type(data) == 'genai'

    def test_standard_when_no_ai_services(self):
        data = {
            'architecture_components': [
                {'name': 'Cloud Run', 'role': 'API'},
                {'name': 'Cloud SQL', 'role': 'DB'},
            ],
            'architecture_description': 'Web backend with relational DB.',
            'executive_summary': 'A standard 3-tier web app.',
        }
        assert _infer_project_type(data) == 'standard'

    def test_empty_data_returns_standard(self):
        assert _infer_project_type({}) == 'standard'


class TestProjectTypeFromTestFixture:
    def test_fixture_is_genai_by_default(self, sow_data):
        _apply_defaults(sow_data)
        _auto_derive_fields(sow_data)
        assert sow_data['project_type'] == 'genai'


def _stage(ctx, payload, stage='full'):
    """Helper: stage a SOW payload in mock_tool_context as the tool expects."""
    ctx.state['app:sow:current'] = payload
    ctx.state['app:sow:stage'] = stage


class _FakeDoc:
    """Drop-in for ``DocxTemplate`` that captures ``render`` data and writes
    an empty file on ``save`` so the artifact-bytes read step succeeds."""

    last_render_data: dict | None = None

    def __init__(self, *args, **kwargs):
        pass

    def render(self, data, autoescape=True):  # noqa: ARG002 — match real signature
        type(self).last_render_data = data

    def save(self, path):
        with open(path, 'wb') as f:
            f.write(b'')


def _stub_renderer(monkeypatch):
    """Replace DocxTemplate + logo helpers so render-path tests don't need
    the real .docx template, network, or logo bytes."""
    _FakeDoc.last_render_data = None
    monkeypatch.setattr(
        'app.tools.sow.generate_sow_document.DocxTemplate', _FakeDoc
    )
    monkeypatch.setattr(
        'app.tools.sow.generate_sow_document.load_logo',
        lambda *a, **kw: '[Logo]',
    )
    monkeypatch.setattr(
        'app.tools.sow.generate_sow_document._fetch_customer_logo_to_tempfile',
        lambda *a, **kw: None,
    )


class TestGenerateSowDocumentErrorPaths:
    """Contract tests that don't require rendering the .docx template.

    The tool no longer takes ``sow_data``; it reads the validated payload
    from ``state['app:sow:current']``. Each test stages a payload in the
    mock context's state and calls the tool with no SOW argument.
    """

    async def test_missing_staged_sow_returns_tool_error(
        self, mock_tool_context
    ):
        # state has neither app:sow:current nor app:sow:stage
        result = await generate_sow_document(tool_context=mock_tool_context)
        assert result['status'] == 'error'
        assert 'no staged sow' in result['error'].lower()
        assert result['retryable'] is False

    async def test_stage_must_be_full_to_generate(
        self, sow_data, mock_tool_context
    ):
        # A content-stage payload must be rejected — the document is
        # only rendered from a fully validated `full`-stage SOW.
        _stage(mock_tool_context, sow_data, stage='content')
        result = await generate_sow_document(tool_context=mock_tool_context)
        assert result['status'] == 'error'
        assert "'content'" in result['error']
        assert result['retryable'] is False

    async def test_missing_required_fields_returns_tool_error(
        self, mock_tool_context
    ):
        _stage(mock_tool_context, {'partner_name': 'GFT'})
        result = await generate_sow_document(tool_context=mock_tool_context)
        assert result['status'] == 'error'
        assert 'missing required fields' in result['error'].lower()
        assert result['retryable'] is False

    async def test_quality_gate_failure_is_retryable(
        self, sow_data, mock_tool_context
    ):
        sow_data['out_of_scope'] = ['only one']
        _stage(mock_tool_context, sow_data)
        result = await generate_sow_document(tool_context=mock_tool_context)
        assert result['status'] == 'error'
        assert result['retryable'] is True

    async def test_structural_validation_failure_is_retryable(
        self, sow_data, mock_tool_context
    ):
        sow_data['functional_requirements'][0]['number'] = 'BAD'
        _stage(mock_tool_context, sow_data)
        result = await generate_sow_document(tool_context=mock_tool_context)
        assert result['status'] == 'error'
        assert result['retryable'] is True

    async def test_error_messages_do_not_ask_model_to_edit_payload(
        self, sow_data, mock_tool_context
    ):
        """Regression: as the model no longer sends JSON, no error message
        may instruct it to "fix the JSON" or "edit the payload" — the only
        recovery is to regenerate the section and re-stage."""
        sow_data['functional_requirements'][0]['number'] = 'BAD'
        _stage(mock_tool_context, sow_data)
        result = await generate_sow_document(tool_context=mock_tool_context)
        combined = (
            (result.get('error') or '') + ' ' + (result.get('suggestion') or '')
        ).lower()
        for forbidden in (
            'edite o payload',
            'edit the payload',
            'reenvi',
            'corrija o json',
            'json inválido',
            'invalid json',
            'payload idêntico',
            'sow_data',
        ):
            assert forbidden not in combined, (
                f'error/suggestion still uses forbidden phrasing {forbidden!r}: '
                f'{combined!r}'
            )

    async def test_missing_template_returns_tool_error(
        self, sow_data, mock_tool_context
    ):
        _stage(mock_tool_context, sow_data)
        # Force the template existence check to fail
        with patch(
            'app.tools.sow.generate_sow_document.Path'
        ) as path_mock:
            instance = path_mock.return_value
            instance.__truediv__.return_value = instance
            instance.exists.return_value = False
            result = await generate_sow_document(
                tool_context=mock_tool_context
            )
        assert result['status'] == 'error'


class TestGenerateSowDocumentFromState:
    """The two load-bearing tests for the state-sourced design:

    - non-mutation: the staged dict in state must NOT be touched even
      though the internal pipeline mutates ``data`` in place.
    - staleness: the renderer receives the dict as it is in state at call
      time, not a version the agent might be holding in its context.
    """

    async def test_state_is_not_mutated_during_generation(
        self, sow_data, mock_tool_context, monkeypatch
    ):
        # Use a payload that would exercise every mutation point:
        # - a multiline string (would become a Listing without deepcopy)
        # - a missing default (would gain organization_term without deepcopy)
        sow_data['executive_summary'] = (
            'Acme Corp is modernizing.\n\nThe project leverages BigQuery and '
            'Vertex AI for ML and scalable analytics.'
        )
        sow_data.pop('organization_term', None)
        sow_data.pop('architecture_diagram', None)

        _stage(mock_tool_context, sow_data)
        before = deepcopy(mock_tool_context.state['app:sow:current'])

        _stub_renderer(monkeypatch)
        result = await generate_sow_document(tool_context=mock_tool_context)
        assert result['status'] == 'success', result

        staged_after = mock_tool_context.state['app:sow:current']
        assert staged_after == before, (
            "state['app:sow:current'] was mutated during generation."
        )
        # Targeted asserts so a future regression has a clear signal:
        assert isinstance(staged_after['executive_summary'], str), (
            'multiline string in state was converted to a Listing — '
            'deepcopy regression.'
        )
        assert 'organization_term' not in staged_after, (
            '_apply_defaults leaked into state.'
        )
        # No docxtpl objects ever land in state.
        assert 'partner_logo' not in staged_after
        assert 'customer_logo' not in staged_after
        assert 'architecture_diagram' not in staged_after

    async def test_renderer_uses_the_value_in_state_at_call_time(
        self, sow_data, mock_tool_context, monkeypatch
    ):
        """Staleness proof: the renderer sees the dict as it is in state at
        call time. The model can hold no other version because it has no
        SOW argument; this test pins that behavior so future signature
        changes cannot re-introduce a stale-payload path."""
        # Stage a payload with a unique marker so the assertion is sharp.
        sow_data['customer_name'] = 'NEW VERSION CUSTOMER'
        _stage(mock_tool_context, sow_data)

        _stub_renderer(monkeypatch)
        result = await generate_sow_document(tool_context=mock_tool_context)
        assert result['status'] == 'success', result

        rendered = _FakeDoc.last_render_data
        assert rendered is not None, 'DocxTemplate.render was not called.'
        assert rendered['customer_name'] == 'NEW VERSION CUSTOMER', (
            'render did not receive the value present in state — the doc '
            'would not reflect the validated payload.'
        )


class TestNormalizeMultilineString:
    def test_plain_string_passthrough(self):
        assert _normalize_multiline_string('single line') == 'single line'

    def test_empty_string_passthrough(self):
        assert _normalize_multiline_string('') == ''

    def test_real_newline_wraps_into_listing(self):
        result = _normalize_multiline_string('line one\nline two')
        assert isinstance(result, Listing)

    def test_literal_backslash_n_is_normalized_then_wrapped(self):
        # Model double-escaped: JSON string contains the two characters \ and n
        result = _normalize_multiline_string('line one\\nline two')
        assert isinstance(result, Listing)
        # The Listing's internal text now contains a real newline, not the
        # literal two-character sequence.
        assert '\n' in str(result)
        assert '\\n' not in str(result)

    def test_crlf_normalized_to_lf(self):
        result = _normalize_multiline_string('line one\r\nline two')
        assert isinstance(result, Listing)
        assert '\r' not in str(result)

    def test_lone_cr_normalized_to_lf(self):
        result = _normalize_multiline_string('line one\rline two')
        assert isinstance(result, Listing)
        assert '\r' not in str(result)

    def test_runs_of_blank_lines_collapse_to_two(self):
        result = _normalize_multiline_string('a\n\n\n\n\nb')
        assert isinstance(result, Listing)
        assert '\n\n\n' not in str(result)
        assert '\n\n' in str(result)


class TestNormalizeTextFields:
    def test_top_level_strings_with_newlines_become_listing(self):
        data = {
            'executive_summary': 'Para 1.\n\nPara 2.',
            'partner_name': 'GFT',  # no newline → stays str
        }
        _normalize_text_fields(data)
        assert isinstance(data['executive_summary'], Listing)
        assert data['partner_name'] == 'GFT'

    def test_recurses_into_list_of_dicts(self):
        data = {
            'functional_requirements': [
                {'number': 'FR-01', 'description': 'Line A\nLine B'},
                {'number': 'FR-02', 'description': 'Single line.'},
            ],
        }
        _normalize_text_fields(data)
        assert isinstance(
            data['functional_requirements'][0]['description'], Listing
        )
        assert (
            data['functional_requirements'][1]['description']
            == 'Single line.'
        )
        # Sibling fields without newlines stay untouched
        assert data['functional_requirements'][0]['number'] == 'FR-01'

    def test_recurses_into_simple_lists(self):
        data = {'objectives': ['short objective', 'multi\nline objective']}
        _normalize_text_fields(data)
        assert data['objectives'][0] == 'short objective'
        assert isinstance(data['objectives'][1], Listing)

    def test_preserves_inline_image_and_richtext(self):
        # Use sentinel objects of the preserved types
        image = InlineImage.__new__(InlineImage)
        rich = RichText('already rich')
        listing = Listing('already a listing')
        data = {
            'partner_logo': image,
            'rich_field': rich,
            'listing_field': listing,
        }
        _normalize_text_fields(data)
        assert data['partner_logo'] is image
        assert data['rich_field'] is rich
        assert data['listing_field'] is listing

    def test_idempotent_on_second_pass(self):
        data = {'executive_summary': 'a\nb'}
        _normalize_text_fields(data)
        first = data['executive_summary']
        _normalize_text_fields(data)
        # Second pass keeps the same Listing instance (preserved by guard)
        assert data['executive_summary'] is first

    def test_non_string_scalars_passthrough(self):
        data = {
            'taxes_included': True,
            'non_commit_psf': False,
            'count': 7,
        }
        _normalize_text_fields(data)
        assert data['taxes_included'] is True
        assert data['non_commit_psf'] is False
        assert data['count'] == 7
