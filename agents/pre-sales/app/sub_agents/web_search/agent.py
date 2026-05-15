"""Google Search sub-agent — single instance shared by the root and narrative.

Defined in its own module so ``narrative_agent`` can wrap it as an
``AgentTool`` without importing ``app.agent`` (which would close the
import cycle, since ``app.agent`` imports the section sub-agents).
"""

from __future__ import annotations

from google.adk.agents import Agent
from google.adk.models import Gemini
from google.adk.tools.google_search_tool import GoogleSearchTool
from google.genai import types

from ...config import config
from ...shared.safety import build_safety_settings

google_search_agent = Agent(
    name='google_search_agent',
    description='Searches the web for current and relevant information.',
    model=Gemini(
        model=config.GEMINI_MODEL,
        retry_options=types.HttpRetryOptions(attempts=config.MAX_RETRIES),
    ),
    instruction=(
        'You are a web search specialist. Search the web and return '
        'relevant, factual results.'
    ),
    tools=[GoogleSearchTool()],
    generate_content_config=types.GenerateContentConfig(
        temperature=config.TEMPERATURE,
        safety_settings=build_safety_settings(),
    ),
)
