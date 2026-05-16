"""Step 1 of validation_critic — deterministic checks via ContentValidator.

A thin BaseAgent wrapper that calls the existing Python validator and
writes the structured result to ``state[STATE_DET_RESULT]``. No LLM call,
no awaits beyond the event yield. Owns the deterministic half of the
report's contract.
"""

from __future__ import annotations

from typing import AsyncGenerator, ClassVar

import structlog
from google.adk.agents import BaseAgent
from google.adk.agents.base_agent_config import BaseAgentConfig
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions

from ...shared.validators import ContentValidator
from .schema import (
    STATE_DET_RESULT,
    STATE_SOW,
    STATE_STAGE,
    DeterministicIssue,
    DeterministicResult,
)

logger = structlog.get_logger()

_validator = ContentValidator()


def _to_result(raw: dict, sow: dict) -> DeterministicResult:
    """Coerce ContentValidator output into the report schema."""
    issues = [
        DeterministicIssue(
            severity=i.get('severity', 'warning'),
            field=i.get('field', ''),
            message=i.get('message', ''),
            suggestion=i.get('suggestion', ''),
        )
        for i in raw.get('issues', [])
    ]
    return DeterministicResult(
        passed=bool(raw.get('passed', False)),
        error_count=int(raw.get('error_count', 0)),
        warning_count=int(raw.get('warning_count', 0)),
        issues=issues,
    )


class DeterministicCheckAgent(BaseAgent):
    """Runs `ContentValidator` and persists the result to session state."""

    config_type: ClassVar[type[BaseAgentConfig]] = BaseAgentConfig

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        sow = state.get(STATE_SOW) or {}
        stage = state.get(STATE_STAGE) or 'full'

        if not isinstance(sow, dict) or not sow:
            result = DeterministicResult(
                passed=False,
                error_count=1,
                issues=[
                    DeterministicIssue(
                        severity='error',
                        field='sow_data',
                        message=(
                            'No SOW payload found in state. Call `stage_sow` '
                            'with the SOW JSON before transferring to '
                            'validation_critic.'
                        ),
                        suggestion='Stage the SOW JSON via `stage_sow` first.',
                    )
                ],
            )
        else:
            raw = _validator.validate(sow, stage=stage).to_dict()
            result = _to_result(raw, sow)

        state[STATE_DET_RESULT] = result.model_dump()
        logger.info(
            'deterministic_check_completed',
            passed=result.passed,
            error_count=result.error_count,
            warning_count=result.warning_count,
            stage=stage,
        )

        # Emit a state-only event — no Content so this internal step never
        # surfaces as a chat message. Telemetry stays in Cloud Logging.
        yield Event(
            invocation_id=ctx.invocation_id,
            author=self.name,
            branch=ctx.branch,
            actions=EventActions(
                state_delta={STATE_DET_RESULT: result.model_dump()},
            ),
        )


deterministic_check_agent = DeterministicCheckAgent(
    name='deterministic_check_agent',
    description=(
        'Runs the Python ContentValidator on the staged SOW and writes the '
        'structured deterministic result to session state.'
    ),
)
