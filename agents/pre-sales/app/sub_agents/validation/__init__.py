"""Validation Critic sub-agent — single-pass SequentialAgent.

Separates SOW validation into deterministic checks, five modular
semantic skills running in parallel, and a Python-only gate decision.
LLM never decides pass/fail.

The critic is invoked from the root via ``AgentTool`` (not via
``transfer_to_agent``) so its internal events stay out of the chat and
control returns to the root in the same turn. The 4-round correction
loop is owned by the root agent's prompt: when ``validation_critic``
returns ``blocked``, the root applies edits using the ``sow-generator``
SkillToolset (already loaded) and re-invokes the tool.
"""

from .agent import validation_critic
from .schema import (
    Finding,
    SKILL_NAMES,
    Severity,
    SkillFindings,
    SkillName,
    SkillRunMetadata,
    Stage,
    Status,
    SummaryDraft,
    ValidationReport,
)

__all__ = [
    'Finding',
    'SKILL_NAMES',
    'Severity',
    'SkillFindings',
    'SkillName',
    'SkillRunMetadata',
    'Stage',
    'Status',
    'SummaryDraft',
    'ValidationReport',
    'validation_critic',
]
