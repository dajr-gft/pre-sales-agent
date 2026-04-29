"""Unit tests for ``app.tools.sow.validate_sow_content`` (the ADK tool)."""
from __future__ import annotations

import json

import pytest

from app.tools.sow.validate_sow_content import validate_sow_content


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
