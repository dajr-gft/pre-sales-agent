"""Unit tests for ``discovery_agent`` wiring.

Discovery is the one sub-agent that does NOT follow the section
worker+formatter pattern. The contract this file pins:

- ``discovery_agent`` is a single ``Agent`` (no ``SequentialAgent`` wrap).
- It owns the four manifest construction/validation tools that used to
  live on the root, plus ``load_artifacts`` for Path B.
- It does NOT own ``load_extraction_manifest`` — that stays on the
  root for the consumer side of the handoff.
- It runs with ``include_contents='default'`` because the discovery
  flow is multi-turn conversational; isolating context would break
  Path A's guided interview.
- It does NOT have an ``output_schema`` — the manifest is persisted
  by ``finalize_extraction_manifest`` writing
  ``state['extraction_manifest']`` directly.
- It is wired into the root via ``sub_agents=`` (transfer-of-control),
  NOT as an AgentTool — multi-turn delegation requires the parent to
  hand the conversation over.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from google.adk.agents import Agent

from app.sub_agents._resources_toolset import SectionResourcesToolset
from app.sub_agents.discovery import discovery_agent
from app.tools.sow.manifest_tools import (
    append_extraction_items,
    finalize_extraction_manifest,
    initialize_extraction_buffer,
    load_extraction_manifest,
    validate_extraction_manifest,
)

_SKILLS_DIR = (
    Path(__file__).resolve().parents[5] / 'app' / 'skills' / 'sow-discovery'
)


class TestPublicAgentShape:
    def test_is_a_plain_llm_agent(self):
        """Discovery is NOT a SequentialAgent — only the section sub-agents
        use the worker+formatter split. Discovery produces side-effects
        (manifest in state), not a typed bundle."""
        assert isinstance(discovery_agent, Agent)

    def test_name(self):
        """The root prompt instructs the model to
        `transfer_to_agent(agent_name="discovery_agent")` — renaming this
        breaks the transfer-of-control wiring."""
        assert discovery_agent.name == 'discovery_agent'

    def test_description_mentions_manifest_and_handoff(self):
        desc = discovery_agent.description.lower()
        assert 'manifest' in desc
        assert 'discovery' in desc

    def test_no_output_schema(self):
        """A schema would silently disable the manifest tools — the whole
        flow depends on iterative ``append_extraction_items`` calls."""
        assert discovery_agent.output_schema is None

    def test_no_output_key(self):
        """``finalize_extraction_manifest`` writes the manifest itself.
        An ``output_key`` here would overwrite that with the model's
        chatty reply text."""
        assert discovery_agent.output_key is None


class TestContextIsolation:
    def test_include_contents_is_default_not_none(self):
        """Discovery NEEDS the conversation history — Path A is a multi-turn
        guided interview, Path B emits anchor messages between artifacts.
        Setting ``include_contents='none'`` here would re-trigger the
        ``silent collapse`` failure mode the SKILL.md exists to prevent."""
        assert discovery_agent.include_contents == 'default'

    def test_can_transfer_back_to_parent(self):
        """Discovery must be able to return control to the root after the
        user confirms the manifest handoff. The default is
        ``disallow_transfer_to_parent=False`` and we keep it."""
        assert discovery_agent.disallow_transfer_to_parent is False

    def test_cannot_transfer_to_peers(self):
        """Discovery hands control back to the root, never sideways to a
        section sub-agent. Section delegation is the root's job."""
        assert discovery_agent.disallow_transfer_to_peers is True


class TestToolset:
    def test_has_four_manifest_construction_tools(self):
        """All four manifest tools moved from the root to discovery in PR 5."""
        tool_set = set(discovery_agent.tools)
        for required in (
            initialize_extraction_buffer,
            append_extraction_items,
            finalize_extraction_manifest,
            validate_extraction_manifest,
        ):
            assert required in tool_set, (
                f'discovery_agent is missing the manifest tool {required.__name__}.'
            )

    def test_does_not_own_load_extraction_manifest(self):
        """The consumer-side ``load_extraction_manifest`` stays on the root
        per plan v2.1 §8 — exposing it inside discovery would let the
        agent read back its own work in a loop."""
        assert load_extraction_manifest not in discovery_agent.tools

    def test_load_artifacts_present_for_path_b(self):
        """Path B (artifact extraction) requires ``load_artifacts`` so the
        agent can read user-uploaded PDFs, transcripts, screenshots, etc."""
        from google.adk.tools import load_artifacts

        assert load_artifacts in discovery_agent.tools

    def test_section_resources_toolset_present(self):
        """The skill references (extraction-rules.md, coverage-protocol.md,
        guided-discovery-blocks.md) are loaded via load_skill_resource."""
        toolsets = [
            t for t in discovery_agent.tools
            if isinstance(t, SectionResourcesToolset)
        ]
        assert len(toolsets) == 1


class TestInstruction:
    def test_instruction_is_sow_discovery_skill_md(self):
        skill_md = (_SKILLS_DIR / 'SKILL.md').read_text(encoding='utf-8')
        body = skill_md.split('---', 2)[-1].strip()
        assert body, 'sow-discovery SKILL.md is empty after frontmatter'
        assert body in discovery_agent.instruction


class TestRootWiring:
    """Discovery is wired via ``sub_agents=``, NOT via ``AgentTool``.

    These tests fail loudly if someone accidentally swaps the binding
    pattern — that would break multi-turn delegation.
    """

    def test_discovery_is_a_sub_agent_of_root(self):
        from app.agent import root_agent

        sub_agent_names = [a.name for a in root_agent.sub_agents]
        assert 'discovery_agent' in sub_agent_names

    def test_discovery_is_not_registered_as_agent_tool_on_root(self):
        from google.adk.tools.agent_tool import AgentTool

        from app.agent import root_agent

        agent_tools = [t for t in root_agent.tools if isinstance(t, AgentTool)]
        for at in agent_tools:
            assert at.agent is not discovery_agent, (
                'discovery_agent must NOT be wrapped as AgentTool on root '
                '— it requires transfer-of-control via sub_agents=.'
            )

    def test_manifest_construction_tools_removed_from_root(self):
        """The four manifest tools moved to discovery — leaving them on
        the root would let the root build a manifest behind discovery's
        back, defeating the migration."""
        from app.agent import root_agent

        callable_tools = [t for t in root_agent.tools if callable(t)]
        for moved in (
            initialize_extraction_buffer,
            append_extraction_items,
            finalize_extraction_manifest,
            validate_extraction_manifest,
        ):
            assert moved not in callable_tools, (
                f'{moved.__name__} must NOT be on the root anymore.'
            )

    def test_load_extraction_manifest_kept_on_root(self):
        """The consumer-side reader stays on the root."""
        from app.agent import root_agent

        assert load_extraction_manifest in root_agent.tools
