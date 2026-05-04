import os
from pathlib import Path

import google.auth
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
from .guardrails import scope_guardrail
from .prompts import build_instruction
from .shared.logging_config import setup_logging
from .tools.sow.generate_architecture_diagram import \
    generate_architecture_diagram
from .tools.sow.generate_sow_document import generate_sow_document
from .tools.sow.manifest_tools import (
    append_extraction_items,
    finalize_extraction_manifest,
    initialize_extraction_buffer,
    load_extraction_manifest,
    validate_extraction_manifest,
)
from .tools.sow.validate_sow_content import validate_sow_content

# --- Bootstrap ---
setup_logging(level=config.LOG_LEVEL, json_output=config.LOG_JSON)
logger = structlog.get_logger()

_, project_id = google.auth.default()
os.environ['GOOGLE_CLOUD_PROJECT'] = project_id
os.environ['GOOGLE_CLOUD_LOCATION'] = 'global'
os.environ['GOOGLE_GENAI_USE_VERTEXAI'] = 'True'

# --- Skills ---
_SKILLS_DIR = Path(__file__).parent / 'skills'

pre_sales_skill_toolset = skill_toolset.SkillToolset(
    skills=[
        load_skill_from_dir(_SKILLS_DIR / 'sow-generator'),
        load_skill_from_dir(_SKILLS_DIR / 'sow-discovery')
    ]
)

_SAFETY_THRESHOLD = types.HarmBlockThreshold(config.SAFETY_HARM_BLOCK_THRESHOLD)
_SAFETY_SETTINGS = [
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
        threshold=_SAFETY_THRESHOLD,
    ),
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
        threshold=_SAFETY_THRESHOLD,
    ),
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
        threshold=_SAFETY_THRESHOLD,
    ),
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
        threshold=_SAFETY_THRESHOLD,
    ),
]

# --- Sub-agents ---
google_search_agent = Agent(
    name='google_search_agent',
    description='Searches the web for current and relevant information.',
    model=Gemini(
        model=config.GEMINI_MODEL,
        retry_options=types.HttpRetryOptions(attempts=config.MAX_RETRIES),
    ),
    instruction='You are a web search specialist. Search the web and return relevant, factual results.',
    tools=[GoogleSearchTool()],
    generate_content_config=types.GenerateContentConfig(
        temperature=config.TEMPERATURE,
        safety_settings=_SAFETY_SETTINGS,
    ),
)

# --- Tools ---
_TOOLS = [
    pre_sales_skill_toolset,
    load_artifacts,
    generate_architecture_diagram,
    validate_sow_content,
    generate_sow_document,
    initialize_extraction_buffer,
    append_extraction_items,
    finalize_extraction_manifest,
    load_extraction_manifest,
    validate_extraction_manifest,
    AgentTool(agent=google_search_agent),
]

# --- Root Agent ---
root_agent = Agent(
    name='pre_sales_assistant',
    description=(
        'Assists the Pre-Sales team with technical and commercial routines, '
        'including the elaboration of Statements of Work (SOW) and other pre-sales artifacts.'
    ),
    model=Gemini(
        model=config.GEMINI_MODEL,
        retry_options=types.HttpRetryOptions(attempts=config.MAX_RETRIES),
    ),
    instruction=build_instruction(company_name=config.COMPANY_NAME),
    tools=_TOOLS,
    before_model_callback=scope_guardrail,
    before_tool_callback=before_tool_callback,
    after_tool_callback=after_tool_callback,
    generate_content_config=types.GenerateContentConfig(
        temperature=config.TEMPERATURE,
        safety_settings=_SAFETY_SETTINGS,
        thinking_config=types.ThinkingConfig(
            include_thoughts=False,
            thinking_budget=config.THINKING_BUDGET,
        ),
    ),
)

app = App(
    root_agent=root_agent,
    name='app',
)

logger.info(
    'agent_initialized',
    model=config.GEMINI_MODEL,
    tools=len(_TOOLS),
    thinking_budget=config.THINKING_BUDGET,
    skills_dir=str(_SKILLS_DIR),
    safety_threshold=config.SAFETY_HARM_BLOCK_THRESHOLD,
    safety_guardrail_enabled=config.SAFETY_GUARDRAIL_ENABLED,
    safety_judge_model=config.SAFETY_JUDGE_MODEL,
)
