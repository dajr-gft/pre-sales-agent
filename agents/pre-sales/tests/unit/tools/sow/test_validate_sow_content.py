"""Unit tests for ``app.tools.sow.validate_sow_content`` (legacy helper).

The function used to be an ADK tool that returned ``ToolSuccess`` /
``ToolError`` and was awaited with a ``tool_context``. After the
Validation Critic migration it became a CI-only Python helper:
synchronous, no ``tool_context``, returns the raw ContentValidator dict
(with a human-readable ``summary``), raises ``ValueError`` on bad JSON.

These tests follow the new contract.
"""
from __future__ import annotations

import json

import pytest

from app.tools.sow.validate_sow_content import validate_sow_content


class TestContractShape:
    def test_success_returns_validator_dict(self, sow_data):
        result = validate_sow_content(
            sow_data=json.dumps(sow_data),
            funding_type='DAF',
            stage='full',
        )
        assert 'passed' in result
        assert 'error_count' in result
        assert 'warning_count' in result
        assert 'issues' in result
        assert 'summary' in result

    def test_invalid_json_raises_value_error(self):
        with pytest.raises(ValueError, match='Invalid SOW JSON'):
            validate_sow_content(sow_data='{not valid')


class TestValidationOutcomes:
    def test_valid_payload_passes(self, sow_data):
        result = validate_sow_content(sow_data=json.dumps(sow_data))
        assert result['passed'] is True
        assert result['error_count'] == 0

    def test_invalid_fr_id_fails(self, sow_data):
        sow_data['functional_requirements'][0]['number'] = 'bad-id'
        result = validate_sow_content(sow_data=json.dumps(sow_data))
        assert result['passed'] is False
        assert result['error_count'] >= 1


class TestFundingTypeHandling:
    def test_funding_type_auto_detected_from_payload(self, sow_data_psf):
        """When caller doesn't pass funding_type, validator infers from data."""
        result = validate_sow_content(
            sow_data=json.dumps(sow_data_psf),
            funding_type='',
        )
        assert result['passed'] is True


class TestStageHandling:
    def test_content_stage_skips_arch_checks(self, sow_data):
        sow_data['architecture_description'] = 'short'
        result = validate_sow_content(
            sow_data=json.dumps(sow_data),
            stage='content',
        )
        assert not any(
            i['field'] == 'architecture_description' for i in result['issues']
        )

    def test_full_stage_runs_arch_checks(self, sow_data):
        sow_data['architecture_description'] = 'short'
        result = validate_sow_content(
            sow_data=json.dumps(sow_data),
            stage='full',
        )
        assert any(
            i['field'] == 'architecture_description' for i in result['issues']
        )

    @pytest.mark.parametrize('bad_stage', ['bogus', 'phase-1', ''])
    def test_invalid_stage_falls_back_to_full(self, sow_data, bad_stage):
        """Unknown stage values silently fall back to 'full' (defensive)."""
        sow_data['architecture_description'] = 'short'
        result = validate_sow_content(
            sow_data=json.dumps(sow_data),
            stage=bad_stage,
        )
        # Arch check fires → proves full stage was applied
        assert any(
            i['field'] == 'architecture_description' for i in result['issues']
        )


class TestSummary:
    def test_summary_when_all_passed(self, sow_data):
        result = validate_sow_content(sow_data=json.dumps(sow_data))
        assert 'All structural checks passed' in result['summary']

    def test_summary_when_warnings_only(self, sow_data):
        # trigger a warning: too few OOS items
        sow_data['out_of_scope'] = ['one', 'two']
        result = validate_sow_content(sow_data=json.dumps(sow_data))
        summary = result['summary']
        assert 'No blocking errors' in summary
        assert 'warning' in summary.lower()

    def test_summary_when_errors(self, sow_data):
        sow_data['functional_requirements'][0]['number'] = 'BAD'
        result = validate_sow_content(sow_data=json.dumps(sow_data))
        summary = result['summary']
        assert 'error' in summary.lower()
        assert 'must be fixed' in summary
