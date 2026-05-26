"""Unit tests for ``requirements_repair_agent`` — Phase 3 rollout.

Compact mirror of the delivery_plan repair tests (which already pin
the framework behaviour). Here we only assert the section-specific
contract:

- The agent is a single Agent with no output_schema.
- Its tool surface includes ``apply_requirements_patch`` and excludes
  every other section's patch tool.
- The instruction provider renders ``<repair_findings>`` +
  ``<previous_bundle>`` and appends the tool-mode footer mentioning
  this section's patch tool.
- ``sow_quality_loop['requirements']`` is the repair agent.
"""

from __future__ import annotations

from typing import Any

from google.adk.agents import Agent as _AdkAgent, SequentialAgent

from app.sub_agents._section_agent import STATE_REPAIR_FINDINGS
from app.sub_agents._resources_toolset import SectionResourcesToolset
from app.sub_agents.requirements import requirements_repair_agent
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
    }
    if findings is not None:
        state[STATE_REPAIR_FINDINGS] = findings
    if bundle is not None:
        state[SOW_BUNDLE_STATE_KEYS['requirements']] = bundle
    return state


def _empty_bundle() -> dict[str, Any]:
    return {
        'functional_requirements': [],
        'non_functional_requirements': [],
    }


class TestShape:
    def test_is_single_agent(self):
        assert isinstance(requirements_repair_agent, _AdkAgent)
        assert not isinstance(requirements_repair_agent, SequentialAgent)

    def test_no_output_schema(self):
        assert requirements_repair_agent.output_schema is None

    def test_no_output_key(self):
        assert getattr(requirements_repair_agent, 'output_key', None) is None

    def test_isolated_from_root_history(self):
        assert requirements_repair_agent.include_contents == 'none'

    def test_name(self):
        assert requirements_repair_agent.name == 'requirements_repair_agent'


class TestTools:
    def test_resources_toolset_present(self):
        assert any(
            isinstance(t, SectionResourcesToolset)
            for t in requirements_repair_agent.tools
        )

    def test_only_its_own_patch_tool(self):
        names = {
            getattr(t, '__name__', '')
            for t in requirements_repair_agent.tools
            if callable(t)
        }
        assert 'apply_requirements_patch' in names
        for foreign in (
            'apply_delivery_plan_patch',
            'apply_scope_boundaries_patch',
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
        prompt = requirements_repair_agent.instruction(ctx)
        assert '<repair_findings>' in prompt
        assert '<previous_bundle>' in prompt
        assert 'apply_requirements_patch(ops=[' in prompt
        assert 'Output protocol (binding)' not in prompt

    def test_missing_repair_findings_emits_stop(self):
        ctx = _Ctx(_seeded_state(findings=None, bundle=_empty_bundle()))
        prompt = requirements_repair_agent.instruction(ctx)
        assert 'Stop — no repair findings' in prompt
        assert 'apply_requirements_patch(ops=[' not in prompt

    def test_missing_manifest_triggers_stop_inputs(self):
        state = _seeded_state(findings=[{'id': 'F-1'}], bundle=_empty_bundle())
        del state[SOW_BUNDLE_STATE_KEYS['manifest']]
        ctx = _Ctx(state)
        prompt = requirements_repair_agent.instruction(ctx)
        assert 'STOP' in prompt
        assert 'extraction_manifest' in prompt


class TestQualityLoopWiring:
    def test_wiring(self):
        from app.sub_agents.quality_loop.agent import sow_quality_loop

        assert (
            sow_quality_loop.repair_section_agents['requirements']
            is requirements_repair_agent
        )
