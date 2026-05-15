"""Unit tests for ``record_revision_log_entries``."""

from __future__ import annotations

import pytest

from app.sub_agents.revision.log_tools import (
    REVISION_LOG_STATE_KEY,
    record_revision_log_entries,
)


def _valid_entry(**overrides):
    base = {
        'finding_id': 'coverage-001',
        'skill': 'coverage',
        'category': 'manifest_item_uncovered',
        'action': 'addition',
        'fields_touched': ['functional_requirements'],
    }
    base.update(overrides)
    return base


class TestAppendBehaviour:
    async def test_first_call_initializes_log(self, mock_tool_context):
        entry = _valid_entry()

        result = await record_revision_log_entries(
            entries=[entry],
            tool_context=mock_tool_context,
        )

        assert result['status'] == 'success'
        assert mock_tool_context.state[REVISION_LOG_STATE_KEY] == [entry]
        assert result['data']['appended'] == 1
        assert result['data']['total'] == 1

    async def test_second_call_appends_does_not_replace(self, mock_tool_context):
        first = _valid_entry(finding_id='coverage-001')
        second = _valid_entry(finding_id='contradictions-002', skill='contradictions')

        await record_revision_log_entries(
            entries=[first], tool_context=mock_tool_context,
        )
        result = await record_revision_log_entries(
            entries=[second], tool_context=mock_tool_context,
        )

        assert mock_tool_context.state[REVISION_LOG_STATE_KEY] == [first, second]
        assert result['data']['total'] == 2

    async def test_empty_entries_list_is_valid(self, mock_tool_context):
        result = await record_revision_log_entries(
            entries=[], tool_context=mock_tool_context,
        )

        assert result['status'] == 'success'
        assert mock_tool_context.state[REVISION_LOG_STATE_KEY] == []

    async def test_non_list_state_replaced_with_fresh(self, mock_tool_context):
        """Defensive: someone else wrote a non-list — start over."""
        mock_tool_context.state[REVISION_LOG_STATE_KEY] = 'corrupted-string'

        entry = _valid_entry()
        result = await record_revision_log_entries(
            entries=[entry], tool_context=mock_tool_context,
        )

        assert result['status'] == 'success'
        assert mock_tool_context.state[REVISION_LOG_STATE_KEY] == [entry]


class TestEntryValidation:
    async def test_rejects_non_list_entries_argument(self, mock_tool_context):
        result = await record_revision_log_entries(
            entries='not a list',  # type: ignore[arg-type]
            tool_context=mock_tool_context,
        )
        assert result['status'] == 'error'
        assert 'list' in result['error']

    async def test_rejects_non_dict_entry(self, mock_tool_context):
        result = await record_revision_log_entries(
            entries=['not a dict'],  # type: ignore[list-item]
            tool_context=mock_tool_context,
        )
        assert result['status'] == 'error'

    async def test_rejects_entry_missing_required_key(self, mock_tool_context):
        bad = _valid_entry()
        del bad['fields_touched']

        result = await record_revision_log_entries(
            entries=[bad], tool_context=mock_tool_context,
        )

        assert result['status'] == 'error'
        assert 'fields_touched' in result['error']

    async def test_one_bad_entry_blocks_whole_batch(self, mock_tool_context):
        good = _valid_entry()
        bad = _valid_entry()
        del bad['action']

        result = await record_revision_log_entries(
            entries=[good, bad], tool_context=mock_tool_context,
        )

        assert result['status'] == 'error'
        assert REVISION_LOG_STATE_KEY not in mock_tool_context.state
