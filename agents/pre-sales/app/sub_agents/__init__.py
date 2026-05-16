"""Sub-agents owned by the pre-sales root agent."""

from .architecture import architecture_agent
from .delivery_plan import delivery_plan_agent
from .discovery import discovery_agent
from .narrative import narrative_agent
from .quality_loop import sow_quality_loop
from .requirements import requirements_agent
from .revision import revision_agent
from .scope_boundaries import scope_boundaries_agent
from .validation import validation_critic
from .web_search import google_search_agent

__all__ = [
    'architecture_agent',
    'delivery_plan_agent',
    'discovery_agent',
    'google_search_agent',
    'narrative_agent',
    'requirements_agent',
    'revision_agent',
    'scope_boundaries_agent',
    'sow_quality_loop',
    'validation_critic',
]
