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
from .prompts import build_instruction
from .shared.auto_scoped_skill_toolset import AutoScopedSkillToolset
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
from .tools.sow.save_section_bundle import SAVE_BUNDLE_TOOLS
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
# Root-skills variant (feat/sow-kill-manifest-root-skills): the root
# loads section skills sequentially and generates each section inline,
# persisting bundles via the save_<section>_bundle tools. An
# AutoScopedSkillToolset prunes a skill's SKILL.md + resources from the
# LLM context once the root moves on to the next skill, so the root
# never carries all five skills at once. The section sub-agents below
# remain registered (as AgentTools) for rollback to the multi-agent
# path; the active root_prompt protocol does not call them.
#
# Allowlist: the five SOW section skills plus sow-shared (consultative
# reference pack). sow-discovery is intentionally excluded — discovery
# is removed in this variant. sow-narrative declares google_search_agent
# via `adk_additional_tools`; the toolset surfaces that tool only while
# sow-narrative is the current skill (see AutoScopedSkillToolset).
_SKILLS_DIR = Path(__file__).parent / 'skills'
_ROOT_SKILL_NAMES = (
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
    # Root-skills generation tools. `save_sow_metadata` persists the 13
    # administrative fields (the metadata envelope the assembler reads
    # before falling back to the manifest); the save_<section>_bundle
    # tools validate+persist each section the root generates inline after
    # loading the matching skill.
    save_sow_metadata,
    *SAVE_BUNDLE_TOOLS,
    # AutoScopedSkillToolset exposes load_skill / load_skill_resource /
    # list_skills / run_skill_script and prunes inactive-skill content
    # from the LLM context on each skill switch.
    _skill_toolset,
    # Manifest construction tools (initialize_extraction_buffer,
    # append_extraction_items, finalize_extraction_manifest,
    # validate_extraction_manifest) moved into discovery_agent. The root
    # keeps only the consumer-side `load_extraction_manifest` because the
    # orchestrator Phase 1 reads the manifest after discovery transfers
    # control back here.
    load_extraction_manifest,
    _request_continuation,
    # F-10: `google_search_agent` is NOT exposed at the root. Web search
    # is a section-level concern owned by `narrative_agent`, which wraps
    # the same singleton (defined in `app.sub_agents.web_search`) as an
    # internal AgentTool. Exposing it twice would invite the root LLM
    # to run searches outside the narrative flow, leaking unverified
    # context into other section sub-agents on subsequent turns.
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
