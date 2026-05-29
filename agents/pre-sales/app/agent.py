import os
from pathlib import Path

import google.auth
import structlog
from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models import Gemini
from google.adk.skills import load_skill_from_dir
from google.adk.tools import load_artifacts
from google.adk.tools.agent_tool import AgentTool
from google.genai import types

from . import _genai_patches
from .callbacks import (
    after_tool_callback,
    before_tool_callback,
    empty_response_guard,
)
from .config import config
from .guardrails import scope_guardrail
from .prompts import build_instruction_provider
from .shared.auto_scoped_skill_toolset import AutoScopedSkillToolset
from .shared.logging_config import setup_logging
from .sub_agents import (
    google_search_agent,
    sow_quality_loop,
)
from .tools.recovery import _request_continuation
from .tools.sow.confirm_phase import confirm_phase_completion
from .tools.sow.generate_architecture_diagram import \
    generate_architecture_diagram
from .tools.sow.generate_sow_document import generate_sow_document
from .tools.sow.save_section_bundle import SAVE_BUNDLE_TOOLS
from .tools.sow.save_sow_intake_summary import save_sow_intake_summary
from .tools.sow.save_sow_metadata import save_sow_metadata
from .tools.sow.stage_sow import stage_sow

# --- Bootstrap ---
setup_logging(
    level=config.LOG_LEVEL,
    json_output=config.LOG_JSON,
    log_file=config.LOG_FILE,
)
logger = structlog.get_logger()

_genai_patches.apply()

_, project_id = google.auth.default()
os.environ['GOOGLE_CLOUD_PROJECT'] = project_id
os.environ['GOOGLE_CLOUD_LOCATION'] = 'global'
os.environ['GOOGLE_GENAI_USE_VERTEXAI'] = 'True'

# --- Skills ---
# Root-skills architecture: the root loads section skills sequentially
# and generates each section inline, persisting bundles via the
# save_<section>_bundle tools. An AutoScopedSkillToolset prunes a
# skill's SKILL.md + resources from the
# LLM context once the root moves on to the next skill, so the root
# never carries all five skills at once. The section first-gen agents
# are gone — only the section *repair* agents survive, wired into the
# QualityLoopAgent (not the root).
#
# Allowlist: the five SOW section skills plus sow-shared (consultative
# reference pack), sow-guided-intake (Path A interview), and
# sow-document-readiness (Path B readiness/gap check, run right after
# load_artifacts). The old monolithic discovery skill no longer exists
# in this variant — sow-document-readiness is a small, conversational
# gap-check that writes no state and recreates none of that machinery.
# sow-narrative declares google_search_agent via `adk_additional_tools`;
# the toolset surfaces that tool only while sow-narrative is the current
# skill (see AutoScopedSkillToolset).
_SKILLS_DIR = Path(__file__).parent / 'skills'
_ROOT_SKILL_NAMES = (
    'sow-guided-intake',
    'sow-document-readiness',
    'sow-requirements',
    'sow-delivery-plan',
    'sow-scope-boundaries',
    'sow-architecture',
    'sow-narrative',
    'sow-shared',
)
_skill_toolset = AutoScopedSkillToolset(
    skills=[load_skill_from_dir(_SKILLS_DIR / name) for name in _ROOT_SKILL_NAMES],
    additional_tools=[AgentTool(agent=google_search_agent)],
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

# --- Tools ---
# google_search_agent lives in app.sub_agents.web_search; it is wrapped
# as an AgentTool and registered as an additional tool of the
# AutoScopedSkillToolset (above), surfaced only while the sow-narrative
# skill is the current skill.

_TOOLS = [
    load_artifacts,
    generate_architecture_diagram,
    confirm_phase_completion,
    stage_sow,
    generate_sow_document,
    # Root-skills generation tools. `save_sow_metadata` persists the 13
    # administrative fields (the metadata envelope the assembler reads);
    # the save_<section>_bundle tools validate+persist each section the
    # root generates inline after loading the matching skill.
    save_sow_metadata,
    # Path A: the guided-intake skill persists its structured summary
    # through this tool. The root then extracts administrative fields
    # from state['app:sow:intake_summary'] for save_sow_metadata and the
    # section skills read the same key as upstream context.
    save_sow_intake_summary,
    *SAVE_BUNDLE_TOOLS,
    # AutoScopedSkillToolset exposes load_skill / load_skill_resource /
    # list_skills / run_skill_script and prunes inactive-skill content
    # from the LLM context on each skill switch.
    _skill_toolset,
    _request_continuation,
    # `sow_quality_loop` is the SINGLE validation entry-point exposed
    # to the root. It wraps `validation_critic` and the section repair
    # agents internally, so the critic → (repair if blocked) → re-critic
    # dance runs inside one AgentTool call. The critic is intentionally
    # NOT registered here: exposing it would contradict the root prompt
    # ("do not call validation_critic directly") and let the root bypass
    # the loop's stop conditions.
    #
    # NOTE: do NOT set skip_summarization=True on this AgentTool.
    # AgentTool propagates that flag onto the function_response event,
    # which then satisfies `Event.is_final_response()` (see event.py:
    # `if self.actions.skip_summarization or self.long_running_tool_ids:
    # return True`). The root LLM flow's outer while-loop checks
    # is_final_response on the last event and breaks (base_llm_flow.py:
    # `if last_event.is_final_response(): break`), ending the root's
    # turn BEFORE it can produce the user-facing summary. Leaving the
    # default (False) makes the root take another LLM turn on the tool
    # result and reply normally.
    AgentTool(agent=sow_quality_loop),
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
    instruction=build_instruction_provider(company_name=config.COMPANY_NAME),
    tools=_TOOLS,
    before_model_callback=scope_guardrail,
    after_model_callback=empty_response_guard,
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
    sub_agents=[a.name for a in root_agent.sub_agents],
    thinking_budget=config.THINKING_BUDGET,
    safety_threshold=config.SAFETY_HARM_BLOCK_THRESHOLD,
    safety_guardrail_enabled=config.SAFETY_GUARDRAIL_ENABLED,
    safety_judge_model=config.SAFETY_JUDGE_MODEL,
)
