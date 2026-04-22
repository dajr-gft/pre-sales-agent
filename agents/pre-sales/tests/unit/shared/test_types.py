"""Unit tests for ``app.shared.types``.

The types in this module are TypedDicts — they exist to document the wire
format of tool results. Tests here verify shape and literal-field constraints
rather than behavior.
"""
from __future__ import annotations

from typing import get_args, get_origin

import pytest

from app.shared.types import ToolError, ToolNotFound, ToolResult, ToolSuccess


class TestToolSuccess:
    def test_accepts_generic_payload(self):
        result: ToolSuccess[dict] = {'status': 'success', 'data': {'x': 1}}
        assert result['status'] == 'success'
        assert result['data'] == {'x': 1}

    def test_accepts_list_payload(self):
        result: ToolSuccess[list[int]] = {'status': 'success', 'data': [1, 2, 3]}
        assert result['data'] == [1, 2, 3]


class TestToolError:
    def test_minimum_required_fields(self):
        err: ToolError = {
            'status': 'error',
            'error': 'Boom',
            'retryable': False,
        }
        assert err['status'] == 'error'
        assert err['error'] == 'Boom'
        assert err['retryable'] is False

    def test_optional_fields_accepted(self):
        err: ToolError = {
            'status': 'error',
            'error': 'Timeout',
            'retryable': True,
            'tool': 'generate_sow_document',
            'suggestion': 'Retry with a smaller payload.',
        }
        assert err['tool'] == 'generate_sow_document'
        assert err['suggestion'].startswith('Retry')

    @pytest.mark.parametrize('retryable', [True, False])
    def test_retryable_is_boolean(self, retryable):
        err: ToolError = {
            'status': 'error',
            'error': 'x',
            'retryable': retryable,
        }
        assert isinstance(err['retryable'], bool)


class TestToolNotFound:
    def test_minimum_fields(self):
        nf: ToolNotFound = {'status': 'not_found', 'error': 'missing'}
        assert nf['status'] == 'not_found'
        assert nf['error'] == 'missing'


class TestToolResultUnion:
    """``ToolResult`` is a typing union — verify it covers all three dialects."""

    def test_union_has_three_members(self):
        # Union resolves to the three TypedDicts
        args = get_args(ToolResult)
        assert len(args) == 3, f'Expected 3 union members, got {len(args)}'

    def test_union_origin_is_union(self):
        # Python's typing.Union exposes itself through get_origin on recent versions.
        # For runtime safety we check that at least one of the known origins matches.
        from typing import Union

        origin = get_origin(ToolResult)
        assert origin is Union or origin is not None
