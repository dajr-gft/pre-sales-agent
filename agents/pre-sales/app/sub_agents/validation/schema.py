"""Pydantic schema for the Validation Critic — single source of truth.

The 5 semantic skills produce ``Finding`` instances; they never touch
gate fields. ``ValidationAggregator`` (Python) is the only place where
``overall_status``, severity counts and human-review flags are decided.
``ValidationSummarySkill`` only fills the textual fields.

State keys are namespaced per skill so the ``ParallelAgent`` writes
into isolated slots — no race condition is possible.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Severity = Literal['BLOCKER', 'MAJOR', 'MINOR']
Status = Literal['passed', 'blocked', 'needs_human_review']
SkillName = Literal[
    'coverage',
    'contradictions',
    'contractual_exposure',
    'disclosures',
    'semantic_quality',
]
Stage = Literal['content', 'full']

SKILL_NAMES: tuple[str, ...] = (
    'coverage',
    'contradictions',
    'contractual_exposure',
    'disclosures',
    'semantic_quality',
)

# State keys shared across the validation pipeline. Keep names stable —
# downstream consumers (tests, telemetry) read them by literal string.
STATE_SOW = 'app:sow:current'
STATE_STAGE = 'app:sow:stage'
STATE_DET_RESULT = 'app:det_result'
STATE_MANIFEST_RESIDUAL = 'app:manifest_residual'
STATE_REPORT_PARTIAL = 'app:validation_report:partial'
STATE_SUMMARY_DRAFT = 'app:validation_summary:draft'
STATE_VALIDATION_RESULT = 'app:validation_result'


def skill_findings_state_key(name: str) -> str:
    """Resolve the per-skill state key.

    Centralizing this avoids drift between the skill agents that write the
    key and the aggregator that reads it.
    """
    return f'app:skill_findings:{name}'


class Finding(BaseModel):
    """A single defect emitted by one of the semantic skills.

    Each finding carries the originating ``skill`` (= dimension name) so
    the aggregator can measure quality per dimension and decompose later
    without breaking the contract.
    """

    model_config = ConfigDict(extra='forbid')

    id: str = Field(description='Sequential id, e.g. "coverage-001".')
    skill: SkillName = Field(description='Originating skill / dimension.')
    category: str = Field(
        description='Sub-type within the skill (e.g. "fr_vs_nfr").',
    )
    severity: Severity
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        default=0.8,
        description='Reviewer-reported confidence used by the aggregator.',
    )
    evidence: str = Field(description='Verbatim quote(s) from the SOW.')
    recommendation: str = Field(description='Concrete corrective instruction.')
    fields: list[str] = Field(
        default_factory=list,
        description='Top-level sow_data keys the recommendation would touch.',
    )
    manifest_item_id: str | None = Field(
        default=None,
        description='Manifest item id when skill="coverage".',
    )
    persistent: bool = Field(
        default=False,
        description='Flag set when the finding re-appears across loop rounds.',
    )
    requires_human_review: bool = Field(default=False)
    model_used: str = Field(
        default='', description='Model id when emitted by an LLM.'
    )


class DeterministicIssue(BaseModel):
    """Mirror of shared.validators.ValidationIssue for the report contract."""

    model_config = ConfigDict(extra='ignore')

    severity: Literal['error', 'warning']
    field: str
    message: str
    suggestion: str = ''


class DeterministicResult(BaseModel):
    """Output of `ContentValidator` wrapped for the report contract."""

    model_config = ConfigDict(extra='forbid')

    passed: bool
    error_count: int = 0
    warning_count: int = 0
    issues: list[DeterministicIssue] = Field(default_factory=list)


class SkillRunMetadata(BaseModel):
    """Telemetry about each LLM skill invocation."""

    model_config = ConfigDict(extra='forbid')

    skill: str
    model: str = ''
    ran: bool = True
    fallback_reason: str | None = None
    latency_ms: int = 0
    finding_count: int = 0


class SummaryDraft(BaseModel):
    """Structured output of `ValidationSummarySkill` — text-only fields."""

    model_config = ConfigDict(extra='forbid')

    summary: str = Field(
        description='Human-readable, language-matched summary of the report.',
    )
    next_action: str = Field(
        description=(
            'One-sentence instruction to the calling agent. Examples: '
            '"Proceed to user review." or "Fix BLOCKER findings before retry."'
        ),
    )


class SkillFindings(BaseModel):
    """JSON shape every semantic skill returns to its `output_key`."""

    model_config = ConfigDict(extra='forbid')

    findings: list[Finding] = Field(default_factory=list)


class ValidationReport(BaseModel):
    """Final report assembled by `validation_assembler` and read by root."""

    model_config = ConfigDict(extra='forbid')

    overall_status: Status
    overall_score: float = Field(ge=0.0, le=1.0)
    requires_human_review: bool
    deterministic: DeterministicResult
    findings: list[Finding] = Field(default_factory=list)
    skills_run: list[SkillRunMetadata] = Field(default_factory=list)
    skills_not_run: list[str] = Field(default_factory=list)
    stage: Stage
    blocker_count: int = 0
    major_count: int = 0
    minor_count: int = 0
    findings_by_skill: dict[str, int] = Field(default_factory=dict)
    summary: str = ''
    next_action: str = ''
