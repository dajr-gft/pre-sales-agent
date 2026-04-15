# Resilience & Production Deployment

## Table of Contents
1. [429 Rate Limit Handling — THE RULE](#429-rule)
2. [Error Handling Strategy](#error-strategy)
3. [Callbacks (Guardrails)](#callbacks)
4. [Circuit Breaker](#circuit-breaker)
5. [Observability](#observability)
6. [Deployment Decision Matrix](#deployment-decision)
7. [Deployment — Agent Engine (1st Choice)](#agent-engine)
8. [Deployment — Cloud Run](#cloud-run)
8. [Security](#security)
9. [Performance](#performance)
10. [Production Checklist](#checklist)

---

## 429 Rate Limit Handling — THE RULE

> **EVERY external call MUST handle HTTP 429. This is NON-NEGOTIABLE.**

### Where 429 Occurs in ADK Projects

| Source | When | Pattern |
|---|---|---|
| **Gemini API** | Exceeding QPM/TPM quota | Model calls via ADK runtime |
| **Google Cloud APIs** | BigQuery, Firestore, Vertex AI quotas | Tool functions |
| **MCP Servers** | MCP server rate limits | MCP tool calls |
| **REST APIs** | External API rate limits | Custom tool HTTP calls |
| **Google Search** | Search API quotas | Built-in google_search tool |

### Handling Gemini 429 at Agent Level

ADK handles Gemini retries internally, BUT you should configure:

```python
from google.adk.agents import Agent
from google.genai.types import GenerateContentConfig

agent = Agent(
    model="gemini-3-flash",
    name="my_agent",
    instruction=INSTRUCTION,
    generate_content_config=GenerateContentConfig(
        temperature=0.7,
        max_output_tokens=8192,
        # ADK's internal retry handles 429 for model calls
        # but you should handle it in your API server layer too
    ),
)
```

### API Server Layer — Protect with 429 Response

```python
# main.py — FastAPI server
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
import asyncio
import time

app = FastAPI()

# Global rate limiter
class RateLimiter:
    def __init__(self, max_requests: int, window_seconds: int):
        self.max_requests = max_requests
        self.window = window_seconds
        self.requests: dict[str, list[float]] = {}

    def check(self, key: str) -> bool:
        now = time.time()
        if key not in self.requests:
            self.requests[key] = []
        # Clean old entries
        self.requests[key] = [t for t in self.requests[key] if now - t < self.window]
        if len(self.requests[key]) >= self.max_requests:
            return False
        self.requests[key].append(now)
        return True

limiter = RateLimiter(max_requests=60, window_seconds=60)

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    client_ip = request.client.host
    if not limiter.check(client_ip):
        return JSONResponse(
            status_code=429,
            content={"error": "Rate limit exceeded. Try again later."},
            headers={"Retry-After": "60"},
        )
    return await call_next(request)
```

### Tool-Level 429 — The Complete Pattern

```python
import aiohttp
from app.shared.retry import with_rate_limit_retry, RetryableError
from app.shared.errors import safe_tool

@safe_tool
@with_rate_limit_retry(max_retries=5, base_delay=1.0, max_delay=60.0)
async def call_api(
    endpoint: str,
    method: str = "GET",
    payload: dict | None = None,
    tool_context: ToolContext = None,
) -> dict:
    """Call external API with full 429 protection.

    Args:
        endpoint: API endpoint URL.
        method: HTTP method (GET or POST). Defaults to GET.
        payload: Request body for POST. Optional.
    """
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        request_method = session.post if method == "POST" else session.get
        kwargs = {"json": payload} if payload else {}

        async with request_method(endpoint, **kwargs) as resp:
            # --- 429 HANDLING ---
            if resp.status == 429:
                retry_after = resp.headers.get("Retry-After")
                raise RetryableError(
                    f"HTTP 429 from {endpoint}",
                    retry_after=float(retry_after) if retry_after else None,
                )

            # --- 5xx TRANSIENT ERRORS ---
            if resp.status >= 500:
                raise RetryableError(f"HTTP {resp.status} from {endpoint}")

            # --- 4xx CLIENT ERRORS (non-retryable) ---
            if resp.status == 404:
                return {"status": "not_found", "error": f"Resource not found: {endpoint}"}
            if resp.status == 403:
                return {"status": "forbidden", "error": "Access denied."}
            if resp.status == 401:
                return {"status": "unauthorized", "error": "Authentication failed."}

            resp.raise_for_status()
            data = await resp.json()

            return {"status": "success", "data": data}
```

### Google Cloud API 429 Handling

```python
from google.api_core.exceptions import (
    TooManyRequests,
    ResourceExhausted,
    ServiceUnavailable,
    InternalServerError,
)
from google.api_core.retry import Retry

# Google Cloud client libraries support automatic retry:
GCP_RETRY = Retry(
    initial=1.0,
    maximum=60.0,
    multiplier=2.0,
    deadline=300.0,
    predicate=lambda exc: isinstance(exc, (
        TooManyRequests,
        ResourceExhausted,
        ServiceUnavailable,
        InternalServerError,
    )),
)

# Usage with BigQuery
@safe_tool
def query_bq(sql: str, max_rows: int = 100) -> dict:
    """Execute BigQuery query with rate limit protection.

    Args:
        sql: SQL SELECT query.
        max_rows: Max rows to return (1-500).
    """
    try:
        client = _get_bq()
        job = client.query(sql, retry=GCP_RETRY)
        rows = [dict(r) for r in job.result(max_results=min(max_rows, 500), retry=GCP_RETRY)]
        return {"status": "success", "rows": rows, "count": len(rows)}
    except TooManyRequests:
        return {"status": "error", "error": "BigQuery rate limit (429). Wait and retry.", "retryable": True}
    except ResourceExhausted:
        return {"status": "error", "error": "BigQuery quota exhausted. Try smaller query.", "retryable": True}
```

---

## Error Handling Strategy

### Layered Error Defense

```
Layer 1: @safe_tool decorator          → catches ALL exceptions, returns error dict
Layer 2: @with_rate_limit_retry        → retries 429/5xx with backoff
Layer 3: before_tool_callback          → validates inputs before tool runs
Layer 4: after_tool_callback           → validates/transforms outputs
Layer 5: before_model_callback         → blocks harmful inputs to LLM
Layer 6: after_model_callback          → redacts PII from LLM output
Layer 7: API server rate limiter       → protects entire agent from abuse
Layer 8: Circuit breaker               → stops calling failing services
```

### Structured Error Categories

```python
from enum import Enum

class ErrorCategory(str, Enum):
    VALIDATION = "validation_error"
    AUTH = "auth_error"
    NOT_FOUND = "not_found"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    QUOTA = "quota_exceeded"
    INTERNAL = "internal_error"

def make_error(
    category: ErrorCategory,
    message: str,
    retryable: bool = False,
    suggestion: str = "",
    **extra,
) -> dict:
    return {
        "status": "error",
        "category": category.value,
        "error": message,
        "retryable": retryable,
        "suggestion": suggestion or "Try a different approach.",
        **extra,
    }
```

---

## Callbacks

### Input Guardrail (before_model_callback)

```python
from google.adk.agents.callback_context import CallbackContext
from google.adk.models import LlmResponse, LlmRequest
from google.genai import types

def input_guardrail(
    callback_context: CallbackContext,
    llm_request: LlmRequest,
) -> LlmResponse | None:
    """Block harmful/invalid input before it reaches the LLM.
    
    Return LlmResponse to skip the LLM call entirely.
    Return None to proceed normally.
    """
    user_msg = ""
    if llm_request.contents:
        last = llm_request.contents[-1]
        if last.parts:
            user_msg = last.parts[0].text or ""

    if contains_pii(user_msg):
        return LlmResponse(
            content=types.Content(
                role="model",
                parts=[types.Part(text=(
                    "I cannot process requests containing personal information "
                    "(SSN, credit cards, passwords). Please remove sensitive data."
                ))],
            )
        )

    if detect_injection(user_msg):
        logger.warning("Prompt injection detected")
        return LlmResponse(
            content=types.Content(
                role="model",
                parts=[types.Part(text="I'm unable to process this request.")],
            )
        )

    return None  # proceed with LLM call

agent = Agent(before_model_callback=input_guardrail, ...)
```

### Output Guardrail (after_model_callback)

```python
def output_guardrail(
    callback_context: CallbackContext,
    llm_response: LlmResponse,
) -> LlmResponse | None:
    """Validate and sanitize model output. Return LlmResponse to override."""
    if not llm_response or not llm_response.content:
        return None

    text = ""
    if llm_response.content.parts:
        text = llm_response.content.parts[0].text or ""

    sanitized = redact_pii(text)
    if sanitized != text:
        return LlmResponse(
            content=types.Content(
                role="model",
                parts=[types.Part(text=sanitized)],
            )
        )

    return None  # unchanged

agent = Agent(after_model_callback=output_guardrail, ...)
```

### Tool Callbacks

```python
def before_tool(callback_context, tool_name, tool_args) -> dict | None:
    logger.info(f"Tool call: {tool_name}({tool_args})")

    # Block dangerous ops
    if tool_name == "execute_sql" and "DROP" in tool_args.get("sql", "").upper():
        return {"status": "error", "error": "DROP not allowed."}

    return None

def after_tool(callback_context, tool_name, tool_args, tool_response) -> dict | None:
    logger.info(f"Tool {tool_name} → {tool_response.get('status')}")

    # Truncate oversized responses
    if len(str(tool_response)) > 50_000:
        return {
            "status": "truncated",
            "summary": str(tool_response)[:5000],
            "message": "Response truncated (>50KB).",
        }
    return None
```

### Agent-Level Callbacks (before/after agent run)

```python
from google.adk.agents.callback_context import CallbackContext
from google.genai import types

def before_agent_run(callback_context: CallbackContext) -> types.Content | None:
    """Called before the agent's core logic executes.
    
    Use for: setup, state validation, logging entry point.
    Return Content to skip the agent entirely.
    Return None to proceed.
    """
    logger.info(f"Agent {callback_context.agent_name} starting")
    
    # Example: check if user is authorized
    if not callback_context.state.get("user:authorized", True):
        return types.Content(
            role="model",
            parts=[types.Part(text="You are not authorized to use this agent.")],
        )
    return None

def after_agent_run(callback_context: CallbackContext) -> types.Content | None:
    """Called after the agent's core logic completes.
    
    Use for: cleanup, metrics, appending disclaimers.
    Return Content to append an additional response.
    Return None for no additional output.
    """
    logger.info(f"Agent {callback_context.agent_name} completed")
    return None

agent = Agent(
    before_agent_callback=before_agent_run,
    after_agent_callback=after_agent_run,
    before_model_callback=input_guardrail,
    after_model_callback=output_guardrail,
    before_tool_callback=before_tool,
    after_tool_callback=after_tool,
    ...
)
```

### All 6 Callback Types — Summary

| Callback | When | Return to Override | Use For |
|---|---|---|---|
| `before_agent_callback` | Before agent logic | `Content` = skip agent | Auth, setup, validation |
| `after_agent_callback` | After agent logic | `Content` = append response | Cleanup, disclaimers |
| `before_model_callback` | Before LLM call | `LlmResponse` = skip LLM | PII detection, injection block |
| `after_model_callback` | After LLM response | `LlmResponse` = override | PII redaction, sanitization |
| `before_tool_callback` | Before tool exec | `dict` = skip tool | Caching, rate limiting, blocking |
| `after_tool_callback` | After tool exec | `dict` = override result | Logging, caching, truncation |

> **All callbacks**: return `None` to proceed normally. Return a value to override.
> **Lists supported**: pass a list of callbacks — they run in order until one returns non-None.

---

## Circuit Breaker

Stops calling a service after repeated failures:

```python
import time
import logging

logger = logging.getLogger(__name__)

class CircuitBreaker:
    """Prevents cascading failures by stopping calls to failing services."""

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        reset_timeout: int = 60,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.failure_count = 0
        self.last_failure_time = 0.0
        self.state = "closed"  # closed → open → half-open → closed

    def can_execute(self) -> bool:
        if self.state == "closed":
            return True
        if self.state == "open":
            if time.time() - self.last_failure_time > self.reset_timeout:
                self.state = "half-open"
                logger.info(f"[CircuitBreaker:{self.name}] half-open — allowing probe")
                return True
            return False
        return True  # half-open: allow one attempt

    def record_success(self):
        if self.state == "half-open":
            logger.info(f"[CircuitBreaker:{self.name}] closed — service recovered")
        self.failure_count = 0
        self.state = "closed"

    def record_failure(self):
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= self.failure_threshold:
            self.state = "open"
            logger.warning(
                f"[CircuitBreaker:{self.name}] OPEN — {self.failure_count} failures. "
                f"Blocking calls for {self.reset_timeout}s"
            )

    def get_error(self) -> dict:
        return {
            "status": "error",
            "error": f"Service '{self.name}' temporarily unavailable (circuit open).",
            "retryable": True,
            "suggestion": f"Service will be retried in ~{self.reset_timeout}s.",
        }


# Usage
crm_breaker = CircuitBreaker("crm_api", failure_threshold=3, reset_timeout=30)

@safe_tool
async def call_crm(customer_id: str) -> dict:
    """Fetch customer from CRM.

    Args:
        customer_id: Customer identifier.
    """
    if not crm_breaker.can_execute():
        return crm_breaker.get_error()

    try:
        result = await _fetch_crm(customer_id)
        crm_breaker.record_success()
        return result
    except Exception as e:
        crm_breaker.record_failure()
        return {"status": "error", "error": str(e)}
```

---

## Observability

### Structured JSON Logging (Cloud Run compatible)

```python
# utils/logging_config.py
import logging
import json
import sys

class StructuredFormatter(logging.Formatter):
    def format(self, record):
        entry = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "timestamp": self.formatTime(record),
        }
        if hasattr(record, "extra_data"):
            entry.update(record.extra_data)
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry)

def setup_logging(level: str = "INFO"):
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(StructuredFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(getattr(logging, level))
```

### Cloud Trace

```python
# utils/tracing.py
from opentelemetry import trace
from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

def setup_tracing(project_id: str):
    provider = TracerProvider()
    provider.add_span_processor(
        BatchSpanProcessor(CloudTraceSpanExporter(project_id=project_id))
    )
    trace.set_tracer_provider(provider)

tracer = trace.get_tracer("agent")

# Instrument tools
@safe_tool
def my_tool(query: str) -> dict:
    with tracer.start_as_current_span("my_tool") as span:
        span.set_attribute("query", query)
        result = execute(query)
        span.set_attribute("result_count", len(result))
        return {"status": "success", "data": result}
```

---

## Deployment Decision Matrix

| Scenario | Deploy To | Why |
|---|---|---|
| **Internal conversational agent** | **Agent Engine** | Managed sessions, eval, zero-infra, fastest path |
| **AgentSpace / enterprise chatbot** | **Agent Engine** | Native integration, SSO, IAM |
| **Custom API / webhook** | Cloud Run | Full control, custom server logic |
| **WhatsApp / external channel** | Cloud Run | Custom webhook handling needed |
| **High-scale microservice** | GKE | Kubernetes orchestration, multi-region |

---

## Agent Engine Deployment (FIRST CHOICE for Conversational Agents)

> **Agent Engine is the preferred deployment target for internal conversational agents.**
> It provides managed sessions, built-in auth, Cloud Trace, evaluation support,
> and enterprise-grade security — all without writing a Dockerfile or server code.

### One-Command Deploy via ASP

```bash
# Create with Agent Engine target
agent-starter-pack create my-agent -a adk -d agent_engine

# Deploy
cd my-agent
make setup-dev    # provision GCP resources
make deploy       # deploy agent
```

### Programmatic Deploy

```python
import vertexai
from vertexai import agent_engines

vertexai.init(project=config.project_id, location=config.location)

# Deploy — no Dockerfile, no server code needed
remote_agent = agent_engines.create(
    agent_engine=root_agent,
    requirements=["google-adk>=1.29.0", "google-cloud-bigquery", "aiohttp"],
    display_name="My Production Agent",
    description="Internal conversational agent for the analytics team.",
)

# Agent Engine handles: sessions, auth, scaling, tracing, eval
```

### Test Deployed Agent

```python
# Create session on remote agent
session = remote_agent.create_session(user_id="user_123")

# Run
response = remote_agent.run(
    session_id=session.id,
    user_id="user_123",
    message="Hello, what can you do?",
)
print(response)

# List sessions
sessions = remote_agent.list_sessions(user_id="user_123")
```

### What Agent Engine Gives You (vs Cloud Run)

| Feature | Agent Engine | Cloud Run |
|---|---|---|
| Session management | Built-in (Vertex AI) | You build it |
| Auth / IAM | Built-in | You configure |
| Cloud Trace | Automatic | You instrument |
| Evaluation | `adk eval` integration | Manual |
| Dockerfile | Not needed | Required |
| Server code | Not needed | Required (FastAPI etc) |
| Scaling | Automatic | You configure |
| Cost | Per-invocation | Per-instance |

---

## Cloud Run Deployment (When You Need Full Control)

> Use Cloud Run when you need custom server logic, webhooks, external-facing APIs,
> or integrations that Agent Engine doesn't support (e.g., WhatsApp Business API).

### Dockerfile

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "2"]
```

### FastAPI Server with Full Resilience

```python
# main.py
from fastapi import FastAPI
from contextlib import asynccontextmanager
from app.shared.logging_config import setup_logging
from app.shared.tracing import setup_tracing

@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    setup_tracing(config.project_id)
    yield

app = FastAPI(lifespan=lifespan)

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.post("/chat")
async def chat(request: ChatRequest):
    session = await get_or_create_session(
        session_service, "my_agent", request.user_id, request.session_id,
    )
    events = []
    async for event in runner.run_async(
        session_id=session.id,
        user_id=request.user_id,
        new_message=Content(parts=[Part.from_text(request.message)]),
    ):
        if event.content and event.content.parts:
            events.append({"text": event.content.parts[0].text})
    return {"session_id": session.id, "responses": events}
```

### Deploy Command

```bash
gcloud run deploy my-agent \
  --source . \
  --region us-central1 \
  --service-account agent-sa@PROJECT.iam.gserviceaccount.com \
  --set-env-vars "GOOGLE_CLOUD_PROJECT=PROJECT_ID" \
  --min-instances 1 \
  --max-instances 10 \
  --memory 2Gi \
  --cpu 2 \
  --timeout 300 \
  --concurrency 10
```

---

## Security

### Input Validation

```python
import re

FORBIDDEN_SQL = ["DROP", "DELETE", "INSERT", "UPDATE", "ALTER", "CREATE", "GRANT", "TRUNCATE", "EXEC"]

def validate_sql(sql: str) -> str | None:
    upper = sql.upper()
    for word in FORBIDDEN_SQL:
        if re.search(rf'\b{word}\b', upper):
            return f"'{word}' statements are not allowed."
    return None
```

### Secrets — NEVER Hardcode

```python
from google.cloud import secretmanager

def get_secret(secret_id: str, project_id: str) -> str:
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"
    return client.access_secret_version(name=name).payload.data.decode("utf-8")
```

### Least Privilege SA

```yaml
roles:
  - roles/aiplatform.user          # Vertex AI
  - roles/datastore.user           # Firestore
  - roles/bigquery.dataViewer      # Read-only BQ
  - roles/logging.logWriter        # Logs
  - roles/cloudtrace.agent         # Traces
  - roles/secretmanager.secretAccessor  # Secrets
```

---

## Performance

1. **Flash for routing, Pro for reasoning** — cheapest model that meets quality.
2. **Cache tool results** in session state — avoid duplicate API calls.
3. **Parallelize** with `ParallelAgent` or `asyncio.gather`.
4. **Trim session history** — cap events to prevent context explosion.
5. **Stream responses** — `run_async` yields events incrementally.
6. **Timeout every external call** — 10-30s max.
7. **Paginate tool outputs** — max 20-50 items per call.

---

## Production Checklist

```
RESILIENCE:
□ app/shared/retry.py with with_rate_limit_retry in project
□ @safe_tool on EVERY tool function
□ @with_rate_limit_retry on EVERY external call
□ 429 specifically handled for all APIs (Gemini, GCP, REST, MCP)
□ Retry-After header respected when present
□ Circuit breaker on critical external services
□ Idempotent tool operations (safe to retry)

GUARDRAILS:
□ before_model_callback — input validation, PII detection, injection detection
□ after_model_callback — output sanitization, PII redaction
□ before_tool_callback — dangerous operation blocking, logging
□ after_tool_callback — output truncation, logging
□ SQL injection prevention in all query tools
□ Input validation on all tool parameters

OBSERVABILITY:
□ Structured JSON logging (Cloud Run compatible)
□ Cloud Trace integration
□ Error rate alerting
□ Latency dashboards (p50, p95, p99)
□ Token usage tracking
□ Tool failure rate by tool name
□ 429 retry count metrics

ARCHITECTURE:
□ max_iterations on ALL LoopAgents
□ Model selection per agent role (Flash routing, Pro reasoning)
□ Session service is persistent (not InMemory)
□ Session history trimming configured
□ Tool returns capped in size (<50 items)
□ State keys namespaced and documented

SECURITY:
□ Secrets in Secret Manager (never env vars or code)
□ Service account with least privilege
□ Input validation on every tool
□ PII detection/redaction in callbacks
□ SQL restricted to SELECT/WITH only
□ Health check endpoint

DEPLOYMENT:
□ Deployment target decided (Agent Engine for conversational, Cloud Run for custom API)
□ Agent Engine: requirements list complete, display_name set
□ Cloud Run (if used): Dockerfile tested locally, min instances >= 1
□ Cloud Run (if used): timeout ≥ 120s, CORS configured
□ Rate limiter on API server
□ CI/CD with prompt regression tests
□ Rollback plan documented

PROMPTS:
□ Instructions in prompts.py (not inline)
□ Structured with <role>, <rules>, <tools>, <workflow>, <output_format>
□ Few-shot examples for complex tools
□ Failure modes documented in instructions
□ Output format explicitly specified
□ Prompt regression tests with hash verification
```
