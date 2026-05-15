"""Scope-boundaries section sub-agent — Phase 2 Step C."""

from __future__ import annotations

from ..schemas import ScopeBoundariesBundle, SOW_BUNDLE_STATE_KEYS
from .._section_agent import build_section_agent

SCOPE_BOUNDARIES_OUTPUT_KEY: str = SOW_BUNDLE_STATE_KEYS['scope_boundaries']

_OUTPUT_EXAMPLE = """\
{"assumptions": ["Customer must provide ... before ..."],
 "out_of_scope": ["..."],
 "risks": [{"description": "...", "mitigation": "..."}],
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
)
