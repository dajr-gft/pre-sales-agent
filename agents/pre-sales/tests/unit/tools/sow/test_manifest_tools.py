"""Unit tests for the coverage gate added to ``append_extraction_items``.

Focus: the new aggregate-count validation that prevents silent collapse on
dense enumerated artifacts. Per-item validation is exercised indirectly
through partial/coverage_mismatch interactions.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.tools.sow.manifest_tools import (
    append_extraction_items,
    initialize_extraction_buffer,
)


def _make_inventory_entry(artifact_id: str = 'A1') -> dict[str, Any]:
    return {
        'id': artifact_id,
        'name': f'{artifact_id}.csv',
        'type': 'txt',
        'phase_0_hypothesis': 'capability matrix',
        'source_language': 'pt',
    }


def _make_item(idx: int, artifact_id: str = 'A1') -> dict[str, Any]:
    return {
        'id': f'I-{idx:03d}',
        'category': 'Integrations',
        'value': f'System {idx}',
        'value_detail': f'Detail for system {idx}',
        'primitives': {
            'system_name': f'System{idx}',
            'direction': 'not_stated',
            'operations': 'not_stated',
            'data_class': 'not_stated',
            'protocol': 'not_stated',
            'ownership': 'not_stated',
            'criticality': 'not_stated',
        },
        'source': [{'artifact_id': artifact_id, 'anchor': f'row {idx}'}],
        'confidence': 'stated',
    }


@pytest.fixture
async def initialized_context(mock_tool_context):
    await initialize_extraction_buffer(
        conversation_language='pt-BR',
        inventory=[_make_inventory_entry('A1'), _make_inventory_entry('A2')],
        tool_context=mock_tool_context,
    )
    return mock_tool_context


class TestCoverageGatePass:
    async def test_match_returns_ok_with_no_coverage_error(
        self, initialized_context
    ):
        items = [_make_item(i) for i in range(1, 25)]
        result = await append_extraction_items(
            items=items,
            phase_1_1_enumerated_count=28,
            phase_1_2_skipped_count=4,
            tool_context=initialized_context,
            source_artifact_id='A1',
        )
        assert result['status'] == 'ok'
        assert result['items_appended_this_call'] == 24
        assert result['total_items_in_buffer'] == 24
        assert result['coverage_error'] is None

    async def test_zero_enumerated_skips_gate_for_path_a(
        self, initialized_context
    ):
        """Path A guided conversation passes 0 to bypass the visual gate."""
        items = [_make_item(1), _make_item(2)]
        result = await append_extraction_items(
            items=items,
            phase_1_1_enumerated_count=0,
            phase_1_2_skipped_count=0,
            tool_context=initialized_context,
            source_artifact_id='A1',
        )
        assert result['status'] == 'ok'
        assert result['coverage_error'] is None
        assert result['items_appended_this_call'] == 2

    async def test_path_a_self_consistent_count_passes(
        self, initialized_context
    ):
        """Path A typical pattern: enumerated == items + skipped."""
        items = [_make_item(i) for i in range(1, 4)]
        result = await append_extraction_items(
            items=items,
            phase_1_1_enumerated_count=3,
            phase_1_2_skipped_count=0,
            tool_context=initialized_context,
            source_artifact_id='A1',
        )
        assert result['status'] == 'ok'
        assert result['coverage_error'] is None


class TestCoverageMismatch:
    async def test_silent_collapse_returns_coverage_mismatch(
        self, initialized_context
    ):
        """The bug-report scenario: 17 items submitted from a 70-row matrix."""
        items = [_make_item(i) for i in range(1, 18)]
        result = await append_extraction_items(
            items=items,
            phase_1_1_enumerated_count=70,
            phase_1_2_skipped_count=0,
            tool_context=initialized_context,
            source_artifact_id='A11',
        )
        assert result['status'] == 'coverage_mismatch'
        assert result['coverage_error'] is not None
        assert result['coverage_error']['type'] == 'coverage_mismatch'
        assert 'A11' in result['coverage_error']['msg']

    async def test_mismatch_persists_valid_items(self, initialized_context):
        """Valid items are persisted even when the gate fails — recovery
        appends only the gap, never resubmits the originals."""
        items = [_make_item(i) for i in range(1, 18)]
        result = await append_extraction_items(
            items=items,
            phase_1_1_enumerated_count=70,
            phase_1_2_skipped_count=0,
            tool_context=initialized_context,
            source_artifact_id='A11',
        )
        assert result['status'] == 'coverage_mismatch'
        assert result['items_appended_this_call'] == 17
        assert result['total_items_in_buffer'] == 17
        # Buffer state actually contains the items.
        buffer = initialized_context.state['extraction_buffer']
        assert len(buffer['extracted_items']) == 17

    async def test_error_message_contains_silent_recovery_directive(
        self, initialized_context
    ):
        items = [_make_item(i) for i in range(1, 18)]
        result = await append_extraction_items(
            items=items,
            phase_1_1_enumerated_count=70,
            phase_1_2_skipped_count=0,
            tool_context=initialized_context,
            source_artifact_id='A11',
        )
        msg = result['coverage_error']['msg']
        assert 'DO NOT narrate' in msg
        assert '53' in msg  # gap = 70 - 17 = 53
        assert 'append_extraction_items' in msg

    async def test_recovery_flow_completes_buffer(self, initialized_context):
        """First call mismatches; second call closes the gap → buffer reflects total."""
        first_batch = [_make_item(i) for i in range(1, 18)]
        first = await append_extraction_items(
            items=first_batch,
            phase_1_1_enumerated_count=70,
            phase_1_2_skipped_count=0,
            tool_context=initialized_context,
            source_artifact_id='A11',
        )
        assert first['status'] == 'coverage_mismatch'

        gap_batch = [_make_item(i) for i in range(18, 71)]
        second = await append_extraction_items(
            items=gap_batch,
            phase_1_1_enumerated_count=53,
            phase_1_2_skipped_count=0,
            tool_context=initialized_context,
            source_artifact_id='A11',
        )
        assert second['status'] == 'ok'
        assert second['items_appended_this_call'] == 53
        assert second['total_items_in_buffer'] == 70
        assert second['coverage_error'] is None

    async def test_unspecified_artifact_label_handled(
        self, initialized_context
    ):
        """Omitting source_artifact_id still produces a usable error message."""
        items = [_make_item(1)]
        result = await append_extraction_items(
            items=items,
            phase_1_1_enumerated_count=10,
            phase_1_2_skipped_count=0,
            tool_context=initialized_context,
        )
        assert result['status'] == 'coverage_mismatch'
        assert '<unspecified>' in result['coverage_error']['msg']


class TestStatusPrecedence:
    async def test_partial_with_coverage_match_returns_partial(
        self, initialized_context
    ):
        """Per-item failure but coverage gate satisfied → partial (not coverage_mismatch)."""
        valid_items = [_make_item(i) for i in range(1, 4)]
        bad_item = _make_item(99)
        bad_item['category'] = 'NotARealCategory'
        items = valid_items + [bad_item]
        result = await append_extraction_items(
            items=items,
            phase_1_1_enumerated_count=4,
            phase_1_2_skipped_count=0,
            tool_context=initialized_context,
            source_artifact_id='A1',
        )
        assert result['status'] == 'partial'
        assert result['items_appended_this_call'] == 3
        assert result['coverage_error'] is None
        assert len(result['errors_per_item']) == 1

    async def test_partial_with_coverage_mismatch_returns_coverage_mismatch(
        self, initialized_context
    ):
        """When BOTH per-item errors AND aggregate mismatch exist, coverage_mismatch
        wins. Both error channels are populated; the model gets full info."""
        valid_items = [_make_item(i) for i in range(1, 4)]
        bad_item = _make_item(99)
        bad_item['category'] = 'NotARealCategory'
        items = valid_items + [bad_item]
        result = await append_extraction_items(
            items=items,
            phase_1_1_enumerated_count=20,
            phase_1_2_skipped_count=0,
            tool_context=initialized_context,
            source_artifact_id='A1',
        )
        assert result['status'] == 'coverage_mismatch'
        assert result['coverage_error'] is not None
        assert len(result['errors_per_item']) == 1

    async def test_no_appended_with_coverage_mismatch_returns_error(
        self, initialized_context
    ):
        """If nothing salvageable AND coverage off, status is plain error."""
        bad_item = _make_item(99)
        bad_item['category'] = 'NotARealCategory'
        result = await append_extraction_items(
            items=[bad_item],
            phase_1_1_enumerated_count=10,
            phase_1_2_skipped_count=0,
            tool_context=initialized_context,
            source_artifact_id='A1',
        )
        assert result['status'] == 'error'

    async def test_buffer_not_initialized_returns_error_with_coverage_field(
        self, mock_tool_context
    ):
        """Missing-buffer error path keeps the coverage_error field populated as None."""
        result = await append_extraction_items(
            items=[_make_item(1)],
            phase_1_1_enumerated_count=1,
            phase_1_2_skipped_count=0,
            tool_context=mock_tool_context,
            source_artifact_id='A1',
        )
        assert result['status'] == 'error'
        assert result['coverage_error'] is None
        assert result['errors_per_item'][0]['errors'][0]['type'] == 'buffer_not_initialized'


class TestResponseShape:
    async def test_ok_response_includes_coverage_error_key(
        self, initialized_context
    ):
        """The new coverage_error field is always present in the response."""
        result = await append_extraction_items(
            items=[_make_item(1)],
            phase_1_1_enumerated_count=1,
            phase_1_2_skipped_count=0,
            tool_context=initialized_context,
            source_artifact_id='A1',
        )
        assert 'coverage_error' in result
        assert result['coverage_error'] is None

    async def test_coverage_mismatch_response_shape_complete(
        self, initialized_context
    ):
        result = await append_extraction_items(
            items=[_make_item(1)],
            phase_1_1_enumerated_count=10,
            phase_1_2_skipped_count=0,
            tool_context=initialized_context,
            source_artifact_id='A1',
        )
        assert set(result.keys()) == {
            'status',
            'items_appended_this_call',
            'total_items_in_buffer',
            'errors_per_item',
            'coverage_error',
        }
        assert set(result['coverage_error'].keys()) == {'loc', 'msg', 'type'}
