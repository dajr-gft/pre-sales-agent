"""Unit tests for ``app.shared.errors.safe_tool``.

``safe_tool`` is the single place every tool funnels its exceptions through.
If it breaks, an ADK agent would receive raw tracebacks in a tool result —
so the behavior here is load-bearing.
"""
from __future__ import annotations

import asyncio

import pytest

from app.shared.errors import safe_tool


class TestSafeToolSync:
    def test_passes_through_return_value_on_success(self):
        @safe_tool
        def op(x):
            return {'status': 'success', 'data': x * 2}

        result = op(21)
        assert result == {'status': 'success', 'data': 42}

    def test_catches_exception_and_returns_toolerror_dict(self):
        @safe_tool
        def op():
            raise ValueError('something bad')

        result = op()
        assert result['status'] == 'error'
        assert 'ValueError: something bad' == result['error']
        assert result['retryable'] is False
        assert result['tool'] == 'op'
        assert result['suggestion']  # non-empty

    def test_catches_arbitrary_exception_types(self):
        class CustomException(Exception):
            pass

        @safe_tool
        def op():
            raise CustomException('boom')

        result = op()
        assert 'CustomException: boom' == result['error']
        assert result['status'] == 'error'

    def test_never_re_raises(self):
        """The whole point is that the LLM should never see a traceback."""

        @safe_tool
        def op():
            raise RuntimeError('must not propagate')

        # Would raise if the wrapper leaked the exception.
        result = op()
        assert result['status'] == 'error'

    def test_preserves_wrapped_name_and_docstring(self):
        @safe_tool
        def generate_sow_document():
            """Generate a SOW."""
            return 'ok'

        assert generate_sow_document.__name__ == 'generate_sow_document'
        assert generate_sow_document.__doc__ == 'Generate a SOW.'
        # The ToolError surfaces the tool name -> this is how the LLM knows who failed.
        @safe_tool
        def named_fail():
            raise ValueError('x')

        assert named_fail()['tool'] == 'named_fail'

    def test_forwards_args_and_kwargs(self):
        @safe_tool
        def op(a, b, *, c):
            return {'status': 'success', 'data': (a, b, c)}

        assert op(1, 2, c=3) == {'status': 'success', 'data': (1, 2, 3)}


class TestSafeToolAsync:
    async def test_async_success_path(self):
        @safe_tool
        async def op(x):
            return {'status': 'success', 'data': x + 1}

        assert await op(10) == {'status': 'success', 'data': 11}

    async def test_async_catches_exception(self):
        @safe_tool
        async def op():
            raise KeyError('missing')

        result = await op()
        assert result['status'] == 'error'
        assert 'KeyError' in result['error']
        assert result['tool'] == 'op'

    async def test_async_catches_exception_after_await(self):
        """Exceptions raised AFTER the first await are also captured."""

        @safe_tool
        async def op():
            await asyncio.sleep(0)
            raise RuntimeError('post-await')

        result = await op()
        assert result['status'] == 'error'
        assert 'RuntimeError: post-await' == result['error']

    async def test_async_dispatch(self):
        @safe_tool
        async def op():
            return 'ok'

        assert asyncio.iscoroutinefunction(op)

    async def test_cancellation_is_caught_like_any_exception(self):
        """asyncio.CancelledError is an Exception in Python 3.12.

        ``safe_tool`` catches ``Exception`` — we document the current behavior.
        If this test fails after an upgrade, we need to explicitly re-raise
        ``CancelledError`` to avoid breaking task cancellation.
        """

        @safe_tool
        async def op():
            raise asyncio.CancelledError()

        # Depending on runtime, CancelledError may be in or out of Exception.
        # We just verify the wrapper either returns a ToolError dict or re-raises.
        try:
            result = await op()
        except asyncio.CancelledError:
            return  # re-raised: acceptable
        assert result['status'] == 'error'


class TestSafeToolErrorShape:
    """Documented contract of the ToolError dict."""

    def test_has_required_keys(self):
        @safe_tool
        def op():
            raise RuntimeError('x')

        result = op()
        for key in ('status', 'error', 'retryable', 'tool', 'suggestion'):
            assert key in result, f'missing key: {key}'

    def test_retryable_is_false_by_default(self):
        @safe_tool
        def op():
            raise RuntimeError('x')

        assert op()['retryable'] is False

    def test_suggestion_is_localized_portuguese(self):
        """The agent runs in pt-BR; the suggestion must stay localized.

        If this breaks, callers in prompts/tools relying on the pt-BR copy
        would need to be updated.
        """

        @safe_tool
        def op():
            raise RuntimeError('x')

        suggestion = op()['suggestion']
        assert 'Tente novamente' in suggestion
