"""Delivery-plan section sub-agent — Phase 2 Step B."""

from __future__ import annotations

from ..schemas import DeliveryPlanBundle, SOW_BUNDLE_STATE_KEYS
from .._section_agent import build_section_agent

DELIVERY_PLAN_OUTPUT_KEY: str = SOW_BUNDLE_STATE_KEYS['delivery_plan']

_OUTPUT_EXAMPLE = """\
{"activity_phases": [{"name": "Phase 1: Discovery",
                       "description": "...", "tasks": ["..."]}],
 "deliverables": [{"activity": "Phase 1: Discovery", "name": "...",
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
)
