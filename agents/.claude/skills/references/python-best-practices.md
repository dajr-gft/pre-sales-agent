# Python Best Practices for ADK Agents

## Table of Contents
1. [Project Structure Rules](#project-structure)
2. [Type Safety](#type-safety)
3. [Tool Function Signatures](#tool-signatures)
4. [Safe Tool Decorator](#safe-tool)
5. [Async Patterns](#async-patterns)
6. [Configuration Management](#config)
7. [Testing Strategy](#testing)
8. [Code Style Rules](#code-style)
9. [Common Pitfalls](#pitfalls)

---

## Project Structure

### Rules (ASP-Aligned)
- `app/agent.py` is thin — only imports and wiring, ZERO business logic.
- `app/__init__.py` contains `from . import agent`.
- Sub-agents each in `app/sub_agents/{name}/` with own `agent.py`, `prompts.py`, `tools.py`.
- Instructions as Python string constants in `prompts.py`, never inline strings.
- `app/shared/retry.py` with `with_rate_limit_retry` in EVERY project.
- `app/shared/errors.py` with `safe_tool` decorator in EVERY project.
- Every external call wrapped with 429 retry handling.
- Tests in `tests/unit/`, `tests/integration/`, `tests/load/`.

---

## Type Safety

### Always Type Tool Functions

```python
# ✅ GOOD — fully typed, docstring describes each param
def search_database(
    query: str,
    max_results: int = 10,
    include_metadata: bool = False,
) -> dict:
    """Search the database for matching records.

    Args:
        query: SQL-like search query string.
        max_results: Maximum number of results to return (1-100). Defaults to 10.
        include_metadata: Whether to include record metadata. Defaults to False.

    Returns:
        Dict with 'results' list and 'total_count' int.
    """
    ...
```

> **WHY**: ADK inspects type hints + docstrings to generate the function schema
> sent to the LLM. Without them, the model cannot use the tool correctly.

### Pydantic for Complex I/O

```python
from pydantic import BaseModel, Field

class SearchResult(BaseModel):
    id: str = Field(description="Unique record identifier")
    title: str = Field(description="Record title")
    score: float = Field(description="Relevance score 0.0-1.0", ge=0, le=1)
    metadata: dict | None = Field(default=None, description="Optional metadata")

class SearchResponse(BaseModel):
    results: list[SearchResult]
    total_count: int = Field(description="Total matches found")
    next_cursor: str | None = Field(default=None, description="Pagination cursor")
```

### Enums for Constrained Values

```python
from enum import Enum

class Priority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

def create_ticket(
    title: str,
    priority: Priority = Priority.MEDIUM,
    description: str = "",
) -> dict:
    """Create a support ticket.

    Args:
        title: Brief title for the ticket.
        priority: Urgency level. Defaults to medium.
        description: Detailed description of the issue.
    """
    ...
```

---

## Tool Function Signatures

### The ToolContext Pattern

```python
from google.adk.tools import ToolContext

def my_tool(
    param1: str,
    param2: int,
    tool_context: ToolContext,  # injected by ADK — NOT in docstring Args
) -> dict:
    """Tool description — one sentence explaining what it does.

    Args:
        param1: What param1 is.
        param2: What param2 is.
    """
    previous = tool_context.state.get("app:previous_result")
    tool_context.state["app:my_result"] = result
    return {"status": "success", "data": result}
```

> **CRITICAL**: `tool_context` is injected by ADK — do NOT include it in docstring Args.

### Return Value Rules

1. **Always return a dict** — ADK serializes it for the LLM.
2. **Always include `status`** — `"success"`, `"error"`, or `"no_results"`.
3. **Keep returns concise** — summarize or paginate, never dump raw data.
4. **Never return None** — return `{"status": "no_results"}`.
5. **On error, include `suggestion`** — help the LLM recover gracefully.

---

## Safe Tool Decorator

**Apply to EVERY tool function** — combines error handling + 429 retry:

```python
# app/shared/errors.py
import logging
import asyncio
from functools import wraps
from app.shared.retry import with_rate_limit_retry

logger = logging.getLogger(__name__)


def safe_tool(func):
    """Wraps tool functions with structured error handling.
    
    - Catches all exceptions and returns error dict (never crashes agent).
    - Logs with full traceback for debugging.
    - Provides suggestion to help LLM recover.
    """
    @wraps(func)
    def sync_wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger.exception(f"Tool {func.__name__} failed")
            return {
                "status": "error",
                "error": f"{type(e).__name__}: {e}",
                "tool": func.__name__,
                "suggestion": "Try different parameters or a simpler request.",
            }

    @wraps(func)
    async def async_wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            logger.exception(f"Tool {func.__name__} failed")
            return {
                "status": "error",
                "error": f"{type(e).__name__}: {e}",
                "tool": func.__name__,
                "suggestion": "Try different parameters or a simpler request.",
            }

    return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper


# USAGE — combine @safe_tool with @with_rate_limit_retry for external calls:

@safe_tool
@with_rate_limit_retry(max_retries=3, base_delay=1.0)
async def call_external_api(url: str, tool_context: ToolContext) -> dict:
    """Call an external API.

    Args:
        url: The API endpoint URL.
    """
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status == 429:
                retry_after = resp.headers.get("Retry-After")
                raise RetryableError(f"HTTP 429", retry_after=float(retry_after) if retry_after else None)
            if resp.status >= 500:
                raise RetryableError(f"HTTP {resp.status}")
            resp.raise_for_status()
            data = await resp.json()
            return {"status": "success", "data": data}
```

### Order of Decorators

```python
@safe_tool                    # outermost — catches everything
@with_rate_limit_retry(...)   # middle — retries on 429/5xx
async def my_tool(...):       # innermost — actual logic
    ...
```

---

## Async Patterns

### Event Loop Safety

ADK runs inside asyncio. **NEVER call `asyncio.run()` inside a tool.**

```python
# ❌ CRASHES — nested event loop
def bad_tool() -> dict:
    result = asyncio.run(some_async_func())  # RuntimeError!
    return result

# ✅ Make the tool async
async def good_tool() -> dict:
    result = await some_async_func()
    return result
```

### Concurrent Operations with Semaphore

```python
async def parallel_search(
    queries: list[str],
    tool_context: ToolContext,
) -> dict:
    """Run multiple searches in parallel with concurrency limit.

    Args:
        queries: List of search queries.
    """
    semaphore = asyncio.Semaphore(5)  # max 5 concurrent

    async def bounded(q: str) -> dict:
        async with semaphore:
            return await execute_search(q)

    results = await asyncio.gather(
        *[bounded(q) for q in queries],
        return_exceptions=True,
    )
    return {
        "status": "success",
        "results": [
            r if not isinstance(r, Exception) else {"error": str(r)}
            for r in results
        ],
    }
```

### Blocking I/O — Run in Executor

```python
import asyncio
from concurrent.futures import ThreadPoolExecutor

_executor = ThreadPoolExecutor(max_workers=4)

async def async_wrapper_for_sync(sync_func, *args):
    """Run a blocking function without blocking the event loop."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, sync_func, *args)
```

---

## Configuration Management

```python
# config.py
import os
from dataclasses import dataclass

@dataclass(frozen=True)
class AgentConfig:
    # GCP
    project_id: str = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    location: str = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")

    # Models
    router_model: str = os.environ.get("ROUTER_MODEL", "gemini-3-flash")
    reasoning_model: str = os.environ.get("REASONING_MODEL", "gemini-3.1-pro")

    # Retry config
    max_retries: int = int(os.environ.get("MAX_RETRIES", "5"))
    retry_base_delay: float = float(os.environ.get("RETRY_BASE_DELAY", "1.0"))
    retry_max_delay: float = float(os.environ.get("RETRY_MAX_DELAY", "60.0"))

    # Feature flags
    enable_memory: bool = os.environ.get("ENABLE_MEMORY", "false").lower() == "true"
    max_tool_results: int = int(os.environ.get("MAX_TOOL_RESULTS", "50"))

config = AgentConfig()
```

---

## Testing Strategy

### Unit Test Tools (no LLM)

```python
import pytest
from unittest.mock import MagicMock

@pytest.fixture
def mock_tool_context():
    ctx = MagicMock()
    ctx.state = {}
    return ctx

def test_search_success(mock_tool_context):
    result = search_database("test", tool_context=mock_tool_context)
    assert result["status"] == "success"
    assert "results" in result

def test_search_empty(mock_tool_context):
    result = search_database("", tool_context=mock_tool_context)
    assert result["status"] == "error"

def test_search_updates_state(mock_tool_context):
    search_database("test", tool_context=mock_tool_context)
    assert "app:search_result" in mock_tool_context.state
```

### Test 429 Retry Behavior

```python
import pytest
from unittest.mock import patch, AsyncMock
from app.shared.retry import with_rate_limit_retry, RetryableError

@pytest.mark.asyncio
async def test_retries_on_429():
    call_count = 0

    @with_rate_limit_retry(max_retries=3, base_delay=0.01)
    async def flaky_api():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise RetryableError("HTTP 429")
        return {"status": "success"}

    result = await flaky_api()
    assert result["status"] == "success"
    assert call_count == 3

@pytest.mark.asyncio
async def test_exhausts_retries():
    @with_rate_limit_retry(max_retries=2, base_delay=0.01)
    async def always_fails():
        raise RetryableError("HTTP 429")

    result = await always_fails()
    assert result["status"] == "error"
    assert result["retryable"] is True
```

### Integration Test with Runner

```python
from google.adk.runners import InMemoryRunner
from google.adk.sessions import InMemorySessionService

@pytest.fixture
def runner():
    return InMemoryRunner(
        agent=root_agent,
        session_service=InMemorySessionService(),
    )

@pytest.mark.asyncio
async def test_agent_responds(runner):
    session = await runner.session_service.create_session(
        app_name="test", user_id="tester"
    )
    response = await runner.run(
        session_id=session.id, user_id="tester",
        new_message="Hello, what can you do?",
    )
    events = [e async for e in response]
    text_events = [e for e in events if e.content and e.content.parts]
    assert len(text_events) > 0
```

### Prompt Regression Tests

```python
import hashlib
from prompts.discovery import DISCOVERY_INSTRUCTION

EXPECTED_HASH = "a1b2c3d4"  # update when intentionally changing

def test_prompt_unchanged():
    actual = hashlib.sha256(DISCOVERY_INSTRUCTION.encode()).hexdigest()[:8]
    assert actual == EXPECTED_HASH, (
        f"Prompt changed! Expected {EXPECTED_HASH}, got {actual}. "
        "Update hash if this was intentional."
    )
```

---

## Code Style Rules

1. **Python 3.11+** — `X | None` not `Optional[X]`.
2. **f-strings** — never `.format()` or `%`.
3. **Descriptive names** — `fetch_customer_orders()` not `get_data()`.
4. **Google-style docstrings** on every public function.
5. **No star imports** — `from tools import *` is forbidden.
6. **Constants UPPER_SNAKE** — `MAX_RETRIES = 3`.
7. **No mutable defaults** — `None` + factory.
8. **Explicit returns** — every path returns a dict.

---

## Common Pitfalls

### Module-Level Side Effects

```python
# ❌ Runs on import — fails in tests
client = bigquery.Client()

# ✅ Lazy initialization
_client = None
def get_client():
    global _client
    if _client is None:
        _client = bigquery.Client()
    return _client
```

### Blocking I/O in Async

```python
# ❌ Blocks the event loop
async def bad():
    return requests.get(url)

# ✅ Use async HTTP
async def good():
    async with aiohttp.ClientSession() as s:
        async with s.get(url) as r:
            return await r.json()
```

### Missing 429 Handling

```python
# ❌ No retry — will fail under load
def call_api(url: str) -> dict:
    return requests.get(url).json()

# ✅ With retry + error handling
@safe_tool
@with_rate_limit_retry(max_retries=5)
def call_api(url: str) -> dict:
    """Call API endpoint.

    Args:
        url: API endpoint URL.
    """
    resp = requests.get(url, timeout=30)
    if resp.status_code == 429:
        raise RetryableError("HTTP 429", retry_after=resp.headers.get("Retry-After"))
    if resp.status_code >= 500:
        raise RetryableError(f"HTTP {resp.status_code}")
    resp.raise_for_status()
    return {"status": "success", "data": resp.json()}
```

### Giant Tool Returns

```python
# ❌ Dumps 100k rows into LLM context
def get_all() -> dict:
    return {"records": db.fetch_all()}

# ✅ Paginated with cap
def get_records(page: int = 1, page_size: int = 20) -> dict:
    """Fetch records with pagination.

    Args:
        page: Page number (1-indexed).
        page_size: Records per page (max 50).
    """
    page_size = min(page_size, 50)
    records = db.fetch_page(page, page_size)
    total = db.count()
    return {
        "status": "success",
        "records": records,
        "page": page,
        "total_pages": -(-total // page_size),
        "total_records": total,
    }
```
