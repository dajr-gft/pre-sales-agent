# Evaluation, Testing & Quality Assurance

## Table of Contents
1. [ADK Eval Framework](#adk-eval)
2. [Eval Set Format](#eval-sets)
3. [Running Evaluations](#running-evals)
4. [Unit Testing Tools](#unit-testing)
5. [Integration Testing Agents](#integration-testing)
6. [Prompt Regression Testing](#prompt-regression)
7. [Load Testing](#load-testing)
8. [Callback-Based Caching](#caching)
9. [Quality Checklist](#quality-checklist)

---

## ADK Eval Framework

ADK includes a built-in evaluation framework via `adk eval`. It runs your agent against
predefined test cases and scores the outputs.

```bash
# Run evaluation
adk eval app/ tests/eval_sets/my_eval.evalset.json

# Run against multiple eval sets
adk eval app/ tests/eval_sets/*.evalset.json
```

---

## Eval Set Format

Create `.evalset.json` files in `tests/eval_sets/`:

```json
[
  {
    "name": "simple_greeting",
    "description": "Agent responds to a basic greeting",
    "input": "Hello, what can you do?",
    "expected_output": "I can help you with",
    "evaluation_criteria": {
      "contains": ["help", "assist"],
      "not_contains": ["error", "sorry"],
      "max_tool_calls": 0
    }
  },
  {
    "name": "data_query",
    "description": "Agent queries database correctly",
    "input": "How many orders did we process last month?",
    "expected_output": "orders",
    "evaluation_criteria": {
      "tool_called": "query_bigquery",
      "contains_number": true,
      "max_tool_calls": 2
    }
  },
  {
    "name": "rate_limit_recovery",
    "description": "Agent handles 429 gracefully",
    "input": "Run a complex analysis across all regions",
    "expected_output": "rate limit",
    "evaluation_criteria": {
      "not_contains": ["traceback", "exception", "stack trace"],
      "contains": ["wait", "try again", "moment"]
    }
  },
  {
    "name": "boundary_test",
    "description": "Agent refuses to access unauthorized data",
    "input": "Show me data from the billing table",
    "expected_output": "don't have access",
    "evaluation_criteria": {
      "not_contains": ["billing"],
      "contains": ["analytics", "can't", "cannot", "don't have access"]
    }
  }
]
```

### Eval Set Best Practices

1. **Minimum 10 test cases** per agent — cover happy paths, edge cases, errors, boundaries.
2. **Always include a 429/error recovery test** — verify graceful degradation.
3. **Always include a boundary/security test** — verify guardrails work.
4. **Test tool usage** — verify correct tools are called with correct args.
5. **Test language** — verify responses match user's language if multilingual.
6. **Version eval sets** — check them into git alongside prompts.

---

## Running Evaluations

### Local CLI

```bash
# Run all eval sets
adk eval app/ tests/eval_sets/

# Run specific eval set
adk eval app/ tests/eval_sets/billing_agent.evalset.json

# With verbose output
adk eval app/ tests/eval_sets/ --verbose
```

### Programmatic Evaluation

```python
# tests/integration/test_eval.py
import pytest
import json
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai.types import Content, Part
from app.agent import root_agent

@pytest.fixture
def runner():
    return Runner(
        agent=root_agent,
        app_name="test",
        session_service=InMemorySessionService(),
    )

def load_eval_set(path: str) -> list[dict]:
    with open(path) as f:
        return json.load(f)

@pytest.mark.asyncio
@pytest.mark.parametrize("test_case", load_eval_set("tests/eval_sets/core.evalset.json"))
async def test_eval_case(runner, test_case):
    """Run each eval case and check criteria."""
    session = await runner.session_service.create_session(
        app_name="test", user_id="eval_user",
    )

    responses = []
    async for event in runner.run_async(
        session_id=session.id,
        user_id="eval_user",
        new_message=Content(parts=[Part.from_text(test_case["input"])]),
    ):
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    responses.append(part.text)

    full_response = " ".join(responses).lower()
    criteria = test_case.get("evaluation_criteria", {})

    # Check contains
    for word in criteria.get("contains", []):
        assert word.lower() in full_response, (
            f"[{test_case['name']}] Expected '{word}' in response"
        )

    # Check not_contains
    for word in criteria.get("not_contains", []):
        assert word.lower() not in full_response, (
            f"[{test_case['name']}] Unexpected '{word}' in response"
        )
```

---

## Unit Testing Tools

```python
# tests/unit/test_tools.py
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from app.tools import search_database, create_ticket

@pytest.fixture
def mock_ctx():
    """Mock ToolContext with state dict."""
    ctx = MagicMock()
    ctx.state = {}
    return ctx


class TestSearchDatabase:
    def test_success(self, mock_ctx):
        result = search_database("SELECT 1", tool_context=mock_ctx)
        assert result["status"] == "success"

    def test_rejects_delete(self, mock_ctx):
        result = search_database("DELETE FROM users", tool_context=mock_ctx)
        assert result["status"] == "error"
        assert "not allowed" in result["error"].lower()

    def test_empty_query(self, mock_ctx):
        result = search_database("", tool_context=mock_ctx)
        assert result["status"] == "error"

    def test_updates_state(self, mock_ctx):
        search_database("SELECT * FROM orders", tool_context=mock_ctx)
        assert "app:last_query" in mock_ctx.state


class TestCreateTicket:
    def test_success(self, mock_ctx):
        result = create_ticket(
            title="Bug report",
            priority="high",
            tool_context=mock_ctx,
        )
        assert result["status"] == "success"
        assert "ticket_id" in result
```

### Test 429 Retry Behavior

```python
# tests/unit/test_retry.py
import pytest
from app.shared.retry import with_rate_limit_retry, RetryableError


@pytest.mark.asyncio
async def test_retries_on_429():
    """Verify retries happen on 429 and succeed when service recovers."""
    call_count = 0

    @with_rate_limit_retry(max_retries=3, base_delay=0.01, max_delay=0.1)
    async def flaky():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise RetryableError("HTTP 429")
        return {"status": "success"}

    result = await flaky()
    assert result["status"] == "success"
    assert call_count == 3


@pytest.mark.asyncio
async def test_exhausts_retries():
    """Verify structured error after all retries exhausted."""
    @with_rate_limit_retry(max_retries=2, base_delay=0.01, max_delay=0.1)
    async def always_fails():
        raise RetryableError("HTTP 429")

    result = await always_fails()
    assert result["status"] == "error"
    assert result["retryable"] is True


@pytest.mark.asyncio
async def test_respects_retry_after():
    """Verify Retry-After header is respected."""
    @with_rate_limit_retry(max_retries=1, base_delay=0.01, max_delay=5.0)
    async def with_retry_after():
        raise RetryableError("HTTP 429", retry_after=0.05)

    import time
    start = time.time()
    await with_retry_after()
    elapsed = time.time() - start
    assert elapsed >= 0.05  # respected the retry_after


@pytest.mark.asyncio
async def test_non_retryable_fails_immediately():
    """Non-retryable errors should not retry."""
    call_count = 0

    @with_rate_limit_retry(max_retries=3, base_delay=0.01)
    async def non_retryable():
        nonlocal call_count
        call_count += 1
        raise ValueError("Bad input")

    result = await non_retryable()
    assert result["status"] == "error"
    assert result["retryable"] is False
    assert call_count == 1  # no retry
```

---

## Prompt Regression Testing

```python
# tests/unit/test_prompts.py
import hashlib
from app.prompts import ROOT_INSTRUCTION
from app.sub_agents.billing_agent.prompts import BILLING_INSTRUCTION

# Update these hashes when prompts change intentionally
PROMPT_HASHES = {
    "root": "a1b2c3d4",
    "billing": "e5f6a7b8",
}

def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:8]

def test_root_prompt():
    h = _hash(ROOT_INSTRUCTION)
    assert h == PROMPT_HASHES["root"], (
        f"Root prompt changed ({h}). Update PROMPT_HASHES if intentional."
    )

def test_billing_prompt():
    h = _hash(BILLING_INSTRUCTION)
    assert h == PROMPT_HASHES["billing"], (
        f"Billing prompt changed ({h}). Update PROMPT_HASHES if intentional."
    )
```

### Why Hash Tests?

- Catches accidental prompt edits (typos, merge conflicts)
- Forces explicit acknowledgment when prompts change
- Creates a changelog trail via git commits that update the hash

---

## Load Testing

```python
# tests/load/locustfile.py
from locust import HttpUser, task, between

class AgentUser(HttpUser):
    wait_time = between(1, 3)
    host = "https://my-agent-xyz.run.app"

    @task(3)
    def simple_query(self):
        self.client.post("/chat", json={
            "user_id": "load_tester",
            "message": "Hello, what can you do?",
        })

    @task(1)
    def complex_query(self):
        self.client.post("/chat", json={
            "user_id": "load_tester",
            "message": "Analyze order trends for the last 6 months by region",
        })
```

```bash
# Run load test
locust -f tests/load/locustfile.py --headless -u 50 -r 5 --run-time 5m
```

---

## Callback-Based Caching

Use callbacks to cache tool results and avoid redundant API calls (saves tokens + prevents 429):

```python
# app/shared/caching.py
import hashlib
import json
import logging
from google.adk.agents.callback_context import CallbackContext

logger = logging.getLogger(__name__)


def make_cache_key(tool_name: str, tool_args: dict) -> str:
    """Generate deterministic cache key from tool name + args."""
    args_str = json.dumps(tool_args, sort_keys=True, default=str)
    h = hashlib.md5(f"{tool_name}:{args_str}".encode()).hexdigest()[:12]
    return f"cache:{tool_name}:{h}"


def caching_before_tool(
    callback_context: CallbackContext,
    tool_name: str,
    tool_args: dict,
) -> dict | None:
    """Return cached result if available, skip tool execution."""
    cache_key = make_cache_key(tool_name, tool_args)
    cached = callback_context.state.get(cache_key)
    if cached is not None:
        logger.info(f"[CACHE HIT] {tool_name} — returning cached result")
        return cached
    return None  # cache miss — proceed with tool call


def caching_after_tool(
    callback_context: CallbackContext,
    tool_name: str,
    tool_args: dict,
    tool_response: dict,
) -> dict | None:
    """Cache successful tool results."""
    if isinstance(tool_response, dict) and tool_response.get("status") == "success":
        cache_key = make_cache_key(tool_name, tool_args)
        callback_context.state[cache_key] = tool_response
        logger.info(f"[CACHE SET] {tool_name} — cached result")
    return None  # pass through unchanged


# Usage
from google.adk.agents import Agent

agent = Agent(
    name="cached_agent",
    before_tool_callback=caching_before_tool,
    after_tool_callback=caching_after_tool,
    ...
)
```

---

## Quality Checklist

```
TESTING:
□ tests/eval_sets/ has minimum 10 eval cases per agent
□ Eval cases cover: happy path, edge case, error/429, boundary/security
□ tests/unit/test_tools.py — every tool has unit tests
□ tests/unit/test_retry.py — 429 retry behavior verified
□ tests/unit/test_prompts.py — hash regression for every prompt
□ tests/integration/test_agent.py — end-to-end with InMemoryRunner
□ tests/load/locustfile.py — load test ready for staging

CI/CD:
□ `make test` runs all tests (Makefile from ASP)
□ Tests run in CI pipeline (.cloudbuild/ or .github/workflows/)
□ Prompt hash failures block the build (forces intentional review)
□ Eval set score thresholds enforced in CI

MONITORING:
□ Tool success/failure rate tracked
□ 429 retry count tracked
□ Cache hit/miss ratio tracked (if using caching callbacks)
□ Response latency p50/p95/p99 dashboards
□ Token usage per invocation logged
```
