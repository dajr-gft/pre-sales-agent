"""Requirements section sub-agent — FR + NFR via worker + formatter."""

from __future__ import annotations

from ..schemas import RequirementsBundle, SOW_BUNDLE_STATE_KEYS
from .._section_agent import build_section_agent

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
)
