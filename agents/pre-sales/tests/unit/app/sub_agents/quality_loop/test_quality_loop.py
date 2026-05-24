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
    STATE_LAST_LOOP_HASH,
    QualityLoopAgent,
)
from app.sub_agents.validation.schema import STATE_SOW, STATE_VALIDATION_RESULT


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
    blocker_counts: List[int] = []
    major_counts: List[int] = []
    # Per-round churn scripted by tests that exercise the no_progress
    # detector. Defaults to 0 so all pre-existing tests stay unaffected
    # (round-1 reports always carry zero churn by the aggregator's
    # construction, so 0 is also the production default).
    new_blocking_counts: List[int] = []
    resolved_blocking_counts: List[int] = []

    async def run_async(self, ctx) -> AsyncGenerator[Event, None]:  # type: ignore[override]
        idx = min(self.calls, len(self.statuses) - 1) if self.statuses else 0
        status = self.statuses[idx] if self.statuses else 'passed'
        # Per-round severity counts default to 0 when not scripted so
        # existing tests that ignore the envelope severity surface stay
        # unaffected.
        blocker = (
            self.blocker_counts[idx]
            if self.blocker_counts and idx < len(self.blocker_counts)
            else 0
        )
        major = (
            self.major_counts[idx]
            if self.major_counts and idx < len(self.major_counts)
            else 0
        )
        new_blocking = (
            self.new_blocking_counts[idx]
            if self.new_blocking_counts and idx < len(self.new_blocking_counts)
            else 0
        )
        resolved_blocking = (
            self.resolved_blocking_counts[idx]
            if self.resolved_blocking_counts
            and idx < len(self.resolved_blocking_counts)
            else 0
        )
        self.calls += 1

        ctx.session.state[STATE_VALIDATION_RESULT] = {
            'overall_status': status,
            'summary': f'round {self.calls} produced {status}',
            'next_action': '...',
            'findings': [],
            'blocker_count': blocker,
            'major_count': major,
            'new_blocking_finding_count': new_blocking,
            'resolved_blocking_finding_count': resolved_blocking,
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
    blocker_counts: List[int] | None = None,
    major_counts: List[int] | None = None,
    new_blocking_counts: List[int] | None = None,
    resolved_blocking_counts: List[int] | None = None,
) -> tuple[QualityLoopAgent, FakeCritic, FakeReviser]:
    critic = FakeCritic(
        name='fake_critic',
        statuses=critic_statuses,
        blocker_counts=blocker_counts or [],
        major_counts=major_counts or [],
        new_blocking_counts=new_blocking_counts or [],
        resolved_blocking_counts=resolved_blocking_counts or [],
    )
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
# Path 5b: no-progress detection — diagnostic stop when reviser churns
#
# The aggregator publishes ``new_blocking_finding_count`` and
# ``resolved_blocking_finding_count`` per round. When ``new >= resolved``
# for ``NO_PROGRESS_WINDOW`` consecutive rounds (currently 2), the loop
# stops with the TECHNICAL ``no_progress`` status — distinct from
# ``needs_human_review`` so the root prompt can frame it as automatic
# non-convergence rather than dumping every churned finding on the user.
#
# Round 1 reports always carry zero churn (by the aggregator's
# construction — no prior round to diff against), so the detector skips
# round 1 and only starts comparing from round 2 onwards. Tests below
# pin both halves of that contract.
# ---------------------------------------------------------------------------


class TestNoProgressDetection:
    @staticmethod
    def _envelope_body(events: list[Event]) -> dict:
        import json as _json
        terminal = _terminal_event(events)
        return _json.loads(terminal.content.parts[0].text)

    async def test_two_consecutive_non_progress_rounds_trigger_stop(self):
        """Round 1: 0/0 (skipped). Round 2: new=3, resolved=1 → counter=1.
        Round 3: new=2, resolved=2 → counter=2 → no_progress fires."""
        loop, critic, reviser = _build_loop(
            critic_statuses=['blocked', 'blocked', 'blocked', 'blocked'],
            max_rounds=5,
            # Round 1 churn is meaningless (no prior round), but the
            # aggregator emits zeros in production — mirror that here.
            new_blocking_counts=[0, 3, 2, 0],
            resolved_blocking_counts=[0, 1, 2, 0],
        )
        ctx = _fake_ctx()

        await _run_loop(loop, ctx)

        result = ctx.session.state[QUALITY_LOOP_RESULT_KEY]
        assert result['status'] == 'no_progress'
        # The loop stopped at round 3 (the second consecutive non-progress
        # round). Reviser ran twice (after rounds 1 and 2); the round-3
        # critic exposed the second non-progress signal, so revision
        # MUST NOT run a third time.
        assert critic.calls == 3
        assert reviser.calls == 2
        assert result['rounds_used'] == 3

    async def test_progress_in_between_resets_the_counter(self):
        """Counter must track *consecutive* non-progress, not lifetime.
        Round 2 churns (counter=1) but round 3 makes real progress
        (counter resets to 0). The next two churned rounds would be
        needed to trigger no_progress — exhaustion happens first."""
        loop, critic, _ = _build_loop(
            # Round 1: blocked, churn=0/0 (skipped)
            # Round 2: blocked, new=3, resolved=1 → counter=1
            # Round 3: blocked, new=1, resolved=3 → counter RESET to 0
            # Round 4: blocked, new=2, resolved=0 → counter=1
            # Round 5: blocked AND last round → exhausted wins
            critic_statuses=['blocked'] * 5,
            max_rounds=5,
            new_blocking_counts=[0, 3, 1, 2, 1],
            resolved_blocking_counts=[0, 1, 3, 0, 0],
        )
        ctx = _fake_ctx()

        await _run_loop(loop, ctx)

        result = ctx.session.state[QUALITY_LOOP_RESULT_KEY]
        # Exhausted, not no_progress — the counter reset on round 3
        # prevented the window from closing.
        assert result['status'] == 'exhausted'
        assert critic.calls == 5

    async def test_round_one_alone_cannot_trigger_no_progress(self):
        """Even when round 1 *technically* satisfies ``new >= resolved``
        (a counter-intuitive 0 >= 0), the detector must NOT increment on
        round 1. Otherwise a single blocked round could close the window
        on round 2 — too aggressive."""
        loop, critic, reviser = _build_loop(
            critic_statuses=['blocked', 'passed'],
            max_rounds=5,
            new_blocking_counts=[0, 0],
            resolved_blocking_counts=[0, 5],
        )
        ctx = _fake_ctx()

        await _run_loop(loop, ctx)

        result = ctx.session.state[QUALITY_LOOP_RESULT_KEY]
        # Round 1 blocked → reviser runs → round 2 passed → done. No
        # no_progress firing because round 1 churn never counts.
        assert result['status'] == 'passed'
        assert critic.calls == 2
        assert reviser.calls == 1

    async def test_no_progress_status_is_distinct_from_needs_human_review(self):
        """The status must surface as the literal ``'no_progress'`` so
        the root prompt's distinct branch fires. Mapping it to
        ``needs_human_review`` would re-introduce the over-escalation
        pattern this commit was designed to kill."""
        loop, _, _ = _build_loop(
            critic_statuses=['blocked'] * 4,
            max_rounds=5,
            new_blocking_counts=[0, 4, 3, 0],
            resolved_blocking_counts=[0, 2, 1, 0],
        )
        ctx = _fake_ctx()

        await _run_loop(loop, ctx)

        result = ctx.session.state[QUALITY_LOOP_RESULT_KEY]
        assert result['status'] == 'no_progress'
        assert result['status'] != 'needs_human_review', (
            'no_progress must remain a distinct technical status; '
            'collapsing it into needs_human_review re-introduces the '
            'over-escalation pattern.'
        )

    async def test_no_progress_envelope_carries_diagnostic_message(self):
        """The user-visible message must frame the stop as a technical
        non-convergence, not a user decision. The literal substring is
        the anchor the root prompt's branch keys on."""
        loop, _, _ = _build_loop(
            critic_statuses=['blocked'] * 3,
            max_rounds=5,
            new_blocking_counts=[0, 5, 4],
            resolved_blocking_counts=[0, 3, 2],
        )
        ctx = _fake_ctx()

        events = await _run_loop(loop, ctx)
        body = self._envelope_body(events)

        assert body['status'] == 'no_progress'
        # The compact envelope must carry a ``message`` so the root sees
        # the diagnostic without having to read full state.
        assert 'message' in body
        assert 'non-convergence' in body['message'].lower() or 'converge' in body['message'].lower()

    async def test_first_signal_alone_does_not_trigger(self):
        """A single non-progress round (round 2) must not close the
        window — we require TWO consecutive rounds. This guards against
        a single round whose patch exposes a previously masked defect
        from being misread as systemic churn."""
        loop, critic, reviser = _build_loop(
            critic_statuses=['blocked', 'blocked', 'passed'],
            max_rounds=5,
            new_blocking_counts=[0, 2, 0],
            resolved_blocking_counts=[0, 1, 2],
        )
        ctx = _fake_ctx()

        await _run_loop(loop, ctx)

        result = ctx.session.state[QUALITY_LOOP_RESULT_KEY]
        # Round 2 ticked the counter to 1, round 3 passed → loop ended
        # cleanly. No no_progress because the window needs 2 consecutive
        # signals and round 3 was not blocked at all.
        assert result['status'] == 'passed'
        assert critic.calls == 3


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


# ---------------------------------------------------------------------------
# Envelope severity surface — blocker_count + major_count + blocking_total
#
# Production incident: the QualityLoopAgent's compact JSON envelope used
# to expose ``blocking_findings`` populated from ``final_report.blocker_count``.
# The aggregator's gate, however, treats BOTH ``BLOCKER`` and ``MAJOR``
# as blocking (see ``_is_blocking_finding``). So a report with ten MAJOR
# findings and zero BLOCKERs surfaced as ``blocking_findings: 0`` while
# the summary text mentioned the ten MAJORs — the root LLM had no
# reliable single number to branch on.
#
# The envelope now exposes the three numbers the root might need:
# ``blocker_count``, ``major_count``, and the explicit
# ``blocking_total = blocker_count + major_count`` (the number the gate
# actually used). These tests pin both the names AND the math so a future
# rename or arithmetic mistake regresses loudly.
# ---------------------------------------------------------------------------


class TestEnvelopeSeveritySurface:
    """The JSON in ``terminal.content.parts[0].text`` is the AgentTool's
    response back to the root — every field it carries must be both
    well-named and arithmetically correct."""

    @staticmethod
    def _body(events: list[Event]) -> dict:
        import json as _json
        terminal = _terminal_event(events)
        return _json.loads(terminal.content.parts[0].text)

    async def test_passed_envelope_exposes_three_severity_keys(self):
        loop, _, _ = _build_loop(
            critic_statuses=['passed'],
            blocker_counts=[0],
            major_counts=[0],
        )
        ctx = _fake_ctx()

        events = await _run_loop(loop, ctx)
        body = self._body(events)

        # All three keys present, even when the run is clean — the root
        # branch logic should never have to defaulting.
        assert body['blocker_count'] == 0
        assert body['major_count'] == 0
        assert body['blocking_total'] == 0

    async def test_exhausted_envelope_sums_blocker_and_major(self):
        """The original failure mode: 10 MAJORs, 0 BLOCKERs.
        ``blocking_total`` must equal 10, not 0."""
        loop, _, _ = _build_loop(
            critic_statuses=['blocked'] * 3,
            max_rounds=3,
            blocker_counts=[0, 0, 0],
            major_counts=[8, 9, 10],
        )
        ctx = _fake_ctx()

        events = await _run_loop(loop, ctx)
        body = self._body(events)

        assert body['status'] == 'exhausted'
        assert body['blocker_count'] == 0
        assert body['major_count'] == 10
        assert body['blocking_total'] == 10, (
            'blocking_total must reflect what the gate actually used '
            '(BLOCKER + MAJOR), not just BLOCKER. Surfacing 0 here while '
            'the summary mentions 10 MAJORs is the production bug this '
            'commit kills.'
        )

    @pytest.mark.parametrize(
        ('blockers', 'majors', 'expected_total'),
        [
            (3, 5, 8),
            (1, 0, 1),
            (0, 1, 1),
            (7, 2, 9),
        ],
    )
    async def test_blocking_total_is_blocker_plus_major(
        self, blockers, majors, expected_total
    ):
        loop, _, _ = _build_loop(
            critic_statuses=['needs_human_review'],
            blocker_counts=[blockers],
            major_counts=[majors],
        )
        ctx = _fake_ctx()

        events = await _run_loop(loop, ctx)
        body = self._body(events)

        assert body['blocker_count'] == blockers
        assert body['major_count'] == majors
        assert body['blocking_total'] == expected_total

    async def test_envelope_does_not_carry_legacy_blocking_findings_name(self):
        """The old key ``blocking_findings`` is dropped — keeping it as
        an alias would invite a future reader to read the wrong number."""
        loop, _, _ = _build_loop(
            critic_statuses=['blocked'],
            max_rounds=1,
            blocker_counts=[0],
            major_counts=[4],
        )
        ctx = _fake_ctx()

        events = await _run_loop(loop, ctx)
        body = self._body(events)

        assert 'blocking_findings' not in body, (
            'Legacy name must be removed; readers should consume the '
            'three replacement keys.'
        )


# ---------------------------------------------------------------------------
# F-05 — anti-thrashing cache via SOW hash
#
# The loop short-circuits when the staged SOW hash matches the hash
# from the previous loop's terminal write. The cache key is the SOW
# *at termination time* (post-revision) so a re-invocation on that
# exact payload returns the cached result instead of burning a fresh
# critic budget. ``stage_sow`` overwriting STATE_SOW with new content
# changes the hash → cache misses naturally.
# ---------------------------------------------------------------------------


def _ctx_with_sow(sow: dict) -> MagicMock:
    ctx = _fake_ctx()
    ctx.session.state[STATE_SOW] = sow
    return ctx


class TestAntiThrashingCache:
    async def test_first_run_writes_terminal_hash(self):
        loop, _, _ = _build_loop(critic_statuses=['passed'])
        ctx = _ctx_with_sow({'project_title': 'P', 'fr': []})

        await _run_loop(loop, ctx)

        cached_hash = ctx.session.state.get(STATE_LAST_LOOP_HASH)
        assert cached_hash is not None
        # The cached hash must match the SOW currently in state, since
        # nothing else patched it during this passed-on-round-1 run.
        from app.tools.sow._sow_helpers import sow_data_hash
        assert cached_hash == sow_data_hash(ctx.session.state[STATE_SOW])

    async def test_second_run_on_same_sow_hits_cache_and_skips_critic(
        self,
    ):
        loop, critic, reviser = _build_loop(
            critic_statuses=['passed', 'blocked'],  # would fail if rerun
            max_rounds=3,
        )
        ctx = _ctx_with_sow({'project_title': 'P', 'fr': []})

        await _run_loop(loop, ctx)
        first_critic_calls = critic.calls
        first_result = ctx.session.state[QUALITY_LOOP_RESULT_KEY]

        # Re-invoke with the SOW untouched — critic must NOT run again.
        events = await _run_loop(loop, ctx)

        assert critic.calls == first_critic_calls, (
            'Cache hit must skip the critic — re-running it on the same '
            'staged SOW is the whole thrashing scenario the cache prevents.'
        )
        assert reviser.calls == 0
        # The cached envelope is re-emitted; the in-state result payload
        # equals the first run's terminal payload (status, rounds_used,
        # final_report all stable).
        assert ctx.session.state[QUALITY_LOOP_RESULT_KEY] == first_result
        # The terminal event must still carry state_delta + content so
        # the AgentTool caller sees a consistent envelope.
        terminal = _terminal_event(events)
        assert terminal.content is not None
        import json as _json
        body = _json.loads(terminal.content.parts[0].text)
        assert body['cached'] is True
        assert body['status'] == 'passed'
        # The cached envelope must match the fresh-run severity surface
        # so the root LLM cannot tell whether a tool result was cached
        # apart from the explicit ``cached`` flag. Surfacing different
        # keys on cache hits vs misses would force the root to branch
        # twice on every loop result — a regression we deliberately avoid.
        assert 'blocker_count' in body
        assert 'major_count' in body
        assert 'blocking_total' in body
        assert 'blocking_findings' not in body

    async def test_sow_change_invalidates_cache(self):
        loop, critic, _ = _build_loop(
            critic_statuses=['passed', 'passed'],
            max_rounds=3,
        )
        ctx = _ctx_with_sow({'project_title': 'P', 'fr': []})

        await _run_loop(loop, ctx)
        assert critic.calls == 1

        # Simulate stage_sow writing a different payload (the cache key
        # the loop wrote is now stale).
        ctx.session.state[STATE_SOW] = {'project_title': 'P', 'fr': ['FR-01']}

        await _run_loop(loop, ctx)

        assert critic.calls == 2, (
            'Different SOW hash MUST miss the cache and re-run the critic.'
        )

    async def test_cache_miss_when_result_key_absent(self):
        """If a previous loop crashed before writing the result, the
        cache key alone is not enough — the loop must re-run."""
        loop, critic, _ = _build_loop(critic_statuses=['passed'])
        ctx = _ctx_with_sow({'project_title': 'P'})
        # Seed: hash present, but result missing (crash midway scenario).
        from app.tools.sow._sow_helpers import sow_data_hash
        ctx.session.state[STATE_LAST_LOOP_HASH] = sow_data_hash(
            ctx.session.state[STATE_SOW]
        )
        # Deliberately do NOT seed QUALITY_LOOP_RESULT_KEY.

        await _run_loop(loop, ctx)

        # Critic ran because cache check failed on the missing-result side.
        assert critic.calls == 1
        # And the loop completed cleanly — result key now populated.
        assert (
            ctx.session.state[QUALITY_LOOP_RESULT_KEY]['status'] == 'passed'
        )

    async def test_no_sow_in_state_skips_cache_check(self):
        """If STATE_SOW is absent the loop simply runs (and the critic
        will surface the missing-payload deterministic error). The cache
        must not crash on a None payload."""
        loop, critic, _ = _build_loop(critic_statuses=['passed'])
        ctx = _fake_ctx()  # no STATE_SOW

        await _run_loop(loop, ctx)

        assert critic.calls == 1
        # And nothing was written to the cache key (would mislead a
        # later run that DOES have a SOW).
        assert STATE_LAST_LOOP_HASH not in ctx.session.state

    async def test_cache_hit_does_not_invoke_reviser(self):
        loop, _, reviser = _build_loop(
            critic_statuses=['blocked', 'passed'],  # would call reviser
            max_rounds=3,
        )
        ctx = _ctx_with_sow({'project_title': 'P'})

        await _run_loop(loop, ctx)
        reviser_calls_after_first = reviser.calls

        # Replay — reviser must not be called via the cache path.
        await _run_loop(loop, ctx)

        assert reviser.calls == reviser_calls_after_first

    async def test_post_revision_terminal_hash_matches_current_sow(self):
        """Critical contract: STATE_LAST_LOOP_HASH must equal the hash
        of the SOW as it stands at termination time, NOT the entry
        hash. So a follow-up call on the patched SOW hits the cache."""
        loop = QualityLoopAgent(
            name='loop',
            description='test',
            sub_agents=[
                _CriticThatTouchesSow(
                    name='critic',
                    statuses=['blocked', 'passed'],
                ),
                _ReviserThatPatches(name='reviser'),
            ],
            max_rounds=3,
        )
        ctx = _ctx_with_sow({'project_title': 'P', 'fr': ['FR-01']})

        await _run_loop(loop, ctx)

        from app.tools.sow._sow_helpers import sow_data_hash
        terminal_sow = ctx.session.state[STATE_SOW]
        # Reviser patched the SOW between critic rounds — final hash
        # must reflect that, not the entry hash.
        assert ctx.session.state[STATE_LAST_LOOP_HASH] == sow_data_hash(
            terminal_sow
        )


class _CriticThatTouchesSow(BaseAgent):
    """Variant of FakeCritic for the terminal-hash test — does not
    mutate the SOW itself; just emits the status as a report write."""

    config_type: ClassVar[type[BaseAgentConfig]] = BaseAgentConfig
    statuses: List[str] = []
    calls: int = 0

    async def run_async(self, ctx) -> AsyncGenerator[Event, None]:  # type: ignore[override]
        idx = min(self.calls, len(self.statuses) - 1)
        status = self.statuses[idx]
        self.calls += 1
        ctx.session.state[STATE_VALIDATION_RESULT] = {
            'overall_status': status,
            'summary': '',
            'next_action': '',
            'findings': [],
        }
        if False:  # pragma: no cover
            yield  # type: ignore[unreachable]


class _ReviserThatPatches(BaseAgent):
    """Reviser stub that patches STATE_SOW so the terminal hash
    differs from the entry hash — exercises the "post-revision cache
    key" contract."""

    config_type: ClassVar[type[BaseAgentConfig]] = BaseAgentConfig

    async def run_async(self, ctx) -> AsyncGenerator[Event, None]:  # type: ignore[override]
        sow = ctx.session.state.get(STATE_SOW) or {}
        patched = dict(sow)
        patched.setdefault('fr', []).append('FR-02-added-by-reviser')
        ctx.session.state[STATE_SOW] = patched
        if False:  # pragma: no cover
            yield  # type: ignore[unreachable]
