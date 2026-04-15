# Memory & Session Management

## Table of Contents
1. [Session Services](#session-services)
2. [State Management](#state-management)
3. [Memory Bank (Long-Term)](#memory-bank)
4. [Artifacts](#artifacts)
5. [Production Patterns](#production-patterns)

---

## Session Services

| Service | Persistence | Use Case |
|---|---|---|
| `InMemorySessionService` | None (RAM) | Dev/testing only |
| `DatabaseSessionService` | SQLite/PostgreSQL | Single-server |
| `VertexAiSessionService` | Managed | Multi-instance prod (recommended) |
| Custom Firestore | Firestore | Custom needs |

### InMemory (Dev Only)

```python
from google.adk.sessions import InMemorySessionService
session_service = InMemorySessionService()
```

### Vertex AI (Production)

```python
from google.adk.sessions import VertexAiSessionService

session_service = VertexAiSessionService(
    project=config.project_id,
    location=config.location,
)

# Create
session = await session_service.create_session(
    app_name="my_agent",
    user_id="user_123",
    state={"app:initial": "value"},
)

# Get
session = await session_service.get_session(
    app_name="my_agent", user_id="user_123", session_id=session.id,
)

# List
sessions = await session_service.list_sessions(
    app_name="my_agent", user_id="user_123",
)
```

### Runner Wiring

> **NOTE**: Vertex AI Session Service calls can return 429 (ResourceExhausted) under
> high load. The ADK Runner handles retries internally for session operations,
> but your API server layer should also implement 429 protection (see resilience reference).

```python
from google.adk.runners import Runner
from google.genai.types import Content, Part

runner = Runner(
    agent=root_agent,
    app_name="my_agent",
    session_service=session_service,
)

async for event in runner.run_async(
    session_id=session.id,
    user_id="user_123",
    new_message=Content(parts=[Part.from_text("Hello")]),
):
    if event.content and event.content.parts:
        print(event.content.parts[0].text)
```

---

## State Management

### Scopes

```python
from google.adk.tools import ToolContext

def my_tool(tool_context: ToolContext) -> dict:
    # APP — shared across all agents in session
    tool_context.state["app:workflow_step"] = 3

    # USER — persists across sessions (with persistent session service)
    tool_context.state["user:preferences"] = {"lang": "pt-BR"}

    # TEMP — cleared after invocation
    tool_context.state["temp:buffer"] = "..."

    return {"status": "ok"}
```

### Key Conventions

```python
class StateKeys:
    """Centralized state key definitions — prevents typos and collisions."""
    CART = "app:cart"
    USER_PREFS = "user:preferences"
    WORKFLOW_STEP = "app:workflow:step"
    LAST_QUERY = "app:last_query"
    PROCESSING_STATUS = "app:processing_status"
    TEMP_BUFFER = "temp:buffer"

# Always use defaults when reading
cart = tool_context.state.get(StateKeys.CART, [])
step = tool_context.state.get(StateKeys.WORKFLOW_STEP, 0)
```

### Rules

1. **Store only serializable data** — dict, list, str, int, float, bool, None.
2. **Use `.isoformat()` for datetimes** — not datetime objects.
3. **Keep values small** — state loads on every request. For big data, use artifacts.
4. **Namespace keys** — `app:{agent}:{data}` to prevent collision.
5. **Document every key** — in StateKeys class with comments.

---

## Memory Bank

Long-term memory across sessions:

```python
from google.adk.memory import VertexAiMemoryBankService

memory_service = VertexAiMemoryBankService(
    project=config.project_id,
    location=config.location,
)

runner = Runner(
    agent=root_agent,
    app_name="my_agent",
    session_service=session_service,
    memory_service=memory_service,
)
```

### Memory-Aware Prompt

```python
INSTRUCTION = """<role>You are a personal assistant with long-term memory.</role>

<memory_rules>
- You may receive memories from past conversations — use them naturally.
- Reference past topics to personalize responses.
- If the user corrects a memory, acknowledge and update your understanding.
- NEVER invent memories that weren't provided to you.
- NEVER mention the memory system mechanics to the user.
</memory_rules>"""
```

### InMemory Memory (Testing)

```python
from google.adk.memory import InMemoryMemoryService
memory_service = InMemoryMemoryService()
```

---

## Artifacts

For large or binary data within a session:

```python
from google.genai.types import Part

# Save text artifact
async def save_report(content: str, tool_context: ToolContext) -> dict:
    """Save generated report as artifact.

    Args:
        content: Report content in markdown.
    """
    artifact = Part.from_text(text=content)
    version = await tool_context.save_artifact("report.md", artifact)
    return {"status": "success", "artifact": "report.md", "version": version}

# Load artifact
async def load_report(tool_context: ToolContext) -> dict:
    """Load the previously generated report."""
    artifact = await tool_context.load_artifact("report.md")
    if artifact is None:
        return {"status": "not_found", "error": "No report found."}
    return {"status": "success", "content": artifact.text}

# Binary artifact
async def save_image(image_bytes: bytes, tool_context: ToolContext) -> dict:
    artifact = Part.from_data(data=image_bytes, mime_type="image/png")
    version = await tool_context.save_artifact("output.png", artifact)
    return {"status": "success", "version": version}
```

---

## Production Patterns

### Get or Create Session (with 429 safety)

```python
from google.api_core.exceptions import TooManyRequests, ResourceExhausted

async def get_or_create_session(
    session_service, app_name: str, user_id: str, session_id: str | None = None,
):
    if session_id:
        try:
            session = await session_service.get_session(
                app_name=app_name, user_id=user_id, session_id=session_id,
            )
            if session:
                return session
        except (TooManyRequests, ResourceExhausted) as e:
            logger.warning(f"Session service 429: {e}. Creating new session.")
        except Exception:
            pass
    return await session_service.create_session(app_name=app_name, user_id=user_id)
```

### Multi-Tenant Isolation

```python
# ✅ Tenant-scoped user IDs
session = await session_service.create_session(
    app_name="my_saas",
    user_id=f"tenant_{tenant_id}:user_{user_id}",
)

# ❌ No tenant isolation — collision risk
session = await session_service.create_session(
    app_name="my_saas",
    user_id=user_id,  # same user_id across tenants!
)
```

### Session History Trimming

```python
def trim_session_events(session, max_events: int = 50):
    """Prevent unbounded context growth.
    
    Keep the most recent max_events to control token usage.
    """
    if len(session.events) > max_events:
        session.events = session.events[-max_events:]
```
