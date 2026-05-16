"""Validation Critic sub-agent — single-pass SequentialAgent.

Separates SOW validation into deterministic checks, five modular
semantic skills running in parallel, and a Python-only gate decision.
LLM never decides pass/fail.

The critic is invoked from inside :class:`QualityLoopAgent` (see
``app/sub_agents/quality_loop``), which is the single ``AgentTool``
the root exposes for SOW validation. The loop owns the round budget
and decides — based on ``overall_status`` — whether to invoke the
``revision_agent`` for a surgical patch before re-running the critic.
The root never calls the critic or the revision agent directly.
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
