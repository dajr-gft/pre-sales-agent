"""Requirements section sub-agent — FR + NFR via worker + formatter.

Phase 3 ships ``requirements_repair_agent`` alongside the first-gen
``requirements_agent``. The repair agent is invoked by the
QualityLoopAgent in repair mode and patches the bundle via
``apply_requirements_patch`` instead of regenerating it.
"""

from __future__ import annotations

from ...tools.sow.apply_section_patch import apply_requirements_patch
from ..schemas import RequirementsBundle, SOW_BUNDLE_STATE_KEYS
from .._section_agent import build_section_agent, build_section_repair_agent

REQUIREMENTS_OUTPUT_KEY: str = SOW_BUNDLE_STATE_KEYS['requirements']

_OUTPUT_EXAMPLE = """\
{"functional_requirements": [{"number": "FR-01", "description": "..."}],
 "non_functional_requirements": [{"number": "NFR-01", "description": "..."}]}"""


requirements_agent = build_section_agent(
    name='requirements_agent',
    description=(
        'Generates Functional Requirements (FR) and Non-Functional '
        'Requirements (NFR) for the SOW, including FR↔NFR cross-validation '
        '(fr_vs_nfr, fr_restated_as_nfr, anti-uptime Reliability). '
        'Worker drafts using sow-requirements references, then a '
        'formatter enforces the RequirementsBundle schema. Writes the '
        f'bundle to `state[{REQUIREMENTS_OUTPUT_KEY!r}]`.'
    ),
    skill_name='sow-requirements',
    output_schema=RequirementsBundle,
    output_key=REQUIREMENTS_OUTPUT_KEY,
    output_example=_OUTPUT_EXAMPLE,
    state_inputs=(
        ('extraction_manifest', SOW_BUNDLE_STATE_KEYS['manifest']),
    ),
)


requirements_repair_agent = build_section_repair_agent(
    name='requirements_repair_agent',
    description=(
        'Repair-mode counterpart of `requirements_agent`. Applies a batch '
        'of patch ops to the in-state RequirementsBundle via '
        '`apply_requirements_patch` instead of regenerating the bundle. '
        'Invoked exclusively by the QualityLoopAgent.'
    ),
    section_name='requirements',
    skill_name='sow-requirements',
    bundle_key=REQUIREMENTS_OUTPUT_KEY,
    patch_tool=apply_requirements_patch,
    state_inputs=(
        ('extraction_manifest', SOW_BUNDLE_STATE_KEYS['manifest']),
    ),
)
