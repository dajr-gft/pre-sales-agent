"""``QualityLoopAgent`` — critic → conditional revision → repeat.

Why a custom ``BaseAgent`` instead of ``LoopAgent`` from the ADK:
``LoopAgent`` iterates every sub-agent in order on each round, with no
built-in way to skip the second sub-agent when the first one is happy.
With our pair ``[validation_critic, revision_agent]`` that means
``revision_agent`` would run even when the critic returned ``passed``
or ``needs_human_review`` — a regression vs the current behaviour where
the root prompt branches on ``overall_status`` before invoking
``sow-revision``.

This agent encodes the branching explicitly:

    for round in [1..MAX_ROUNDS]:
        run validation_critic
        match overall_status:
            passed              -> emit result, return
            needs_human_review  -> emit result, return
            blocked AND last    -> emit "exhausted" with last validated
                                   report, return  (do NOT patch without
                                   a follow-up critic run)
            blocked             -> run revision_agent, continue
            anything else       -> emit unexpected-status result, return

The "blocked AND last" branch is critical: running ``revision_agent``
on the final round would leave the SOW in ``state['app:sow:current']``
in a modified-but-unvalidated state, while ``final_report`` would still
reflect the pre-patch document. Skipping revision on the last round
preserves the invariant "every patch is followed by a critic run".

The final outcome is written to ``state['app:sow:quality_loop_result']``
via ``EventActions.state_delta`` so the root can read it after the
``AgentTool`` returns and decide the next step (proceed, ask the user,
or surface the loop's failure mode).

The state keys this agent reads / writes:

- Reads ``state['app:validation_result']`` — the ``ValidationReport``
  the critic's assembler writes on every round.
- Writes ``state['app:sow:quality_loop_result']`` — exactly once, when
  the loop terminates.
"""

from __future__ import annotations

import json
from typing import Any, AsyncGenerator, ClassVar, Optional

import structlog
from google.adk.agents import BaseAgent
from google.adk.agents.base_agent_config import BaseAgentConfig
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from google.genai import types

from ..revision import revision_agent
from ..validation import validation_critic
from ..validation.schema import STATE_VALIDATION_RESULT

logger = structlog.get_logger()

QUALITY_LOOP_RESULT_KEY = 'app:sow:quality_loop_result'

# Cap mirrors the 4-round budget the legacy root prompt used (rounds 1-4
# allowed to patch, round 5 caps with a downgrade). Tunable per project
# via the constructor when needed.
DEFAULT_MAX_ROUNDS = 5

LoopStatus = str  # one of: 'passed', 'needs_human_review', 'blocked',
# 'exhausted', 'unexpected_status'


class QualityLoopAgent(BaseAgent):
    """Critic → (conditional revision) loop with explicit stop conditions."""

    config_type: ClassVar[type[BaseAgentConfig]] = BaseAgentConfig

    max_rounds: int = DEFAULT_MAX_ROUNDS

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        critic, reviser = self.sub_agents[0], self.sub_agents[1]

        last_status: Optional[str] = None
        for round_idx in range(self.max_rounds):
            round_number = round_idx + 1
            logger.info(
                'quality_loop_round_start',
                round=round_number,
                max_rounds=self.max_rounds,
            )

            async for event in critic.run_async(ctx):
                yield event

            report = ctx.session.state.get(STATE_VALIDATION_RESULT) or {}
            status = report.get('overall_status') if isinstance(report, dict) else None
            last_status = status

            if status == 'passed':
                yield self._emit_result(
                    ctx,
                    status='passed',
                    rounds_used=round_number,
                    final_report=report,
                    message=(
                        f'Validation passed after {round_number} round(s).'
                    ),
                )
                return

            if status == 'needs_human_review':
                yield self._emit_result(
                    ctx,
                    status='needs_human_review',
                    rounds_used=round_number,
                    final_report=report,
                    message=(
                        f'Validation needs human review (round {round_number}).'
                    ),
                )
                return

            if status != 'blocked':
                yield self._emit_result(
                    ctx,
                    status='unexpected_status',
                    rounds_used=round_number,
                    final_report=report,
                    observed_status=status,
                    message=(
                        f"Unexpected validation status '{status}' at round "
                        f'{round_number}; aborting loop.'
                    ),
                )
                return

            # status == 'blocked' from here on.
            if round_idx == self.max_rounds - 1:
                # Last round: skipping revision keeps the final_report
                # consistent with the SOW currently in state. Running a
                # patch we cannot revalidate would silently desync them.
                yield self._emit_result(
                    ctx,
                    status='exhausted',
                    rounds_used=round_number,
                    final_report=report,
                    message=(
                        f'Quality loop exhausted {self.max_rounds} rounds '
                        'without converging. Last critic run returned '
                        '`blocked`; no patch applied on the final round '
                        'so the staged SOW matches the report you see.'
                    ),
                )
                return

            logger.info(
                'quality_loop_invoking_revision',
                round=round_number,
                finding_count=len(report.get('findings', []) or []),
            )
            async for event in reviser.run_async(ctx):
                yield event

        # Defensive: the loop body must return before this point (the
        # blocked branch above handles the last iteration explicitly).
        yield self._emit_result(
            ctx,
            status='unexpected_status',
            rounds_used=self.max_rounds,
            final_report=ctx.session.state.get(STATE_VALIDATION_RESULT) or {},
            observed_status=last_status,
            message='Quality loop fell through without emitting a result.',
        )

    def _emit_result(
        self,
        ctx: InvocationContext,
        *,
        status: LoopStatus,
        rounds_used: int,
        final_report: dict[str, Any],
        observed_status: Optional[str] = None,
        message: Optional[str] = None,
    ) -> Event:
        """Build the terminal event that publishes the loop result.

        State is written through both channels:
        - ``ctx.session.state[KEY] = payload`` so any downstream agent
          inside the same invocation sees the update immediately.
        - ``EventActions.state_delta`` so the session service persists
          the change. Mirrors the pattern used by
          ``ValidationAssemblerAgent``.

        The terminal event always carries a ``content`` part — when
        wrapped in an ``AgentTool``, the caller's tool response is built
        from the agent's events, so an empty content can leave the root
        without an explicit signal that the loop finished. Including a
        compact JSON envelope guarantees the root sees the outcome both
        in the tool result and in state, regardless of how the runtime
        composes the AgentTool response.
        """
        payload: dict[str, Any] = {
            'status': status,
            'rounds_used': rounds_used,
            'final_report': final_report,
        }
        if observed_status is not None:
            payload['observed_status'] = observed_status

        ctx.session.state[QUALITY_LOOP_RESULT_KEY] = payload

        logger.info(
            'quality_loop_result',
            status=status,
            rounds_used=rounds_used,
            has_message=bool(message),
        )

        # Compact summary for the AgentTool response — full report stays
        # in state to avoid burning tokens on the root's context.
        content_payload = {
            'status': status,
            'rounds_used': rounds_used,
            'summary': (final_report or {}).get('summary', ''),
            'blocking_findings': (final_report or {}).get('blocker_count', 0),
            'state_key': QUALITY_LOOP_RESULT_KEY,
        }
        if observed_status is not None:
            content_payload['observed_status'] = observed_status
        if message:
            content_payload['message'] = message

        text = json.dumps(content_payload, ensure_ascii=False)
        return Event(
            invocation_id=ctx.invocation_id,
            author=self.name,
            branch=ctx.branch,
            content=types.Content(
                role='model',
                parts=[types.Part.from_text(text=text)],
            ),
            actions=EventActions(state_delta={QUALITY_LOOP_RESULT_KEY: payload}),
        )


sow_quality_loop = QualityLoopAgent(
    name='sow_quality_loop',
    description=(
        'Validates the staged SOW and applies surgical patches until the '
        'critic returns `passed`, escalates `needs_human_review`, or the '
        'loop exhausts its round budget. Reads `state[app:sow:current]` + '
        '`state[app:validation_result]`; writes the terminal outcome to '
        '`state[app:sow:quality_loop_result]`.'
    ),
    sub_agents=[validation_critic, revision_agent],
)
