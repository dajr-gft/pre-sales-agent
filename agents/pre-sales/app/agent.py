import logging
import os
from datetime import date
from pathlib import Path

import google.auth
from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models import Gemini
from google.adk.skills import load_skill_from_dir
from google.adk.tools import load_artifacts, skill_toolset
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.google_search_tool import GoogleSearchTool
from google.genai import types

from .prompts import ROOT_PROMPT
from .tools.sow.generate_architecture_diagram import (
    generate_architecture_diagram,
)
from .tools.sow.generate_sow_document import generate_sow_document

logger = logging.getLogger(__name__)

_, project_id = google.auth.default()
os.environ['GOOGLE_CLOUD_PROJECT'] = project_id
os.environ['GOOGLE_CLOUD_LOCATION'] = 'global'
os.environ['GOOGLE_GENAI_USE_VERTEXAI'] = 'True'

GEMINI_MODEL = os.environ.get('GEMINI_MODEL', 'gemini-3.1-pro-preview')
COMPANY_NAME = os.environ.get('COMPANY_NAME', 'GFT Technologies')

_THINKING_BUDGET = 1024
_SKILLS_DIR = Path(__file__).parent / 'skills'


def _build_instruction() -> str:
    """
    Assembles the root agent's instruction prompt by injecting all
    runtime variables. Called once at module load time.
    """

    class _PreservingDict(dict):
        def __missing__(self, key: str) -> str:
            return '{' + key + '}'

    variables = {
        'todays_date': date.today().strftime('%d/%m/%Y'),
        'company_name': COMPANY_NAME,
    }
    return ROOT_PROMPT.format_map(_PreservingDict(variables))


pre_sales_skill_toolset = skill_toolset.SkillToolset(
    skills=[
        load_skill_from_dir(_SKILLS_DIR / 'sow-generator'),
    ]
)

google_search_agent = Agent(
    name='google_search_agent',
    description='Searches the web for current and relevant information.',
    model=Gemini(
        model=GEMINI_MODEL,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction='You are a web search specialist. Search the web and return relevant, factual results.',
    tools=[GoogleSearchTool()],
    generate_content_config=types.GenerateContentConfig(
        temperature=0.2,
    ),
)

_TOOLS = [
    pre_sales_skill_toolset,
    load_artifacts,
    generate_architecture_diagram,
    generate_sow_document,
    AgentTool(agent=google_search_agent),
]

root_agent = Agent(
    name='pre_sales_assistant',
    description=(
        'Assists the Pre-Sales team with technical and commercial routines, '
        'including the elaboration of Statements of Work (SOW) and other pre-sales artifacts.'
    ),
    model=Gemini(
        model=GEMINI_MODEL,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=_build_instruction(),
    tools=_TOOLS,
    generate_content_config=types.GenerateContentConfig(
        temperature=0.2,
        thinking_config=types.ThinkingConfig(
            include_thoughts=False,
            thinking_budget=_THINKING_BUDGET,
        ),
    ),
)

app = App(
    root_agent=root_agent,
    name='app',
)

logger.info(
    'Pre-Sales Assistant agent initialized | model=%s | tools=%d | thinking_budget=%d | skills_dir=%s',
    GEMINI_MODEL,
    len(_TOOLS),
    _THINKING_BUDGET,
    _SKILLS_DIR,
)
