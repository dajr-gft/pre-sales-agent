"""Unit tests for ``requirements_agent`` wiring (worker + formatter split).

The agent is a :class:`SequentialAgent` because of the ADK
``output_schema + tools`` limitation: setting ``output_schema`` makes
an ``LlmAgent`` reply-only. The smoke test on the single-agent variant
confirmed Gemini silently dropped the resources toolset and produced a
bundle without ever consulting SKILL.md references. The fallback —
documented in plan v2.1 §6.3 — splits the work in two:

1. ``requirements_worker``: tools enabled, no ``output_schema``,
   produces a JSON draft saved to ``state[_REQUIREMENTS_DRAFT_KEY]``.
2. ``requirements_formatter``: ``output_schema=RequirementsBundle``,
   no tools, reads the draft from state and emits the validated bundle
   to ``state['app:sow:requirements']``.

These tests pin the structural contract — fast, deterministic, no LLM
calls. End-to-end behaviour stays the smoke-test script's job.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from google.adk.agents import Agent, SequentialAgent

from app.sub_agents._resources_toolset import SectionResourcesToolset
from app.sub_agents.requirements import REQUIREMENTS_OUTPUT_KEY, requirements_agent
from app.sub_agents.schemas import RequirementsBundle


_SKILLS_DIR = (
    Path(__file__).resolve().parents[5] / 'app' / 'skills' / 'sow-requirements'
)

# Derived the same way the factory does (``f"{output_key}:draft"``). Pinned
# here so the test fails loudly if the convention drifts.
_REQUIREMENTS_DRAFT_KEY = f'{REQUIREMENTS_OUTPUT_KEY}:draft'


def _worker() -> Agent:
    return requirements_agent.sub_agents[0]  # type: ignore[return-value]


def _formatter() -> Agent:
    return requirements_agent.sub_agents[1]  # type: ignore[return-value]


class TestPublicAgentShape:
    """The root sees a SequentialAgent and never has to know about the split."""

    def test_is_sequential_agent(self):
        assert isinstance(requirements_agent, SequentialAgent)

    def test_name_kept_for_root_compatibility(self):
        """Renaming this breaks AgentTool wiring in app/agent.py."""
        assert requirements_agent.name == 'requirements_agent'

    def test_description_mentions_FR_and_NFR(self):
        desc = requirements_agent.description.lower()
        assert 'functional requirement' in desc
        assert 'non-functional' in desc or 'nfr' in desc

    def test_two_sub_agents_in_order(self):
        names = [a.name for a in requirements_agent.sub_agents]
        assert names == ['requirements_worker', 'requirements_formatter']

    def test_canonical_output_key_constant(self):
        assert REQUIREMENTS_OUTPUT_KEY == 'app:sow:requirements'

    def test_draft_key_is_namespaced_separately(self):
        assert _REQUIREMENTS_DRAFT_KEY == 'app:sow:requirements:draft'
        # Worker MUST NOT collide with the canonical bundle key.
        assert _REQUIREMENTS_DRAFT_KEY != REQUIREMENTS_OUTPUT_KEY


class TestWorker:
    """The worker is the only sub-agent allowed to use tools."""

    def test_has_no_output_schema(self):
        """``output_schema`` here would re-trigger the tool-suppression bug."""
        assert _worker().output_schema is None

    def test_output_key_is_draft(self):
        assert _worker().output_key == _REQUIREMENTS_DRAFT_KEY

    def test_isolated_from_root_history(self):
        assert _worker().include_contents == 'none'

    def test_section_resources_toolset_present(self):
        toolsets = [
            t for t in _worker().tools
            if isinstance(t, SectionResourcesToolset)
        ]
        assert len(toolsets) == 1

    async def test_toolset_exposes_only_resource_tools(self):
        toolset = next(
            t for t in _worker().tools
            if isinstance(t, SectionResourcesToolset)
        )
        names = {t.name for t in await toolset.get_tools()}
        assert names == {'load_skill_resource', 'run_skill_script'}

    def test_no_extra_tools_beyond_resources_toolset(self):
        non_toolset = [
            t for t in _worker().tools
            if not isinstance(t, SectionResourcesToolset)
        ]
        assert non_toolset == [], (
            'requirements_worker must rely on the resources toolset alone — '
            f'extras would widen its reach. Got: {non_toolset}'
        )

    def test_instruction_includes_skill_md_body(self):
        """The worker's instruction must carry the real SKILL.md content."""
        skill_md = (_SKILLS_DIR / 'SKILL.md').read_text(encoding='utf-8')
        body = skill_md.split('---', 2)[-1].strip()
        assert body, 'sow-requirements SKILL.md is empty after frontmatter'
        assert body in _worker().instruction

    def test_instruction_appends_output_protocol(self):
        """The closing protocol is what makes the draft consumable downstream."""
        instr = _worker().instruction
        assert 'Output protocol' in instr
        assert 'functional_requirements' in instr
        assert 'non_functional_requirements' in instr


class TestFormatter:
    """The formatter is reply-only; pure schema enforcement."""

    def test_has_output_schema(self):
        assert _formatter().output_schema is RequirementsBundle

    def test_output_key_writes_canonical_bundle(self):
        assert _formatter().output_key == REQUIREMENTS_OUTPUT_KEY

    def test_no_tools(self):
        """If the formatter had tools, ``output_schema`` would silently drop
        them — and we'd be back to the single-agent failure mode."""
        assert _formatter().tools in (None, [])

    def test_isolated_from_root_history(self):
        assert _formatter().include_contents == 'none'

    def test_instruction_is_callable_provider(self):
        """The formatter reads the draft from state via an instruction
        provider; a static string couldn't because the worker hasn't
        produced the draft yet at construction time."""
        assert callable(_formatter().instruction)

    def test_instruction_provider_interpolates_draft(self):
        class _Ctx:
            state = {_REQUIREMENTS_DRAFT_KEY: '{"hello": "world"}'}

        provider = _formatter().instruction
        result = provider(_Ctx())
        assert '{"hello": "world"}' in result
        assert '<draft>' in result
