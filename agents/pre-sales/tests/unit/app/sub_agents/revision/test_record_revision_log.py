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

    async def test_empty_entries_requires_noop_reason(self, mock_tool_context):
        """F-11: silent empty rounds masked bugs where the revision
        agent ran but did nothing. Calling with ``entries=[]`` now
        requires a ``noop_reason`` so the log has evidence of why the
        round produced zero patches."""
        result = await record_revision_log_entries(
            entries=[], tool_context=mock_tool_context,
        )

        assert result['status'] == 'error'
        assert 'noop_reason' in result['error']
        # State must NOT have been mutated on rejection — partial writes
        # would leak the rejected call into telemetry.
        assert REVISION_LOG_STATE_KEY not in mock_tool_context.state

    async def test_empty_entries_with_noop_reason_appends_synthetic_marker(
        self, mock_tool_context
    ):
        """F-11: when the reason is provided the tool appends one
        synthetic entry with ``action='noop'`` and the supplied reason.
        Downstream Revision Note composers skip noop entries — see the
        root prompt."""
        result = await record_revision_log_entries(
            entries=[],
            noop_reason='all findings deferred to human review',
            round_label='round-3',
            tool_context=mock_tool_context,
        )

        assert result['status'] == 'success'
        assert result['data']['noop'] is True
        assert result['data']['appended'] == 1

        log = mock_tool_context.state[REVISION_LOG_STATE_KEY]
        assert len(log) == 1
        entry = log[0]
        assert entry['action'] == 'noop'
        assert entry['fields_touched'] == []
        assert entry['reason'] == 'all findings deferred to human review'
        assert entry['round_label'] == 'round-3'
        # Synthetic finding_id carries the round label so audit reads
        # can group multiple rounds without rebuilding context.
        assert entry['finding_id'] == '__noop__::round-3'

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
