"""Integration test: root-skills SOW generation protocol (Phase 2).

Exercises the deterministic data path of ``<sow_generation_protocol>``
end-to-end without a live LLM — the same philosophy as
``test_sow_pipeline.py`` (drive the real components, not a mocked model
flow). It wires the actual ``AutoScopedSkillToolset`` (built from the
on-disk ``app/skills`` allowlist), the real ``save_sow_metadata`` /
``save_<section>_bundle`` tools, and ``assemble_sow_payload`` against a
single shared session ``state`` dict, simulating the root walking the
five section skills in order.

Verifies the Phase 2 acceptance points:
  - every section bundle is persisted and Pydantic-valid,
  - the skill-scope state contract ends on ``sow-narrative`` with the
    four prior skills in ``inactive``,
  - the auto-scope pruning fired once per skill switch (4 events) and
    wrote both ``last_prune`` and the ``prune_totals`` accumulator,
  - ``assemble_sow_payload(stage="full")`` succeeds from the
    ``app:sow:metadata`` envelope (no manifest needed).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from google.genai import types

from app.agent import _skill_toolset
from app.shared.auto_scoped_skill_toolset import (
    STATE_SKILL_CURRENT,
    STATE_SKILL_INACTIVE,
    STATE_SKILL_LAST_PRUNE,
    STATE_SKILL_PRUNE_TOTALS,
    ScopedLoadSkillTool,
)
from app.sub_agents.schemas import (
    SOW_BUNDLE_STATE_KEYS,
    SOW_METADATA_STATE_KEY,
)
from app.tools.sow.assemble_payload import assemble_sow_payload
from app.tools.sow.save_section_bundle import (
    save_architecture_bundle,
    save_delivery_plan_bundle,
    save_narrative_bundle,
    save_requirements_bundle,
    save_scope_boundaries_bundle,
)
from app.tools.sow.save_sow_metadata import save_sow_metadata


# ---------------------------------------------------------------------------
# Synthetic llm_request + skill-call Content builders
# ---------------------------------------------------------------------------


def _load_skill_pair(skill_name: str) -> list[types.Content]:
    return [
        types.Content(
            role='model',
            parts=[
                types.Part(
                    function_call=types.FunctionCall(
                        name='load_skill', args={'skill_name': skill_name}
                    )
                )
            ],
        ),
        types.Content(
            role='user',
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        name='load_skill',
                        response={
                            'skill_name': skill_name,
                            'instructions': f'# SKILL.md {skill_name}\n' + 'x' * 500,
                        },
                    )
                )
            ],
        ),
    ]


class _FakeLlmRequest:
    """Minimal LlmRequest stand-in: mutable ``contents`` + a no-op
    ``append_instructions`` so the parent toolset's L1 injection runs
    without a real request object."""

    def __init__(self, contents: list[types.Content]):
        self.contents = list(contents)
        self.appended_instructions: list[Any] = []

    def append_instructions(self, instructions: Any) -> None:
        self.appended_instructions.append(instructions)


def _ctx(state: dict[str, Any], agent_name: str = 'pre_sales_assistant') -> SimpleNamespace:
    return SimpleNamespace(state=state, agent_name=agent_name)


# ---------------------------------------------------------------------------
# Valid section bundles (minimal but schema-valid)
# ---------------------------------------------------------------------------


def _requirements() -> dict[str, Any]:
    return {
        'functional_requirements': [
            {'number': 'FR-01', 'description': 'Ingest data from SAP.'},
        ],
        'non_functional_requirements': [
            {'number': 'NFR-01', 'description': 'TLS 1.3 in transit.'},
        ],
    }


def _delivery_plan() -> dict[str, Any]:
    return {
        'activity_phases': [
            {'name': 'Phase 1', 'description': 'Discovery.', 'tasks': ['Kickoff']},
        ],
        'deliverables': [
            {'activity': 'Phase 1', 'name': 'Spec', 'description': 'Design spec.', 'format': 'Document'},
        ],
        'timeline': [{'activity': 'Phase 1', 'timeframe': 'W1-2', 'outcomes': 'Spec done.'}],
        'partner_roles': [{'role': 'PM', 'responsibilities': 'Owns plan.'}],
        'customer_roles': [{'role': 'Sponsor', 'responsibilities': 'Approves.'}],
        'success_criteria': ['Plan accepted.'],
        'objectives': ['Modernize.'],
    }


def _scope_boundaries() -> dict[str, Any]:
    return {
        'assumptions': ['Customer provides access.'],
        'out_of_scope': ['Hardware procurement.'],
        'risks': [{'description': 'SAP rate limits.', 'mitigation': 'Backoff.'}],
        'handover_disclaimers': ['KT in week 10.'],
        'change_request_policy_text': 'Written approval required.',
    }


def _architecture() -> dict[str, Any]:
    return {
        'architecture_description': 'Layered serverless architecture on GCP.',
        'architecture_components': [{'name': 'Cloud Run', 'role': 'API host.'}],
        'architecture_integrations': [{'name': 'SAP', 'description': 'Source.'}],
        'technology_stack': [{'service': 'BigQuery', 'purpose': 'Warehouse.'}],
    }


def _narrative() -> dict[str, Any]:
    return {
        'executive_summary': 'Modernizes Acme data analytics on GCP.',
        'partner_overview': 'GFT is a GCP premier partner.',
        'customer_overview': 'Acme manufactures globally.',
        'customer_primary_domain': 'acme.com',
    }


def _metadata_kwargs() -> dict[str, str]:
    return {
        'partner_name': 'GFT Technologies',
        'customer_name': 'Acme Corp',
        'partner_short_name': 'GFT',
        'customer_short_name': 'Acme',
        'project_title': 'Data Analytics Platform',
        'date': '2026-05-27',
        'author': 'Root Agent',
        'funding_type': 'Google DAF',
        'funding_type_short': 'DAF',
        'project_start_date': '2026-06-01',
        'project_end_date': '2026-08-01',
        'engagement_type': 'project',
        'organization_term': 'phases',
    }


_SECTIONS = [
    ('sow-requirements', save_requirements_bundle, _requirements, 'requirements'),
    ('sow-delivery-plan', save_delivery_plan_bundle, _delivery_plan, 'delivery_plan'),
    ('sow-scope-boundaries', save_scope_boundaries_bundle, _scope_boundaries, 'scope_boundaries'),
    ('sow-architecture', save_architecture_bundle, _architecture, 'architecture'),
    ('sow-narrative', save_narrative_bundle, _narrative, 'narrative'),
]


class TestRootSkillsGenerationProtocol:
    async def test_full_protocol_persists_bundles_and_prunes(self):
        state: dict[str, Any] = {}
        ctx = _ctx(state)

        # The toolset substitutes LoadSkillTool with ScopedLoadSkillTool.
        load_tool = next(
            t for t in _skill_toolset._tools if isinstance(t, ScopedLoadSkillTool)
        )

        # Step 1 — metadata.
        meta_result = await save_sow_metadata(tool_context=ctx, **_metadata_kwargs())
        assert meta_result['status'] == 'success'
        assert SOW_METADATA_STATE_KEY in state

        # Steps 2/4 — walk the five section skills in order.
        previous_skill: str | None = None
        for skill_name, save_tool, builder, section_key in _SECTIONS:
            # a) Activate the skill (updates the scope contract).
            await load_tool.run_async(
                args={'skill_name': skill_name}, tool_context=ctx
            )

            # b) Simulate the next outgoing LLM request carrying the
            #    just-deactivated skill's load_skill pair plus the new
            #    one; the toolset must prune the previous skill.
            contents: list[types.Content] = []
            if previous_skill:
                contents += _load_skill_pair(previous_skill)
            contents += _load_skill_pair(skill_name)
            req = _FakeLlmRequest(contents)
            await _skill_toolset.process_llm_request(
                tool_context=ctx, llm_request=req
            )

            # c) Persist the generated bundle.
            save_result = await save_tool(bundle=builder(), tool_context=ctx)
            assert save_result['status'] == 'success', (
                f'{skill_name} bundle save failed: {save_result}'
            )

            previous_skill = skill_name

        # --- Bundle persistence: all five present and Pydantic-valid ---
        for _skill, _save, _builder, section_key in _SECTIONS:
            key = SOW_BUNDLE_STATE_KEYS[section_key]
            assert key in state, f'{key} not persisted'
            assert isinstance(state[key], dict)
        # Spot-check structural validity carried through model_dump().
        assert state[SOW_BUNDLE_STATE_KEYS['requirements']]['functional_requirements'][0]['number'] == 'FR-01'

        # --- Skill-scope state contract ---
        assert state[STATE_SKILL_CURRENT] == 'sow-narrative'
        inactive = state[STATE_SKILL_INACTIVE]
        assert set(inactive) == {
            'sow-requirements',
            'sow-delivery-plan',
            'sow-scope-boundaries',
            'sow-architecture',
        }
        assert 'sow-narrative' not in inactive

        # --- Pruning telemetry: 4 switches => 4 prune events ---
        assert STATE_SKILL_LAST_PRUNE in state
        totals = state[STATE_SKILL_PRUNE_TOTALS]
        assert totals['prune_event_count'] == 4
        assert totals['pruned_messages_total'] == 8  # 2 contents per switch
        assert totals['pruned_bytes_total'] > 0

    async def test_assemble_full_stage_smoke(self):
        """After the protocol populates metadata + all five bundles,
        assemble_sow_payload(stage='full') succeeds from the envelope —
        no Extraction Manifest in state."""
        state: dict[str, Any] = {}
        ctx = _ctx(state)

        await save_sow_metadata(tool_context=ctx, **_metadata_kwargs())
        await save_requirements_bundle(bundle=_requirements(), tool_context=ctx)
        await save_delivery_plan_bundle(bundle=_delivery_plan(), tool_context=ctx)
        await save_scope_boundaries_bundle(bundle=_scope_boundaries(), tool_context=ctx)
        await save_architecture_bundle(bundle=_architecture(), tool_context=ctx)
        await save_narrative_bundle(bundle=_narrative(), tool_context=ctx)

        # No extraction_manifest key in state at all — assembly is
        # envelope-only.
        assert 'extraction_manifest' not in state

        result = await assemble_sow_payload(stage='full', tool_context=ctx)
        assert result['status'] == 'success', result
        sow = result['data']['sow_data']
        assert sow['project_title'] == 'Data Analytics Platform'
        assert sow['executive_summary'].startswith('Modernizes Acme')
        assert sow['architecture_description'].startswith('Layered')
        assert len(sow['functional_requirements']) == 1
