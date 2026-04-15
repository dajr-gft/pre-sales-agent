# Modern Python Patterns — World-Class Quality

> **Este arquivo substitui patterns antigos nos outros reference files.**
> Quando houver conflito, ESTE arquivo tem prioridade.
> Todos os padrões aqui refletem Python 3.12+ e as melhores práticas de 2026.

## Table of Contents
1. [Typed Tool Results](#typed-tool-results)
2. [Configuration with pydantic-settings](#config)
3. [HTTP Client — httpx with Connection Pool](#http-client)
4. [Service Layer with Dependency Injection](#service-layer)
5. [Structured Logging with structlog](#logging)
6. [OpenTelemetry Metrics](#metrics)
7. [Modern safe_tool + retry Stack](#modern-decorators)
8. [Requirements Template](#requirements)

---

## Typed Tool Results

> **NEVER return `-> dict` from tools.** Always use typed results.

```python
# app/shared/types.py
from typing import TypedDict, Literal, NotRequired, Generic, TypeVar

T = TypeVar("T")


class ToolSuccess(TypedDict, Generic[T]):
    """Successful tool result."""
    status: Literal["success"]
    data: T


class ToolError(TypedDict):
    """Failed tool result — LLM uses this to recover gracefully."""
    status: Literal["error"]
    error: str
    retryable: bool
    tool: NotRequired[str]
    suggestion: NotRequired[str]


class ToolNotFound(TypedDict):
    """Resource not found — not an error, just empty."""
    status: Literal["not_found"]
    error: str
    suggestion: NotRequired[str]


# Union type — tool functions return one of these
type ToolResult[T] = ToolSuccess[T] | ToolError | ToolNotFound


# Domain-specific result types
class SearchRow(TypedDict):
    id: str
    title: str
    score: float


class PaginatedResult(TypedDict, Generic[T]):
    items: list[T]
    total: int
    page: int
    total_pages: int
    truncated: bool
```

### Usage in Tool Functions

```python
from app.shared.types import ToolResult, ToolError, SearchRow, PaginatedResult

def search_database(
    query: str,
    page: int = 1,
    page_size: int = 20,
    tool_context: ToolContext = None,
) -> ToolResult[PaginatedResult[SearchRow]]:
    """Search the database for matching records.

    Args:
        query: Search query string.
        page: Page number (1-indexed). Defaults to 1.
        page_size: Records per page (max 50). Defaults to 20.
    """
    if not query.strip():
        return ToolError(
            status="error",
            error="Query cannot be empty.",
            retryable=False,
            suggestion="Provide a search term.",
        )

    page_size = min(page_size, 50)
    results = db.search(query, page, page_size)
    total = db.count(query)

    return ToolSuccess(
        status="success",
        data=PaginatedResult(
            items=results,
            total=total,
            page=page,
            total_pages=-(-total // page_size),
            truncated=total > page_size,
        ),
    )
```

---

## Configuration with pydantic-settings

```python
# app/config.py
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentConfig(BaseSettings):
    """Centralized, validated configuration.

    Reads from environment variables with AGENT_ prefix.
    Falls back to .env file. Validates on startup.
    """
    model_config = SettingsConfigDict(
        env_prefix="AGENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # GCP
    project_id: str = Field(..., description="GCP project ID (required)")
    location: str = Field("us-central1", description="GCP region")

    # Models
    router_model: str = Field("gemini-3-flash", description="Model for routing agents")
    reasoning_model: str = Field("gemini-3.1-pro", description="Model for complex reasoning")
    lite_model: str = Field("gemini-3.1-flash-lite", description="Model for high-volume tasks")

    # Resilience
    max_retries: int = Field(5, ge=1, le=20, description="Max retry attempts for 429/5xx")
    retry_base_delay: float = Field(1.0, gt=0, le=10, description="Base delay in seconds")
    retry_max_delay: float = Field(60.0, gt=0, le=300, description="Max delay cap in seconds")

    # Limits
    max_tool_results: int = Field(50, ge=1, le=500, description="Max items per tool response")
    session_max_events: int = Field(100, ge=10, le=1000, description="Max events before trimming")

    # Features
    enable_memory: bool = Field(False, description="Enable long-term memory")
    enable_caching: bool = Field(True, description="Enable callback-based tool caching")

    @field_validator("project_id")
    @classmethod
    def project_must_exist(cls, v: str) -> str:
        if not v or v == "your-project-id":
            raise ValueError(
                "AGENT_PROJECT_ID is required. Set it in .env or environment."
            )
        return v


# Singleton — validated at import time
config = AgentConfig()
```

```ini
# .env
AGENT_PROJECT_ID=my-gcp-project
AGENT_LOCATION=us-central1
AGENT_ROUTER_MODEL=gemini-3-flash
AGENT_REASONING_MODEL=gemini-3.1-pro
AGENT_MAX_RETRIES=5
AGENT_ENABLE_MEMORY=false
```

---

## HTTP Client — httpx with Connection Pool

```python
# app/shared/http.py
import httpx
import structlog
from functools import lru_cache

logger = structlog.get_logger()


@lru_cache(maxsize=1)
def get_http_client() -> httpx.AsyncClient:
    """Singleton async HTTP client with connection pooling.

    - Reuses connections across tool calls (no per-call overhead).
    - HTTP/2 ready.
    - Built-in timeout and retry at transport level.
    - Call `await close_http_client()` on shutdown.
    """
    return httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=10.0),
        limits=httpx.Limits(
            max_connections=100,
            max_keepalive_connections=20,
            keepalive_expiry=30,
        ),
        follow_redirects=True,
        http2=True,
    )


async def close_http_client() -> None:
    """Call on app shutdown to close connection pool."""
    client = get_http_client()
    await client.aclose()
    get_http_client.cache_clear()
```

### Usage in Tools

```python
from app.shared.http import get_http_client
from app.shared.retry import with_rate_limit_retry, RetryableError
from app.shared.errors import safe_tool

@safe_tool
@with_rate_limit_retry(max_retries=5)
async def call_crm_api(
    customer_id: str,
    tool_context: ToolContext,
) -> ToolResult[dict]:
    """Fetch customer data from CRM.

    Args:
        customer_id: Customer identifier (e.g., 'CUST-12345').
    """
    client = get_http_client()
    resp = await client.get(
        f"https://api.crm.com/v2/customers/{customer_id}",
        headers={"Authorization": f"Bearer {config.crm_token}"},
    )

    if resp.status_code == 429:
        raise RetryableError("HTTP 429", retry_after=resp.headers.get("Retry-After"))
    if resp.status_code >= 500:
        raise RetryableError(f"HTTP {resp.status_code}")
    if resp.status_code == 404:
        return ToolNotFound(status="not_found", error=f"Customer {customer_id} not found.")

    resp.raise_for_status()
    return ToolSuccess(status="success", data=resp.json())
```

---

## Service Layer with Dependency Injection

```python
# app/services/bigquery_service.py
from google.cloud import bigquery
from google.api_core.exceptions import TooManyRequests, ServiceUnavailable
from google.api_core.retry import Retry
from functools import lru_cache
import structlog

from app.shared.types import ToolResult, ToolSuccess, ToolError

logger = structlog.get_logger()

GCP_RETRY = Retry(
    initial=1.0, maximum=60.0, multiplier=2.0, deadline=300.0,
    predicate=lambda exc: isinstance(exc, (TooManyRequests, ServiceUnavailable)),
)


class BigQueryService:
    """BigQuery access layer — injectable, testable, type-safe."""

    def __init__(self, client: bigquery.Client | None = None):
        self._client = client  # None = lazy init

    @property
    def client(self) -> bigquery.Client:
        if self._client is None:
            self._client = bigquery.Client()
        return self._client

    FORBIDDEN = {"DROP", "DELETE", "INSERT", "UPDATE", "ALTER", "CREATE", "GRANT", "TRUNCATE"}

    def validate_sql(self, sql: str) -> str | None:
        upper = sql.upper().split()
        for word in upper:
            if word in self.FORBIDDEN:
                return f"'{word}' statements are not allowed."
        return None

    def query(self, sql: str, max_rows: int = 100) -> ToolResult[list[dict]]:
        log = logger.bind(service="bigquery", sql=sql[:100])

        error = self.validate_sql(sql)
        if error:
            return ToolError(status="error", error=error, retryable=False)

        max_rows = min(max(max_rows, 1), 500)

        try:
            job = self.client.query(sql, retry=GCP_RETRY)
            rows = [dict(row) for row in job.result(max_results=max_rows, retry=GCP_RETRY)]
            log.info("query_success", row_count=len(rows))
            return ToolSuccess(status="success", data=rows)
        except TooManyRequests:
            log.warning("rate_limited")
            return ToolError(
                status="error", error="BigQuery rate limit (429).", retryable=True,
                suggestion="Wait 30 seconds and try a simpler query.",
            )
        except ServiceUnavailable:
            log.warning("service_unavailable")
            return ToolError(
                status="error", error="BigQuery temporarily unavailable.", retryable=True,
            )


@lru_cache(maxsize=1)
def get_bq_service() -> BigQueryService:
    """Singleton — no global mutable state."""
    return BigQueryService()
```

### Testing with DI

```python
# tests/unit/test_bigquery_service.py
from unittest.mock import MagicMock
from app.services.bigquery_service import BigQueryService

def test_rejects_delete():
    service = BigQueryService(client=MagicMock())  # injected mock
    result = service.query("DELETE FROM users")
    assert result["status"] == "error"
    assert "DELETE" in result["error"]

def test_query_success():
    mock_client = MagicMock()
    mock_client.query.return_value.result.return_value = [
        {"id": "1", "name": "Test"}
    ]
    service = BigQueryService(client=mock_client)
    result = service.query("SELECT * FROM users")
    assert result["status"] == "success"
    assert len(result["data"]) == 1
```

---

## Structured Logging with structlog

```python
# app/shared/logging_config.py
import logging
import structlog


def setup_logging(level: str = "INFO", json_output: bool = True) -> None:
    """Configure structlog for Cloud Run / Agent Engine.

    - JSON output for production (Cloud Logging compatible).
    - Console output for local development.
    - Context binding (user_id, tool, session propagate automatically).
    """
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.add_logger_name,
    ]

    if json_output:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level),
        ),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[*shared_processors, renderer],
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(getattr(logging, level))
```

### Usage

```python
import structlog

logger = structlog.get_logger()

# Context binding — persists across calls
log = logger.bind(user_id="user_123", session_id="sess_456")
log.info("tool_called", tool="search_db", query="SELECT ...")
log.info("tool_completed", rows=42, duration_ms=150)

# Output (JSON — Cloud Logging compatible):
# {"user_id": "user_123", "session_id": "sess_456", "tool": "search_db",
#  "event": "tool_called", "level": "info", "timestamp": "2026-04-10T..."}
```

---

## OpenTelemetry Metrics

```python
# app/shared/metrics.py
from opentelemetry import metrics
from opentelemetry.exporter.cloud_monitoring import CloudMonitoringMetricsExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader


def setup_metrics(project_id: str) -> None:
    """Configure OTel metrics → Cloud Monitoring."""
    exporter = CloudMonitoringMetricsExporter(project_id=project_id)
    reader = PeriodicExportingMetricReader(exporter, export_interval_millis=60_000)
    provider = MeterProvider(metric_readers=[reader])
    metrics.set_meter_provider(provider)


# Global meter
meter = metrics.get_meter("adk_agent", version="1.0.0")

# Counters
tool_calls = meter.create_counter(
    "agent.tool.calls",
    description="Total tool invocations",
    unit="1",
)
tool_errors = meter.create_counter(
    "agent.tool.errors",
    description="Tool errors (non-retryable)",
    unit="1",
)
retry_count = meter.create_counter(
    "agent.retries",
    description="Retry attempts (429/5xx)",
    unit="1",
)
cache_hits = meter.create_counter(
    "agent.cache.hits",
    description="Tool cache hits",
    unit="1",
)

# Histograms
tool_latency = meter.create_histogram(
    "agent.tool.latency",
    description="Tool execution duration",
    unit="s",
)
token_usage = meter.create_histogram(
    "agent.tokens.consumed",
    description="Tokens consumed per invocation",
    unit="tokens",
)
```

---

## Modern safe_tool + retry Stack

```python
# app/shared/errors.py — UPDATED with metrics + structlog
import asyncio
import time
import structlog
from functools import wraps
from app.shared.metrics import tool_calls, tool_errors, tool_latency
from app.shared.types import ToolError

logger = structlog.get_logger()


def safe_tool(func):
    """Production-grade tool wrapper.

    - Catches ALL exceptions → returns ToolError (never crashes agent).
    - Records OTel metrics (call count, latency, errors).
    - Logs with structlog (structured, context-bound).
    """
    @wraps(func)
    async def _async(*args, **kwargs):
        start = time.perf_counter()
        tool_calls.add(1, {"tool": func.__name__})
        log = logger.bind(tool=func.__name__)
        try:
            result = await func(*args, **kwargs)
            elapsed = time.perf_counter() - start
            tool_latency.record(elapsed, {"tool": func.__name__})
            log.info("tool_completed", duration_s=round(elapsed, 3))
            return result
        except Exception as e:
            elapsed = time.perf_counter() - start
            tool_errors.add(1, {"tool": func.__name__, "error_type": type(e).__name__})
            tool_latency.record(elapsed, {"tool": func.__name__})
            log.exception("tool_failed", error=str(e), duration_s=round(elapsed, 3))
            return ToolError(
                status="error",
                error=f"{type(e).__name__}: {e}",
                retryable=False,
                tool=func.__name__,
                suggestion="Try different parameters or a simpler request.",
            )

    @wraps(func)
    def _sync(*args, **kwargs):
        start = time.perf_counter()
        tool_calls.add(1, {"tool": func.__name__})
        log = logger.bind(tool=func.__name__)
        try:
            result = func(*args, **kwargs)
            elapsed = time.perf_counter() - start
            tool_latency.record(elapsed, {"tool": func.__name__})
            log.info("tool_completed", duration_s=round(elapsed, 3))
            return result
        except Exception as e:
            elapsed = time.perf_counter() - start
            tool_errors.add(1, {"tool": func.__name__, "error_type": type(e).__name__})
            tool_latency.record(elapsed, {"tool": func.__name__})
            log.exception("tool_failed", error=str(e), duration_s=round(elapsed, 3))
            return ToolError(
                status="error",
                error=f"{type(e).__name__}: {e}",
                retryable=False,
                tool=func.__name__,
                suggestion="Try different parameters or a simpler request.",
            )

    return _async if asyncio.iscoroutinefunction(func) else _sync
```

---

## Requirements Template

```txt
# requirements.txt — Modern ADK project (April 2026)

# Core
google-adk>=1.29.0

# Models & GCP
google-cloud-aiplatform>=1.74.0
google-cloud-bigquery>=3.28.0
google-cloud-firestore>=2.20.0
google-cloud-secret-manager>=2.22.0

# HTTP
httpx[http2]>=0.28.0

# Config
pydantic>=2.10.0
pydantic-settings>=2.7.0

# Logging
structlog>=24.4.0

# Observability
opentelemetry-api>=1.29.0
opentelemetry-sdk>=1.29.0
opentelemetry-exporter-gcp-trace>=1.9.0
opentelemetry-exporter-gcp-monitoring>=1.9.0

# Testing
pytest>=8.3.0
pytest-asyncio>=0.24.0
httpx[testing]  # for mock transport
locust>=2.32.0

# Dev
ruff>=0.8.0
mypy>=1.13.0
```
