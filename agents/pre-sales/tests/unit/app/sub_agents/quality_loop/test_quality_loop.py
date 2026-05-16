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
from google.adk.events import Event, EventActions

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


# ---------------------------------------------------------------------------
# F-06: state_delta-only critic — loop must read what production writes
# ---------------------------------------------------------------------------


class StateDeltaOnlyCritic(BaseAgent):
    """Critic stub that ONLY emits ``EventActions.state_delta``.

    Production sub-agents inside ``validation_critic`` write to
    ``ctx.session.state`` directly AND emit ``state_delta``; either
    channel alone would suffice in production because the ADK runner
    applies ``state_delta`` to the live session state when it consumes
    yielded events. But the QualityLoopAgent reads
    ``ctx.session.state.get(STATE_VALIDATION_RESULT)`` between sub-agent
    invocations — outside of the runner's processing loop. If the loop
    does not itself apply ``state_delta`` from yielded sub-agent events,
    the read returns ``None`` and the loop terminates with
    ``unexpected_status`` even though the critic produced a valid
    report. This stub exercises the state_delta-only path and pins the
    loop's read-side guarantee.
    """

    config_type: ClassVar[type[BaseAgentConfig]] = BaseAgentConfig

    statuses: List[str] = []
    calls: int = 0

    async def run_async(self, ctx) -> AsyncGenerator[Event, None]:  # type: ignore[override]
        idx = min(self.calls, len(self.statuses) - 1) if self.statuses else 0
        status = self.statuses[idx] if self.statuses else 'passed'
        self.calls += 1

        report = {
            'overall_status': status,
            'summary': f'round {self.calls} produced {status}',
            'next_action': '...',
            'findings': [],
        }
        # NOTE: deliberately NO direct ``ctx.session.state[KEY] = report``
        # write here. The whole point of this fixture is to verify that
        # the QualityLoopAgent applies state_delta itself, exactly as the
        # production ADK runner would on the event flowing back up.
        yield Event(
            invocation_id='test-invocation',
            author='state_delta_only_critic',
            branch=None,
            actions=EventActions(state_delta={STATE_VALIDATION_RESULT: report}),
        )


def _build_state_delta_only_loop(
    *,
    statuses: List[str],
    max_rounds: int = 5,
) -> tuple[QualityLoopAgent, StateDeltaOnlyCritic, FakeReviser]:
    critic = StateDeltaOnlyCritic(
        name='state_delta_only_critic', statuses=statuses
    )
    reviser = FakeReviser(name='fake_reviser')
    loop = QualityLoopAgent(
        name='sow_quality_loop',
        description='test',
        sub_agents=[critic, reviser],
        max_rounds=max_rounds,
    )
    return loop, critic, reviser


class TestStateDeltaOnlyCritic:
    """Production sub-agents are allowed to write state ONLY via the
    canonical ``EventActions.state_delta`` channel. The QualityLoopAgent
    must read what they wrote regardless of whether they also mirrored
    it into ``ctx.session.state`` directly — otherwise the loop would
    couple itself to an implementation detail of the critic's helpers.
    """

    async def test_passed_via_state_delta_short_circuits(self):
        loop, critic, reviser = _build_state_delta_only_loop(
            statuses=['passed']
        )
        ctx = _fake_ctx()

        await _run_loop(loop, ctx)

        assert critic.calls == 1
        assert reviser.calls == 0, (
            'loop must see the state_delta payload and short-circuit on '
            'passed; running revision means the loop misread the report.'
        )
        result = ctx.session.state[QUALITY_LOOP_RESULT_KEY]
        assert result['status'] == 'passed'
        assert result['rounds_used'] == 1

    async def test_blocked_then_passed_runs_revision_once(self):
        loop, critic, reviser = _build_state_delta_only_loop(
            statuses=['blocked', 'passed']
        )
        ctx = _fake_ctx()

        await _run_loop(loop, ctx)

        assert critic.calls == 2
        assert reviser.calls == 1
        assert ctx.session.state[QUALITY_LOOP_RESULT_KEY]['status'] == 'passed'

    async def test_loop_mirrors_state_delta_into_session_state(self):
        """Direct contract: after each critic run the loop's session
        state must reflect the latest report written via state_delta."""
        loop, critic, _ = _build_state_delta_only_loop(statuses=['passed'])
        ctx = _fake_ctx()

        await _run_loop(loop, ctx)

        # The critic only emitted state_delta — but the loop must have
        # applied it so the report is queryable through session.state
        # exactly like production reads would do.
        report = ctx.session.state.get(STATE_VALIDATION_RESULT)
        assert report is not None
        assert report['overall_status'] == 'passed'
        assert report['summary'] == 'round 1 produced passed'

    async def test_needs_human_review_via_state_delta_terminates(self):
        loop, _, reviser = _build_state_delta_only_loop(
            statuses=['needs_human_review']
        )
        ctx = _fake_ctx()

        await _run_loop(loop, ctx)

        assert reviser.calls == 0
        result = ctx.session.state[QUALITY_LOOP_RESULT_KEY]
        assert result['status'] == 'needs_human_review'


class TestApplyStateDeltaHelper:
    """Direct coverage of the helper so the contract is testable in
    isolation, independent of the critic/reviser stubs."""

    def test_empty_state_delta_is_noop(self):
        ctx = _fake_ctx()
        event = Event(
            invocation_id='t',
            author='x',
            branch=None,
            actions=EventActions(),
        )

        QualityLoopAgent._apply_state_delta(ctx, event)

        assert ctx.session.state == {}

    def test_applies_every_key_in_delta(self):
        ctx = _fake_ctx()
        event = Event(
            invocation_id='t',
            author='x',
            branch=None,
            actions=EventActions(
                state_delta={
                    'app:foo': 1,
                    'app:bar': {'nested': True},
                }
            ),
        )

        QualityLoopAgent._apply_state_delta(ctx, event)

        assert ctx.session.state['app:foo'] == 1
        assert ctx.session.state['app:bar'] == {'nested': True}

    def test_idempotent_when_runner_already_applied(self):
        """The helper may be called for an event the ADK runner will
        also process later; re-applying must overwrite with the same
        value (idempotent), not crash or accumulate."""
        ctx = _fake_ctx()
        ctx.session.state['app:foo'] = 'stale'
        event = Event(
            invocation_id='t',
            author='x',
            branch=None,
            actions=EventActions(state_delta={'app:foo': 'fresh'}),
        )

        QualityLoopAgent._apply_state_delta(ctx, event)
        QualityLoopAgent._apply_state_delta(ctx, event)  # second time

        assert ctx.session.state['app:foo'] == 'fresh'
