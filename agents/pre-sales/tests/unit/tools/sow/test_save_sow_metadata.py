"""Unit tests for ``save_sow_metadata``.

The tool is the root-skills variant's source of the 13 administrative
metadata fields. Coverage focuses on: the happy path writing the
canonical envelope key, the required-field gate, and the no-context
guard.
"""

from __future__ import annotations

import pytest

from app.sub_agents.schemas import SOW_METADATA_STATE_KEY
from app.tools.sow.save_sow_metadata import save_sow_metadata


class TestSaveSowMetadata:
    async def test_persists_envelope_to_canonical_key(self, mock_tool_context):
        result = await save_sow_metadata(
            partner_name='GFT Technologies',
            customer_name='Acme Corp',
            project_title='Data Analytics Platform',
            funding_type='Google DAF',
            partner_short_name='GFT',
            tool_context=mock_tool_context,
        )

        assert result['status'] == 'success'
        envelope = mock_tool_context.state[SOW_METADATA_STATE_KEY]
        assert envelope['partner_name'] == 'GFT Technologies'
        assert envelope['customer_name'] == 'Acme Corp'
        # Unset fields default to '' (schema-valid envelope).
        assert envelope['author'] == ''
        # All 13 canonical fields are present.
        assert len(envelope) == 13

    async def test_reports_populated_fields(self, mock_tool_context):
        result = await save_sow_metadata(
            partner_name='GFT',
            customer_name='Acme',
            project_title='Proj',
            funding_type='DAF',
            tool_context=mock_tool_context,
        )
        data = result['data']
        assert data['state_key'] == SOW_METADATA_STATE_KEY
        assert data['populated_count'] == 4
        assert set(data['populated_fields']) == {
            'partner_name',
            'customer_name',
            'project_title',
            'funding_type',
        }

    async def test_rejects_when_required_field_blank(self, mock_tool_context):
        result = await save_sow_metadata(
            partner_name='GFT',
            customer_name='Acme',
            project_title='Proj',
            # funding_type omitted -> blank
            tool_context=mock_tool_context,
        )
        assert result['status'] == 'error'
        assert 'funding_type' in result['error']
        # Nothing persisted on rejection.
        assert SOW_METADATA_STATE_KEY not in mock_tool_context.state

    async def test_whitespace_required_field_treated_as_blank(
        self, mock_tool_context
    ):
        result = await save_sow_metadata(
            partner_name='   ',
            customer_name='Acme',
            project_title='Proj',
            funding_type='DAF',
            tool_context=mock_tool_context,
        )
        assert result['status'] == 'error'
        assert 'partner_name' in result['error']

    async def test_missing_tool_context_returns_error(self):
        result = await save_sow_metadata(
            partner_name='GFT',
            customer_name='Acme',
            project_title='Proj',
            funding_type='DAF',
        )
        assert result['status'] == 'error'
        assert 'tool_context' in result['error']
