from __future__ import annotations

import asyncio
import random
import time
from functools import wraps

import structlog

logger = structlog.get_logger()


class RetryableError(Exception):
    """Raise inside a tool to signal that the operation can be retried."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


def with_rate_limit_retry(
    max_retries: int = 5,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
):
    """Decorator for exponential backoff + jitter on RetryableError.

    Handles HTTP 429 / 5xx by retrying with exponential backoff.
    Respects ``Retry-After`` header when provided via ``RetryableError.retry_after``.

    Args:
        max_retries: Maximum number of retry attempts.
        base_delay: Initial delay in seconds before first retry.
        max_delay: Maximum delay cap in seconds.
    """

    def decorator(func):
        @wraps(func)
        async def _async_wrapper(*args, **kwargs):
            last_error = None
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except RetryableError as e:
                    last_error = e
                    if attempt == max_retries:
                        break
                    if e.retry_after is not None:
                        delay = min(e.retry_after, max_delay)
                    else:
                        delay = min(
                            base_delay * (2**attempt) + random.uniform(0, 1),
                            max_delay,
                        )
                    logger.warning(
                        "retrying",
                        tool=func.__name__,
                        attempt=attempt + 1,
                        max_retries=max_retries,
                        delay_s=round(delay, 2),
                        error=str(e),
                    )
                    await asyncio.sleep(delay)
            raise last_error  # type: ignore[misc]

        @wraps(func)
        def _sync_wrapper(*args, **kwargs):
            last_error = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except RetryableError as e:
                    last_error = e
                    if attempt == max_retries:
                        break
                    if e.retry_after is not None:
                        delay = min(e.retry_after, max_delay)
                    else:
                        delay = min(
                            base_delay * (2**attempt) + random.uniform(0, 1),
                            max_delay,
                        )
                    logger.warning(
                        "retrying",
                        tool=func.__name__,
                        attempt=attempt + 1,
                        max_retries=max_retries,
                        delay_s=round(delay, 2),
                        error=str(e),
                    )
                    time.sleep(delay)
            raise last_error  # type: ignore[misc]

        if asyncio.iscoroutinefunction(func):
            return _async_wrapper
        return _sync_wrapper

    return decorator
