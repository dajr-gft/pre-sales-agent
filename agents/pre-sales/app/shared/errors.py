from __future__ import annotations

import asyncio
import time
from functools import wraps

import structlog

from .types import ToolError

logger = structlog.get_logger()


def safe_tool(func):
    """Production-grade tool wrapper.

    - Catches ALL exceptions -> returns ToolError (never crashes agent).
    - Logs execution time and errors with structlog.
    """

    @wraps(func)
    async def _async_wrapper(*args, **kwargs):
        start = time.perf_counter()
        log = logger.bind(tool=func.__name__)
        try:
            result = await func(*args, **kwargs)
            elapsed = time.perf_counter() - start
            log.info('tool_completed', duration_s=round(elapsed, 3))
            return result
        except Exception as e:
            elapsed = time.perf_counter() - start
            log.exception(
                'tool_failed',
                error=str(e),
                error_type=type(e).__name__,
                duration_s=round(elapsed, 3),
            )
            return ToolError(
                status='error',
                error=f'{type(e).__name__}: {e}',
                retryable=False,
                tool=func.__name__,
                suggestion='Tente novamente com parâmetros diferentes.',
            )

    @wraps(func)
    def _sync_wrapper(*args, **kwargs):
        start = time.perf_counter()
        log = logger.bind(tool=func.__name__)
        try:
            result = func(*args, **kwargs)
            elapsed = time.perf_counter() - start
            log.info('tool_completed', duration_s=round(elapsed, 3))
            return result
        except Exception as e:
            elapsed = time.perf_counter() - start
            log.exception(
                'tool_failed',
                error=str(e),
                error_type=type(e).__name__,
                duration_s=round(elapsed, 3),
            )
            return ToolError(
                status='error',
                error=f'{type(e).__name__}: {e}',
                retryable=False,
                tool=func.__name__,
                suggestion='Tente novamente com parâmetros diferentes.',
            )

    if asyncio.iscoroutinefunction(func):
        return _async_wrapper
    return _sync_wrapper
