"""Scope-boundaries section sub-agent — Phase 2 Step C.

Phase 3 ships ``scope_boundaries_repair_agent`` alongside the
first-gen ``scope_boundaries_agent``. The repair agent is invoked by
the QualityLoopAgent in repair mode and patches the bundle via
``apply_scope_boundaries_patch`` instead of regenerating it.
"""

from __future__ import annotations

from ...tools.sow.apply_section_patch import apply_scope_boundaries_patch
from ..schemas import ScopeBoundariesBundle, SOW_BUNDLE_STATE_KEYS
from .._section_agent import build_section_agent, build_section_repair_agent

SCOPE_BOUNDARIES_OUTPUT_KEY: str = SOW_BUNDLE_STATE_KEYS['scope_boundaries']

_OUTPUT_EXAMPLE = """\
{"assumptions": ["Customer must provide ... before ..."],
 "out_of_scope": ["..."],
 "risks": [{"number": "R-01", "description": "...", "mitigation": "..."}],
 "handover_disclaimers": ["..."],
 "change_request_policy_text": "..."}"""


scope_boundaries_agent = build_section_agent(
    name='scope_boundaries_agent',
    description=(
        'Generates the contractual cluster: assumptions, out-of-scope, '
        'change-request policy, handover disclaimers, and risks. Runs the '
        'cross-anchor gate (Assumption↔OOS, Handover↔Reliability NFR, '
        'AI/ML disclosure) before returning. Writes a '
        'ScopeBoundariesBundle to '
        f'`state[{SCOPE_BOUNDARIES_OUTPUT_KEY!r}]`.'
    ),
    skill_name='sow-scope-boundaries',
    output_schema=ScopeBoundariesBundle,
    output_key=SCOPE_BOUNDARIES_OUTPUT_KEY,
    output_example=_OUTPUT_EXAMPLE,
    state_inputs=(
        ('extraction_manifest', SOW_BUNDLE_STATE_KEYS['manifest']),
        ('prior_requirements', SOW_BUNDLE_STATE_KEYS['requirements']),
        ('prior_delivery_plan', SOW_BUNDLE_STATE_KEYS['delivery_plan']),
    ),
)


scope_boundaries_repair_agent = build_section_repair_agent(
    name='scope_boundaries_repair_agent',
    description=(
        'Repair-mode counterpart of `scope_boundaries_agent`. Applies a '
        'batch of patch ops to the in-state ScopeBoundariesBundle via '
        '`apply_scope_boundaries_patch` instead of regenerating the bundle. '
        'Invoked exclusively by the QualityLoopAgent.'
    ),
    section_name='scope_boundaries',
    skill_name='sow-scope-boundaries',
    bundle_key=SCOPE_BOUNDARIES_OUTPUT_KEY,
    patch_tool=apply_scope_boundaries_patch,
    state_inputs=(
        ('extraction_manifest', SOW_BUNDLE_STATE_KEYS['manifest']),
        ('prior_requirements', SOW_BUNDLE_STATE_KEYS['requirements']),
        ('prior_delivery_plan', SOW_BUNDLE_STATE_KEYS['delivery_plan']),
    ),
)
