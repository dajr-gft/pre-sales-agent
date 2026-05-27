"""Unit tests for ``save_sow_intake_summary``.

The tool is Path A's persistence point: the ``sow-guided-intake`` skill
calls it to commit the structured interview summary to
``state['app:sow:intake_summary']``. Coverage focuses on:

  - the happy path writing the canonical key + the marker roll-ups;
  - the marker contract on the four cannot-skip fields (markers /
    blanks rejected there, accepted everywhere else);
  - the scalar + list marker conventions classifying into
    ``inferred_fields`` / ``open_fields``;
  - schema rejection of extra fields;
  - the no-context guard.
"""

from __future__ import annotations

import pytest

from app.sub_agents.schemas import (
    INTAKE_MARKER_INFERRED,
    INTAKE_MARKER_TO_BE_DEFINED,
    SOW_INTAKE_SUMMARY_STATE_KEY,
)
from app.tools.sow.save_sow_intake_summary import save_sow_intake_summary


def _valid_summary(**overrides) -> dict:
    """A minimal schema-valid intake summary; override per test."""
    base = {
        'customer_name': 'Acme Corp',
        'project_title': 'Data Analytics Platform',
        'problem_goal': 'Consolidate fragmented sales reporting on GCP.',
        'solution_direction': 'BigQuery warehouse fed by Cloud Run ingestion.',
        'funding_type': 'DAF',
        'integrations': ['SAP — source, REST, read'],
        'timeline': '10 weeks starting 2026-06-01',
        'nfr_quality_targets': ['Dashboards refresh under 5 minutes'],
        # Inference-eligible fields left as markers.
        'engagement_shape': INTAKE_MARKER_INFERRED,
        'technology_stack': [INTAKE_MARKER_INFERRED],
        'out_of_scope': [INTAKE_MARKER_INFERRED],
        'operational_constraints': [INTAKE_MARKER_TO_BE_DEFINED],
        'inferred_items': ['engagement_shape', 'technology_stack', 'out_of_scope'],
        'open_items': ['operational_constraints'],
    }
    base.update(overrides)
    return base


class TestSaveSowIntakeSummary:
    async def test_persists_to_canonical_key(self, mock_tool_context):
        result = await save_sow_intake_summary(
            intake_summary=_valid_summary(),
            tool_context=mock_tool_context,
        )
        assert result['status'] == 'success', result
        persisted = mock_tool_context.state[SOW_INTAKE_SUMMARY_STATE_KEY]
        assert persisted['customer_name'] == 'Acme Corp'
        assert persisted['partner_name'] == 'GFT Technologies'  # default
        # Schema default-fills omitted list fields.
        assert persisted['regulatory_constraints'] == []

    async def test_classifies_markers_into_rollups(self, mock_tool_context):
        result = await save_sow_intake_summary(
            intake_summary=_valid_summary(),
            tool_context=mock_tool_context,
        )
        data = result['data']
        assert data['state_key'] == SOW_INTAKE_SUMMARY_STATE_KEY
        # Scalar (engagement_shape) + list ([(inferred)]) both classify.
        assert 'engagement_shape' in data['inferred_fields']
        assert 'technology_stack' in data['inferred_fields']
        assert 'out_of_scope' in data['inferred_fields']
        # [TO BE DEFINED] list classifies as open.
        assert 'operational_constraints' in data['open_fields']
        # Real values are populated, not markers.
        assert 'customer_name' in data['populated_fields']
        assert 'integrations' in data['populated_fields']
        assert 'timeline' in data['populated_fields']

    async def test_scalar_to_be_defined_classifies_open(self, mock_tool_context):
        result = await save_sow_intake_summary(
            intake_summary=_valid_summary(timeline=INTAKE_MARKER_TO_BE_DEFINED),
            tool_context=mock_tool_context,
        )
        assert result['status'] == 'success'
        assert 'timeline' in result['data']['open_fields']

    @pytest.mark.parametrize(
        'field',
        ['customer_name', 'project_title', 'problem_goal', 'solution_direction'],
    )
    async def test_rejects_marker_on_required_field(
        self, mock_tool_context, field
    ):
        """The four cannot-skip fields must carry real values."""
        result = await save_sow_intake_summary(
            intake_summary=_valid_summary(**{field: INTAKE_MARKER_TO_BE_DEFINED}),
            tool_context=mock_tool_context,
        )
        assert result['status'] == 'error'
        assert field in result['error']
        assert SOW_INTAKE_SUMMARY_STATE_KEY not in mock_tool_context.state

    @pytest.mark.parametrize(
        'field',
        ['customer_name', 'project_title', 'problem_goal', 'solution_direction'],
    )
    async def test_rejects_inferred_marker_on_required_field(
        self, mock_tool_context, field
    ):
        result = await save_sow_intake_summary(
            intake_summary=_valid_summary(**{field: INTAKE_MARKER_INFERRED}),
            tool_context=mock_tool_context,
        )
        assert result['status'] == 'error'
        assert field in result['error']

    async def test_rejects_blank_required_field(self, mock_tool_context):
        result = await save_sow_intake_summary(
            intake_summary=_valid_summary(customer_name='   '),
            tool_context=mock_tool_context,
        )
        assert result['status'] == 'error'
        assert 'customer_name' in result['error']

    async def test_funding_type_to_be_defined_is_allowed(self, mock_tool_context):
        """funding_type tolerates the marker — it is not a cannot-skip field."""
        result = await save_sow_intake_summary(
            intake_summary=_valid_summary(funding_type=INTAKE_MARKER_TO_BE_DEFINED),
            tool_context=mock_tool_context,
        )
        assert result['status'] == 'success'
        assert 'funding_type' in result['data']['open_fields']

    async def test_rejects_extra_field(self, mock_tool_context):
        result = await save_sow_intake_summary(
            intake_summary=_valid_summary(manifest_item_id='X-01'),
            tool_context=mock_tool_context,
        )
        assert result['status'] == 'error'
        assert SOW_INTAKE_SUMMARY_STATE_KEY not in mock_tool_context.state

    async def test_rejects_non_dict_summary(self, mock_tool_context):
        result = await save_sow_intake_summary(
            intake_summary='not a dict',
            tool_context=mock_tool_context,
        )
        assert result['status'] == 'error'
        assert 'JSON object' in result['error']

    async def test_missing_tool_context_returns_error(self):
        result = await save_sow_intake_summary(intake_summary=_valid_summary())
        assert result['status'] == 'error'
        assert 'tool_context' in result['error']

    async def test_all_real_values_no_markers(self, mock_tool_context):
        """A fully-specified intake produces empty roll-ups."""
        full = _valid_summary(
            engagement_shape='greenfield',
            technology_stack=['BigQuery', 'Cloud Run'],
            out_of_scope=['Hardware procurement'],
            operational_constraints=['VPN access on day 1'],
            regulatory_constraints=['LGPD'],
            partner_team=['PM', 'Architect'],
            customer_team=['Sponsor', 'SME'],
            inferred_items=[],
            open_items=[],
        )
        result = await save_sow_intake_summary(
            intake_summary=full, tool_context=mock_tool_context
        )
        assert result['status'] == 'success'
        assert result['data']['inferred_fields'] == []
        assert result['data']['open_fields'] == []
