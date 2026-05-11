"""Unit tests for ``app.tools.recovery._request_continuation``.

The tool is dispatched by ``empty_response_guard``; the contract here is
simply that it returns a stable payload ADK can feed back to the model
without referencing any specific skill or phase.
"""
from __future__ import annotations

from app.tools.recovery import _CONTINUATION_INSTRUCTION, _request_continuation


def test_returns_continue_status(mock_tool_context):
    out = _request_continuation(mock_tool_context)
    assert out['status'] == 'continue'


def test_returns_instruction_string(mock_tool_context):
    out = _request_continuation(mock_tool_context)
    assert out['instruction'] == _CONTINUATION_INSTRUCTION


def test_instruction_is_phase_and_skill_agnostic():
    """The recovery instruction must not bake in specific names so the
    same tool serves any place the model emits an empty terminal turn."""
    needle = _CONTINUATION_INSTRUCTION.lower()
    forbidden = (
        'content review',
        'inference summary',
        'architecture review',
        'sow-generator',
        'sow-discovery',
        'phase 1',
        'phase 2',
        'phase 3',
        'style-guide',
        'scope-examples',
    )
    for token in forbidden:
        assert token not in needle, f'instruction must not reference {token!r}'


def test_state_with_attempt_counter_is_read_safely(mock_tool_context):
    """Reading the optional ``_empty_response_attempts`` counter must not raise
    when it's missing, set to zero, or set to a normal positive value."""
    # Missing counter
    _request_continuation(mock_tool_context)

    # Zero
    mock_tool_context.state['_empty_response_attempts'] = 0
    _request_continuation(mock_tool_context)

    # Positive
    mock_tool_context.state['_empty_response_attempts'] = 2
    _request_continuation(mock_tool_context)
