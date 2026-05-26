"""Delivery-plan section sub-agent — Phase 2 Step B.

Two agents live here:

- :data:`delivery_plan_agent` (the first-gen ``SequentialAgent``) —
  invoked by the root orchestrator to produce the bundle from
  upstream packets the first time around. Same shape every other
  section follows (worker + formatter).
- :data:`delivery_plan_repair_agent` (single Agent, tool-based) —
  invoked by the QualityLoopAgent in repair mode. Uses
  :func:`app.tools.sow.apply_section_patch.apply_delivery_plan_patch`
  to mutate the bundle transactionally instead of regenerating it.
  Phase 2 of the quality-loop refactor; only this section ships
  the repair variant until the vertical slice checkpoints positive
  on a real SOW.
"""

from __future__ import annotations

from ...tools.sow.apply_section_patch import apply_delivery_plan_patch
from ..schemas import DeliveryPlanBundle, SOW_BUNDLE_STATE_KEYS
from .._section_agent import build_section_agent, build_section_repair_agent

DELIVERY_PLAN_OUTPUT_KEY: str = SOW_BUNDLE_STATE_KEYS['delivery_plan']

_OUTPUT_EXAMPLE = """\
{"activity_phases": [{"name": "Phase 1: Discovery",
                       "description": "...", "tasks": ["..."]}],
 "deliverables": [{"number": "WS-01",
                   "activity": "Phase 1: Discovery", "name": "...",
                   "description": "...", "format": "Document"}],
 "timeline": [{"activity": "Phase 1: Discovery",
               "timeframe": "Weeks 1-2", "outcomes": "..."}],
 "partner_roles": [{"role": "Project Manager", "responsibilities": "..."}],
 "customer_roles": [{"role": "Sponsor", "responsibilities": "..."}],
 "success_criteria": ["..."],
 "objectives": ["..."]}"""


delivery_plan_agent = build_section_agent(
    name='delivery_plan_agent',
    description=(
        'Generates the delivery cluster of the SOW: activity phases, '
        'deliverables, timeline, partner/customer roles, success criteria, '
        'and objectives. Runs the Activities↔Deliverables↔Timeline↔Roles '
        'cross-validation internally so structural inconsistencies are '
        'caught before the validation critic sees them. Writes a '
        'DeliveryPlanBundle to '
        f'`state[{DELIVERY_PLAN_OUTPUT_KEY!r}]`.'
    ),
    skill_name='sow-delivery-plan',
    output_schema=DeliveryPlanBundle,
    output_key=DELIVERY_PLAN_OUTPUT_KEY,
    output_example=_OUTPUT_EXAMPLE,
    state_inputs=(
        ('extraction_manifest', SOW_BUNDLE_STATE_KEYS['manifest']),
        ('prior_requirements', SOW_BUNDLE_STATE_KEYS['requirements']),
    ),
)


delivery_plan_repair_agent = build_section_repair_agent(
    name='delivery_plan_repair_agent',
    description=(
        'Repair-mode counterpart of `delivery_plan_agent`. Reads '
        '`<repair_findings>` from state and applies a batch of patch ops '
        'to the in-state DeliveryPlanBundle via `apply_delivery_plan_patch`, '
        'instead of regenerating the bundle. Invoked exclusively by the '
        'QualityLoopAgent; never wired into the root.'
    ),
    section_name='delivery_plan',
    skill_name='sow-delivery-plan',
    bundle_key=DELIVERY_PLAN_OUTPUT_KEY,
    patch_tool=apply_delivery_plan_patch,
    state_inputs=(
        ('extraction_manifest', SOW_BUNDLE_STATE_KEYS['manifest']),
        ('prior_requirements', SOW_BUNDLE_STATE_KEYS['requirements']),
    ),
)
