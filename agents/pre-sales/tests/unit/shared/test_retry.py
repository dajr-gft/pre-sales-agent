"""Unit tests for ``app.shared.retry``.

Covers both the sync and async variants of ``with_rate_limit_retry`` so a
regression in either branch is caught. Time-sensitive behavior is patched
(``asyncio.sleep``, ``time.sleep``, ``random.uniform``) to keep tests
deterministic and sub-second.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.shared.retry import RetryableError, with_rate_limit_retry


class TestRetryableError:
    def test_default_retry_after_is_none(self):
        err = RetryableError('rate-limited')
        assert err.retry_after is None
        assert str(err) == 'rate-limited'

    def test_retry_after_preserved(self):
        err = RetryableError('429', retry_after=2.5)
        assert err.retry_after == 2.5


class TestSyncRetry:
    def test_returns_result_on_first_success(self):
        calls = []

        @with_rate_limit_retry(max_retries=3)
        def op():
            calls.append(1)
            return 'ok'

        assert op() == 'ok'
        assert len(calls) == 1

    def test_retries_on_retryable_error_and_succeeds(self):
        calls = {'n': 0}

        @with_rate_limit_retry(max_retries=3, base_delay=0.0)
        def op():
            calls['n'] += 1
            if calls['n'] < 3:
                raise RetryableError('temporary')
            return 'ok'

        with patch('app.shared.retry.time.sleep'), patch(
            'app.shared.retry.random.uniform', return_value=0.0
        ):
            assert op() == 'ok'
        assert calls['n'] == 3

    def test_exhausts_retries_then_raises(self):
        @with_rate_limit_retry(max_retries=2, base_delay=0.0)
        def op():
            raise RetryableError('always')

        with patch('app.shared.retry.time.sleep'), patch(
            'app.shared.retry.random.uniform', return_value=0.0
        ):
            with pytest.raises(RetryableError, match='always'):
                op()

    def test_non_retryable_exception_propagates_immediately(self):
        calls = []

        @with_rate_limit_retry(max_retries=5)
        def op():
            calls.append(1)
            raise ValueError('fatal')

        with pytest.raises(ValueError, match='fatal'):
            op()
        assert len(calls) == 1, 'non-retryable errors must not trigger retries'

    def test_respects_retry_after_header(self):
        """When the error carries ``retry_after``, that value wins over backoff."""
        calls = {'n': 0}

        @with_rate_limit_retry(max_retries=2, base_delay=100.0, max_delay=60.0)
        def op():
            calls['n'] += 1
            if calls['n'] == 1:
                raise RetryableError('429', retry_after=1.5)
            return 'ok'

        with patch('app.shared.retry.time.sleep') as sleep_mock, patch(
            'app.shared.retry.random.uniform', return_value=0.0
        ):
            assert op() == 'ok'
        sleep_mock.assert_called_once_with(1.5)

    def test_retry_after_capped_at_max_delay(self):
        @with_rate_limit_retry(max_retries=1, base_delay=0.0, max_delay=5.0)
        def op():
            raise RetryableError('429', retry_after=999.0)

        with patch('app.shared.retry.time.sleep') as sleep_mock, patch(
            'app.shared.retry.random.uniform', return_value=0.0
        ):
            with pytest.raises(RetryableError):
                op()
        # 999 > max_delay=5 → should sleep(5)
        sleep_mock.assert_called_once_with(5.0)

    def test_exponential_backoff_progression(self):
        @with_rate_limit_retry(max_retries=3, base_delay=1.0, max_delay=60.0)
        def op():
            raise RetryableError('fail')

        with patch('app.shared.retry.time.sleep') as sleep_mock, patch(
            'app.shared.retry.random.uniform', return_value=0.0
        ):
            with pytest.raises(RetryableError):
                op()

        # attempts 0,1,2 → delays 1, 2, 4 (2^attempt * base_delay, jitter=0)
        called_delays = [c.args[0] for c in sleep_mock.call_args_list]
        assert called_delays == [1.0, 2.0, 4.0]

    def test_backoff_capped_at_max_delay(self):
        @with_rate_limit_retry(max_retries=5, base_delay=100.0, max_delay=10.0)
        def op():
            raise RetryableError('fail')

        with patch('app.shared.retry.time.sleep') as sleep_mock, patch(
            'app.shared.retry.random.uniform', return_value=0.0
        ):
            with pytest.raises(RetryableError):
                op()
        called_delays = [c.args[0] for c in sleep_mock.call_args_list]
        # All attempts would compute >= 100 → always capped to 10
        assert all(d == 10.0 for d in called_delays)

    def test_preserves_function_metadata(self):
        @with_rate_limit_retry()
        def my_function(x, y):
            """docstring"""
            return x + y

        assert my_function.__name__ == 'my_function'
        assert my_function.__doc__ == 'docstring'

    def test_forwards_args_and_kwargs(self):
        @with_rate_limit_retry()
        def op(a, b, *, c):
            return (a, b, c)

        assert op(1, 2, c=3) == (1, 2, 3)


class TestAsyncRetry:
    async def test_returns_result_on_first_success(self):
        @with_rate_limit_retry(max_retries=3)
        async def op():
            return 'ok'

        assert await op() == 'ok'

    async def test_retries_async_on_retryable_error(self):
        state = {'n': 0}

        @with_rate_limit_retry(max_retries=3, base_delay=0.0)
        async def op():
            state['n'] += 1
            if state['n'] < 3:
                raise RetryableError('temporary')
            return 'ok'

        with patch(
            'app.shared.retry.asyncio.sleep', new_callable=AsyncMock
        ), patch('app.shared.retry.random.uniform', return_value=0.0):
            assert await op() == 'ok'
        assert state['n'] == 3

    async def test_async_exhausts_retries_then_raises(self):
        @with_rate_limit_retry(max_retries=2, base_delay=0.0)
        async def op():
            raise RetryableError('always')

        with patch(
            'app.shared.retry.asyncio.sleep', new_callable=AsyncMock
        ), patch('app.shared.retry.random.uniform', return_value=0.0):
            with pytest.raises(RetryableError, match='always'):
                await op()

    async def test_async_uses_asyncio_sleep_not_time_sleep(self):
        @with_rate_limit_retry(max_retries=1, base_delay=0.0)
        async def op():
            raise RetryableError('fail')

        with patch(
            'app.shared.retry.asyncio.sleep', new_callable=AsyncMock
        ) as aio_sleep, patch(
            'app.shared.retry.time.sleep'
        ) as time_sleep, patch(
            'app.shared.retry.random.uniform', return_value=0.0
        ):
            with pytest.raises(RetryableError):
                await op()
        assert aio_sleep.called
        assert not time_sleep.called

    async def test_async_respects_retry_after(self):
        attempts = {'n': 0}

        @with_rate_limit_retry(max_retries=2, base_delay=100.0)
        async def op():
            attempts['n'] += 1
            if attempts['n'] == 1:
                raise RetryableError('429', retry_after=2.0)
            return 'ok'

        with patch(
            'app.shared.retry.asyncio.sleep', new_callable=AsyncMock
        ) as aio_sleep:
            assert await op() == 'ok'
        aio_sleep.assert_awaited_once_with(2.0)


class TestDispatch:
    """``with_rate_limit_retry`` must dispatch sync vs async based on the target."""

    def test_returns_async_wrapper_for_coroutine(self):
        import asyncio

        @with_rate_limit_retry()
        async def op():
            return 1

        assert asyncio.iscoroutinefunction(op)

    def test_returns_sync_wrapper_for_plain_function(self):
        import asyncio

        @with_rate_limit_retry()
        def op():
            return 1

        assert not asyncio.iscoroutinefunction(op)


class TestRealisticScenario:
    """Slightly higher-level sanity check that ties everything together."""

    def test_sync_recovers_from_two_rate_limits(self):
        client = MagicMock()
        client.call.side_effect = [
            RetryableError('429', retry_after=0.1),
            RetryableError('429'),
            'result',
        ]

        @with_rate_limit_retry(max_retries=5, base_delay=0.0)
        def op():
            return client.call()

        with patch('app.shared.retry.time.sleep'), patch(
            'app.shared.retry.random.uniform', return_value=0.0
        ):
            assert op() == 'result'
        assert client.call.call_count == 3
