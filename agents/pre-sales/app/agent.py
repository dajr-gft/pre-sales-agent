import os
from pathlib import Path

import structlog
from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models import Gemini
from google.adk.skills import load_skill_from_dir
from google.adk.tools import load_artifacts, skill_toolset
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.google_search_tool import GoogleSearchTool
from google.genai import types

from .config import config
from .prompts import build_instruction
from .shared.logging_config import setup_logging
from .tools.sow.generate_architecture_diagram import (
    generate_architecture_diagram,
)
from .tools.sow.generate_sow_document import generate_sow_document

# --- Bootstrap ---
setup_logging(level=config.log_level, json_output=config.log_json)
logger = structlog.get_logger()

project_id = config.resolve_project_id()
os.environ["GOOGLE_CLOUD_PROJECT"] = project_id
os.environ["GOOGLE_CLOUD_LOCATION"] = config.location
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "True"

# --- Skills ---
_SKILLS_DIR = Path(__file__).parent / "skills"

pre_sales_skill_toolset = skill_toolset.SkillToolset(
    skills=[
        load_skill_from_dir(_SKILLS_DIR / "sow-generator"),
    ]
)

# --- Sub-agents ---
google_search_agent = Agent(
    name="google_search_agent",
    description="Searches the web for current and relevant information.",
    model=Gemini(
        model=config.gemini_model,
        retry_options=types.HttpRetryOptions(attempts=config.max_retries),
    ),
    instruction="You are a web search specialist. Search the web and return relevant, factual results.",
    tools=[GoogleSearchTool()],
    generate_content_config=types.GenerateContentConfig(
        temperature=config.temperature,
    ),
)

# --- Tools ---
_TOOLS = [
    pre_sales_skill_toolset,
    load_artifacts,
    generate_architecture_diagram,
    generate_sow_document,
    AgentTool(agent=google_search_agent),
]

# --- Root Agent ---
root_agent = Agent(
    name="pre_sales_assistant",
    description=(
        "Assists the Pre-Sales team with technical and commercial routines, "
        "including the elaboration of Statements of Work (SOW) and other pre-sales artifacts."
    ),
    model=Gemini(
        model=config.gemini_model,
        retry_options=types.HttpRetryOptions(attempts=config.max_retries),
    ),
    instruction=build_instruction(company_name=config.company_name),
    tools=_TOOLS,
    generate_content_config=types.GenerateContentConfig(
        temperature=config.temperature,
        thinking_config=types.ThinkingConfig(
            include_thoughts=False,
            thinking_budget=config.thinking_budget,
        ),
    ),
)

app = App(
    root_agent=root_agent,
    name="app",
)

logger.info(
    "agent_initialized",
    model=config.gemini_model,
    tools=len(_TOOLS),
    thinking_budget=config.thinking_budget,
    skills_dir=str(_SKILLS_DIR),
)
