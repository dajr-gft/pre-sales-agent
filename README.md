# Pre-Sales Agent

AI-powered pre-sales assistant built with [Google Agent Development Kit (ADK)](https://google.github.io/adk-docs/) and deployed on [Vertex AI Agent Engine](https://cloud.google.com/vertex-ai/generative-ai/docs/agent-engine/overview).

The agent supports the pre-sales team with structured technical and commercial routines through a skills-based architecture. Each skill encapsulates a complete workflow — from information gathering to document generation — and can be activated on demand by the agent.

## Skills

| Skill | Description | Status |
|-------|-------------|--------|
| **SOW Generator** | Generates complete Statement of Work documents (.docx) following the Google DAF/PSF template. Supports two paths: guided interview or transcript extraction. Includes architecture diagram generation with official GCP service icons. | ✅ Active |

> Additional skills will be added as the project evolves.

## Project Structure

```
pre-sales-agent/
├── app/
│   ├── agent.py                    # Agent definition and tool registration
│   ├── agent_engine_app.py         # Agent Engine (AdkApp) configuration
│   ├── app_utils/                  # App-level utilities
│   ├── prompts/                    # Root agent and skill prompts
│   ├── skills/                     # Skill definitions (SKILL.md + references)
│   └── tools/
│       └── sow/
│           ├── generate_sow.py                  # SOW document generation tool
│           ├── generate_architecture_diagram.py  # Architecture diagram generation tool
│           ├── request_customer_logo.py          # Logo request flow tool
│           ├── _sow_helpers.py                   # Quality gates, logo loading, GCS utils
│           ├── _diagram_models.py                # Enums, Pydantic models, icon mapping
│           └── templates/                        # .docx template and partner logo
├── tests/                          # Unit, integration, and evaluation tests
├── Makefile                        # Development and deployment commands
├── pyproject.toml                  # Project dependencies
└── installation_scripts/           # Agent Engine setup (e.g. Graphviz install)
```

## Requirements

- **Python 3.12+**
- **Google Cloud SDK** — [Install](https://cloud.google.com/sdk/docs/install)
- **make** — pre-installed on most Unix-based systems
- **Graphviz** — required for architecture diagram generation (installed automatically on Agent Engine via `installation_scripts/`)

## Quick Start

Install dependencies and launch the local development environment:

```bash
make install && make playground
```

Edit your agent logic in `app/agent.py` and test with `make playground` — it auto-reloads on save.

## Commands

| Command              | Description                                      |
|----------------------|--------------------------------------------------|
| `make install`       | Install dependencies                             |
| `make playground`    | Launch local development environment (ADK Web)   |
| `make lint`          | Run code quality checks                          |
| `make test`          | Run unit and integration tests                   |
| `make deploy`        | Deploy agent to Vertex AI Agent Engine            |

For full command options, refer to the [Makefile](Makefile).

## Deployment

```bash
gcloud config set project <your-project-id>
make deploy
```

The agent is deployed to Vertex AI Agent Engine with managed sessions, GCS-backed artifact storage, and Cloud Logging telemetry.

## Architecture Overview

The agent follows a **root orchestrator + skills** pattern:

1. **Root Agent** — Receives user requests, identifies the appropriate skill, and activates it.
2. **Skills** — Self-contained workflows defined by SKILL.md prompt files with supporting reference documents.
3. **Tools** — Python functions registered in ADK that execute concrete actions (generate documents, render diagrams, handle uploads).
4. **Callbacks** — Before-model interceptors for handling multimodal inputs (e.g., customer logo uploads).

### SOW Generation Flow

```
User request → Root Agent → SOW Generator Skill
                                ├── Discovery (guided interview or transcript extraction)
                                ├── Content Generation & Review
                                ├── Architecture Diagram (generate_architecture_diagram)
                                ├── Customer Logo (intercept_image_uploads callback)
                                └── Document Assembly (generate_sow_document → .docx)
```

## Observability

Built-in telemetry exports to Cloud Trace, BigQuery, and Cloud Logging. Agent interactions, tool calls, and model completions are logged in the configured GCS bucket.