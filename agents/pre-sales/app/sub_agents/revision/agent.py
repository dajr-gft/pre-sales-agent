"""SOW revision specialist — surgical patches driven by validation findings.

Wrapped in ``AgentTool`` by ``QualityLoopAgent`` so the root never holds
this agent's instruction in its own context. The agent reads
``state[app:sow:current]`` (the staged SOW) and
``state[app:validation_result]`` (the latest ``ValidationReport``),
loads the references mapped from each finding via ``load_sow_reference``,
applies minimum patches per the three anti-regeneration contracts in the
SKILL.md, and persists results via ``stage_sow`` and
``record_revision_log_entries``.

The agent intentionally has no ``output_schema``: its outputs are side
effects on session state (``app:sow:current`` updated by ``stage_sow``;
``app:sow:revision_log`` appended by ``record_revision_log_entries``).
That keeps it away from the known ``output_schema + tools`` loop hazard
described in the ADK docs.
"""

from __future__ import annotations

from pathlib import Path

from google.adk.agents import Agent
from google.adk.models import Gemini
from google.adk.skills import load_skill_from_dir
from google.genai import types

from ...config import config
from ...shared.safety import build_safety_settings
from ...tools.sow.stage_sow import stage_sow
from .log_tools import record_revision_log_entries
from .tools import load_sow_reference

_SKILL_DIR = Path(__file__).parents[2] / 'skills' / 'sow-revision'
_skill = load_skill_from_dir(_SKILL_DIR)


revision_agent = Agent(
    name='revision_agent',
    description=(
        'Applies minimum-change patches to the staged SOW based on a '
        'ValidationReport. Loads section references via load_sow_reference '
        '(allowlist-protected), patches affected fields only, then calls '
        'stage_sow and record_revision_log_entries. Never regenerates '
        'whole sections.'
    ),
    model=Gemini(
        model=config.GEMINI_MODEL,
        retry_options=types.HttpRetryOptions(attempts=config.MAX_RETRIES),
    ),
    instruction=_skill.instructions,
    # Real context isolation: the revision agent must operate solely on
    # its instruction (SKILL.md), the staged SOW, and the latest
    # ValidationReport — all reached via tool calls / state reads. Letting
    # it inherit the root's conversation history would re-introduce the
    # monolithic-context problem the decomposition was designed to fix:
    # revision rounds that have already seen the user discussing
    # "make the timeline shorter" would bias the patcher toward content
    # changes outside `finding.fields`, violating Contract 1.
    include_contents='none',
    tools=[
        load_sow_reference,
        stage_sow,
        record_revision_log_entries,
    ],
    generate_content_config=types.GenerateContentConfig(
        temperature=config.TEMPERATURE,
        safety_settings=build_safety_settings(),
        thinking_config=types.ThinkingConfig(
            include_thoughts=False,
            thinking_budget=config.THINKING_BUDGET,
        ),
    ),
)
