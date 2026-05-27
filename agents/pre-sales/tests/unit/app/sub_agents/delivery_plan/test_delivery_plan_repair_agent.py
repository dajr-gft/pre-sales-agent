"""Unit tests for ``delivery_plan_repair_agent`` — Phase 2 vertical slice.

Pins the contract of the tool-based repair flow:

- The agent is a single :class:`Agent` (NOT a SequentialAgent — no
  formatter, no output_schema).
- Its tools include the section's ``SectionResourcesToolset`` and
  ``apply_delivery_plan_patch`` (the per-section patch tool). It must
  NOT carry tools for other sections.
- The instruction provider renders ``<repair_findings>`` +
  ``<previous_bundle>`` as XML blocks at runtime and appends the
  tool-mode footer (not the legacy bundle-emit footer).
- Missing required inputs trigger the STOP-and-emit-empty footer.
- Missing repair findings (the signal that authorises a patch) trigger
  a STOP directive — the agent must not call the patch tool when the
  loop did not actually populate the findings packet.
"""

from __future__ import annotations

from typing import Any

import pytest
from google.adk.agents import Agent as _AdkAgent, SequentialAgent

from app.sub_agents._section_agent import (
    STATE_REPAIR_FINDINGS,
)
from app.sub_agents._resources_toolset import SectionResourcesToolset
from app.sub_agents.delivery_plan import (
    DELIVERY_PLAN_OUTPUT_KEY,
    delivery_plan_repair_agent,
)
from app.sub_agents.schemas import SOW_BUNDLE_STATE_KEYS
from app.tools.sow.apply_section_patch import (
    apply_delivery_plan_patch,
    apply_requirements_patch,
)


class _Ctx:
    """Minimal stand-in for ReadonlyContext used by the instruction
    provider — only the ``state`` attribute is consulted."""

    def __init__(self, state: dict[str, Any] | None = None) -> None:
        self.state: dict[str, Any] = state or {}


def _seeded_state(
    *,
    findings: list[dict[str, Any]] | None = None,
    bundle: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a state dict with the required upstream packet populated.

    The repair agent declares ``prior_requirements`` as its required
    input. Without it the provider switches to the MISSING-and-stop
    footer.
    """
    state: dict[str, Any] = {
        SOW_BUNDLE_STATE_KEYS['requirements']: {
            'functional_requirements': [
                {'number': 'FR-01', 'description': 'Ingest data.'},
            ],
            'non_functional_requirements': [
                {'number': 'NFR-01', 'description': 'TLS 1.3.'},
            ],
        },
    }
    if findings is not None:
        state[STATE_REPAIR_FINDINGS] = findings
    if bundle is not None:
        state[SOW_BUNDLE_STATE_KEYS['delivery_plan']] = bundle
    return state


# ---------------------------------------------------------------------------
# Public shape
# ---------------------------------------------------------------------------


class TestRepairAgentShape:
    def test_is_single_agent_not_sequential(self):
        """No formatter — the patch tool IS the writer."""
        assert isinstance(delivery_plan_repair_agent, _AdkAgent)
        assert not isinstance(delivery_plan_repair_agent, SequentialAgent)

    def test_name(self):
        assert delivery_plan_repair_agent.name == 'delivery_plan_repair_agent'

    def test_no_output_schema(self):
        """ADK refuses ``output_schema`` + ``tools`` together — the
        tool-based repair flow keeps the tools and drops the schema."""
        assert delivery_plan_repair_agent.output_schema is None

    def test_no_output_key(self):
        """The patch tool writes ``state[bundle_key]`` directly; the
        agent itself must NOT set an output_key (that would let an
        accidental JSON reply overwrite the bundle)."""
        # ADK normalises missing output_key to ``None``.
        assert (
            getattr(delivery_plan_repair_agent, 'output_key', None) is None
        )

    def test_isolated_from_root_history(self):
        assert delivery_plan_repair_agent.include_contents == 'none'

    def test_disallows_transfer(self):
        assert delivery_plan_repair_agent.disallow_transfer_to_parent
        assert delivery_plan_repair_agent.disallow_transfer_to_peers


class TestRepairAgentTools:
    def test_includes_resources_toolset(self):
        non_toolset = [
            t for t in delivery_plan_repair_agent.tools
            if not isinstance(t, SectionResourcesToolset)
        ]
        # Resources toolset is always first; the rest must be the patch tool.
        toolsets = [
            t for t in delivery_plan_repair_agent.tools
            if isinstance(t, SectionResourcesToolset)
        ]
        assert len(toolsets) == 1, 'SectionResourcesToolset must be present.'
        # The patch tool MUST be there. It will be a function (post-decorator).
        callables = [t for t in non_toolset if callable(t)]
        assert callables, 'patch tool not registered.'

    def test_includes_only_its_own_patch_tool(self):
        """delivery_plan repair must NOT see other sections' patch tools.
        ADK exposes function ``__name__`` to Gemini; finding
        ``apply_requirements_patch`` here would let the LLM patch the
        wrong section."""
        names = {
            getattr(t, '__name__', '')
            for t in delivery_plan_repair_agent.tools
            if callable(t)
        }
        assert 'apply_delivery_plan_patch' in names
        assert 'apply_requirements_patch' not in names
        assert 'apply_scope_boundaries_patch' not in names
        assert 'apply_architecture_patch' not in names
        assert 'apply_narrative_patch' not in names

    def test_patch_tool_is_the_real_one(self):
        """Sanity: the wired tool is the actual factory output, not a
        stub passed accidentally."""
        callable_tools = [
            t for t in delivery_plan_repair_agent.tools if callable(t)
        ]
        # The factory mints fresh callables — compare by __name__ rather
        # than identity, since the test importing apply_delivery_plan_patch
        # may have a different reference under reload edge cases.
        assert any(
            getattr(t, '__name__', '') == 'apply_delivery_plan_patch'
            for t in callable_tools
        )
        # Sanity: the other-section instance has a distinct __name__.
        assert (
            apply_requirements_patch.__name__ != apply_delivery_plan_patch.__name__
        )


# ---------------------------------------------------------------------------
# Instruction provider — branches by what's present in state
# ---------------------------------------------------------------------------


class TestInstructionProviderHappyPath:
    """When required inputs + repair_findings + previous_bundle are
    all present, the provider must render the runtime packet and
    append the tool-mode footer."""

    def test_renders_repair_findings_block(self):
        ctx = _Ctx(_seeded_state(
            findings=[{
                'id': 'F-1',
                'skill': 'sow-delivery-plan',
                'category': 'activities_vs_deliverables',
                'severity': 'MAJOR',
                'recommendation': 'Update WS-03 to reference Phase 2.',
                'fields': ['deliverables'],
            }],
            bundle={
                'activity_phases': [],
                'deliverables': [],
                'timeline': [],
                'partner_roles': [],
                'customer_roles': [],
                'success_criteria': [],
            },
        ))
        prompt = delivery_plan_repair_agent.instruction(ctx)
        assert '<repair_findings>' in prompt
        assert 'activities_vs_deliverables' in prompt
        assert '<previous_bundle>' in prompt

    def test_appends_tool_mode_footer_not_bundle_emit_footer(self):
        ctx = _Ctx(_seeded_state(
            findings=[{'id': 'F-1', 'recommendation': 'Fix x.'}],
            bundle={
                'activity_phases': [],
                'deliverables': [],
                'timeline': [],
                'partner_roles': [],
                'customer_roles': [],
                'success_criteria': [],
            },
        ))
        prompt = delivery_plan_repair_agent.instruction(ctx)
        assert '# Repair mode (tool-based — binding)' in prompt
        assert 'apply_delivery_plan_patch(ops=[' in prompt
        # The legacy bundle-emit footer must NOT show up here — its
        # signature instruction is the bundle JSON output protocol.
        assert 'Output protocol (binding)' not in prompt

    def test_footer_mentions_tool_op_vocabulary(self):
        ctx = _Ctx(_seeded_state(
            findings=[{'id': 'F-1'}],
            bundle={
                'activity_phases': [],
                'deliverables': [],
                'timeline': [],
                'partner_roles': [],
                'customer_roles': [],
                'success_criteria': [],
            },
        ))
        prompt = delivery_plan_repair_agent.instruction(ctx)
        for op_name in ('update_item', 'add_item', 'remove_item', 'update_field'):
            assert op_name in prompt, f'{op_name} missing from footer'

    def test_footer_mentions_max_ops_cap(self):
        ctx = _Ctx(_seeded_state(
            findings=[{'id': 'F-1'}],
            bundle={
                'activity_phases': [],
                'deliverables': [],
                'timeline': [],
                'partner_roles': [],
                'customer_roles': [],
                'success_criteria': [],
            },
        ))
        prompt = delivery_plan_repair_agent.instruction(ctx)
        # Pilot cap is 5 (configurable per section).
        assert 'Maximum 5 ops' in prompt

    def test_renders_upstream_packet(self):
        ctx = _Ctx(_seeded_state(
            findings=[{'id': 'F-1'}],
            bundle={
                'activity_phases': [],
                'deliverables': [],
                'timeline': [],
                'partner_roles': [],
                'customer_roles': [],
                'success_criteria': [],
            },
        ))
        prompt = delivery_plan_repair_agent.instruction(ctx)
        assert '<extraction_manifest>' not in prompt
        assert '<prior_requirements>' in prompt


class TestInstructionProviderMissingInputs:
    def test_missing_prior_requirements_triggers_stop(self):
        state = _seeded_state(findings=[{'id': 'F-1'}])
        del state[SOW_BUNDLE_STATE_KEYS['requirements']]
        ctx = _Ctx(state)
        prompt = delivery_plan_repair_agent.instruction(ctx)
        assert 'STOP' in prompt
        assert 'prior_requirements' in prompt
        # Tool footer must NOT appear when required inputs are missing.
        assert '# Repair mode (tool-based' not in prompt


class TestInstructionProviderMissingRepairFindings:
    """When the loop populates the upstream packet but forgets the
    findings (or invokes the repair agent by mistake), the provider
    must STOP without calling any tool — invoking the patch tool with
    no finding context is what the design is preventing."""

    def test_no_findings_emits_stop_no_repair_findings_directive(self):
        ctx = _Ctx(_seeded_state(
            findings=None,
            bundle={
                'activity_phases': [],
                'deliverables': [],
                'timeline': [],
                'partner_roles': [],
                'customer_roles': [],
                'success_criteria': [],
            },
        ))
        prompt = delivery_plan_repair_agent.instruction(ctx)
        assert 'Stop — no repair findings' in prompt
        # No tool footer in this branch either.
        assert '# Repair mode (tool-based' not in prompt
        # And no instruction telling the agent to emit JSON.
        assert 'Output protocol (binding)' not in prompt

    def test_empty_findings_list_treated_as_missing(self):
        """``[]`` is the explicit 'no findings' shape; the provider must
        switch to the STOP directive same as if the key was absent."""
        ctx = _Ctx(_seeded_state(
            findings=[],  # explicit empty
            bundle={
                'activity_phases': [],
                'deliverables': [],
                'timeline': [],
                'partner_roles': [],
                'customer_roles': [],
                'success_criteria': [],
            },
        ))
        prompt = delivery_plan_repair_agent.instruction(ctx)
        assert 'Stop — no repair findings' in prompt


# ---------------------------------------------------------------------------
# Quality-loop wiring — Phase 2 only points delivery_plan at the new agent
# ---------------------------------------------------------------------------


class TestQualityLoopWiring:
    def test_delivery_plan_route_uses_repair_variant(self):
        from app.sub_agents.quality_loop.agent import sow_quality_loop

        assert (
            sow_quality_loop.repair_section_agents['delivery_plan']
            is delivery_plan_repair_agent
        )

    def test_all_sections_now_use_repair_agents(self):
        """Phase 3 completed the rollout — every section's repair route
        now goes through its tool-based repair agent. Each section is
        also covered by its own ``test_<section>_repair_agent.py``."""
        from app.sub_agents.architecture import architecture_repair_agent
        from app.sub_agents.narrative import narrative_repair_agent
        from app.sub_agents.quality_loop.agent import sow_quality_loop
        from app.sub_agents.requirements import requirements_repair_agent
        from app.sub_agents.scope_boundaries import scope_boundaries_repair_agent

        wired = sow_quality_loop.repair_section_agents
        assert wired['requirements'] is requirements_repair_agent
        assert wired['scope_boundaries'] is scope_boundaries_repair_agent
        assert wired['architecture'] is architecture_repair_agent
        assert wired['narrative'] is narrative_repair_agent
