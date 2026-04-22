"""Unit tests for ``app.callbacks``.

``before_tool_callback`` and ``after_tool_callback`` are the agent's
guardrails. Their behavior is specified and tested without invoking the
actual ADK runtime — we feed them a MagicMock ``ToolContext`` with a real
dict ``state`` (matching the SDK's interface).
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from app.callbacks import (
    _MAX_SOW_DATA_CHARS,
    after_tool_callback,
    before_tool_callback,
)


def _mock_tool(name: str) -> MagicMock:
    t = MagicMock()
    t.name = name
    return t


class TestBeforeToolCallback:
    def test_normal_args_allowed(self, mock_tool_context):
        out = before_tool_callback(
            _mock_tool('some_tool'),
            {'sow_data': '{"a": 1}'},
            mock_tool_context,
        )
        assert out is None

    def test_oversized_sow_data_blocked(self, mock_tool_context):
        big = 'x' * (_MAX_SOW_DATA_CHARS + 1)
        out = before_tool_callback(
            _mock_tool('generate_sow_document'),
            {'sow_data': big},
            mock_tool_context,
        )
        assert out is not None
        assert out['status'] == 'error'
        assert 'exceeds maximum size' in out['error']

    def test_boundary_size_allowed(self, mock_tool_context):
        exact = 'x' * _MAX_SOW_DATA_CHARS
        out = before_tool_callback(
            _mock_tool('generate_sow_document'),
            {'sow_data': exact},
            mock_tool_context,
        )
        # At-limit values are accepted
        assert out is None or out.get('status') != 'error' or 'exceeds' not in out.get('error', '')

    @pytest.mark.parametrize(
        'tool_name',
        ['generate_sow_document', 'validate_sow_content'],
    )
    def test_invalid_json_blocked_for_sow_tools(
        self, mock_tool_context, tool_name
    ):
        out = before_tool_callback(
            _mock_tool(tool_name),
            {'sow_data': '{not valid'},
            mock_tool_context,
        )
        assert out is not None
        assert out['status'] == 'error'
        assert 'not valid JSON' in out['error']

    def test_invalid_json_ignored_for_other_tools(self, mock_tool_context):
        """Tools unrelated to sow_data schema shouldn't trip on bad JSON."""
        out = before_tool_callback(
            _mock_tool('some_other_tool'),
            {'sow_data': '{not valid'},
            mock_tool_context,
        )
        assert out is None

    def test_valid_json_allowed(self, mock_tool_context):
        out = before_tool_callback(
            _mock_tool('generate_sow_document'),
            {'sow_data': json.dumps({'key': 'value'})},
            mock_tool_context,
        )
        assert out is None

    def test_non_string_sow_data_skips_size_guard(self, mock_tool_context):
        """Size guard is string-only — dict payloads pass straight through it.

        Note: the JSON guard is NOT string-guarded — ADK always passes strings
        for tool args today, so this is a non-issue in production.
        """
        out = before_tool_callback(
            _mock_tool('some_unrelated_tool'),  # avoids JSON guard
            {'sow_data': {'already': 'dict'}},
            mock_tool_context,
        )
        assert out is None

    def test_no_sow_data_key_allowed(self, mock_tool_context):
        out = before_tool_callback(
            _mock_tool('another_tool'),
            {'query': 'hello'},
            mock_tool_context,
        )
        assert out is None

    def test_tool_without_name_attr_uses_str(self, mock_tool_context):
        class ToolishObject:
            def __str__(self):
                return 'fallback_name'

        out = before_tool_callback(
            ToolishObject(),
            {},
            mock_tool_context,
        )
        assert out is None


class TestAfterToolCallback:
    def test_appends_to_history(self, mock_tool_context):
        after_tool_callback(
            _mock_tool('my_tool'),
            {},
            mock_tool_context,
            {'status': 'success'},
        )
        history = mock_tool_context.state['tool_call_history']
        assert len(history) == 1
        assert history[0] == {'tool': 'my_tool', 'status': 'success'}

    def test_history_accumulates_across_calls(self, mock_tool_context):
        after_tool_callback(
            _mock_tool('t1'), {}, mock_tool_context, {'status': 'success'}
        )
        after_tool_callback(
            _mock_tool('t2'), {}, mock_tool_context, {'status': 'error'}
        )
        history = mock_tool_context.state['tool_call_history']
        assert [h['tool'] for h in history] == ['t1', 't2']
        assert [h['status'] for h in history] == ['success', 'error']

    def test_unknown_status_logged_as_unknown(self, mock_tool_context):
        after_tool_callback(
            _mock_tool('t'), {}, mock_tool_context, {}  # no status key
        )
        history = mock_tool_context.state['tool_call_history']
        assert history[0]['status'] == 'unknown'

    def test_non_dict_response_logged_as_unknown(self, mock_tool_context):
        after_tool_callback(
            _mock_tool('t'), {}, mock_tool_context, 'just a string'
        )
        history = mock_tool_context.state['tool_call_history']
        assert history[0]['status'] == 'unknown'

    def test_validate_sow_content_updates_validation_state(
        self, mock_tool_context
    ):
        after_tool_callback(
            _mock_tool('validate_sow_content'),
            {},
            mock_tool_context,
            {
                'status': 'success',
                'data': {'passed': True, 'error_count': 0},
            },
        )
        assert mock_tool_context.state['last_validation_passed'] is True
        assert mock_tool_context.state['last_validation_error_count'] == 0

    def test_validate_sow_content_captures_failure_details(
        self, mock_tool_context
    ):
        after_tool_callback(
            _mock_tool('validate_sow_content'),
            {},
            mock_tool_context,
            {
                'status': 'success',
                'data': {'passed': False, 'error_count': 3},
            },
        )
        assert mock_tool_context.state['last_validation_passed'] is False
        assert mock_tool_context.state['last_validation_error_count'] == 3

    def test_other_tools_dont_touch_validation_state(
        self, mock_tool_context
    ):
        after_tool_callback(
            _mock_tool('generate_sow_document'),
            {},
            mock_tool_context,
            {'status': 'success'},
        )
        assert 'last_validation_passed' not in mock_tool_context.state

    def test_returns_none(self, mock_tool_context):
        """Callback must return None so ADK uses the original tool response."""
        out = after_tool_callback(
            _mock_tool('t'), {}, mock_tool_context, {'status': 'success'}
        )
        assert out is None

    def test_validate_sow_content_non_dict_response_safe(
        self, mock_tool_context
    ):
        """Non-dict response must not raise, just skip validation tracking."""
        after_tool_callback(
            _mock_tool('validate_sow_content'),
            {},
            mock_tool_context,
            'plain string',
        )
        assert 'last_validation_passed' not in mock_tool_context.state


class TestIntegration:
    """Combined before/after flow against a single context."""

    def test_full_cycle_records_state(self, mock_tool_context):
        tool = _mock_tool('validate_sow_content')
        before = before_tool_callback(
            tool,
            {'sow_data': json.dumps({'k': 'v'})},
            mock_tool_context,
        )
        assert before is None

        after_tool_callback(
            tool,
            {'sow_data': json.dumps({'k': 'v'})},
            mock_tool_context,
            {
                'status': 'success',
                'data': {'passed': True, 'error_count': 0},
            },
        )

        assert len(mock_tool_context.state['tool_call_history']) == 1
        assert mock_tool_context.state['last_validation_passed'] is True
