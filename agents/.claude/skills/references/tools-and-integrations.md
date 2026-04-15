# Tools & Integrations

## Table of Contents
1. [Tool Types](#tool-types)
2. [Function Tools](#function-tools)
3. [MCP Integration](#mcp)
4. [REST API Tools](#rest-api)
5. [Google Cloud Tools](#gcp-tools)
6. [Built-in Tools](#built-in)
7. [Composition Patterns](#composition)
8. [Design Guidelines](#guidelines)

---

## Tool Types

| Type | Class/Pattern | Best For |
|---|---|---|
| **Function Tool** | Python function | Custom logic, DB queries, computations |
| **MCP Tool** | `MCPToolset` | MCP-compatible external services |
| **REST Tool** | `RestApiTool` / manual | External REST APIs |
| **Agent Tool** | `AgentTool` | Wrapping another agent as a tool |
| **A2A Tool** | `A2ATool` | Cross-service agent communication |
| **Built-in** | `google_search`, `code_execution` | Web search, code sandbox |

---

## Function Tools

### Basic Tool with Error Handling

```python
from app.shared.errors import safe_tool

@safe_tool
def calculate_mortgage(
    principal: float,
    annual_rate: float,
    years: int,
) -> dict:
    """Calculate monthly mortgage payment and total cost.

    Args:
        principal: Loan amount in dollars (e.g., 350000).
        annual_rate: Annual interest rate as percentage (e.g., 6.5 for 6.5%).
        years: Loan term in years (e.g., 30).
    """
    if principal <= 0:
        return {"status": "error", "error": "Principal must be positive."}
    if annual_rate < 0:
        return {"status": "error", "error": "Rate cannot be negative."}
    if years <= 0:
        return {"status": "error", "error": "Term must be positive."}

    monthly_rate = annual_rate / 100 / 12
    n = years * 12

    if monthly_rate == 0:
        monthly = principal / n
    else:
        monthly = principal * (monthly_rate * (1 + monthly_rate)**n) / ((1 + monthly_rate)**n - 1)

    return {
        "status": "success",
        "monthly_payment": round(monthly, 2),
        "total_paid": round(monthly * n, 2),
        "total_interest": round(monthly * n - principal, 2),
    }
```

### Tool with State + 429 Retry

```python
from google.adk.tools import ToolContext
from app.shared.errors import safe_tool
from app.shared.retry import with_rate_limit_retry, RetryableError
import aiohttp

@safe_tool
@with_rate_limit_retry(max_retries=5, base_delay=1.0)
async def enrich_customer(
    customer_id: str,
    tool_context: ToolContext,
) -> dict:
    """Fetch and enrich customer data from external CRM API.

    Args:
        customer_id: The customer identifier (e.g., 'CUST-12345').
    """
    url = f"https://api.crm.com/customers/{customer_id}"
    async with aiohttp.ClientSession() as session:
        async with session.get(
            url,
            headers={"Authorization": f"Bearer {os.environ['CRM_TOKEN']}"},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status == 429:
                retry_after = resp.headers.get("Retry-After")
                raise RetryableError("HTTP 429", retry_after=float(retry_after) if retry_after else None)
            if resp.status >= 500:
                raise RetryableError(f"HTTP {resp.status}")
            if resp.status == 404:
                return {"status": "not_found", "error": f"Customer {customer_id} not found."}
            resp.raise_for_status()

            data = await resp.json()
            tool_context.state["app:customer_data"] = data
            return {
                "status": "success",
                "customer": {
                    "name": data["name"],
                    "segment": data["segment"],
                    "lifetime_value": data["ltv"],
                },
            }
```

---

## MCP Integration

### SSE-Based MCP Server

```python
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset, SseServerParams
import asyncio

async def get_mcp_tools():
    """Connect to MCP server with timeout + error handling."""
    try:
        tools, exit_stack = await asyncio.wait_for(
            MCPToolset.from_server(
                connection_params=SseServerParams(
                    url="http://localhost:3000/sse",
                    headers={"Authorization": f"Bearer {os.environ.get('MCP_TOKEN', '')}"},
                ),
            ),
            timeout=15.0,  # fail fast if MCP server is down
        )
        return tools, exit_stack
    except asyncio.TimeoutError:
        logger.error("MCP server connection timed out (15s)")
        return [], None
    except Exception as e:
        logger.error(f"MCP setup failed: {e}")
        return [], None
```

### Stdio MCP Server

```python
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset, StdioServerParams

async def get_filesystem_tools():
    tools, exit_stack = await MCPToolset.from_server(
        connection_params=StdioServerParams(
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "/data"],
        ),
    )
    return tools, exit_stack
```

### MCP Best Practices

1. **Always set connection timeout** (10-15s).
2. **Always close exit_stack** — use `async with` or explicit `aclose()`.
3. **Cache tool lists** — don't reconnect on every request.
4. **Handle MCP downtime** — agent must work (degraded) without MCP.
5. **MCP calls can also return 429** — ensure the MCP server handles rate limits.

```python
# Production pattern — graceful degradation
async def create_agent_with_mcp():
    mcp_tools, exit_stack = await get_mcp_tools()
    
    all_tools = [*local_tools]  # local tools always available
    if mcp_tools:
        all_tools.extend(mcp_tools)
        logger.info(f"Loaded {len(mcp_tools)} MCP tools")
    else:
        logger.warning("MCP unavailable — running with local tools only")

    agent = Agent(
        name="agent",
        tools=all_tools,
        instruction=INSTRUCTION,
    )
    return agent, exit_stack
```

---

## REST API Tools

### From OpenAPI Spec

```python
from google.adk.tools.openapi_tool.openapi_spec_parser import OpenApiSpecParser

toolset = OpenApiSpecParser.parse("path/to/openapi.yaml")
agent = Agent(name="api_agent", tools=toolset)
```

### Manual REST Tool (with 429 handling)

```python
@safe_tool
@with_rate_limit_retry(max_retries=5, base_delay=1.0)
async def get_weather(
    city: str,
    country_code: str = "BR",
) -> dict:
    """Get current weather for a city.

    Args:
        city: City name (e.g., 'São Paulo').
        country_code: ISO 3166-1 alpha-2 country code. Defaults to 'BR'.
    """
    api_key = os.environ["WEATHER_API_KEY"]
    url = "https://api.openweathermap.org/data/2.5/weather"
    params = {"q": f"{city},{country_code}", "appid": api_key, "units": "metric"}

    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 429:
                raise RetryableError("HTTP 429", retry_after=resp.headers.get("Retry-After"))
            if resp.status >= 500:
                raise RetryableError(f"HTTP {resp.status}")
            if resp.status == 404:
                return {"status": "not_found", "error": f"City '{city}' not found."}
            resp.raise_for_status()

            data = await resp.json()
            return {
                "status": "success",
                "city": city,
                "temperature_celsius": data["main"]["temp"],
                "description": data["weather"][0]["description"],
                "humidity_percent": data["main"]["humidity"],
            }
```

---

## Google Cloud Tools

### BigQuery (with 429)

```python
from google.cloud import bigquery
from google.api_core.exceptions import TooManyRequests, ServiceUnavailable

_bq = None
def _get_bq():
    global _bq
    if _bq is None:
        _bq = bigquery.Client()
    return _bq

@safe_tool
def query_bigquery(
    sql: str,
    max_rows: int = 100,
) -> dict:
    """Execute a read-only BigQuery SQL query.

    Args:
        sql: SQL SELECT query to execute. Only SELECT is allowed.
        max_rows: Maximum rows to return (1-500). Defaults to 100.
    """
    sql_upper = sql.strip().upper()
    if not sql_upper.startswith("SELECT") and not sql_upper.startswith("WITH"):
        return {"status": "error", "error": "Only SELECT/WITH queries allowed."}

    max_rows = min(max(max_rows, 1), 500)

    try:
        client = _get_bq()
        job = client.query(sql)
        rows = [dict(row) for row in job.result(max_results=max_rows)]
        return {
            "status": "success",
            "row_count": len(rows),
            "rows": rows,
            "total_rows": job.result().total_rows,
            "truncated": job.result().total_rows > max_rows,
        }
    except TooManyRequests as e:
        return {
            "status": "error",
            "error": "BigQuery rate limit exceeded (429). Wait and retry.",
            "retryable": True,
        }
    except ServiceUnavailable as e:
        return {
            "status": "error",
            "error": "BigQuery temporarily unavailable (503). Wait and retry.",
            "retryable": True,
        }
```

### Firestore (with 429)

```python
from google.cloud import firestore
from google.api_core.exceptions import TooManyRequests, ResourceExhausted

@safe_tool
def save_to_firestore(
    collection: str,
    document_id: str,
    data: dict,
) -> dict:
    """Save data to Firestore.

    Args:
        collection: Firestore collection name.
        document_id: Document identifier.
        data: JSON object to store.
    """
    try:
        db = firestore.Client()
        doc_ref = db.collection(collection).document(document_id)
        doc_ref.set(data, merge=True)
        return {"status": "success", "path": f"{collection}/{document_id}"}
    except (TooManyRequests, ResourceExhausted) as e:
        return {
            "status": "error",
            "error": f"Firestore rate limited: {e}",
            "retryable": True,
        }
```

### Vertex AI Search — RAG (SERVERLESS — PREFERRED)

> **ALWAYS prefer Vertex AI Search** for RAG workloads. It's fully managed,
> serverless, auto-scales, and requires zero infrastructure management.
> Prioritize over self-hosted vector DBs (Pinecone, Weaviate, pgvector).

```python
from google.adk.tools import VertexAiSearchTool

# Basic RAG — point to your datastore
rag_tool = VertexAiSearchTool(
    data_store_id="projects/PROJECT/locations/global/collections/default_collection/dataStores/DS_ID",
)

# Use in agent with grounding instructions
rag_agent = Agent(
    model="gemini-3-flash",
    name="knowledge_agent",
    description="Answers questions from the internal knowledge base.",
    instruction="""<role>You answer questions using the knowledge base.</role>
    <rules>
    - ALWAYS search the knowledge base before answering
    - Cite the source document for every claim
    - If no relevant result, say "I don't have that information"
    - NEVER fabricate information not in search results
    </rules>""",
    tools=[rag_tool],
)
```

**Serverless GCP Services — Priority Order:**

| Service | Use For | Why Prioritize |
|---|---|---|
| **Vertex AI Agent Engine** | **Internal conversational agents (1st choice)** | **Managed runtime, sessions, eval, zero-infra** |
| **Vertex AI Search** | RAG, knowledge base, document Q&A | Managed, auto-indexed, serverless |
| **BigQuery** | Analytics, data warehouse queries | Serverless SQL, pay-per-query |
| **Firestore** | Session state, user data, real-time | Managed NoSQL, auto-scale |
| **Cloud Run** | Custom API, external-facing agents, webhooks | Serverless containers, full control |
| **Cloud Functions** | Lightweight tool backends | Event-driven, zero-infra |
| **Secret Manager** | API keys, credentials | Managed, IAM-integrated |

> **DECISION RULE**: For internal/conversational agents → **Agent Engine first**.
> For external-facing APIs, webhooks, or custom server logic → Cloud Run.
> Agent Engine handles sessions, auth, scaling, and eval natively — no Dockerfile needed.

---

## ADK Skills (SkillToolset)

> **Skills are the ADK way to package reusable agent capabilities.**
> Use SkillToolset for progressive context loading — only loads what's needed,
> saving tokens and cost. Skills are experimental in ADK v1.29.0.

### Inline Skill

```python
from google.adk.skills import models
from google.adk.skills import SkillToolset

# Pattern 1: Inline — for small, stable rules
seo_skill = models.Skill(
    frontmatter=models.Frontmatter(
        name="seo-checklist",
        description="SEO optimization checklist for blog posts.",
    ),
    instructions="Check title tags, meta descriptions, H1 usage, alt text...",
)

# Create toolset that enables progressive loading
skill_toolset = SkillToolset(skills=[seo_skill])

agent = Agent(
    model="gemini-3-flash",
    name="content_agent",
    tools=skill_toolset.tools,  # adds list_skills, load_skill, load_skill_resource
    instruction="You create SEO-optimized content. Use skills when needed.",
)
```

### File-Based Skill

```
# skills/code-reviewer/SKILL.md
---
name: code-reviewer
description: Reviews Python code for security vulnerabilities and best practices.
---

# Code Review Skill

## When to Use
- User asks for code review
- User submits Python code for analysis

## Instructions
1. Check for SQL injection vulnerabilities
2. Verify input validation on all entry points
3. Check for hardcoded secrets
4. Verify error handling patterns
...
```

```python
from google.adk.skills import SkillToolset

# Load from directory
skill_toolset = SkillToolset(skill_dirs=["./skills"])

agent = Agent(
    model="gemini-3-flash",
    name="dev_assistant",
    tools=skill_toolset.tools,
    instruction="You assist developers. Use available skills when relevant.",
)
```

### Progressive Disclosure Architecture

```
L1 — Metadata (~50-100 tokens per skill)
  → list_skills tool: returns names + descriptions
  → Agent decides which skill to activate

L2 — Instructions (~500-2000 tokens)
  → load_skill tool: loads full SKILL.md instructions
  → Agent follows the skill's workflow

L3 — Resources (variable)
  → load_skill_resource tool: loads reference files
  → Only when skill instructions reference them
```

> **Why Skills matter**: An agent with 10 skills starts with ~1000 tokens (L1 only)
> instead of ~10000 tokens in a monolithic prompt. **90% reduction in baseline context.**

---

## Built-in Tools

```python
from google.adk.tools import google_search, code_execution

# Google Search — web grounding (serverless, no setup)
agent = Agent(tools=[google_search], ...)

# Code Execution — sandboxed Python (serverless sandbox)
agent = Agent(tools=[code_execution], ...)
```

---

## Composition Patterns

### By Permission Level

```python
read_tools = [search_db, get_record, list_tables]
write_tools = [create_record, update_record]
admin_tools = [delete_record, modify_schema]

reader_agent = Agent(name="reader", tools=read_tools, ...)
writer_agent = Agent(name="writer", tools=read_tools + write_tools, ...)
admin_agent = Agent(name="admin", tools=read_tools + write_tools + admin_tools, ...)
```

### Dynamic Loading

```python
def get_tools() -> list:
    tools = [search, get_info]  # always available
    if config.enable_writes:
        tools.extend([create, update, delete])
    if config.enable_external:
        tools.extend([weather_api, translate_api])
    return tools
```

---

## Design Guidelines

| Rule | Why |
|---|---|
| Name as **verb phrases** (`search_customers`, not `customers`) | LLM understands actions better |
| **One action per tool** (not `manage_user(action=...)`) | Reduces LLM confusion |
| **Validate inputs early** — return error before doing work | Saves cost and latency |
| **Fail with useful messages** — include `suggestion` field | LLM can self-correct |
| **Cap output size** — max 20-50 items, truncate text | Tokens = cost |
| **Always handle 429** — `@with_rate_limit_retry` on external calls | Non-negotiable |
| **Idempotent operations** — safe to retry | Enables resilient retry |
| **Timeout on every HTTP call** — 10-30s max | Prevents hanging |
