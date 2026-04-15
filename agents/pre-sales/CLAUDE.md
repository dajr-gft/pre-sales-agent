# CLAUDE.md — Project conventions for Claude Code

## Project overview

Pre-sales agent built on Google Agent Development Kit (ADK), deployed to Vertex AI Agent Engine.
Generates Google DAF/PSF Statements of Work (SOW) via a guided discovery flow.

## Key commands

| Command | Purpose |
|---------|---------|
| `make playground` | Interactive local testing (ADK web UI) |
| `make test` | Run unit + integration tests |
| `make lint` | codespell + ruff check + ruff format + ty |
| `make deploy` | Deploy to Agent Engine (use `--display-name=pre-sales-agent`) |

Run Python with `uv run`.

## Architecture

- `app/agent.py` — Root agent definition (LlmAgent), tools, callbacks
- `app/tools/sow/` — Tool functions: `generate_sow_document`, `generate_architecture_diagram`, `validate_sow_content`
- `app/shared/` — Validators, errors (`safe_tool`), types, config
- `app/skills/sow-generator/SKILL.md` — Agent instructions (discovery flow, generation pipeline)
- `app/callbacks.py` — before/after tool callbacks
- `tests/unit/test_sow_pipeline.py` — 16 integration tests

## Critical rules

### DO NOT use `from __future__ import annotations` in tool files

Files decorated with `@safe_tool` (or any decorator that wraps the function) **must not**
use `from __future__ import annotations`. This import turns type annotations into strings.
The `@wraps(func)` decorator copies the string annotations but the wrapper's `__globals__`
points to the decorator's module (e.g., `errors.py`), not the tool's module. When ADK
resolves the type hints at runtime, it can't find names like `ToolContext`,
`ArchitectureNode`, etc. — causing `NameError` in production.

Python 3.10+ supports `list[X]`, `dict[X, Y]`, and `X | Y` natively, so the future
import is unnecessary.

**Affected files** (must never have this import):
- `app/tools/sow/generate_sow_document.py`
- `app/tools/sow/generate_architecture_diagram.py`
- `app/tools/sow/validate_sow_content.py`

### Tool function signatures must use simple types

ADK automatic function calling cannot parse complex types (Pydantic models, custom classes)
in tool signatures. Use `str` (JSON string) for complex parameters and parse manually
inside the function with `json.loads()`. See `generate_sow_document(sow_data: str)` and
`generate_architecture_diagram(nodes: str, edges: str)` as examples.

### Deployment

- Existing agent display name: `pre-sales-agent` (not `my-agent`)
- Project: `gcp-sandbox-br`, region: `us-central1`
- Clean `__pycache__` and fix permissions before deploy: `find app -name __pycache__ -exec rm -rf {} +; chmod -R u+rwX app/`
- Agent Engine ID: `2164551916753780736`

### Code style

- Use `structlog` for logging, not `print()` or `logging`
- Use `@safe_tool` decorator on all tool functions (catches exceptions, returns `ToolError`)
- Commit messages: `feat(pre-sales):`, `fix(pre-sales):`, `refactor(pre-sales):`
