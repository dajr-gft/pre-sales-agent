"""Unit tests for ``architecture_agent`` wiring.

Specifically pins that the worker carries the ``generate_architecture_diagram``
tool — the three-way invariant description↔table↔diagram is impossible
to satisfy without it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from google.adk.agents import Agent, SequentialAgent

from app.sub_agents._resources_toolset import SectionResourcesToolset
from app.sub_agents.architecture import (
    ARCHITECTURE_OUTPUT_KEY,
    architecture_agent,
)
from app.sub_agents.schemas import ArchitectureBundle
from app.tools.sow.generate_architecture_diagram import \
    generate_architecture_diagram


_SKILLS_DIR = (
    Path(__file__).resolve().parents[5] / 'app' / 'skills' / 'sow-architecture'
)
_DRAFT_KEY = f'{ARCHITECTURE_OUTPUT_KEY}:draft'


def _worker() -> Agent:
    return architecture_agent.sub_agents[0]  # type: ignore[return-value]


def _formatter() -> Agent:
    return architecture_agent.sub_agents[1]  # type: ignore[return-value]


class TestPublicAgentShape:
    def test_is_sequential_agent(self):
        assert isinstance(architecture_agent, SequentialAgent)

    def test_name(self):
        assert architecture_agent.name == 'architecture_agent'

    def test_canonical_output_key(self):
        assert ARCHITECTURE_OUTPUT_KEY == 'app:sow:architecture'

    def test_two_sub_agents_in_order(self):
        names = [a.name for a in architecture_agent.sub_agents]
        assert names == ['architecture_worker', 'architecture_formatter']


class TestWorker:
    def test_has_no_output_schema(self):
        assert _worker().output_schema is None

    def test_output_key_is_draft(self):
        assert _worker().output_key == _DRAFT_KEY

    def test_isolated_from_root_history(self):
        assert _worker().include_contents == 'none'

    def test_has_diagram_tool(self):
        """The three-way invariant description↔table↔diagram requires that
        the same agent producing the architecture text also generates
        the diagram. Without this tool the invariant cannot be enforced."""
        assert generate_architecture_diagram in _worker().tools

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
            and t is not generate_architecture_diagram
        ]
        assert non_section == [], (
            f'architecture_worker carries unexpected extras: {non_section}'
        )

    def test_instruction_includes_skill_md_and_protocol(self):
        skill_md = (_SKILLS_DIR / 'SKILL.md').read_text(encoding='utf-8')
        body = skill_md.split('---', 2)[-1].strip()
        assert body in _worker().instruction
        assert 'Output protocol' in _worker().instruction
        assert 'architecture_description' in _worker().instruction
        assert 'technology_stack' in _worker().instruction


class TestFormatter:
    def test_has_output_schema(self):
        assert _formatter().output_schema is ArchitectureBundle

    def test_output_key_writes_canonical_bundle(self):
        assert _formatter().output_key == ARCHITECTURE_OUTPUT_KEY

    def test_no_tools(self):
        """Formatter must not have diagram tool either — pure schema enforcement.
        Letting the diagram tool leak here would re-trigger the
        output_schema + tools silent-drop bug."""
        assert _formatter().tools in (None, [])

    def test_isolated_from_root_history(self):
        assert _formatter().include_contents == 'none'
