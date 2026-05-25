"""Unit tests for ``narrative_repair_agent`` — Phase 3 rollout.

Compact mirror of the delivery_plan repair tests. Pins the
section-specific contract and the deliberate exclusion of the
``google_search_agent`` AgentTool from the repair toolset (web
search is a first-gen seed only; in repair mode bundle text is
already grounded and findings are edits, not research prompts).
"""

from __future__ import annotations

from typing import Any

from google.adk.agents import Agent as _AdkAgent, SequentialAgent

from app.sub_agents._section_agent import STATE_REPAIR_FINDINGS
from app.sub_agents._resources_toolset import SectionResourcesToolset
from app.sub_agents.narrative import narrative_repair_agent
from app.sub_agents.schemas import SOW_BUNDLE_STATE_KEYS


class _Ctx:
    def __init__(self, state: dict[str, Any] | None = None) -> None:
        self.state: dict[str, Any] = state or {}


def _seeded_state(
    *,
    findings: list[dict[str, Any]] | None = None,
    bundle: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state: dict[str, Any] = {
        SOW_BUNDLE_STATE_KEYS['manifest']: {
            'project': {
                'title': 'Test',
                'customer_name': 'Acme',
                'partner_name': 'GFT',
                'funding_type': 'Google DAF',
            },
        },
        SOW_BUNDLE_STATE_KEYS['requirements']: {
            'functional_requirements': [],
            'non_functional_requirements': [],
        },
        SOW_BUNDLE_STATE_KEYS['delivery_plan']: {
            'activity_phases': [],
            'deliverables': [],
            'timeline': [],
            'partner_roles': [],
            'customer_roles': [],
            'success_criteria': [],
        },
        SOW_BUNDLE_STATE_KEYS['scope_boundaries']: {
            'assumptions': [],
            'out_of_scope': [],
        },
        SOW_BUNDLE_STATE_KEYS['architecture']: {
            'architecture_description': '',
            'architecture_components': [],
            'architecture_integrations': [],
            'technology_stack': [],
        },
    }
    if findings is not None:
        state[STATE_REPAIR_FINDINGS] = findings
    if bundle is not None:
        state[SOW_BUNDLE_STATE_KEYS['narrative']] = bundle
    return state


def _empty_bundle() -> dict[str, Any]:
    return {
        'executive_summary': '',
        'partner_overview': '',
        'customer_overview': '',
    }


class TestShape:
    def test_is_single_agent(self):
        assert isinstance(narrative_repair_agent, _AdkAgent)
        assert not isinstance(narrative_repair_agent, SequentialAgent)

    def test_no_output_schema(self):
        assert narrative_repair_agent.output_schema is None

    def test_no_output_key(self):
        assert getattr(narrative_repair_agent, 'output_key', None) is None

    def test_name(self):
        assert narrative_repair_agent.name == 'narrative_repair_agent'


class TestTools:
    def test_resources_toolset_present(self):
        assert any(
            isinstance(t, SectionResourcesToolset)
            for t in narrative_repair_agent.tools
        )

    def test_only_its_own_patch_tool(self):
        names = {
            getattr(t, '__name__', '')
            for t in narrative_repair_agent.tools
            if callable(t)
        }
        assert 'apply_narrative_patch' in names
        for foreign in (
            'apply_requirements_patch',
            'apply_delivery_plan_patch',
            'apply_scope_boundaries_patch',
            'apply_architecture_patch',
        ):
            assert foreign not in names

    def test_no_google_search_tool_in_repair_toolset(self):
        """Web search is a first-gen seed only; in repair mode the
        bundle text is already grounded and findings are edits, not
        research prompts. Including the search agent here would burn
        quota inside the loop with no upside."""
        from google.adk.tools.agent_tool import AgentTool

        agent_tools = [
            t for t in narrative_repair_agent.tools
            if isinstance(t, AgentTool)
        ]
        # If any AgentTool ended up here at all, fail loudly — none
        # are expected for repair mode.
        assert not agent_tools, (
            f'Unexpected AgentTool(s) in narrative repair toolset: '
            f'{[t.agent.name for t in agent_tools]}'
        )


class TestInstructionProvider:
    def test_happy_path_renders_findings_and_footer(self):
        ctx = _Ctx(_seeded_state(
            findings=[{'id': 'F-1', 'recommendation': 'Tighten executive summary.'}],
            bundle=_empty_bundle(),
        ))
        prompt = narrative_repair_agent.instruction(ctx)
        assert '<repair_findings>' in prompt
        assert '<previous_bundle>' in prompt
        assert 'apply_narrative_patch(ops=[' in prompt
        # Full upstream packet wired.
        assert '<prior_architecture>' in prompt

    def test_missing_repair_findings_emits_stop(self):
        ctx = _Ctx(_seeded_state(findings=None, bundle=_empty_bundle()))
        prompt = narrative_repair_agent.instruction(ctx)
        assert 'Stop — no repair findings' in prompt


class TestQualityLoopWiring:
    def test_wiring(self):
        from app.sub_agents.quality_loop.agent import sow_quality_loop

        assert (
            sow_quality_loop.repair_section_agents['narrative']
            is narrative_repair_agent
        )
