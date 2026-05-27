"""Narrative section repair sub-agent.

In the root-skills variant the first-gen narrative section is generated
by the root inline (``load_skill('sow-narrative')`` →
``save_narrative_bundle``), and the four web-search queries run via the
``google_search_agent`` tool surfaced while that skill is active. This
module ships only ``narrative_repair_agent``, invoked by the
QualityLoopAgent in repair mode to patch the bundle via
``apply_narrative_patch``.

The repair agent does NOT run web search — repair operates on the
bundle text already present; if a finding calls for new market data the
orchestrator re-generates the narrative from the root rather than
burning search quota inside the loop.
"""

from __future__ import annotations

from ...tools.sow.apply_section_patch import apply_narrative_patch
from ..schemas import SOW_BUNDLE_STATE_KEYS
from .._section_agent import build_section_repair_agent

NARRATIVE_OUTPUT_KEY: str = SOW_BUNDLE_STATE_KEYS['narrative']


narrative_repair_agent = build_section_repair_agent(
    name='narrative_repair_agent',
    description=(
        'Repair specialist for the narrative section. Applies a batch '
        'of patch ops to the in-state NarrativeBundle via '
        '`apply_narrative_patch` instead of regenerating the bundle. '
        'Does NOT run web search — repair mode operates on the bundle '
        'text already present. Invoked exclusively by the QualityLoopAgent.'
    ),
    section_name='narrative',
    skill_name='sow-narrative',
    bundle_key=NARRATIVE_OUTPUT_KEY,
    patch_tool=apply_narrative_patch,
    state_inputs=(
        ('prior_requirements', SOW_BUNDLE_STATE_KEYS['requirements']),
        ('prior_delivery_plan', SOW_BUNDLE_STATE_KEYS['delivery_plan']),
        ('prior_scope_boundaries', SOW_BUNDLE_STATE_KEYS['scope_boundaries']),
        ('prior_architecture', SOW_BUNDLE_STATE_KEYS['architecture']),
    ),
)
