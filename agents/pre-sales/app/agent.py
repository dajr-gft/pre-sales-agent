import os

import google.auth
import structlog
from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models import Gemini
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
from .prompts import build_instruction
from .shared.logging_config import setup_logging
from .sub_agents import (
    architecture_agent,
    delivery_plan_agent,
    discovery_agent,
    google_search_agent,
    narrative_agent,
    requirements_agent,
    scope_boundaries_agent,
    sow_quality_loop,
)
from .tools.recovery import _request_continuation
from .tools.sow.confirm_phase import confirm_phase_completion
from .tools.sow.generate_architecture_diagram import \
    generate_architecture_diagram
from .tools.sow.generate_sow_document import generate_sow_document
from .tools.sow.manifest_tools import load_extraction_manifest
from .tools.sow.assemble_payload import assemble_sow_payload
from .tools.sow.stage_sow import stage_sow

# --- Bootstrap ---
setup_logging(level=config.LOG_LEVEL, json_output=config.LOG_JSON)
logger = structlog.get_logger()

_genai_patches.apply()

_, project_id = google.auth.default()
os.environ['GOOGLE_CLOUD_PROJECT'] = project_id
os.environ['GOOGLE_CLOUD_LOCATION'] = 'global'
os.environ['GOOGLE_GENAI_USE_VERTEXAI'] = 'True'

# --- Skills ---
# The global SkillToolset has been removed. Skills now live exclusively
# inside each section sub-agent (via SectionResourcesToolset) and inside
# discovery_agent. The root never holds a SKILL.md in its own context —
# that's the whole point of the decomposition. The skill folders on disk
# under app/skills/ are still consumed, but only by the sub-agents that
# wrap them.

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
# google_search_agent now lives in app.sub_agents.web_search so the
# narrative_agent can wrap it as an AgentTool without importing this
# module (which would close the import cycle).

_TOOLS = [
    load_artifacts,
    generate_architecture_diagram,
    confirm_phase_completion,
    stage_sow,
    assemble_sow_payload,
    generate_sow_document,
    # Manifest construction tools (initialize_extraction_buffer,
    # append_extraction_items, finalize_extraction_manifest,
    # validate_extraction_manifest) moved into discovery_agent. The root
    # keeps only the consumer-side `load_extraction_manifest` because the
    # orchestrator Phase 1 reads the manifest after discovery transfers
    # control back here.
    load_extraction_manifest,
    _request_continuation,
    AgentTool(agent=google_search_agent),
    # NOTE: do NOT set skip_summarization=True on any AgentTool here.
    # AgentTool propagates that flag onto the function_response event,
    # which then satisfies `Event.is_final_response()` (see event.py:
    # `if self.actions.skip_summarization or self.long_running_tool_ids:
    # return True`). The root LLM flow's outer while-loop checks
    # is_final_response on the last event and breaks (base_llm_flow.py:
    # `if last_event.is_final_response(): break`), ending the root's
    # turn BEFORE it can produce the user-facing summary. Leaving the
    # default (False) makes the root take another LLM turn on the tool
    # result and reply normally.
    #
    # `sow_quality_loop` is the SINGLE validation entry-point exposed
    # to the root. It wraps `validation_critic` and `revision_agent`
    # internally, so the critic → (revision if blocked) → re-critic
    # dance runs inside one AgentTool call. The critic is intentionally
    # NOT registered here: exposing it would contradict the root prompt
    # ("do not call validation_critic directly") and let the root bypass
    # the loop's stop conditions.
    AgentTool(agent=sow_quality_loop),
    # Section specialist sub-agents — one per Phase 2 Step. Each
    # produces one bundle of the final sow_data and writes it to its
    # canonical state key (`app:sow:<section>`). The root calls them
    # in the orchestrator's Phase 2 order (A → B → C → D → E) and then
    # assembles the staged payload via `assemble_sow_payload`. The
    # legacy `load_skill("sow-<section>")` path is deprecated; see the
    # `<section_sub_agents>` block in the root prompt for the active
    # contract.
    AgentTool(agent=requirements_agent),       # Step A
    AgentTool(agent=delivery_plan_agent),      # Step B
    AgentTool(agent=scope_boundaries_agent),   # Step C
    AgentTool(agent=architecture_agent),       # Step D — includes diagram tool
    AgentTool(agent=narrative_agent),          # Step E — includes web search
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
    # `sub_agents=` (NOT AgentTool) for discovery: the discovery flow is
    # multi-turn conversational, so control must transfer from the root
    # to discovery_agent until the user confirms the manifest, then back
    # to the root for orchestration. The five section sub-agents stay as
    # AgentTools above because each one runs as a single isolated
    # invocation per Phase Step.
    sub_agents=[discovery_agent],
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
