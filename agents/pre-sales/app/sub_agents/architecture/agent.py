"""Architecture section sub-agent — Phase 2 Step D.

Receives :func:`generate_architecture_diagram` as an extra worker tool
so the same agent that drafts ``architecture_description`` /
``architecture_components`` / ``architecture_integrations`` /
``technology_stack`` also produces the diagram PNG artifact in the
same turn (Contract 3 of the legacy ``sow-architecture`` skill: the
three-way invariant description↔table↔diagram).

The bundle Pydantic schema does NOT carry the diagram bytes — the
diagram is saved as a session artifact and the document generator
attaches it during ``generate_sow_document``. This agent's
:class:`ArchitectureBundle` covers only the structured text fields.
"""

from __future__ import annotations

from ...tools.sow.generate_architecture_diagram import \
    generate_architecture_diagram
from ..schemas import ArchitectureBundle, SOW_BUNDLE_STATE_KEYS
from .._section_agent import build_section_agent

ARCHITECTURE_OUTPUT_KEY: str = SOW_BUNDLE_STATE_KEYS['architecture']

_OUTPUT_EXAMPLE = """\
{"architecture_description": "End users send HTTPS requests to ...",
 "architecture_components": [{"name": "Cloud Run", "role": "Hosts the API."}],
 "architecture_integrations": [{"name": "SAP ERP",
                                "description": "Source system via REST."}],
 "technology_stack": [{"service": "Cloud Run",
                       "purpose": "Serverless API layer."}]}"""


architecture_agent = build_section_agent(
    name='architecture_agent',
    description=(
        'Generates the architecture cluster: description, components, '
        'integrations, technology stack, and the diagram PNG artifact '
        '(via generate_architecture_diagram). Runs the three-way '
        'invariant description↔table↔diagram + component checklist '
        'before returning. Writes an ArchitectureBundle to '
        f'`state[{ARCHITECTURE_OUTPUT_KEY!r}]`.'
    ),
    skill_name='sow-architecture',
    output_schema=ArchitectureBundle,
    output_key=ARCHITECTURE_OUTPUT_KEY,
    output_example=_OUTPUT_EXAMPLE,
    extra_tools=[generate_architecture_diagram],
    state_inputs=(
        ('extraction_manifest', SOW_BUNDLE_STATE_KEYS['manifest']),
        ('prior_requirements', SOW_BUNDLE_STATE_KEYS['requirements']),
        ('prior_delivery_plan', SOW_BUNDLE_STATE_KEYS['delivery_plan']),
        ('prior_scope_boundaries', SOW_BUNDLE_STATE_KEYS['scope_boundaries']),
    ),
)
