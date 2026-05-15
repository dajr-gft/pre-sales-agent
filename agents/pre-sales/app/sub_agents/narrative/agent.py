"""Narrative section sub-agent — Phase 2 Step E (last).

Receives an ``AgentTool(google_search_agent)`` as an extra worker tool
so the narrative worker can run the four web-search queries the
legacy ``sow-narrative`` skill mandates (partner/customer/domain
context) inside the same isolated invocation. The search agent is
shared with the root via :mod:`app.sub_agents.web_search` — defined
in a dedicated module to avoid the import cycle that would arise if
narrative tried to import it from ``app.agent``.
"""

from __future__ import annotations

from google.adk.tools.agent_tool import AgentTool

from ..schemas import NarrativeBundle, SOW_BUNDLE_STATE_KEYS
from ..web_search import google_search_agent
from .._section_agent import build_section_agent

NARRATIVE_OUTPUT_KEY: str = SOW_BUNDLE_STATE_KEYS['narrative']

_OUTPUT_EXAMPLE = """\
{"executive_summary": "Acme Corp is modernizing ... (250-450 words).",
 "partner_overview": "GFT Technologies is a ...",
 "customer_overview": "Acme Corp is a ...",
 "customer_primary_domain": "acme.com"}"""


narrative_agent = build_section_agent(
    name='narrative_agent',
    description=(
        'Synthesizes the narrative cluster: executive summary, partner '
        'overview, customer overview, and customer_primary_domain. Runs '
        'the four web-search queries via the embedded google_search_agent '
        'AgentTool. Must run LAST in Phase 2 — depends on every upstream '
        'section. Writes a NarrativeBundle to '
        f'`state[{NARRATIVE_OUTPUT_KEY!r}]`.'
    ),
    skill_name='sow-narrative',
    output_schema=NarrativeBundle,
    output_key=NARRATIVE_OUTPUT_KEY,
    output_example=_OUTPUT_EXAMPLE,
    extra_tools=[AgentTool(agent=google_search_agent)],
)
