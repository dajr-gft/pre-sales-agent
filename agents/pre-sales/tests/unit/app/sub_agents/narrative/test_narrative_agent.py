"""Unit tests for ``narrative_agent`` wiring.

Pins that the worker carries an ``AgentTool`` wrapping
``google_search_agent`` — the four web-search queries the legacy skill
mandates cannot run without it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from google.adk.agents import Agent, SequentialAgent
from google.adk.tools.agent_tool import AgentTool

from app.sub_agents._resources_toolset import SectionResourcesToolset
from app.sub_agents.narrative import NARRATIVE_OUTPUT_KEY, narrative_agent
from app.sub_agents.schemas import NarrativeBundle
from app.sub_agents.web_search import google_search_agent


_SKILLS_DIR = (
    Path(__file__).resolve().parents[5] / 'app' / 'skills' / 'sow-narrative'
)
_DRAFT_KEY = f'{NARRATIVE_OUTPUT_KEY}:draft'


def _worker() -> Agent:
    return narrative_agent.sub_agents[0]  # type: ignore[return-value]


def _formatter() -> Agent:
    return narrative_agent.sub_agents[1]  # type: ignore[return-value]


class TestPublicAgentShape:
    def test_is_sequential_agent(self):
        assert isinstance(narrative_agent, SequentialAgent)

    def test_name(self):
        assert narrative_agent.name == 'narrative_agent'

    def test_canonical_output_key(self):
        assert NARRATIVE_OUTPUT_KEY == 'app:sow:narrative'

    def test_two_sub_agents_in_order(self):
        names = [a.name for a in narrative_agent.sub_agents]
        assert names == ['narrative_worker', 'narrative_formatter']


class TestWorker:
    def test_has_no_output_schema(self):
        assert _worker().output_schema is None

    def test_output_key_is_draft(self):
        assert _worker().output_key == _DRAFT_KEY

    def test_isolated_from_root_history(self):
        assert _worker().include_contents == 'none'

    def test_has_web_search_agent_tool(self):
        """The four web-search queries the sow-narrative skill mandates
        cannot run without an AgentTool wrapping google_search_agent."""
        agent_tools = [t for t in _worker().tools if isinstance(t, AgentTool)]
        assert len(agent_tools) == 1
        assert agent_tools[0].agent is google_search_agent

    def test_section_resources_toolset_present(self):
        toolsets = [
            t for t in _worker().tools
            if isinstance(t, SectionResourcesToolset)
        ]
        assert len(toolsets) == 1

    def test_no_unexpected_extras(self):
        non_section = [
            t for t in _worker().tools
            if not isinstance(t, SectionResourcesToolset)
            and not isinstance(t, AgentTool)
        ]
        assert non_section == [], (
            f'narrative_worker carries unexpected extras: {non_section}'
        )

    def test_instruction_includes_skill_md_and_protocol(self):
        skill_md = (_SKILLS_DIR / 'SKILL.md').read_text(encoding='utf-8')
        body = skill_md.split('---', 2)[-1].strip()
        assert body in _worker().instruction
        assert 'Output protocol' in _worker().instruction
        assert 'executive_summary' in _worker().instruction


class TestFormatter:
    def test_has_output_schema(self):
        assert _formatter().output_schema is NarrativeBundle

    def test_output_key_writes_canonical_bundle(self):
        assert _formatter().output_key == NARRATIVE_OUTPUT_KEY

    def test_no_tools(self):
        assert _formatter().tools in (None, [])

    def test_isolated_from_root_history(self):
        assert _formatter().include_contents == 'none'
