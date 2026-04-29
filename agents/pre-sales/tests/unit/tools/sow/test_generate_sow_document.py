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

from app.tools.sow.generate_sow_document import (
    _apply_defaults,
    _auto_derive_fields,
    _infer_project_type,
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
            ('technology_stack', []),
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
            'technology_stack': [{'service': 'x', 'purpose': 'y'}],
            'risks': [{'description': 'r', 'mitigation': 'm'}],
        }
        _apply_defaults(data)
        assert data['taxes_included'] is False
        assert len(data['technology_stack']) == 1
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

    def test_tech_stack_derived_from_components(self):
        data = {
            'architecture_components': [
                {'name': 'Cloud Run', 'role': 'API'},
                {'name': 'BigQuery', 'role': 'Warehouse'},
            ],
        }
        _auto_derive_fields(data)
        assert data['technology_stack'] == [
            {'service': 'Cloud Run', 'purpose': 'API'},
            {'service': 'BigQuery', 'purpose': 'Warehouse'},
        ]

    def test_existing_tech_stack_not_overwritten(self):
        data = {
            'technology_stack': [{'service': 'x', 'purpose': 'y'}],
            'architecture_components': [
                {'name': 'Cloud Run', 'role': 'API'}
            ],
        }
        _auto_derive_fields(data)
        assert data['technology_stack'] == [
            {'service': 'x', 'purpose': 'y'}
        ]

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


class TestGenerateSowDocumentErrorPaths:
    """Contract tests that don't require rendering the .docx template."""

    async def test_invalid_json_returns_tool_error(self, mock_tool_context):
        result = await generate_sow_document(
            sow_data='{not valid',
            tool_context=mock_tool_context,
        )
        assert result['status'] == 'error'
        assert 'JSON' in result['error']
        assert result['retryable'] is False

    async def test_missing_required_fields_returns_tool_error(
        self, mock_tool_context
    ):
        minimal = {'partner_name': 'GFT'}
        result = await generate_sow_document(
            sow_data=json.dumps(minimal),
            tool_context=mock_tool_context,
        )
        assert result['status'] == 'error'
        assert 'obrigatórios' in result['error'].lower() or 'missing' in result['error'].lower()

    async def test_quality_gate_failure_is_retryable(
        self, sow_data, mock_tool_context
    ):
        sow_data['out_of_scope'] = ['only one']
        result = await generate_sow_document(
            sow_data=json.dumps(sow_data),
            tool_context=mock_tool_context,
        )
        assert result['status'] == 'error'
        assert result['retryable'] is True

    async def test_structural_validation_failure_is_retryable(
        self, sow_data, mock_tool_context
    ):
        sow_data['functional_requirements'][0]['number'] = 'BAD'
        result = await generate_sow_document(
            sow_data=json.dumps(sow_data),
            tool_context=mock_tool_context,
        )
        assert result['status'] == 'error'
        assert result['retryable'] is True

    async def test_repeated_identical_payload_triggers_anti_loop_message(
        self, sow_data, mock_tool_context
    ):
        sow_data['functional_requirements'][0]['number'] = 'BAD'
        payload = json.dumps(sow_data)

        first = await generate_sow_document(
            sow_data=payload, tool_context=mock_tool_context
        )
        second = await generate_sow_document(
            sow_data=payload, tool_context=mock_tool_context
        )

        assert first['status'] == 'error'
        assert second['status'] == 'error'
        assert 'PAYLOAD IDÊNTICO' in second['error']

    async def test_missing_template_returns_tool_error(
        self, sow_data, mock_tool_context
    ):
        # Force the template existence check to fail
        with patch(
            'app.tools.sow.generate_sow_document.Path'
        ) as path_mock:
            instance = path_mock.return_value
            instance.__truediv__.return_value = instance
            instance.exists.return_value = False
            result = await generate_sow_document(
                sow_data=json.dumps(sow_data),
                tool_context=mock_tool_context,
            )
        # When template is missing → ToolError, not success
        assert result['status'] == 'error'
