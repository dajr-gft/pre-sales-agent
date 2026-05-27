"""Sub-agents owned by the pre-sales root agent.

In the root-skills variant the section first-gen agents are gone — the
root generates each section inline via skills. Only the section *repair*
agents survive (wired into the QualityLoopAgent, not exported here), plus
the validation critic, the revision agent, the quality loop, and the
shared web-search agent.
"""

from .quality_loop import sow_quality_loop
from .revision import revision_agent
from .validation import validation_critic
from .web_search import google_search_agent

__all__ = [
    'google_search_agent',
    'revision_agent',
    'sow_quality_loop',
    'validation_critic',
]
