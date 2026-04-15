# Architecture & Multi-Agent Patterns

## Table of Contents
1. [Agent Types](#agent-types)
2. [Orchestration Patterns](#orchestration-patterns)
3. [Multi-Agent Communication](#multi-agent-communication)
4. [Agent-to-Agent (A2A) Protocol](#a2a-protocol)
5. [When to Split vs Merge](#split-vs-merge)
6. [State Architecture](#state-architecture)
7. [Model Selection Strategy](#model-selection)
8. [Grounding & RAG Patterns](#grounding)
9. [Anti-Patterns](#anti-patterns)

---

## Agent Types

> **NOTE**: `Agent` and `LlmAgent` are interchangeable — `Agent` is an alias for `LlmAgent`.
> Both imports work: `from google.adk.agents import Agent` or `from google.adk.agents import LlmAgent`.
> The quickstart docs use `Agent`; the GitHub README uses `LlmAgent`. This guide uses `Agent` for brevity.

| Type | Class | Use When |
|---|---|---|
| **LLM Agent** | `Agent` | LLM decides flow dynamically |
| **Sequential** | `SequentialAgent` | Fixed-order pipeline |
| **Parallel** | `ParallelAgent` | Independent concurrent tasks |
| **Loop** | `LoopAgent` | Repeat until exit condition |
| **Custom** | `BaseAgent` | Full control of `_run_async_impl` |

### LLM Agent

```python
from google.adk.agents import Agent

analyst = Agent(
    model="gemini-3-flash",
    name="financial_analyst",
    description="Analyzes financial datasets — route here for any data analysis request.",
    instruction=ANALYST_INSTRUCTION,  # from prompts.py
    tools=[query_database, generate_chart],
)
```

### Sequential Agent

```python
from google.adk.agents import SequentialAgent

pipeline = SequentialAgent(
    name="etl_pipeline",
    description="Extract → transform → load pipeline. Use for batch data processing.",
    sub_agents=[extractor, transformer, validator, loader],
)
```

### Parallel Agent

```python
from google.adk.agents import ParallelAgent

research = ParallelAgent(
    name="parallel_research",
    description="Researches multiple sources simultaneously.",
    sub_agents=[web_researcher, db_researcher, doc_researcher],
)
```

### Loop Agent (ALWAYS set max_iterations)

```python
from google.adk.agents import LoopAgent

refiner = LoopAgent(
    name="iterative_refiner",
    description="Refines output until quality threshold is met.",
    sub_agents=[draft_agent, review_agent],
    max_iterations=5,  # MANDATORY — prevents runaway cost
)
```

### Custom Agent

```python
from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.genai.types import Content, Part

class ConditionalAgent(BaseAgent):
    """Routes based on session state, not LLM decision."""

    async def _run_async_impl(self, ctx: InvocationContext):
        step = ctx.session.state.get("app:workflow_step", 0)
        
        if step == 0:
            target = self.sub_agents[0]  # extractor
        elif step == 1:
            target = self.sub_agents[1]  # processor
        else:
            yield Event(
                author=self.name,
                content=Content(parts=[Part.from_text("Workflow complete.")]),
            )
            return
        
        async for event in target.run_async(ctx):
            yield event
```

---

## Orchestration Patterns

### Pattern 1: Router (Agent Transfer)

```python
root_agent = Agent(
    model="gemini-3-flash",  # Flash for routing = cheap + fast
    name="router",
    description="Routes requests to the appropriate specialist.",
    instruction=ROUTER_INSTRUCTION,
    sub_agents=[billing_agent, tech_agent, account_agent],
)
```

**Router Prompt (engineered):**

```python
ROUTER_INSTRUCTION = """<role>
You are a routing coordinator. Your ONLY job is to analyze the user's request 
and delegate to the correct specialist agent. You NEVER answer domain questions yourself.
</role>

<routing_rules>
| User Intent | Route To | Signal Words |
|---|---|---|
| Billing, payment, invoice, refund, charge | billing_agent | "cobrar", "fatura", "pagamento" |
| Technical issue, bug, error, crash, performance | tech_agent | "erro", "bug", "lento", "crash" |
| Account, password, login, access, permissions | account_agent | "senha", "login", "acesso" |
| General question not matching above | Answer directly | — |
</routing_rules>

<rules>
- If the category is ambiguous, ask ONE clarifying question before routing.
- After routing, do NOT add commentary — let the specialist handle it.
- If the user asks to speak with a human, provide: support@company.com
- NEVER attempt to answer billing/tech/account questions yourself.
</rules>"""
```

### Pattern 2: Orchestrator-Workers

```python
orchestrator = Agent(
    model="gemini-3-flash",
    name="orchestrator",
    description="Coordinates data pipeline — fetching, analysis, reporting.",
    instruction=ORCHESTRATOR_INSTRUCTION,
    sub_agents=[data_worker, analysis_worker, report_worker],
)
```

### Pattern 3: Pipeline with Guardrails

```python
safe_pipeline = SequentialAgent(
    name="safe_pipeline",
    description="Validated input → process → validated output.",
    sub_agents=[
        input_validator,   # checks and sanitizes
        processor,          # main work
        output_validator,   # quality/safety check
    ],
)
```

### Pattern 4: Hierarchical Multi-Agent

```
root_orchestrator (Flash — routing only)
├── infra_orchestrator (Flash)
│   ├── network_agent (Pro — complex reasoning)
│   ├── compute_agent (Flash)
│   └── storage_agent (Flash)
├── data_orchestrator (Flash)
│   ├── ingestion_agent (Flash)
│   └── quality_agent (Pro — analysis)
└── security_agent (Pro — compliance reasoning)
```

> **MAX 3 LEVELS** of nesting. Deeper = more latency + harder debugging.

---

## Multi-Agent Communication

### Via Session State (Primary — Idiomatic ADK)

```python
# Agent A writes
def fetch_data(query: str, tool_context: ToolContext) -> dict:
    results = db.query(query)
    tool_context.state["app:fetched_data"] = results
    return {"status": "success", "row_count": len(results)}

# Agent B reads (via instruction)
agent_b = Agent(
    instruction="""...
    The data from the previous step is available in state key 'app:fetched_data'.
    Process it according to the rules below.
    """,
)
```

### State Key Convention

```python
# Namespaced, predictable, documented
"app:{agent_name}:{data_name}"     # e.g., "app:extractor:raw_rows"
"app:shared:{data_name}"           # cross-agent shared data
"user:{preference_name}"           # persists across sessions
"temp:{operation}:{data}"          # cleared after invocation
```

### Via Artifacts (Large/Binary Data)

```python
from google.genai.types import Part

async def save_report(content: str, tool_context: ToolContext) -> dict:
    artifact = Part.from_text(text=content)
    version = await tool_context.save_artifact("report_v1", artifact)
    return {"status": "success", "version": version}
```

---

## Agent Transfer & Flow Control

> **CRITICAL for multi-agent systems.** This is where most ADK projects get wrong.

### sub_agents vs AgentTool — THE KEY DIFFERENCE

```python
from google.adk.agents import Agent
from google.adk.tools import AgentTool

specialist = Agent(name="specialist", description="Domain expert.", ...)

# OPTION 1: sub_agents — TRANSFERS CONTROL
# Parent is OUT OF THE LOOP. Specialist answers the user directly.
# All subsequent user input goes to specialist until it transfers back.
parent = Agent(
    name="parent",
    sub_agents=[specialist],  # specialist takes over completely
    instruction="Transfer to specialist for domain questions.",
)

# OPTION 2: AgentTool — TOOL CALL (parent keeps control)
# Specialist runs, returns result TO THE PARENT. Parent answers the user.
specialist_tool = AgentTool(agent=specialist)
parent = Agent(
    name="parent",
    tools=[specialist_tool],  # parent calls specialist as tool, keeps control
    instruction="Use specialist tool for domain analysis, then summarize.",
)
```

**When to use which:**

| Use | sub_agents | AgentTool |
|---|---|---|
| Who answers the user? | Sub-agent directly | Parent (synthesizes) |
| Parent involved? | No (out of loop) | Yes (orchestrates) |
| Context shared? | Full session | Only tool I/O |
| Best for | Routing/delegation | Data gathering/analysis |

### transfer_to_agent — Programmatic Transfer

Tools can force transfer to another agent:

```python
from google.adk.tools import ToolContext

def escalate_to_human(
    reason: str,
    tool_context: ToolContext,
) -> dict:
    """Escalate the conversation to a human agent.

    Args:
        reason: Why the issue needs human intervention.
    """
    tool_context.actions.transfer_to_agent = "human_handoff_agent"
    return {"status": "transferred", "reason": reason}

def give_up(tool_context: ToolContext) -> dict:
    """Escalate to parent agent when unable to handle request."""
    tool_context.actions.escalate = True
    return {"status": "escalated", "message": "Returning to parent agent."}
```

### ToolContext.actions — Complete Reference

```python
def my_tool(query: str, tool_context: ToolContext) -> dict:
    # Transfer control to named agent
    tool_context.actions.transfer_to_agent = "other_agent"

    # Escalate to parent (exit current agent)
    tool_context.actions.escalate = True

    # Skip LLM summarization of tool result
    tool_context.actions.skip_summarization = True

    # End the entire invocation immediately
    tool_context.actions.end_invocation = True

    return {"status": "ok"}
```

### Transfer Scope Control

```python
leaf_agent = Agent(
    name="leaf_worker",
    disallow_transfer_to_parent=True,   # cannot escalate to parent
    disallow_transfer_to_peers=True,    # cannot transfer to siblings
    instruction="You must handle all requests yourself. Never escalate.",
    ...
)

# global_instruction — inherited by ALL sub-agents
root = Agent(
    name="root",
    global_instruction="Always respond in Brazilian Portuguese. Be concise. Cite sources.",
    sub_agents=[agent_a, agent_b, agent_c],
    ...
)
```

---

## Structured Output (output_schema)

Force agents to return structured JSON:

```python
from pydantic import BaseModel, Field

class AnalysisResult(BaseModel):
    summary: str = Field(description="One-paragraph analysis summary")
    risk_level: str = Field(description="low, medium, high, or critical")
    recommendations: list[str] = Field(description="Actionable next steps")

analyst = Agent(
    name="analyst",
    output_schema=AnalysisResult,
    output_key="app:analysis_result",  # auto-saved to session state
    instruction="""Analyze the data and return a structured result.
    After using tools, produce your final output matching the schema.""",
    tools=[query_database],
)
```

> **KNOWN ISSUE**: `output_schema` + `tools` can cause infinite loops in some ADK versions.
> **Workaround**: Add explicit instructions like "After receiving tool results, produce
> your final structured output. Do NOT call tools more than once."

---

## A2A Protocol

For cross-service agent communication:

```python
from google.adk.tools.a2a_tool import A2ATool

remote_tool = A2ATool(
    name="remote_specialist",
    description="Connects to remote agent for domain-specific tasks.",
    agent_url="https://specialist-agent.run.app/.well-known/agent.json",
)

coordinator = Agent(
    name="coordinator",
    tools=[remote_tool],
    instruction="Use remote_specialist when the request requires domain expertise.",
)
```

---

## Split vs Merge

### Split when:
- Tasks need **different models** (Flash for speed, Pro for reasoning)
- Tasks have **different tool sets** with no overlap
- Responsibilities are **cleanly separable** (billing vs tech support)
- You need **independent testing/deployment** of capabilities
- **Latency/cost profiles** differ (fast classifier vs slow analyzer)

### Keep single when:
- Tasks are **tightly coupled** and share context heavily
- Splitting forces **excessive state passing** between agents
- The task is **simple enough** for one instruction + tool set
- Adding agents would **increase latency** without benefit

---

## Model Selection

### Gemini 3.x Model Family (Current — April 2026)

| Model | String | Use For | Cost | Thinking |
|---|---|---|---|---|
| **3 Flash** | `gemini-3-flash` | Routing, agentic workflows, general | $0.50/1M in | Yes (thinking_level) |
| **3.1 Pro** | `gemini-3.1-pro` | Complex reasoning, synthesis, analysis | Higher | Yes |
| **3.1 Flash-Lite** | `gemini-3.1-flash-lite` | High-volume, classification, translation | $0.25/1M in | Minimal |

```python
# ROUTING — Flash (fast, cheap, best for agentic workflows)
router = Agent(model="gemini-3-flash", ...)

# HIGH-VOLUME LOW-COMPLEXITY — Flash-Lite (cheapest)
classifier = Agent(model="gemini-3.1-flash-lite", ...)

# COMPLEX REASONING — Pro (most capable)
reasoner = Agent(model="gemini-3.1-pro", ...)

# GENERAL PURPOSE / DEFAULT — Flash
worker = Agent(model="gemini-3-flash", ...)
```

### Thinking Level Configuration (Gemini 3.x)

```python
from google.genai.types import GenerateContentConfig, ThinkingConfig

# Control reasoning depth per agent
agent = Agent(
    model="gemini-3-flash",
    generate_content_config=GenerateContentConfig(
        thinking_config=ThinkingConfig(
            thinking_level="medium",  # minimal, low, medium, high
        ),
        temperature=0.7,
        max_output_tokens=8192,
    ),
)
```

| thinking_level | Use When | Latency | Cost |
|---|---|---|---|
| `minimal` | Routing, classification, simple tasks | Fastest | Lowest |
| `low` | Tool selection, structured output | Fast | Low |
| `medium` | General purpose, balanced | Moderate | Medium |
| `high` | Complex analysis, multi-step reasoning | Slower | Higher |

**Rule**: Use the cheapest model + lowest thinking_level that meets quality.
Start with Flash + minimal → upgrade only if quality insufficient.

---

## Grounding & RAG Patterns

### Vertex AI Search Grounding

```python
from google.adk.tools import VertexAiSearchTool

rag_tool = VertexAiSearchTool(
    data_store_id="projects/P/locations/global/collections/default_collection/dataStores/DS_ID",
)

agent = Agent(
    tools=[rag_tool],
    instruction="""<role>You answer questions based on the knowledge base.</role>
    <rules>
    - ALWAYS search the knowledge base before answering
    - Cite the source document for every claim
    - If the knowledge base has no relevant result, say "I don't have that information"
    - NEVER fabricate information not present in search results
    </rules>""",
)
```

### Google Search Grounding

```python
from google.adk.tools import google_search

agent = Agent(
    tools=[google_search],
    instruction="Search the web for current information when needed.",
)
```

---

## Anti-Patterns

| Anti-Pattern | Problem | Fix |
|---|---|---|
| **God Agent** | One agent, 50 tools, 3000-word prompt | Split into specialized sub-agents |
| **Chatty Agents** | Agents pass messages back and forth | Use session state for data sharing |
| **Stateful Tools** | Module-level variables in tools | Use `tool_context.state` |
| **Missing Descriptions** | Sub-agents without `description` | Parent uses description for routing — be precise |
| **Unbounded Loops** | LoopAgent without `max_iterations` | Always set max_iterations (3-10) |
| **Over-Nesting** | 5+ levels of hierarchy | Flatten to 2-3 levels |
| **No 429 Handling** | External calls without retry | Apply `with_rate_limit_retry` to ALL calls |
| **Inline Prompts** | Instructions as raw strings in agent.py | Move to `prompts.py` as constants |
| **Untyped Tools** | No type hints or docstrings | ADK needs these for schema generation |
| **Giant Returns** | Tools returning 100k rows to LLM | Paginate, cap at 20-50, summarize |
| **Wrong Delegation** | Using sub_agents when AgentTool is needed | sub_agents = transfer control; AgentTool = keep control |
| **No Transfer Control** | Agents can escalate/transfer freely | Set `disallow_transfer_to_parent/peers` on leaf agents |
| **output_schema + tools** | Infinite loop bug | Add explicit "produce final output" instructions |
| **Hardcoded Models** | Pinned model versions that expire | Use env vars or `gemini-3-flash` (stable) |
