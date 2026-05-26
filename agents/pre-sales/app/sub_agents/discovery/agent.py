"""Discovery sub-agent — Phase 1 of the SOW pipeline.

Discovery is fundamentally different from the section sub-agents:

- **Multi-turn conversational.** Path A (guided interview) needs several
  user turns; Path B (artifact extraction) emits anchor messages between
  artifacts. A single isolated invocation cannot represent this flow.
- **Side-effect output.** The manifest is built incrementally through
  manifest tools and persisted via ``finalize_extraction_manifest``,
  which writes ``state['extraction_manifest']`` directly. No
  ``output_schema`` is involved — the section agents' worker+formatter
  split does not apply here.
- **Needs conversation history.** The agent must remember the user's
  previous answers and uploaded artifacts across turns, so
  ``include_contents='default'`` (NOT ``'none'``).

For these reasons the discovery agent is wired into the root via
``sub_agents=[discovery_agent]`` (transfer-of-control), not via
``AgentTool``. When the user wants a SOW the root transfers to
``discovery_agent``; the agent runs the discovery flow turn-by-turn;
when the user confirms after ``finalize_extraction_manifest``, the
agent transfers back to ``pre_sales_assistant`` (root) for SOW
orchestration. ADK auto-provisions the ``transfer_to_agent`` tool when
``sub_agents`` is set, so no explicit transfer tool is wired here.

The 4 manifest construction/validation tools that used to live on the
root move into this sub-agent. ``load_extraction_manifest`` stays on
the root because it is consumer-side: the orchestrator phase 1 reads
the manifest after discovery hands control back.
"""

from __future__ import annotations

from pathlib import Path

from google.adk.agents import Agent
from google.adk.models import Gemini
from google.adk.skills import load_skill_from_dir
from google.adk.tools import load_artifacts
from google.genai import types

from ...config import config
from ...shared.safety import build_safety_settings
from ...tools.sow.manifest_tools import (
    append_extraction_items,
    finalize_extraction_manifest,
    initialize_extraction_buffer,
    validate_extraction_manifest,
)
from .._resources_toolset import SectionResourcesToolset

_SKILLS_DIR = Path(__file__).parents[2] / 'skills'
_DISCOVERY_SKILL = load_skill_from_dir(_SKILLS_DIR / 'sow-discovery')
_SHARED_SKILL = load_skill_from_dir(_SKILLS_DIR / 'sow-shared')


discovery_agent = Agent(
    name='discovery_agent',
    description=(
        'Owns the SOW Discovery phase: captures project context from uploaded '
        'artifacts (Path B) or guided conversation (Path A) and emits a '
        'validated Extraction Manifest to `state["extraction_manifest"]` via '
        '`finalize_extraction_manifest`. After the user confirms the handoff, '
        'transfers control back to the root for SOW orchestration.'
    ),
    model=Gemini(
        model=config.GEMINI_MODEL,
        retry_options=types.HttpRetryOptions(attempts=config.MAX_RETRIES),
    ),
    instruction=_DISCOVERY_SKILL.instructions,
    # Multi-turn conversation REQUIRES the parent's history — discovery
    # has to remember the user's prior answers and the artifacts they
    # uploaded across turns. This is the one section where context
    # isolation is the wrong call.
    include_contents='default',
    tools=[
        # Path B — read user-uploaded artifacts.
        load_artifacts,
        # The four manifest-building tools (the consumer-side
        # `load_extraction_manifest` deliberately stays on the root).
        initialize_extraction_buffer,
        append_extraction_items,
        finalize_extraction_manifest,
        validate_extraction_manifest,
        # SKILL.md references (extraction-rules.md, coverage-protocol.md,
        # guided-discovery-blocks.md) plus sow-shared references.
        SectionResourcesToolset(
            skills=[_DISCOVERY_SKILL, _SHARED_SKILL],
        ),
    ],
    # Discovery talks only to its parent (the root). No reason to let it
    # hop sideways into a section sub-agent.
    disallow_transfer_to_peers=True,
    generate_content_config=types.GenerateContentConfig(
        temperature=config.TEMPERATURE,
        safety_settings=build_safety_settings(),
        thinking_config=types.ThinkingConfig(
            include_thoughts=False,
            thinking_budget=config.THINKING_BUDGET,
        ),
    ),
)
