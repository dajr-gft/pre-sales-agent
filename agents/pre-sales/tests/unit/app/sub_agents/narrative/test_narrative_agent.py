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

    def test_worker_instruction_is_callable_provider(self):
        assert callable(_worker().instruction)

    def test_instruction_provider_includes_skill_md_and_protocol(self):
        skill_md = (_SKILLS_DIR / 'SKILL.md').read_text(encoding='utf-8')
        body = skill_md.split('---', 2)[-1].strip()

        class _Ctx:
            state: dict = {}

        instr = _worker().instruction(_Ctx())
        assert body in instr
        assert 'Output protocol' in instr
        assert 'executive_summary' in instr

    def test_instruction_provider_requires_full_upstream_packet(self):
        """Step E is the final section — it needs every prior bundle.
        Missing the architecture bundle (the most likely failure when
        someone calls narrative out of order) must trigger the
        MISSING_INPUT abort."""

        class _Ctx:
            state = {
                'extraction_manifest': {'project_name': 'Test'},
                'app:sow:requirements': {
                    'functional_requirements': [{'number': 'FR-01'}],
                    'non_functional_requirements': [],
                },
                'app:sow:delivery_plan': {'activity_phases': []},
                'app:sow:scope_boundaries': {
                    'assumptions': [],
                    'out_of_scope': [],
                },
                # architecture deliberately missing
            }

        instr = _worker().instruction(_Ctx())
        assert 'MISSING' in instr
        assert 'prior_architecture' in instr

    def test_instruction_provider_injects_all_five_when_present(self):
        class _Ctx:
            state = {
                'extraction_manifest': {'project_name': 'Test'},
                'app:sow:requirements': {
                    'functional_requirements': [{'number': 'FR-01'}],
                    'non_functional_requirements': [],
                },
                'app:sow:delivery_plan': {'activity_phases': [{'name': 'P1'}]},
                'app:sow:scope_boundaries': {
                    'assumptions': ['x'],
                    'out_of_scope': [],
                },
                'app:sow:architecture': {
                    'architecture_description': 'd',
                    'architecture_components': [],
                    'architecture_integrations': [],
                    'technology_stack': [],
                },
            }

        instr = _worker().instruction(_Ctx())
        for tag in (
            '<extraction_manifest>',
            '<prior_requirements>',
            '<prior_delivery_plan>',
            '<prior_scope_boundaries>',
            '<prior_architecture>',
        ):
            assert tag in instr
        assert 'MISSING' not in instr


class TestFormatter:
    def test_has_output_schema(self):
        assert _formatter().output_schema is NarrativeBundle

    def test_output_key_writes_canonical_bundle(self):
        assert _formatter().output_key == NARRATIVE_OUTPUT_KEY

    def test_no_tools(self):
        assert _formatter().tools in (None, [])

    def test_isolated_from_root_history(self):
        assert _formatter().include_contents == 'none'


class TestRootDoesNotExposeWebSearch:
    """F-10 — web search is a section-level concern owned by
    ``narrative_agent``. Exposing the same agent at the root would let
    the root LLM run searches outside the narrative flow, leaking
    unverified context into other section sub-agents on later turns.
    """

    def test_google_search_agent_not_a_root_tool(self):
        from google.adk.tools.agent_tool import AgentTool

        from app.agent import root_agent

        for tool in root_agent.tools:
            if isinstance(tool, AgentTool):
                assert tool.agent is not google_search_agent, (
                    'google_search_agent must NOT be registered at the '
                    'root anymore — narrative_agent owns web search. '
                    'See F-10 in the pre-merge audit.'
                )

    def test_narrative_still_owns_the_search_agent(self):
        """Negative-only assertions can rot silently; pair the removal
        with positive proof that narrative continues to embed the agent
        so we know the capability was moved, not lost."""
        from google.adk.tools.agent_tool import AgentTool

        worker_tools = _worker().tools
        agent_tools = [t for t in worker_tools if isinstance(t, AgentTool)]
        assert any(t.agent is google_search_agent for t in agent_tools)
