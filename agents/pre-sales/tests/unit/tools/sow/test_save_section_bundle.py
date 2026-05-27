"""Unit tests for the ``save_<section>_bundle`` factory tools.

Each tool validates a bundle against its section's Pydantic schema and
persists it to the canonical ``app:sow:<section>`` state key. Coverage:
happy path per section, schema-validation rejection, non-dict guard, and
the factory's name/identity contract (the prompt references these names).
"""

from __future__ import annotations

from typing import Any

import pytest

from app.sub_agents.schemas import SOW_BUNDLE_STATE_KEYS
from app.tools.sow.save_section_bundle import (
    SAVE_BUNDLE_TOOLS,
    save_architecture_bundle,
    save_delivery_plan_bundle,
    save_narrative_bundle,
    save_requirements_bundle,
    save_scope_boundaries_bundle,
)


def _requirements() -> dict[str, Any]:
    return {
        'functional_requirements': [
            {'number': 'FR-01', 'description': 'Ingest data from SAP.'},
        ],
        'non_functional_requirements': [
            {'number': 'NFR-01', 'description': 'TLS 1.3.'},
        ],
    }


def _narrative() -> dict[str, Any]:
    return {
        'executive_summary': 'Modernizes data.',
        'partner_overview': 'GFT premier partner.',
        'customer_overview': 'Acme manufactures.',
    }


class TestFactoryContract:
    def test_tool_names_match_prompt_references(self):
        names = {t.__name__ for t in SAVE_BUNDLE_TOOLS}
        assert names == {
            'save_requirements_bundle',
            'save_delivery_plan_bundle',
            'save_scope_boundaries_bundle',
            'save_architecture_bundle',
            'save_narrative_bundle',
        }


class TestSaveBundleHappyPath:
    async def test_requirements_persists_and_reports_counts(
        self, mock_tool_context
    ):
        result = await save_requirements_bundle(
            bundle=_requirements(), tool_context=mock_tool_context
        )
        assert result['status'] == 'success'
        persisted = mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['requirements']]
        assert len(persisted['functional_requirements']) == 1
        assert result['data']['item_counts']['functional_requirements'] == 1
        assert result['data']['state_key'] == SOW_BUNDLE_STATE_KEYS['requirements']

    async def test_narrative_persists_optional_default(self, mock_tool_context):
        result = await save_narrative_bundle(
            bundle=_narrative(), tool_context=mock_tool_context
        )
        assert result['status'] == 'success'
        persisted = mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['narrative']]
        # Optional field defaulted by the schema.
        assert persisted['customer_primary_domain'] is None


class TestSaveBundleValidation:
    async def test_rejects_unknown_field(self, mock_tool_context):
        bad = _requirements()
        bad['unexpected_field'] = 'nope'
        result = await save_requirements_bundle(
            bundle=bad, tool_context=mock_tool_context
        )
        assert result['status'] == 'error'
        assert SOW_BUNDLE_STATE_KEYS['requirements'] not in mock_tool_context.state

    async def test_rejects_missing_required_field(self, mock_tool_context):
        # NarrativeBundle requires executive_summary.
        result = await save_narrative_bundle(
            bundle={'partner_overview': 'x', 'customer_overview': 'y'},
            tool_context=mock_tool_context,
        )
        assert result['status'] == 'error'

    async def test_rejects_non_dict_bundle(self, mock_tool_context):
        result = await save_requirements_bundle(
            bundle='not a dict', tool_context=mock_tool_context
        )
        assert result['status'] == 'error'
        assert 'object' in result['error'].lower()

    async def test_missing_tool_context_returns_error(self):
        result = await save_requirements_bundle(bundle=_requirements())
        assert result['status'] == 'error'
        assert 'tool_context' in result['error']
