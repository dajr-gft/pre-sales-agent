"""Unit tests for ``scope_boundaries_repair_agent`` — Phase 3 rollout.

Compact mirror of the delivery_plan repair tests. Pins the
section-specific contract; framework behaviour is covered exhaustively
in test_apply_section_patch.py + test_delivery_plan_repair_agent.py.
"""

from __future__ import annotations

from typing import Any

from google.adk.agents import Agent as _AdkAgent, SequentialAgent

from app.sub_agents._section_agent import STATE_REPAIR_FINDINGS
from app.sub_agents._resources_toolset import SectionResourcesToolset
from app.sub_agents.schemas import SOW_BUNDLE_STATE_KEYS
from app.sub_agents.scope_boundaries import scope_boundaries_repair_agent


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
    }
    if findings is not None:
        state[STATE_REPAIR_FINDINGS] = findings
    if bundle is not None:
        state[SOW_BUNDLE_STATE_KEYS['scope_boundaries']] = bundle
    return state


def _empty_bundle() -> dict[str, Any]:
    return {
        'assumptions': [],
        'out_of_scope': [],
        'risks': [],
        'handover_disclaimers': [],
        'change_request_policy_text': '',
    }


class TestShape:
    def test_is_single_agent(self):
        assert isinstance(scope_boundaries_repair_agent, _AdkAgent)
        assert not isinstance(scope_boundaries_repair_agent, SequentialAgent)

    def test_no_output_schema(self):
        assert scope_boundaries_repair_agent.output_schema is None

    def test_no_output_key(self):
        assert getattr(scope_boundaries_repair_agent, 'output_key', None) is None

    def test_name(self):
        assert scope_boundaries_repair_agent.name == 'scope_boundaries_repair_agent'


class TestTools:
    def test_resources_toolset_present(self):
        assert any(
            isinstance(t, SectionResourcesToolset)
            for t in scope_boundaries_repair_agent.tools
        )

    def test_only_its_own_patch_tool(self):
        names = {
            getattr(t, '__name__', '')
            for t in scope_boundaries_repair_agent.tools
            if callable(t)
        }
        assert 'apply_scope_boundaries_patch' in names
        for foreign in (
            'apply_requirements_patch',
            'apply_delivery_plan_patch',
            'apply_architecture_patch',
            'apply_narrative_patch',
        ):
            assert foreign not in names


class TestInstructionProvider:
    def test_happy_path_renders_findings_and_footer(self):
        ctx = _Ctx(_seeded_state(
            findings=[{'id': 'F-1', 'recommendation': 'Fix x.'}],
            bundle=_empty_bundle(),
        ))
        prompt = scope_boundaries_repair_agent.instruction(ctx)
        assert '<repair_findings>' in prompt
        assert '<previous_bundle>' in prompt
        assert 'apply_scope_boundaries_patch(ops=[' in prompt
        # Upstream packet wired.
        assert '<prior_requirements>' in prompt
        assert '<prior_delivery_plan>' in prompt

    def test_missing_repair_findings_emits_stop(self):
        ctx = _Ctx(_seeded_state(findings=None, bundle=_empty_bundle()))
        prompt = scope_boundaries_repair_agent.instruction(ctx)
        assert 'Stop — no repair findings' in prompt

    def test_missing_prior_delivery_plan_triggers_stop_inputs(self):
        state = _seeded_state(findings=[{'id': 'F-1'}], bundle=_empty_bundle())
        del state[SOW_BUNDLE_STATE_KEYS['delivery_plan']]
        ctx = _Ctx(state)
        prompt = scope_boundaries_repair_agent.instruction(ctx)
        assert 'STOP' in prompt
        assert 'prior_delivery_plan' in prompt


class TestQualityLoopWiring:
    def test_wiring(self):
        from app.sub_agents.quality_loop.agent import sow_quality_loop

        assert (
            sow_quality_loop.repair_section_agents['scope_boundaries']
            is scope_boundaries_repair_agent
        )
