"""Unit tests for ``SectionResourcesToolset``.

Verifies the only meaningful behaviour difference vs the parent
``SkillToolset``: ``process_llm_request`` must NOT inject the default
skill-activation system prompt. Sub-agents already carry their SKILL.md
in ``instruction=``; duplicating that guidance via the toolset would
defeat the isolation the new architecture buys.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.sub_agents._resources_toolset import SectionResourcesToolset


def _fake_skill(name: str) -> SimpleNamespace:
    """Return a minimum stand-in for ``google.adk.skills.models.Skill``.

    ``SkillToolset.__init__`` only reads ``skill.name`` for duplicate
    detection; nothing else is needed for this test.
    """
    return SimpleNamespace(
        name=name,
        instructions='',
        frontmatter=SimpleNamespace(metadata={}),
        resources=SimpleNamespace(
            list_references=lambda: [],
            list_assets=lambda: [],
            list_scripts=lambda: [],
        ),
    )


class TestProcessLlmRequestIsNoop:
    """``process_llm_request`` must not mutate the outgoing request."""

    async def test_does_not_append_default_skill_instructions(self):
        toolset = SectionResourcesToolset(skills=[_fake_skill('sow-architecture')])
        llm_request = MagicMock(name='LlmRequest')

        result = await toolset.process_llm_request(
            tool_context=MagicMock(),
            llm_request=llm_request,
        )

        assert result is None
        llm_request.append_instructions.assert_not_called()

    async def test_resource_tools_exposed(self):
        """load_skill_resource (and run_skill_script) must remain callable —
        they are the whole point of the toolset."""
        toolset = SectionResourcesToolset(skills=[_fake_skill('sow-architecture')])
        tools = await toolset.get_tools()
        names = {t.name for t in tools}

        assert 'load_skill_resource' in names
        assert 'run_skill_script' in names

    async def test_activation_tools_filtered_out(self):
        """``load_skill`` and ``list_skills`` MUST NOT be exposed.

        A section sub-agent that can call ``load_skill`` on its own
        skill would reload SKILL.md as a tool response and reproduce the
        monolithic-context bug the decomposition was meant to fix.
        """
        toolset = SectionResourcesToolset(skills=[_fake_skill('sow-architecture')])
        tools = await toolset.get_tools()
        names = {t.name for t in tools}

        assert 'load_skill' not in names
        assert 'list_skills' not in names
