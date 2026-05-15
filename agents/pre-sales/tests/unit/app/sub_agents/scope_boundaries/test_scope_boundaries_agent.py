"""Unit tests for ``scope_boundaries_agent`` wiring."""

from __future__ import annotations

from pathlib import Path

import pytest
from google.adk.agents import Agent, SequentialAgent

from app.sub_agents._resources_toolset import SectionResourcesToolset
from app.sub_agents.scope_boundaries import (
    SCOPE_BOUNDARIES_OUTPUT_KEY,
    scope_boundaries_agent,
)
from app.sub_agents.schemas import ScopeBoundariesBundle


_SKILLS_DIR = (
    Path(__file__).resolve().parents[5]
    / 'app' / 'skills' / 'sow-scope-boundaries'
)
_DRAFT_KEY = f'{SCOPE_BOUNDARIES_OUTPUT_KEY}:draft'


def _worker() -> Agent:
    return scope_boundaries_agent.sub_agents[0]  # type: ignore[return-value]


def _formatter() -> Agent:
    return scope_boundaries_agent.sub_agents[1]  # type: ignore[return-value]


class TestPublicAgentShape:
    def test_is_sequential_agent(self):
        assert isinstance(scope_boundaries_agent, SequentialAgent)

    def test_name(self):
        assert scope_boundaries_agent.name == 'scope_boundaries_agent'

    def test_canonical_output_key(self):
        assert SCOPE_BOUNDARIES_OUTPUT_KEY == 'app:sow:scope_boundaries'

    def test_two_sub_agents_in_order(self):
        names = [a.name for a in scope_boundaries_agent.sub_agents]
        assert names == [
            'scope_boundaries_worker',
            'scope_boundaries_formatter',
        ]


class TestWorker:
    def test_has_no_output_schema(self):
        assert _worker().output_schema is None

    def test_output_key_is_draft(self):
        assert _worker().output_key == _DRAFT_KEY

    def test_isolated_from_root_history(self):
        assert _worker().include_contents == 'none'

    def test_only_section_resources_toolset(self):
        non_toolset = [
            t for t in _worker().tools
            if not isinstance(t, SectionResourcesToolset)
        ]
        assert non_toolset == [], (
            'scope_boundaries_worker must have no extra tools. '
            f'Got: {non_toolset}'
        )

    def test_instruction_includes_skill_md_and_protocol(self):
        skill_md = (_SKILLS_DIR / 'SKILL.md').read_text(encoding='utf-8')
        body = skill_md.split('---', 2)[-1].strip()
        assert body in _worker().instruction
        assert 'Output protocol' in _worker().instruction
        assert 'assumptions' in _worker().instruction
        assert 'out_of_scope' in _worker().instruction


class TestFormatter:
    def test_has_output_schema(self):
        assert _formatter().output_schema is ScopeBoundariesBundle

    def test_output_key_writes_canonical_bundle(self):
        assert _formatter().output_key == SCOPE_BOUNDARIES_OUTPUT_KEY

    def test_no_tools(self):
        assert _formatter().tools in (None, [])

    def test_isolated_from_root_history(self):
        assert _formatter().include_contents == 'none'

    def test_instruction_is_callable_provider(self):
        assert callable(_formatter().instruction)
