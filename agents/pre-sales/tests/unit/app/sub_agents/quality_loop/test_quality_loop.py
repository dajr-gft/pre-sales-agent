"""Unit tests for the ``QualityLoopAgent``.

The loop's contract is enumerated in the architecture plan v2.1 §11:

- critic returns ``passed`` on round 1 → revision MUST NOT run; result
  status = ``passed``; rounds_used == 1.
- critic returns ``blocked`` then ``passed`` → revision runs once;
  result status = ``passed``; rounds_used == 2.
- critic returns ``needs_human_review`` → revision MUST NOT run; result
  status = ``needs_human_review``.
- critic returns unexpected status → revision MUST NOT run; result
  status = ``unexpected_status``; ``observed_status`` carries the value.
- ``max_rounds`` rounds without converging → result status =
  ``exhausted``.
- Every terminal event carries ``state_delta`` with the canonical
  result key.

The tests stub the critic and revision sub-agents with deterministic
async generators so we exercise only the loop's branching logic.
"""

from __future__ import annotations

from typing import AsyncGenerator, ClassVar, List, Optional
from unittest.mock import MagicMock

import pytest
from google.adk.agents import BaseAgent
from google.adk.agents.base_agent_config import BaseAgentConfig
from google.adk.events import Event

from app.sub_agents.quality_loop.agent import (
    QUALITY_LOOP_RESULT_KEY,
    QualityLoopAgent,
)
from app.sub_agents.validation.schema import STATE_VALIDATION_RESULT


# ---------------------------------------------------------------------------
# Fakes — minimal BaseAgent stubs the loop can iterate over.
# ---------------------------------------------------------------------------


class FakeCritic(BaseAgent):
    """Stub that writes a scripted ValidationReport per call.

    ``run_async`` is overridden (not ``_run_async_impl``) to bypass the
    ADK plumbing — plugin manager hooks, before/after callbacks, span
    tracing — that requires a fully-wired ``InvocationContext``. We
    only care about the loop's branching logic here.
    """

    config_type: ClassVar[type[BaseAgentConfig]] = BaseAgentConfig

    statuses: List[str] = []
    calls: int = 0

    async def run_async(self, ctx) -> AsyncGenerator[Event, None]:  # type: ignore[override]
        idx = min(self.calls, len(self.statuses) - 1) if self.statuses else 0
        status = self.statuses[idx] if self.statuses else 'passed'
        self.calls += 1

        ctx.session.state[STATE_VALIDATION_RESULT] = {
            'overall_status': status,
            'summary': f'round {self.calls} produced {status}',
            'next_action': '...',
            'findings': [],
        }
        if False:  # pragma: no cover — keep this an async generator
            yield  # type: ignore[unreachable]


class FakeReviser(BaseAgent):
    """Stub that records whether it was invoked, without mutating state."""

    config_type: ClassVar[type[BaseAgentConfig]] = BaseAgentConfig

    calls: int = 0

    async def run_async(self, ctx) -> AsyncGenerator[Event, None]:  # type: ignore[override]
        self.calls += 1
        if False:  # pragma: no cover
            yield  # type: ignore[unreachable]


def _fake_ctx() -> MagicMock:
    """Minimum InvocationContext stand-in the loop reads from."""
    ctx = MagicMock(name='InvocationContext')
    ctx.session.state = {}
    ctx.invocation_id = 'test-invocation'
    ctx.branch = None
    ctx.end_invocation = False
    return ctx


def _build_loop(
    *,
    critic_statuses: List[str],
    max_rounds: int = 5,
) -> tuple[QualityLoopAgent, FakeCritic, FakeReviser]:
    critic = FakeCritic(name='fake_critic', statuses=critic_statuses)
    reviser = FakeReviser(name='fake_reviser')
    loop = QualityLoopAgent(
        name='sow_quality_loop',
        description='test',
        sub_agents=[critic, reviser],
        max_rounds=max_rounds,
    )
    return loop, critic, reviser


async def _run_loop(loop: QualityLoopAgent, ctx) -> list[Event]:
    events: list[Event] = []
    async for event in loop._run_async_impl(ctx):
        events.append(event)
    return events


def _terminal_event(events: list[Event]) -> Event:
    """The loop yields exactly one Event with quality_loop_result in state_delta."""
    finals = [
        e for e in events
        if e.actions and QUALITY_LOOP_RESULT_KEY in (e.actions.state_delta or {})
    ]
    assert len(finals) == 1, (
        f'Expected exactly one terminal event; got {len(finals)}.'
    )
    return finals[0]


# ---------------------------------------------------------------------------
# Path 1: critic passes on round 1 — revision MUST NOT run
# ---------------------------------------------------------------------------


class TestPassedShortCircuit:
    async def test_passed_in_round_one_skips_revision(self):
        loop, critic, reviser = _build_loop(critic_statuses=['passed'])
        ctx = _fake_ctx()

        await _run_loop(loop, ctx)

        assert critic.calls == 1
        assert reviser.calls == 0, (
            'revision MUST NOT run when critic returned passed'
        )

    async def test_passed_writes_state_and_emits_event(self):
        loop, _, _ = _build_loop(critic_statuses=['passed'])
        ctx = _fake_ctx()

        events = await _run_loop(loop, ctx)

        result = ctx.session.state[QUALITY_LOOP_RESULT_KEY]
        assert result['status'] == 'passed'
        assert result['rounds_used'] == 1
        assert result['final_report']['overall_status'] == 'passed'

        terminal = _terminal_event(events)
        assert terminal.actions.state_delta[QUALITY_LOOP_RESULT_KEY] == result

    async def test_passed_terminal_event_has_content(self):
        """Even the happy path must include content — the AgentTool
        response is built from events, so an empty content can leave the
        root without an explicit signal that the loop completed."""
        loop, _, _ = _build_loop(critic_statuses=['passed'])
        ctx = _fake_ctx()

        events = await _run_loop(loop, ctx)

        terminal = _terminal_event(events)
        assert terminal.content is not None
        assert terminal.content.parts
        # Content payload must include status so the root LLM can branch.
        import json as _json
        body = _json.loads(terminal.content.parts[0].text)
        assert body['status'] == 'passed'
        assert body['rounds_used'] == 1


# ---------------------------------------------------------------------------
# Path 2: blocked then passed — revision runs once
# ---------------------------------------------------------------------------


class TestBlockedThenPassed:
    async def test_revision_runs_between_critic_calls(self):
        loop, critic, reviser = _build_loop(
            critic_statuses=['blocked', 'passed'],
        )
        ctx = _fake_ctx()

        await _run_loop(loop, ctx)

        assert critic.calls == 2
        assert reviser.calls == 1, 'revision must run exactly once'

    async def test_final_status_is_passed_with_rounds_used_two(self):
        loop, _, _ = _build_loop(critic_statuses=['blocked', 'passed'])
        ctx = _fake_ctx()

        await _run_loop(loop, ctx)

        result = ctx.session.state[QUALITY_LOOP_RESULT_KEY]
        assert result['status'] == 'passed'
        assert result['rounds_used'] == 2


# ---------------------------------------------------------------------------
# Path 3: needs_human_review — short-circuit, no revision
# ---------------------------------------------------------------------------


class TestNeedsHumanReview:
    async def test_revision_does_not_run(self):
        loop, critic, reviser = _build_loop(
            critic_statuses=['needs_human_review'],
        )
        ctx = _fake_ctx()

        await _run_loop(loop, ctx)

        assert critic.calls == 1
        assert reviser.calls == 0

    async def test_result_carries_status(self):
        loop, _, _ = _build_loop(critic_statuses=['needs_human_review'])
        ctx = _fake_ctx()

        events = await _run_loop(loop, ctx)

        result = ctx.session.state[QUALITY_LOOP_RESULT_KEY]
        assert result['status'] == 'needs_human_review'
        # Terminal event also carries a user-visible message.
        terminal = _terminal_event(events)
        assert terminal.content is not None


# ---------------------------------------------------------------------------
# Path 4: unexpected status — short-circuit with observed_status
# ---------------------------------------------------------------------------


class TestUnexpectedStatus:
    async def test_revision_does_not_run(self):
        loop, critic, reviser = _build_loop(
            critic_statuses=['some_garbage_status'],
        )
        ctx = _fake_ctx()

        await _run_loop(loop, ctx)

        assert critic.calls == 1
        assert reviser.calls == 0

    async def test_observed_status_propagated(self):
        loop, _, _ = _build_loop(critic_statuses=['some_garbage_status'])
        ctx = _fake_ctx()

        await _run_loop(loop, ctx)

        result = ctx.session.state[QUALITY_LOOP_RESULT_KEY]
        assert result['status'] == 'unexpected_status'
        assert result['observed_status'] == 'some_garbage_status'

    async def test_missing_status_treated_as_unexpected(self):
        """Critic that emits a report without overall_status is also unexpected."""
        loop, _, reviser = _build_loop(critic_statuses=[''])
        # Special: empty string ≠ blocked/passed/needs_human_review.
        ctx = _fake_ctx()

        await _run_loop(loop, ctx)

        result = ctx.session.state[QUALITY_LOOP_RESULT_KEY]
        assert result['status'] == 'unexpected_status'
        assert reviser.calls == 0


# ---------------------------------------------------------------------------
# Path 5: max_rounds without converging — exhausted, NO revision on last round
# ---------------------------------------------------------------------------


class TestExhausted:
    async def test_blocked_for_max_rounds_emits_exhausted(self):
        loop, critic, reviser = _build_loop(
            critic_statuses=['blocked'] * 6,
            max_rounds=3,
        )
        ctx = _fake_ctx()

        await _run_loop(loop, ctx)

        assert critic.calls == 3, 'critic runs exactly max_rounds times'
        # Reviser runs after rounds 1 and 2, but MUST NOT run after the
        # last (round 3) critic — otherwise the staged SOW would be
        # patched without a follow-up validation, leaving final_report
        # out of sync with state['app:sow:current'].
        assert reviser.calls == 2, (
            'revision must NOT run on the final round; otherwise the '
            'patched SOW would never be revalidated.'
        )

    async def test_result_has_exhausted_status(self):
        loop, _, _ = _build_loop(
            critic_statuses=['blocked', 'blocked', 'blocked'],
            max_rounds=3,
        )
        ctx = _fake_ctx()

        await _run_loop(loop, ctx)

        result = ctx.session.state[QUALITY_LOOP_RESULT_KEY]
        assert result['status'] == 'exhausted'
        assert result['rounds_used'] == 3

    async def test_exhausted_final_report_is_from_last_critic_run(self):
        """The report attached to `exhausted` must come from the LAST
        critic run (no later patch can have shifted state out of sync)."""
        loop, _, _ = _build_loop(
            critic_statuses=['blocked', 'blocked', 'blocked'],
            max_rounds=3,
        )
        ctx = _fake_ctx()

        await _run_loop(loop, ctx)

        result = ctx.session.state[QUALITY_LOOP_RESULT_KEY]
        assert result['final_report']['summary'] == 'round 3 produced blocked'

    async def test_max_rounds_one_with_blocked_short_circuits(self):
        """Edge case: max_rounds=1 means revision can never run."""
        loop, critic, reviser = _build_loop(
            critic_statuses=['blocked'],
            max_rounds=1,
        )
        ctx = _fake_ctx()

        await _run_loop(loop, ctx)

        assert critic.calls == 1
        assert reviser.calls == 0
        assert ctx.session.state[QUALITY_LOOP_RESULT_KEY]['status'] == 'exhausted'


# ---------------------------------------------------------------------------
# Cross-cutting: terminal event state_delta is the single source of truth
# ---------------------------------------------------------------------------


class TestStateDeltaContract:
    @pytest.mark.parametrize(
        'statuses',
        [
            ['passed'],
            ['blocked', 'passed'],
            ['needs_human_review'],
            ['weird_status'],
            ['blocked', 'blocked', 'blocked'],
        ],
    )
    async def test_terminal_event_carries_state_delta(self, statuses):
        loop, _, _ = _build_loop(
            critic_statuses=statuses,
            max_rounds=3,
        )
        ctx = _fake_ctx()

        events = await _run_loop(loop, ctx)
        terminal = _terminal_event(events)

        delta = terminal.actions.state_delta
        assert QUALITY_LOOP_RESULT_KEY in delta
        # The in-memory state mirror MUST match the state_delta payload —
        # downstream agents inside the same invocation read in-memory,
        # the session service persists the delta. They cannot diverge.
        assert ctx.session.state[QUALITY_LOOP_RESULT_KEY] == delta[QUALITY_LOOP_RESULT_KEY]
