"""Step 6 of validation_critic — merge partial + summary into final report.

The assembler is the only place ``state[STATE_VALIDATION_RESULT]`` is
written. It treats the partial report as authoritative for every
structural field, then merges in the two text fields from the summary
skill. Fallback summary is built from the partial report itself, so a
summary skill outage cannot block the validation result from reaching
the root agent.
"""

from __future__ import annotations

from typing import AsyncGenerator, ClassVar

import structlog
from google.adk.agents import BaseAgent
from google.adk.agents.base_agent_config import BaseAgentConfig
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions

from .schema import (
    STATE_REPORT_PARTIAL,
    STATE_SUMMARY_DRAFT,
    STATE_VALIDATION_RESULT,
    SummaryDraft,
    ValidationReport,
)

logger = structlog.get_logger()


def _fallback_summary(report: ValidationReport) -> SummaryDraft:
    """Deterministic backup when the summary skill failed to produce JSON."""
    if report.overall_status == 'passed':
        summary = (
            f'Validation passed at stage `{report.stage}`. '
            f'{report.minor_count} minor observation(s) recorded.'
        )
        next_action = 'Proceed to user review.'
    elif report.overall_status == 'blocked':
        summary = (
            f'Validation blocked: {report.deterministic.error_count} '
            f'deterministic error(s) and {report.blocker_count} blocker '
            f'finding(s) at stage `{report.stage}`.'
        )
        next_action = 'Fix the blocking findings before re-validating.'
    else:
        summary = (
            f'Validation requires human review at stage `{report.stage}`. '
            f'{report.major_count} major finding(s) with low confidence.'
        )
        next_action = 'Surface the report to the user and request guidance.'
    return SummaryDraft(summary=summary, next_action=next_action)


class ValidationAssemblerAgent(BaseAgent):
    """Merge partial report + summary draft → write final validation result."""

    config_type: ClassVar[type[BaseAgentConfig]] = BaseAgentConfig

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        partial_raw = state.get(STATE_REPORT_PARTIAL) or {}
        if not partial_raw:
            logger.error('assembler_partial_missing')
            return
        report = ValidationReport.model_validate(partial_raw)

        draft_raw = state.get(STATE_SUMMARY_DRAFT) or {}
        try:
            draft = SummaryDraft.model_validate(draft_raw) if draft_raw else _fallback_summary(report)
        except Exception as exc:
            logger.warning('summary_draft_invalid_using_fallback', error=str(exc))
            draft = _fallback_summary(report)

        final = report.model_copy(
            update={'summary': draft.summary, 'next_action': draft.next_action}
        )
        final_dump = final.model_dump()
        state[STATE_VALIDATION_RESULT] = final_dump

        logger.info(
            'validation_result_assembled',
            overall_status=final.overall_status,
            stage=final.stage,
            findings=len(final.findings),
        )

        # State-only event with `escalate=True` so control returns to the
        # root agent (pre_sales_assistant). Without escalate, the parent
        # SequentialAgent finishes but the runtime does not invoke the
        # root for the next turn — the conversation hangs until the user
        # types something. The root reads `state[STATE_VALIDATION_RESULT]`
        # and produces the user-facing reply on its next model call.
        yield Event(
            invocation_id=ctx.invocation_id,
            author=self.name,
            branch=ctx.branch,
            actions=EventActions(
                state_delta={STATE_VALIDATION_RESULT: final_dump},
                escalate=True,
            ),
        )


validation_assembler_agent = ValidationAssemblerAgent(
    name='validation_assembler_agent',
    description=(
        'Merges the partial report with the LLM summary draft and writes '
        'the final ValidationReport to session state.'
    ),
)
