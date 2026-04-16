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

from .callbacks import after_tool_callback, before_tool_callback
from .config import config
from .prompts import build_instruction
from .shared.logging_config import setup_logging
from .tools.sow.generate_architecture_diagram import (
    generate_architecture_diagram,
)
from .tools.sow.generate_sow_document import generate_sow_document
from .tools.sow.validate_sow_content import validate_sow_content

# --- Bootstrap ---
setup_logging(level=config.LOG_LEVEL, json_output=config.LOG_JSON)
logger = structlog.get_logger()

project_id = config.resolve_project_id()
os.environ["GOOGLE_CLOUD_PROJECT"] = project_id
os.environ["GOOGLE_CLOUD_LOCATION"] = config.LOCATION
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = str(config.GOOGLE_GENAI_USE_VERTEXAI)

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
        model=config.GEMINI_MODEL,
        retry_options=types.HttpRetryOptions(attempts=config.MAX_RETRIES),
    ),
    instruction="You are a web search specialist. Search the web and return relevant, factual results.",
    tools=[GoogleSearchTool()],
    generate_content_config=types.GenerateContentConfig(
        temperature=config.TEMPERATURE,
    ),
)

# --- Tools ---
_TOOLS = [
    pre_sales_skill_toolset,
    load_artifacts,
    generate_architecture_diagram,
    validate_sow_content,
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
        model=config.GEMINI_MODEL,
        retry_options=types.HttpRetryOptions(attempts=config.MAX_RETRIES),
    ),
    instruction=build_instruction(company_name=config.COMPANY_NAME),
    tools=_TOOLS,
    before_tool_callback=before_tool_callback,
    after_tool_callback=after_tool_callback,
    generate_content_config=types.GenerateContentConfig(
        temperature=config.TEMPERATURE,
        thinking_config=types.ThinkingConfig(
            include_thoughts=False,
            thinking_budget=config.THINKING_BUDGET,
        ),
    ),
)

app = App(
    root_agent=root_agent,
    name="app",
)

logger.info(
    "agent_initialized",
    model=config.GEMINI_MODEL,
    tools=len(_TOOLS),
    thinking_budget=config.THINKING_BUDGET,
    skills_dir=str(_SKILLS_DIR),
)
