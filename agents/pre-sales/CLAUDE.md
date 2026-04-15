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

### Module-level variables must not reference runtime state used inside methods

Do not define module-level variables that depend on environment state (e.g.,
`os.environ.get(...)`) and then reference them inside class methods like `set_up()`.
The variable is evaluated at import time, before `set_up()` runs, so it may be stale
or `None`. Instead, call `os.environ.get()` directly where the value is needed.

Example — **wrong** (variable may be None at import time):
```python
location = os.environ.get("GOOGLE_CLOUD_LOCATION")  # module level
class App:
    def set_up(self):
        os.environ["GOOGLE_CLOUD_LOCATION"] = location  # may be None
```
**Correct:**
```python
class App:
    def set_up(self):
        location = os.environ.get("GOOGLE_CLOUD_LOCATION")
        if location:
            os.environ["GOOGLE_CLOUD_LOCATION"] = location
```

### Quality gates and validators must stay in sync

Two validation layers exist and must be kept consistent:
- `_sow_helpers.py` → `QUALITY_GATES` dict (hard minimums for list counts)
- `shared/validators.py` → `ContentValidator` (structural checks: ID formats, word counts, cross-refs)

Both run as hard gates in `generate_sow_document`. When adding a new field or changing
thresholds, update both layers and the corresponding tests in `test_sow_pipeline.py`.

### SOW template variables must match `_apply_defaults()`

The docx template (`templates/SOW_Template.docx`) uses Jinja2 variables. Every variable
must have a default in `_apply_defaults()` in `generate_sow_document.py`, or rendering
will fail with `jinja2.exceptions.UndefinedError`. When adding a new template variable,
always add its default too.

### `project_type` drives template conditional sections

The template has conditional assumptions for `ml` and `genai` project types
(`{%p if project_type == 'ml' or project_type == 'genai' %}`). The `_infer_project_type()`
function in `generate_sow_document.py` detects the type from architecture components,
tech stack, and descriptions. When adding new GCP AI/ML services, update `_GENAI_SERVICES`
or `_ML_SERVICES` sets.

### Deployment

- Existing agent display name: `pre-sales-agent` (not `my-agent`)
- Project: `gcp-sandbox-br`, region: `us-central1`
- Clean `__pycache__` and fix permissions before deploy: `find app -name __pycache__ -exec rm -rf {} +; chmod -R u+rwX app/`
- Agent Engine ID: `2164551916753780736`
- Set `PYTHONIOENCODING=utf-8` when running deploy from Windows to avoid cp1252 encoding errors

### Code style

- Use `structlog` for logging, not `print()` or `logging`
- Use `@safe_tool` decorator on all tool functions (catches exceptions, returns `ToolError`)
- Commit messages: `feat(pre-sales):`, `fix(pre-sales):`, `refactor(pre-sales):`
- Lint before commit: `uv run ruff check app/ tests/ && uv run ruff format app/ tests/`
- Run tests before deploy: `uv run python -m pytest tests/unit/test_sow_pipeline.py -v`
