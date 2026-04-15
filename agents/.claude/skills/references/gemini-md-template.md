# GEMINI.md Template for ADK Projects

> Copy this template to your project root as `GEMINI.md`.
> It provides context for AI coding assistants (Gemini CLI, Claude Code)
> working on your ADK agent project.

---

```markdown
# GEMINI.md — AI Assistant Context

## Project Overview

This is a Google ADK (Agent Development Kit) agent project.
Framework: google-adk (Python)
Deployment target: {cloud_run | agent_engine | gke}
GCP Project: {PROJECT_ID}
Region: {REGION}

## Architecture

{Describe your agent architecture here}

### Agent Hierarchy

```
root_agent ({model})
├── {sub_agent_1} ({model}) — {description}
├── {sub_agent_2} ({model}) — {description}
└── {sub_agent_3} ({model}) — {description}
```

## Project Structure

```
app/                    # Agent code
├── agent.py            # root_agent entry point
├── prompts.py          # System instructions
├── tools.py            # Tool functions
├── sub_agents/         # Sub-agent modules
│   └── {name}/
│       ├── agent.py
│       ├── prompts.py
│       └── tools.py
├── shared/             # Shared utilities
│   ├── retry.py        # 429 retry with exponential backoff
│   ├── errors.py       # safe_tool decorator
│   ├── logging_config.py
│   └── tracing.py
└── .env                # Local env vars
tests/                  # Tests
├── unit/
├── integration/
├── load/
└── eval_sets/          # ADK evaluation sets
deployment/             # Terraform IaC
Makefile               # Build automation
```

## Mandatory Rules for AI Assistants

### 429 Rate Limit Handling
- EVERY external API call MUST be wrapped with `@with_rate_limit_retry`
- EVERY tool function MUST be wrapped with `@safe_tool`
- Decorator order: `@safe_tool` (outer) → `@with_rate_limit_retry` (inner) → function
- Import from `app.shared.retry` and `app.shared.errors`

### Tool Functions
- ALWAYS return a dict with `status` field ("success", "error", "not_found")
- ALWAYS include type hints and Google-style docstrings
- NEVER include `tool_context` in docstring Args (ADK injects it)
- Cap list results to max 50 items
- NEVER return None — return `{"status": "no_results"}`

### Prompts
- Store in `prompts.py` as Python string constants (never inline in agent.py)
- Use XML tags for structure: <role>, <tools>, <rules>, <workflow>, <output_format>
- Include minimum 3 few-shot examples per agent
- Always include 429 error recovery example

### Project Conventions
- Python 3.11+
- f-strings only (no .format())
- No star imports
- No mutable default args
- Lazy initialization for clients (no module-level side effects)
- Async tools use `aiohttp` (never `requests` in async context)

## Commands

```bash
# Local development
adk web app/              # Web UI with dev tools
adk run app/              # Terminal interactive
make test                 # Run all tests
make deploy               # Deploy to GCP

# Evaluation
adk eval app/ tests/eval_sets/

# Deployment
make setup-dev            # Provision dev environment
make deploy               # Deploy to target
```

## Dependencies

Key packages:
- google-adk>=1.29.0
- google-cloud-bigquery (if using BQ)
- google-cloud-firestore (if using Firestore)
- aiohttp (async HTTP client)
- pydantic (schemas)

## Environment Variables

See `.env` file for local configuration.
In production, use Google Secret Manager.
```
