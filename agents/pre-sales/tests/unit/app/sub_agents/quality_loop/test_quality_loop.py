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

from typing import Any, AsyncGenerator, ClassVar, List, Optional
from unittest.mock import MagicMock

import pytest
from google.adk.agents import BaseAgent
from google.adk.agents.base_agent_config import BaseAgentConfig
from google.adk.events import Event, EventActions

from app.sub_agents.quality_loop import agent as quality_loop_agent
from app.sub_agents.quality_loop.agent import (
    DEFAULT_MAX_ROUNDS,
    QUALITY_LOOP_RESULT_KEY,
    STATE_LAST_LOOP_HASH,
    QualityLoopAgent,
    _MAX_ROUNDS_ENV,
    _REVIEW_SECTIONS_BY_STAGE,
    _resolve_max_rounds,
    _review_payload,
)
from app.sub_agents.validation.schema import (
    STATE_SOW,
    STATE_STAGE,
    STATE_VALIDATION_RESULT,
)


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
    max_rounds: int = 3,
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


def _build_loop_default_rounds(
    critic_statuses: List[str],
) -> tuple[QualityLoopAgent, FakeCritic, FakeReviser]:
    """Build a loop WITHOUT passing ``max_rounds`` so the agent's own
    default (resolved from env / ``DEFAULT_MAX_ROUNDS``) is exercised."""
    critic = FakeCritic(name='fake_critic', statuses=critic_statuses)
    reviser = FakeReviser(name='fake_reviser')
    loop = QualityLoopAgent(
        name='sow_quality_loop',
        description='test',
        sub_agents=[critic, reviser],
    )
    return loop, critic, reviser


# ---------------------------------------------------------------------------
# Round budget — default is 3, overridable via env, constructor wins
# ---------------------------------------------------------------------------


class TestMaxRoundsDefault:
    def test_default_constant_is_three(self):
        assert DEFAULT_MAX_ROUNDS == 3

    def test_agent_default_max_rounds_is_three(self, monkeypatch):
        """A loop built with no max_rounds and no env override uses 3."""
        monkeypatch.delenv(_MAX_ROUNDS_ENV, raising=False)
        loop, _, _ = _build_loop_default_rounds(critic_statuses=['passed'])
        assert loop.max_rounds == 3

    async def test_exhausted_uses_default_three_rounds(self, monkeypatch):
        """With no override, an all-blocked critic exhausts at round 3."""
        monkeypatch.delenv(_MAX_ROUNDS_ENV, raising=False)
        loop, critic, _ = _build_loop_default_rounds(
            critic_statuses=['blocked'] * 3,
        )
        ctx = _fake_ctx()

        await _run_loop(loop, ctx)

        result = ctx.session.state[QUALITY_LOOP_RESULT_KEY]
        assert loop.max_rounds == 3
        assert critic.calls == 3, 'critic runs exactly the default 3 rounds'
        assert result['status'] == 'exhausted'
        assert result['rounds_used'] == 3

    @pytest.mark.parametrize('override', [1, 2])
    def test_explicit_constructor_override_still_wins(self, override):
        loop, _, _ = _build_loop(
            critic_statuses=['blocked'] * override, max_rounds=override
        )
        assert loop.max_rounds == override


class TestMaxRoundsEnvVar:
    def test_no_env_uses_default(self, monkeypatch):
        monkeypatch.delenv(_MAX_ROUNDS_ENV, raising=False)
        assert _resolve_max_rounds() == 3

    def test_blank_env_uses_default(self, monkeypatch):
        monkeypatch.setenv(_MAX_ROUNDS_ENV, '   ')
        assert _resolve_max_rounds() == 3

    def test_valid_positive_env_is_used(self, monkeypatch):
        monkeypatch.setenv(_MAX_ROUNDS_ENV, '4')
        assert _resolve_max_rounds() == 4

    def test_invalid_env_falls_back_and_warns(self, monkeypatch):
        monkeypatch.setenv(_MAX_ROUNDS_ENV, 'abc')
        warn = MagicMock()
        monkeypatch.setattr(quality_loop_agent.logger, 'warning', warn)
        assert _resolve_max_rounds() == 3
        assert warn.called, 'an invalid env value must log a warning'

    @pytest.mark.parametrize('bad', ['0', '-2'])
    def test_non_positive_env_falls_back_and_warns(self, monkeypatch, bad):
        monkeypatch.setenv(_MAX_ROUNDS_ENV, bad)
        warn = MagicMock()
        monkeypatch.setattr(quality_loop_agent.logger, 'warning', warn)
        assert _resolve_max_rounds() == 3
        assert warn.called, 'a non-positive env value must log a warning'

    def test_env_applies_to_a_newly_built_agent(self, monkeypatch):
        monkeypatch.setenv(_MAX_ROUNDS_ENV, '4')
        loop, _, _ = _build_loop_default_rounds(critic_statuses=['passed'])
        assert loop.max_rounds == 4

    def test_constructor_override_beats_env(self, monkeypatch):
        monkeypatch.setenv(_MAX_ROUNDS_ENV, '4')
        loop, _, _ = _build_loop(critic_statuses=['passed'], max_rounds=2)
        assert loop.max_rounds == 2


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
    max_rounds: int = 3,
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


# ---------------------------------------------------------------------------
# Repair routing — cross-section findings go to the owning section agent
# instead of the generic reviser
#
# Commit 5 added a per-(skill, category) routing table. The loop
# partitions findings each round, invokes the section agent(s) that own
# the routed findings, re-assembles the SOW between section invocations,
# and only hands mechanical residue to the reviser. The tests below
# cover the partition function in isolation + the loop-level branching
# (section invocation order, residue handling, fallback when the section
# agent is not wired).
# ---------------------------------------------------------------------------


from app.sub_agents.quality_loop.agent import (
    _BUNDLE_ANCHOR_ID_PATTERN,
    _CROSS_SECTION_REPAIR_ROUTES,
    _FIELD_TO_SECTION,
    _SECTION_ORDER,
    _extract_anchor_ids,
    _partition_findings,
    _sections_for_finding,
)
from app.sub_agents._section_agent import STATE_REPAIR_FINDINGS
from app.sub_agents.validation.schema import STATE_STAGE
from app.tools.sow._sow_helpers import sow_data_hash as _sow_data_hash


class TestPartitionFindings:
    """Direct coverage of the partition function."""

    def test_empty_findings_returns_empty(self):
        by_section, mechanical = _partition_findings([], set())
        assert by_section == {}
        assert mechanical == []

    def test_known_route_groups_under_section_name(self):
        f = {
            'skill': 'contradictions',
            'category': 'activities_vs_deliverables',
            'severity': 'MAJOR',
        }
        by_section, mechanical = _partition_findings([f], {'delivery_plan'})
        assert by_section == {'delivery_plan': [f]}
        assert mechanical == []

    def test_no_fields_no_route_falls_to_mechanical(self):
        """A finding with no structural ``fields`` AND no (skill,
        category) entry in the route table is mechanical — the reviser
        is the only fallback. Coverage findings without an explicit
        ``fields`` hint are the canonical example (with a structural
        ``fields`` hint they now route to the owning section, see
        ``TestSectionsForFinding``)."""
        f = {
            'skill': 'coverage',
            'category': 'manifest_item_uncovered',
            'severity': 'MAJOR',
        }
        by_section, mechanical = _partition_findings(
            [f], set(_SECTION_ORDER)
        )
        assert by_section == {}
        assert mechanical == [f]

    def test_route_to_unwired_section_falls_to_mechanical(self):
        """Architecture has a route but if the loop wasn't passed an
        architecture agent, the finding must still get a chance — fall
        back to the reviser instead of stalling."""
        f = {
            'skill': 'contradictions',
            'category': 'architecture_vs_stack',
            'severity': 'MAJOR',
        }
        # ``available_sections`` deliberately excludes 'architecture'.
        by_section, mechanical = _partition_findings(
            [f], {'requirements', 'delivery_plan'}
        )
        assert by_section == {}
        assert mechanical == [f]

    def test_preserves_report_order_within_each_bucket(self):
        f1 = {'skill': 'coverage', 'category': 'manifest_item_uncovered'}
        f2 = {
            'skill': 'contradictions',
            'category': 'timeline_vs_deliverables',
        }
        f3 = {
            'skill': 'contradictions',
            'category': 'activities_vs_deliverables',
        }
        f4 = {'skill': 'disclosures', 'category': 'missing_ai_nondeterminism_disclosure'}
        by_section, mechanical = _partition_findings(
            [f1, f2, f3, f4], {'delivery_plan'}
        )
        # delivery_plan bucket holds f2 then f3 (insertion order).
        assert by_section == {'delivery_plan': [f2, f3]}
        # Mechanical bucket holds f1 and f4 in report order.
        assert mechanical == [f1, f4]

    def test_skips_malformed_finding_entries(self):
        """Non-dict entries (None, strings) must not crash the partition;
        they are dropped silently — the aggregator already normalises
        findings, so this is defensive only."""
        good = {
            'skill': 'contradictions',
            'category': 'activities_vs_deliverables',
        }
        by_section, mechanical = _partition_findings(
            [None, 'not a dict', good], {'delivery_plan'}
        )
        assert by_section == {'delivery_plan': [good]}
        assert mechanical == []

    def test_routing_table_only_lists_known_section_names(self):
        """Every section in the routing table must be in the canonical
        invocation order; otherwise the loop would skip findings routed
        to an unknown section."""
        ordered = set(_SECTION_ORDER)
        for (skill, category), section in _CROSS_SECTION_REPAIR_ROUTES.items():
            assert section in ordered, (
                f'Route ({skill}, {category}) -> {section!r} but '
                f'{section!r} is not in _SECTION_ORDER {_SECTION_ORDER}.'
            )


# ---------------------------------------------------------------------------
# _sections_for_finding — derives target sections from Finding.fields
#
# Opção 4 of the convergence work: a single finding whose ``fields``
# cross multiple sections must be routed to ALL involved sections (in
# Phase Step order) so each one can patch its own side. Without this,
# a contradictions/scope_vs_oos finding with
# ``fields=['out_of_scope', 'deliverables']`` only got patched on the
# scope_boundaries side, leaving deliverables stale and reopening the
# contradiction in the next critic round (the production behaviour the
# reviewer flagged as oscillating 4 → 3 → 5).
# ---------------------------------------------------------------------------


_ALL_SECTIONS = set(_SECTION_ORDER)


class TestSectionsForFinding:
    def test_finding_with_no_fields_and_no_route_is_mechanical(self):
        """A finding with no structural ``fields`` AND no (skill,
        category) entry in the route table has no section to route to
        — the reviser sees it as a last resort."""
        f = {
            'skill': 'coverage',
            'category': 'manifest_item_uncovered',
            'fields': [],
        }
        assert _sections_for_finding(f, _ALL_SECTIONS) == []

    def test_single_section_finding_returns_single_section(self):
        """When all the finding's fields belong to one section, only
        that section runs (no point invoking siblings with nothing to
        patch)."""
        f = {
            'skill': 'contradictions',
            'category': 'activities_vs_deliverables',
            'fields': ['activity_phases', 'deliverables'],
        }
        assert _sections_for_finding(f, _ALL_SECTIONS) == ['delivery_plan']

    def test_cross_bundle_finding_returns_multiple_sections_in_phase_order(self):
        """The motivating case for Opção 4: a contradictions finding
        whose fields cross sections returns ALL involved sections in
        Phase Step order so the downstream section sees the upstream
        patches on the next re-assembly."""
        f = {
            'skill': 'contradictions',
            'category': 'scope_vs_oos',
            'fields': ['out_of_scope', 'deliverables'],
        }
        # delivery_plan is upstream of scope_boundaries in _SECTION_ORDER.
        assert _sections_for_finding(f, _ALL_SECTIONS) == [
            'delivery_plan',
            'scope_boundaries',
        ]

    def test_cross_bundle_order_is_phase_step_not_fields_order(self):
        """Field order in the finding must not affect routing order —
        Phase Step ordering wins so upstream always runs first."""
        # Same fields as the previous test, listed in reverse order.
        f = {
            'skill': 'contradictions',
            'category': 'scope_vs_oos',
            'fields': ['deliverables', 'out_of_scope'],
        }
        assert _sections_for_finding(f, _ALL_SECTIONS) == [
            'delivery_plan',
            'scope_boundaries',
        ]

    def test_three_section_cross_bundle(self):
        """Defensive case: a single finding can touch 3+ sections; all
        of them appear in Phase Step order."""
        f = {
            'skill': 'contradictions',
            'category': 'architecture_vs_stack',
            'fields': [
                'architecture_description',
                'functional_requirements',
                'deliverables',
            ],
        }
        result = _sections_for_finding(f, _ALL_SECTIONS)
        # requirements (A) → delivery_plan (B) → architecture (D)
        assert result == ['requirements', 'delivery_plan', 'architecture']

    def test_unknown_field_is_ignored(self):
        """A field that does not appear in ``_FIELD_TO_SECTION`` is
        silently dropped — it does not pin the finding to a wrong
        section. The legacy default-route fallback still kicks in if
        no fields map to a section at all."""
        f = {
            'skill': 'contradictions',
            'category': 'activities_vs_deliverables',
            'fields': ['unknown_field', 'deliverables'],
        }
        # 'deliverables' is the only mappable field → delivery_plan.
        assert _sections_for_finding(f, _ALL_SECTIONS) == ['delivery_plan']

    def test_empty_fields_falls_back_to_default_route(self):
        """Backward-compat: when the finding lists no fields (or all
        of them are unmapped), use the (skill, category) route from
        ``_CROSS_SECTION_REPAIR_ROUTES``. This preserves the commit-5
        behaviour for findings that don't have field telemetry."""
        f = {
            'skill': 'contradictions',
            'category': 'fr_vs_nfr',
            'fields': [],
        }
        assert _sections_for_finding(f, _ALL_SECTIONS) == ['requirements']

    def test_all_unmapped_fields_falls_back_to_default_route(self):
        f = {
            'skill': 'contradictions',
            'category': 'fr_vs_nfr',
            'fields': ['nonsense_a', 'nonsense_b'],
        }
        assert _sections_for_finding(f, _ALL_SECTIONS) == ['requirements']

    def test_section_not_available_is_dropped_from_target_list(self):
        """When `available_sections` excludes a section that owns one
        of the touched fields, that section is silently dropped — the
        other involved sections still patch their side. If NO section
        survives, the partition function falls back to mechanical."""
        f = {
            'skill': 'contradictions',
            'category': 'scope_vs_oos',
            'fields': ['out_of_scope', 'deliverables'],
        }
        # delivery_plan agent is not wired — only scope_boundaries gets it.
        result = _sections_for_finding(
            f, {'scope_boundaries', 'requirements'}
        )
        assert result == ['scope_boundaries']

    def test_all_target_sections_unavailable_returns_empty(self):
        """When the finding routes to N sections and none of them are
        available, return empty — partition then falls to mechanical
        and the reviser sees the finding as a last resort."""
        f = {
            'skill': 'contradictions',
            'category': 'architecture_vs_stack',
            'fields': ['architecture_description', 'technology_stack'],
        }
        # Only requirements is wired; neither architecture nor anything
        # else touched is available.
        assert _sections_for_finding(f, {'requirements'}) == []


class TestFieldsDrivenRoutingIsUniversal:
    """The fields-driven routing rule applies regardless of (skill,
    category) — structural fields belong to section bundles, full
    stop. This guards the two-writers fix: any finding whose fields
    touch a bundle-owned schema field must reach the section agent
    that owns it, not the generic reviser (whose flat-SOW patches
    would be overwritten by the next assembly)."""

    def test_coverage_with_structural_fields_routes_to_owning_section(self):
        """Coverage findings carrying a structural ``fields`` hint
        route to the owning section. Without this rule, the reviser
        would patch the flat SOW's requirements list and the next
        section-agent run would assemble away the patch."""
        f = {
            'skill': 'coverage',
            'category': 'manifest_item_uncovered',
            'fields': ['functional_requirements'],
        }
        assert _sections_for_finding(f, _ALL_SECTIONS) == ['requirements']

    def test_coverage_cross_bundle_routes_to_all_owners(self):
        f = {
            'skill': 'coverage',
            'category': 'manifest_item_uncovered',
            'fields': ['deliverables', 'out_of_scope'],
        }
        assert _sections_for_finding(f, _ALL_SECTIONS) == [
            'delivery_plan',
            'scope_boundaries',
        ]

    def test_semantic_quality_with_structural_fields_routes(self):
        """``semantic_quality`` was traditionally mechanical — the
        new rule means it routes through section agents whenever it
        touches a structural field."""
        f = {
            'skill': 'semantic_quality',
            'category': 'naming_drift',
            'fields': ['executive_summary'],
        }
        assert _sections_for_finding(f, _ALL_SECTIONS) == ['narrative']

    def test_contractual_exposure_with_structural_fields_routes(self):
        f = {
            'skill': 'contractual_exposure',
            'category': 'missing_handover_boundary',
            'fields': ['handover_disclaimers'],
        }
        assert _sections_for_finding(f, _ALL_SECTIONS) == ['scope_boundaries']

    def test_disclosures_with_structural_fields_routes(self):
        f = {
            'skill': 'disclosures',
            'category': 'missing_ai_nondeterminism_disclosure',
            'fields': ['assumptions'],
        }
        assert _sections_for_finding(f, _ALL_SECTIONS) == ['scope_boundaries']

    def test_route_table_only_used_when_fields_produce_no_target(self):
        """When ``fields`` populates a section, the (skill, category)
        route table is NOT consulted — the field rule wins. This
        matters when the two disagree (e.g., a finding nominally for
        ``contradictions/fr_vs_nfr`` whose fields touch ``deliverables``
        should follow the fields, not the legacy default)."""
        f = {
            'skill': 'contradictions',
            'category': 'fr_vs_nfr',  # route table says → 'requirements'
            'fields': ['deliverables'],
        }
        # Fields rule wins → delivery_plan, not requirements.
        assert _sections_for_finding(f, _ALL_SECTIONS) == ['delivery_plan']


class TestFieldToSectionCoverage:
    """Coverage invariant: every top-level ``sow_data`` field that a
    section bundle can emit must be in :data:`_FIELD_TO_SECTION`.
    Otherwise a finding referencing that field would not contribute to
    routing decisions and would silently fall through to mechanical —
    re-introducing the cross-bundle problem this commit fixes."""

    def test_every_field_maps_to_a_section_in_section_order(self):
        for field, section in _FIELD_TO_SECTION.items():
            assert section in set(_SECTION_ORDER), (
                f'Field {field!r} mapped to unknown section {section!r}. '
                f'Either fix the map or add {section!r} to _SECTION_ORDER.'
            )

    def test_no_section_in_field_map_is_missing_from_routing(self):
        """Symmetric check: every section appearing as a value in
        ``_FIELD_TO_SECTION`` must also be a possible value in
        ``_CROSS_SECTION_REPAIR_ROUTES`` (otherwise routing by fields
        would target a section that no (skill, category) route covers,
        which is fine but suggests a stale map entry)."""
        field_sections = set(_FIELD_TO_SECTION.values())
        routes_sections = set(_CROSS_SECTION_REPAIR_ROUTES.values())
        # Allow field_sections to be a SUPERSET — narrative has fields
        # but no current contradictions route. The reverse direction
        # (route to a section not in the field map) IS a bug.
        assert routes_sections.issubset(field_sections), (
            'Routing table references sections that have no fields in '
            f'_FIELD_TO_SECTION: {routes_sections - field_sections}. '
            'Either add the section\'s fields to _FIELD_TO_SECTION or '
            'drop the route.'
        )

    def test_field_map_covers_every_bundle_pydantic_field(self):
        """Lint guard: any field that a section's Pydantic Bundle
        declares MUST be in _FIELD_TO_SECTION. Adding a field to a
        Bundle without updating the map would silently break field-
        based routing for findings that reference the new field."""
        from app.sub_agents.schemas import (
            ArchitectureBundle,
            DeliveryPlanBundle,
            NarrativeBundle,
            RequirementsBundle,
            ScopeBoundariesBundle,
        )

        expected: dict[str, set[str]] = {
            'requirements': set(RequirementsBundle.model_fields.keys()),
            'delivery_plan': set(DeliveryPlanBundle.model_fields.keys()),
            'scope_boundaries': set(
                ScopeBoundariesBundle.model_fields.keys()
            ),
            'architecture': set(ArchitectureBundle.model_fields.keys()),
            'narrative': set(NarrativeBundle.model_fields.keys()),
        }
        actual: dict[str, set[str]] = {
            section: set() for section in expected
        }
        for field, section in _FIELD_TO_SECTION.items():
            actual.setdefault(section, set()).add(field)

        for section, expected_fields in expected.items():
            missing = expected_fields - actual.get(section, set())
            assert not missing, (
                f'Section {section!r} bundle declares fields {missing!r} '
                f'that are missing from _FIELD_TO_SECTION. Add them so '
                f'findings that reference them route correctly.'
            )


class TestPartitionFindingsCrossBundle:
    """End-to-end partition behaviour with cross-bundle findings.
    ``_sections_for_finding`` is unit-tested above; here we verify
    that the partition function uses it correctly — the same finding
    appears under multiple section keys."""

    def test_cross_bundle_finding_appears_in_every_routed_section(self):
        cross = {
            'skill': 'contradictions',
            'category': 'scope_vs_oos',
            'fields': ['out_of_scope', 'deliverables'],
            'id': 'cross-1',
        }
        by_section, mechanical = _partition_findings([cross], _ALL_SECTIONS)
        assert by_section == {
            'delivery_plan': [cross],
            'scope_boundaries': [cross],
        }
        assert mechanical == []

    def test_mixed_batch_partitions_correctly(self):
        single = {
            'skill': 'contradictions',
            'category': 'fr_vs_nfr',
            'fields': ['functional_requirements'],
            'id': 'single-1',
        }
        cross = {
            'skill': 'contradictions',
            'category': 'scope_vs_oos',
            'fields': ['out_of_scope', 'deliverables'],
            'id': 'cross-1',
        }
        # Genuinely mechanical: no structural fields hint AND no
        # (skill, category) entry in the route table. The reviser is
        # the only handler for findings of this shape.
        mech = {
            'skill': 'coverage',
            'category': 'manifest_item_uncovered',
            'fields': [],
            'id': 'mech-1',
        }
        by_section, mechanical = _partition_findings(
            [single, cross, mech], _ALL_SECTIONS,
        )
        assert by_section == {
            'requirements': [single],
            'delivery_plan': [cross],
            'scope_boundaries': [cross],
        }
        assert mechanical == [mech]

    def test_same_finding_appears_in_each_section_bucket_byvalue(self):
        """Identity check: the partition stores REFERENCES, not copies.
        Two sections receiving the same finding receive the same dict
        — the loop writes this dict to STATE_REPAIR_FINDINGS and each
        section agent sees identical content."""
        cross = {
            'skill': 'contradictions',
            'category': 'scope_vs_oos',
            'fields': ['out_of_scope', 'deliverables'],
        }
        by_section, _ = _partition_findings([cross], _ALL_SECTIONS)
        assert by_section['delivery_plan'][0] is cross
        assert by_section['scope_boundaries'][0] is cross


# ---------------------------------------------------------------------------
# Loop-level — section agents are invoked, reviser sees only residue
# ---------------------------------------------------------------------------


class FakeSectionAgent(BaseAgent):
    """Stub that records every invocation + the repair findings it saw.

    Mirrors the production contract: when invoked, it writes a non-empty
    bundle to ``state[output_key]`` so the loop's re-assembly step has
    something to work with. The ``output_key`` is configured per-instance
    so we can simulate distinct sections (requirements, delivery_plan,
    etc.) without standing up the full section agent factory.
    """

    config_type: ClassVar[type[BaseAgentConfig]] = BaseAgentConfig

    output_key: str = ''
    repair_payload_per_call: List[Any] = []
    calls: int = 0

    async def run_async(self, ctx) -> AsyncGenerator[Event, None]:  # type: ignore[override]
        # Record the findings packet the loop placed in state for us.
        self.repair_payload_per_call.append(
            list(ctx.session.state.get(STATE_REPAIR_FINDINGS) or [])
        )
        self.calls += 1
        # Emit a non-empty bundle so the re-assembly can use it.
        ctx.session.state[self.output_key] = {
            '__patched_by': self.name,
            'call_index': self.calls,
        }
        if False:  # pragma: no cover
            yield  # type: ignore[unreachable]


def _critic_emitting(findings: list[dict], *, status: str = 'blocked'):
    """Build a FakeCritic-like stub that emits a scripted report with
    custom findings (the existing FakeCritic only sets findings=[])."""

    class _CriticWithFindings(BaseAgent):
        config_type: ClassVar[type[BaseAgentConfig]] = BaseAgentConfig
        statuses: List[str] = [status, 'passed']
        calls: int = 0

        async def run_async(self, ctx) -> AsyncGenerator[Event, None]:  # type: ignore[override]
            idx = min(self.calls, len(self.statuses) - 1)
            current_status = self.statuses[idx]
            self.calls += 1
            ctx.session.state[STATE_VALIDATION_RESULT] = {
                'overall_status': current_status,
                'summary': f'round {self.calls} ({current_status})',
                'next_action': '...',
                'findings': findings if current_status == 'blocked' else [],
                'blocker_count': sum(
                    1 for f in findings if f.get('severity') == 'BLOCKER'
                ),
                'major_count': sum(
                    1 for f in findings if f.get('severity') == 'MAJOR'
                ),
                'new_blocking_finding_count': 0,
                'resolved_blocking_finding_count': 0,
            }
            if False:  # pragma: no cover
                yield  # type: ignore[unreachable]

    return _CriticWithFindings(name='critic')


def _seed_assembly_state(state: dict) -> None:
    """Populate the in-state bundles + metadata envelope with the minimum
    shape ``apply_sow_assembly_to_state`` accepts so the loop can
    re-assemble after a section agent runs."""
    from app.sub_agents.schemas import (
        SOW_BUNDLE_STATE_KEYS,
        SOW_METADATA_STATE_KEY,
    )

    state[SOW_METADATA_STATE_KEY] = {
        'project_title': 'P',
        'customer_name': 'C',
        'partner_name': 'GFT',
        'funding_type': 'DAF',
    }
    state[SOW_BUNDLE_STATE_KEYS['requirements']] = {
        'functional_requirements': [
            {'number': 'FR-01', 'description': 'x'},
        ],
        'non_functional_requirements': [
            {'number': 'NFR-01', 'description': 'x'},
        ],
    }
    state[SOW_BUNDLE_STATE_KEYS['delivery_plan']] = {
        'activity_phases': [{'name': 'P1', 'description': 'd', 'tasks': []}],
        'deliverables': [
            {'activity': 'P1', 'name': 'D', 'description': 'd', 'format': 'doc'},
        ],
        'timeline': [{'activity': 'P1', 'timeframe': 'W1', 'outcomes': 'o'}],
        'partner_roles': [{'role': 'PM', 'responsibilities': 'r'}],
        'customer_roles': [{'role': 'Sponsor', 'responsibilities': 'r'}],
        'success_criteria': ['ok'],
        'objectives': [],
    }
    state[SOW_BUNDLE_STATE_KEYS['scope_boundaries']] = {
        'assumptions': ['a'],
        'out_of_scope': ['o'],
        'risks': [],
        'handover_disclaimers': [],
        'change_request_policy_text': '',
    }
    state[STATE_STAGE] = 'content'


class TestRepairRoutingWithinLoop:
    """The loop dispatches routed findings to section agents and only
    sends the mechanical residue to the reviser."""

    def _build(
        self,
        *,
        critic,
        sections: dict[str, BaseAgent],
        max_rounds: int = 3,
    ) -> tuple[QualityLoopAgent, FakeReviser]:
        reviser = FakeReviser(name='fake_reviser')
        loop = QualityLoopAgent(
            name='sow_quality_loop',
            description='test',
            sub_agents=[critic, reviser],
            max_rounds=max_rounds,
            repair_section_agents=sections,
        )
        return loop, reviser

    async def test_cross_section_finding_routes_to_section_not_reviser(self):
        """A single contradictions/activities_vs_deliverables finding
        must go to delivery_plan, NOT the reviser."""
        delivery = FakeSectionAgent(
            name='delivery_plan_agent',
            output_key='app:sow:delivery_plan',
            repair_payload_per_call=[],
        )
        critic = _critic_emitting(
            [
                {
                    'id': 'c-1',
                    'skill': 'contradictions',
                    'category': 'activities_vs_deliverables',
                    'severity': 'MAJOR',
                    'fields': ['activity_phases', 'deliverables'],
                    'evidence': 'WS-03 missing activity',
                    'recommendation': 'Add the activity.',
                },
            ],
        )
        loop, reviser = self._build(
            critic=critic, sections={'delivery_plan': delivery}
        )
        ctx = _fake_ctx()
        _seed_assembly_state(ctx.session.state)

        await _run_loop(loop, ctx)

        assert delivery.calls == 1, (
            'delivery_plan section agent must be invoked when the route '
            'matches its (skill, category).'
        )
        # Reviser MUST NOT run when there is no mechanical residue — that
        # would just spend tokens to confirm no-op.
        assert reviser.calls == 0

    async def test_mixed_findings_run_section_then_reviser_on_residue(self):
        """Section repairs handle their share; the reviser sees ONLY the
        mechanical residue, not the whole report."""
        delivery = FakeSectionAgent(
            name='delivery_plan_agent',
            output_key='app:sow:delivery_plan',
            repair_payload_per_call=[],
        )
        coverage_finding = {
            'id': 'cov-1',
            'skill': 'coverage',
            'category': 'manifest_item_uncovered',
            'severity': 'MAJOR',
            'fields': ['functional_requirements'],
            'evidence': 'manifest item I-001 missing',
            'recommendation': 'Anchor it.',
        }
        cross_finding = {
            'id': 'c-1',
            'skill': 'contradictions',
            'category': 'timeline_vs_deliverables',
            'severity': 'MAJOR',
            'fields': ['timeline', 'deliverables'],
            'evidence': 'timeline mismatch',
            'recommendation': 'Reconcile.',
        }
        critic = _critic_emitting([coverage_finding, cross_finding])
        loop, reviser = self._build(
            critic=critic, sections={'delivery_plan': delivery}
        )
        ctx = _fake_ctx()
        _seed_assembly_state(ctx.session.state)

        await _run_loop(loop, ctx)

        assert delivery.calls == 1
        assert reviser.calls == 1, (
            'reviser must still run when mechanical residue exists.'
        )
        # The reviser sees a narrowed report — only the coverage finding.
        # The loop wrote the narrowed report to STATE_VALIDATION_RESULT
        # before invoking the reviser; on the next critic run the report
        # is overwritten so we cannot inspect it here. Instead, we check
        # the section agent saw ONLY its routed finding.
        assert delivery.repair_payload_per_call == [[cross_finding]]

    async def test_section_agents_run_in_phase_step_order(self):
        """When multiple sections have routed findings, the loop must
        invoke them in Phase Step order (A→B→C→D→E) so a later section
        sees the patched upstream bundle."""
        invocation_order: list[str] = []

        class _RecordingSection(FakeSectionAgent):
            async def run_async(self, ctx):  # type: ignore[override]
                invocation_order.append(self.name)
                async for ev in super().run_async(ctx):  # pragma: no cover
                    yield ev

        requirements = _RecordingSection(
            name='requirements_agent',
            output_key='app:sow:requirements',
            repair_payload_per_call=[],
        )
        delivery = _RecordingSection(
            name='delivery_plan_agent',
            output_key='app:sow:delivery_plan',
            repair_payload_per_call=[],
        )
        scope = _RecordingSection(
            name='scope_boundaries_agent',
            output_key='app:sow:scope_boundaries',
            repair_payload_per_call=[],
        )

        critic = _critic_emitting([
            # Listed out of Phase Step order — the loop must still run
            # in (requirements, delivery_plan, scope_boundaries) order.
            {
                'skill': 'contradictions',
                'category': 'scope_vs_oos',
                'severity': 'MAJOR',
                'fields': ['out_of_scope'],
                'evidence': '...',
                'recommendation': '...',
            },
            {
                'skill': 'contradictions',
                'category': 'fr_vs_nfr',
                'severity': 'MAJOR',
                'fields': ['functional_requirements', 'non_functional_requirements'],
                'evidence': '...',
                'recommendation': '...',
            },
            {
                'skill': 'contradictions',
                'category': 'activities_vs_deliverables',
                'severity': 'MAJOR',
                'fields': ['activity_phases', 'deliverables'],
                'evidence': '...',
                'recommendation': '...',
            },
        ])
        loop, _ = self._build(
            critic=critic,
            sections={
                'requirements': requirements,
                'delivery_plan': delivery,
                'scope_boundaries': scope,
            },
        )
        ctx = _fake_ctx()
        _seed_assembly_state(ctx.session.state)

        await _run_loop(loop, ctx)

        assert invocation_order == [
            'requirements_agent',
            'delivery_plan_agent',
            'scope_boundaries_agent',
        ], invocation_order

    async def test_state_repair_findings_is_cleared_between_sections(self):
        """A section agent must not see the previous section's repair
        packet — the loop clears the slot after each invocation."""
        requirements = FakeSectionAgent(
            name='requirements_agent',
            output_key='app:sow:requirements',
            repair_payload_per_call=[],
        )
        delivery = FakeSectionAgent(
            name='delivery_plan_agent',
            output_key='app:sow:delivery_plan',
            repair_payload_per_call=[],
        )
        req_finding = {
            'skill': 'contradictions',
            'category': 'fr_vs_nfr',
            'severity': 'MAJOR',
            'fields': ['functional_requirements'],
        }
        del_finding = {
            'skill': 'contradictions',
            'category': 'activities_vs_deliverables',
            'severity': 'MAJOR',
            'fields': ['activity_phases'],
        }
        critic = _critic_emitting([req_finding, del_finding])
        loop, _ = self._build(
            critic=critic,
            sections={
                'requirements': requirements,
                'delivery_plan': delivery,
            },
        )
        ctx = _fake_ctx()
        _seed_assembly_state(ctx.session.state)

        await _run_loop(loop, ctx)

        # Each section sees ONLY its own routed finding — never both.
        assert requirements.repair_payload_per_call == [[req_finding]]
        assert delivery.repair_payload_per_call == [[del_finding]]
        # And after the loop terminates, the slot is None (or absent).
        assert not ctx.session.state.get(STATE_REPAIR_FINDINGS)

    async def test_no_section_wired_falls_back_to_reviser_only(self):
        """Backwards compatibility: ``repair_section_agents`` defaults to
        ``{}``, in which case the loop must behave exactly as before —
        every finding goes to the reviser."""
        cross_finding = {
            'skill': 'contradictions',
            'category': 'activities_vs_deliverables',
            'severity': 'MAJOR',
            'fields': ['activity_phases'],
        }
        critic = _critic_emitting([cross_finding])
        loop, reviser = self._build(critic=critic, sections={})
        ctx = _fake_ctx()
        _seed_assembly_state(ctx.session.state)

        await _run_loop(loop, ctx)

        assert reviser.calls == 1, (
            'With no section agents wired the reviser remains the sole '
            'patcher — same behaviour as before commit 5.'
        )

    async def test_sow_is_reassembled_after_section_repair(self):
        """After a section agent writes its bundle, the loop must
        re-assemble the flat sow_data so the next critic round (and any
        subsequent section / reviser invocation) sees the patched
        payload, not the pre-patch one."""
        delivery = FakeSectionAgent(
            name='delivery_plan_agent',
            output_key='app:sow:delivery_plan',
            repair_payload_per_call=[],
        )
        critic = _critic_emitting([
            {
                'skill': 'contradictions',
                'category': 'activities_vs_deliverables',
                'severity': 'MAJOR',
                'fields': ['activity_phases', 'deliverables'],
                'evidence': '...',
                'recommendation': '...',
            },
        ])
        loop, _ = self._build(
            critic=critic, sections={'delivery_plan': delivery}
        )
        ctx = _fake_ctx()
        _seed_assembly_state(ctx.session.state)

        await _run_loop(loop, ctx)

        # STATE_SOW must now reflect the new delivery_plan bundle the
        # section agent wrote (via __patched_by marker).
        staged = ctx.session.state.get(STATE_SOW) or {}
        # The patched bundle has deliverables=[] (the section agent stub
        # writes only __patched_by / call_index keys), so the assembler's
        # ``.get('deliverables', [])`` gives an empty list. The
        # functional_requirements still carry FR-01 from the seed.
        assert staged.get('deliverables') == [], (
            'Re-assembly must have replaced the seeded deliverables with '
            "the section agent's patched bundle (which has none)."
        )
        assert staged.get('functional_requirements'), (
            'Non-patched bundles must still appear in the re-assembled '
            'SOW; only the patched section is replaced.'
        )

    async def test_failed_reassembly_does_not_abort_the_loop(self):
        """If a section agent emits a malformed bundle and the
        re-assembly raises AssemblyError, the loop must log and
        continue — the next critic round will surface the problem
        through its normal status branches."""
        class _BadSection(BaseAgent):
            config_type: ClassVar[type[BaseAgentConfig]] = BaseAgentConfig
            calls: int = 0

            async def run_async(self, ctx):  # type: ignore[override]
                self.calls += 1
                # Wipe a required bundle so the assembler raises.
                from app.sub_agents.schemas import SOW_BUNDLE_STATE_KEYS
                ctx.session.state.pop(
                    SOW_BUNDLE_STATE_KEYS['scope_boundaries'], None
                )
                if False:  # pragma: no cover
                    yield  # type: ignore[unreachable]

        bad = _BadSection(name='delivery_plan_agent')
        critic = _critic_emitting([
            {
                'skill': 'contradictions',
                'category': 'activities_vs_deliverables',
                'severity': 'MAJOR',
                'fields': ['activity_phases'],
            },
        ])
        loop, reviser = self._build(
            critic=critic,
            sections={'delivery_plan': bad},
            max_rounds=2,
        )
        ctx = _fake_ctx()
        _seed_assembly_state(ctx.session.state)

        # The loop must NOT raise — it logs and proceeds.
        await _run_loop(loop, ctx)

        assert bad.calls == 1
        # Reviser still runs on the original report (the section agent
        # had no mechanical residue to delegate, so the residue path
        # falls through and reviser sees the report unchanged).
        # The exact reviser invocation count is policy — we only assert
        # the loop completed cleanly.
        result = ctx.session.state[QUALITY_LOOP_RESULT_KEY]
        assert result['status'] in ('passed', 'exhausted', 'no_progress', 'blocked')


class TestProductionSingletonWiring:
    """The exported ``sow_quality_loop`` instance must carry every
    section agent in its ``repair_section_agents`` mapping, with keys
    matching the canonical section names used by
    :data:`_CROSS_SECTION_REPAIR_ROUTES`. Without this, a route to
    'requirements' (for example) would silently fall back to the
    reviser at production runtime — defeating the structural fix.
    """

    def test_singleton_has_all_five_section_agents_wired(self):
        from app.sub_agents.quality_loop.agent import sow_quality_loop

        # The mapping is a frozen-ish view but we treat it as dict-like.
        wired = dict(sow_quality_loop.repair_section_agents)
        assert set(wired.keys()) == {
            'requirements',
            'delivery_plan',
            'scope_boundaries',
            'architecture',
            'narrative',
        }, (
            'Production singleton is missing one or more section agents — '
            'the loop will silently fall back to the reviser for findings '
            'routed to the absent section.'
        )

    def test_each_wired_agent_matches_the_canonical_section_module(self):
        """Phase 3 completed the rollout — every repair route goes
        through the section's tool-based repair agent. The first-gen
        SequentialAgents stay defined for root-side generation only;
        the loop never regenerates a bundle in repair mode anymore."""
        from app.sub_agents.architecture import architecture_repair_agent
        from app.sub_agents.delivery_plan import delivery_plan_repair_agent
        from app.sub_agents.narrative import narrative_repair_agent
        from app.sub_agents.quality_loop.agent import sow_quality_loop
        from app.sub_agents.requirements import requirements_repair_agent
        from app.sub_agents.scope_boundaries import scope_boundaries_repair_agent

        wired = sow_quality_loop.repair_section_agents
        assert wired['requirements'] is requirements_repair_agent
        assert wired['delivery_plan'] is delivery_plan_repair_agent
        assert wired['scope_boundaries'] is scope_boundaries_repair_agent
        assert wired['architecture'] is architecture_repair_agent
        assert wired['narrative'] is narrative_repair_agent

    def test_every_route_section_is_in_the_wiring(self):
        """Coupling guard: ``_CROSS_SECTION_REPAIR_ROUTES`` and the
        ``repair_section_agents`` map are kept in sync by section name.
        A finding routed to a section that isn't wired falls back to the
        reviser (handled by ``_partition_findings``), which is safe but
        defeats the structural fix — this test fails loudly so the gap
        gets closed before production sees it."""
        from app.sub_agents.quality_loop.agent import (
            _CROSS_SECTION_REPAIR_ROUTES,
            sow_quality_loop,
        )

        wired_keys = set(sow_quality_loop.repair_section_agents.keys())
        for (skill, category), section in _CROSS_SECTION_REPAIR_ROUTES.items():
            assert section in wired_keys, (
                f'Route ({skill}, {category}) -> {section!r} but no '
                f'agent for {section!r} is wired into sow_quality_loop. '
                'Add it to repair_section_agents (or remove the route '
                'if the section is intentionally unsupported).'
            )


class TestRepairMechanismTelemetry:
    """Phase 5 — the loop emits ``quality_loop_repair_mechanism_used``
    once per round summarising whether each invoked section repair was
    tool-based (``Agent``) or legacy regenerate (``SequentialAgent``).
    Pinning ``legacy_regenerate=0`` in production logs is the
    anti-regression signal called for by the plan.
    """

    def _build_loop_with_sections(
        self,
        *,
        critic,
        sections: dict[str, BaseAgent],
        max_rounds: int = 2,
    ) -> tuple[QualityLoopAgent, FakeReviser]:
        reviser = FakeReviser(name='fake_reviser')
        loop = QualityLoopAgent(
            name='sow_quality_loop',
            description='test',
            sub_agents=[critic, reviser],
            max_rounds=max_rounds,
            repair_section_agents=sections,
        )
        return loop, reviser

    async def test_tool_based_section_emits_mechanism_event(
        self, monkeypatch,
    ):
        """Default ``FakeSectionAgent`` extends BaseAgent (not
        SequentialAgent), so the loop must classify it as ``tool_based``
        and emit the summary event accordingly."""
        from app.sub_agents.quality_loop import agent as quality_loop_module

        delivery = FakeSectionAgent(
            name='delivery_plan_agent',
            output_key='app:sow:delivery_plan',
            repair_payload_per_call=[],
        )
        critic = _critic_emitting([
            {
                'id': 'c-1',
                'skill': 'contradictions',
                'category': 'activities_vs_deliverables',
                'severity': 'MAJOR',
                'fields': ['activity_phases'],
                'evidence': 'x',
                'recommendation': 'y',
            },
        ])
        loop, _ = self._build_loop_with_sections(
            critic=critic, sections={'delivery_plan': delivery},
        )

        # Capture logger.info call_args so we can assert on the
        # structured event we just added.
        captured: list[tuple[str, dict]] = []
        original_info = quality_loop_module.logger.info

        def _capturing_info(event_name, **kwargs):
            captured.append((event_name, kwargs))
            return original_info(event_name, **kwargs)

        monkeypatch.setattr(quality_loop_module.logger, 'info', _capturing_info)

        ctx = _fake_ctx()
        _seed_assembly_state(ctx.session.state)

        await _run_loop(loop, ctx)

        mechanism_events = [
            kwargs for (name, kwargs) in captured
            if name == 'quality_loop_repair_mechanism_used'
        ]
        assert mechanism_events, (
            'quality_loop_repair_mechanism_used must be emitted at least '
            'once when a section repair runs.'
        )
        first = mechanism_events[0]
        assert first['tool_based'] == 1
        assert first['legacy_regenerate'] == 0
        assert first['by_section'] == {'delivery_plan': 'tool_based'}
        assert first['round'] == 1

    def test_production_repair_agents_are_all_classified_as_tool_based(self):
        """Pin the classification expression the loop relies on:
        every wired ``repair_section_agents`` entry in the production
        singleton is an ``Agent`` (LlmAgent), not a ``SequentialAgent``.

        The loop uses ``isinstance(section_agent, SequentialAgent)``
        to discriminate the mechanism. If a future change accidentally
        wires a first-gen agent into the repair map, this test fails
        with the offending section named — same signal the runtime
        ``quality_loop_repair_mechanism_used`` log would emit, but
        caught at import time."""
        from google.adk.agents import SequentialAgent
        from app.sub_agents.quality_loop.agent import sow_quality_loop

        offending = {
            name: type(agent).__name__
            for name, agent in sow_quality_loop.repair_section_agents.items()
            if isinstance(agent, SequentialAgent)
        }
        assert not offending, (
            f'These sections are wired to a SequentialAgent (legacy '
            f'regenerate flow): {offending}. They must be the section '
            "repair agent (built by build_section_repair_agent), not "
            'the first-gen agent.'
        )

    async def test_no_event_emitted_when_no_section_dispatched(
        self, monkeypatch,
    ):
        """If a round has no section repairs (e.g. only mechanical
        residue), the summary event must NOT fire — emitting an empty
        summary every round would dilute the signal."""
        from app.sub_agents.quality_loop import agent as quality_loop_module

        critic = _critic_emitting([
            {
                # No structural ``fields``; routes to the reviser only.
                'id': 'm-1',
                'skill': 'semantic_quality',
                'category': 'vague_phrasing',
                'severity': 'MINOR',
                'fields': [],
                'evidence': 'x',
                'recommendation': 'y',
            },
        ])
        loop, _ = self._build_loop_with_sections(
            critic=critic, sections={},
        )

        captured: list[tuple[str, dict]] = []
        original_info = quality_loop_module.logger.info

        def _capturing_info(event_name, **kwargs):
            captured.append((event_name, kwargs))
            return original_info(event_name, **kwargs)

        monkeypatch.setattr(quality_loop_module.logger, 'info', _capturing_info)

        ctx = _fake_ctx()
        _seed_assembly_state(ctx.session.state)

        await _run_loop(loop, ctx)

        mechanism_events = [
            kwargs for (name, kwargs) in captured
            if name == 'quality_loop_repair_mechanism_used'
        ]
        assert not mechanism_events, (
            'mechanism event must only fire when ≥1 section repair runs.'
        )


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


# ---------------------------------------------------------------------------
# D1 — anchor extraction + drop detection
#
# These tests pin two layers:
# 1. ``_extract_anchor_ids`` — pure walker over a bundle / SOW value,
#    pulls SOW item ids out of every string regardless of nesting.
# 2. ``_BUNDLE_ANCHOR_ID_PATTERN`` — regex shape, what is and is not
#    treated as an anchor id (must not match generic tokens like
#    ``AES-256`` that would create false-positive drops).
#
# End-to-end "the log actually fires when a section drops an id" is
# exercised in the loop-level fakes section above — those already
# cover the section-repair codepath; here we focus on the helper
# correctness so a future refactor cannot quietly break the diff.
# ---------------------------------------------------------------------------


class TestBundleAnchorIdPattern:
    @pytest.mark.parametrize(
        ('text', 'expected'),
        [
            ('item FR-01 references NFR-03', ['FR-01', 'NFR-03']),
            ('deliverable WS-12 belongs to phase A-02', ['WS-12', 'A-02']),
            ('manifest items I-001, I-002, I-003 are covered', ['I-001', 'I-002', 'I-003']),
            ('out-of-scope OOS-01 vs OOS-02', ['OOS-01', 'OOS-02']),
            ('risk R-04 mitigated by control T-09', ['R-04', 'T-09']),
            ('gap G-04 escalated to priority P-2', ['G-04', 'P-2']),
            # No anchors at all.
            ('the description is too short to flag', []),
        ],
    )
    def test_matches_expected_anchor_shapes(self, text, expected):
        assert _BUNDLE_ANCHOR_ID_PATTERN.findall(text) == expected

    @pytest.mark.parametrize(
        'noise',
        [
            'AES-256 encryption',
            'ISO-9001 compliance certification',
            'PEP-8 style violations',
            'release v1.2 timeline',
        ],
    )
    def test_does_not_match_non_anchor_tokens(self, noise):
        """A bundle full of crypto/standards references must not trigger
        spurious dropped-id warnings."""
        assert _BUNDLE_ANCHOR_ID_PATTERN.findall(noise) == []


class TestExtractAnchorIds:
    def test_none_returns_empty_set(self):
        assert _extract_anchor_ids(None) == set()

    def test_empty_dict_returns_empty_set(self):
        assert _extract_anchor_ids({}) == set()

    def test_empty_list_returns_empty_set(self):
        assert _extract_anchor_ids([]) == set()

    def test_scalar_without_anchors_returns_empty_set(self):
        assert _extract_anchor_ids(42) == set()
        assert _extract_anchor_ids('') == set()
        assert _extract_anchor_ids('no matches here') == set()

    def test_flat_string_returns_matching_anchors_uppercased(self):
        """Casing drift in the source must not produce spurious diffs —
        the walker uppercases every match so ``fr-01`` and ``FR-01``
        collapse to one id."""
        assert _extract_anchor_ids('fr-01 and FR-02') == {'FR-01', 'FR-02'}

    def test_walks_nested_dict_values(self):
        bundle = {
            'functional_requirements': [
                {'number': 'FR-01', 'description': 'covers FR-02 too'},
                {'number': 'FR-03', 'description': 'standalone'},
            ],
            'non_functional_requirements': [
                {'number': 'NFR-01', 'description': 'depends on WS-05'},
            ],
        }
        assert _extract_anchor_ids(bundle) == {
            'FR-01', 'FR-02', 'FR-03', 'NFR-01', 'WS-05',
        }

    def test_walks_through_tuples(self):
        """Defensive: bundles serialized from Pydantic models may carry
        tuples in some fields. The walker must descend through them."""
        value = ('FR-01', {'nested': ('NFR-02',)})
        assert _extract_anchor_ids(value) == {'FR-01', 'NFR-02'}


# ---------------------------------------------------------------------------
# review_payload — the corrected, stage-specific SOW view the loop returns
# so the root can render the review gate without reading session state.
# ---------------------------------------------------------------------------


def _sample_full_sow() -> dict:
    """A flat sow_data with every bundle-owned field populated, plus a few
    manifest-derived keys (which must NOT leak into review_payload)."""
    return {
        # manifest-derived — never part of a review payload
        'partner_name': 'GFT',
        'customer_name': 'ACME',
        'project_title': 'Project P',
        # requirements
        'functional_requirements': [{'number': 'FR-01', 'description': 'x'}],
        'non_functional_requirements': [{'number': 'NFR-01', 'description': 'y'}],
        # delivery_plan
        'activity_phases': [{'name': 'Phase 1'}],
        'deliverables': [{'number': 'WS-01', 'name': 'd'}],
        'timeline': [{'activity': 'Phase 1'}],
        'partner_roles': [{'title': 'PM'}],
        'customer_roles': [{'title': 'Sponsor'}],
        'success_criteria': ['sc-1'],
        'objectives': ['obj-1'],
        # scope_boundaries
        'assumptions': [{'text': 'a'}],
        'out_of_scope': [{'text': 'o'}],
        'risks': [{'number': 'R-01'}],
        'handover_disclaimers': ['hd'],
        'change_request_policy_text': 'crp',
        # architecture
        'architecture_description': 'desc',
        'architecture_components': [{'name': 'c'}],
        'architecture_integrations': [{'name': 'i'}],
        'technology_stack': [{'service': 's', 'purpose': 'p'}],
        # narrative
        'executive_summary': 'es',
        'partner_overview': 'po',
        'customer_overview': 'co',
        'customer_primary_domain': 'cpd',
    }


class _ReviserMutatesOutOfScope(BaseAgent):
    """Reviser stub that appends a real scope_boundaries item to STATE_SOW,
    so review_payload must reflect the post-repair content."""

    config_type: ClassVar[type[BaseAgentConfig]] = BaseAgentConfig

    async def run_async(self, ctx) -> AsyncGenerator[Event, None]:  # type: ignore[override]
        sow = dict(ctx.session.state.get(STATE_SOW) or {})
        oos = list(sow.get('out_of_scope') or [])
        oos.append({'text': 'added-by-repair'})
        sow['out_of_scope'] = oos
        ctx.session.state[STATE_SOW] = sow
        if False:  # pragma: no cover
            yield  # type: ignore[unreachable]


class TestReviewPayloadHelper:
    def test_content_stage_returns_only_content_sections(self):
        rp = _review_payload(_sample_full_sow(), 'content')
        assert set(rp.keys()) == {
            'requirements', 'delivery_plan', 'scope_boundaries',
        }

    def test_full_stage_returns_only_architecture_and_narrative(self):
        rp = _review_payload(_sample_full_sow(), 'full')
        assert set(rp.keys()) == {'architecture', 'narrative'}

    def test_manifest_fields_never_leak_into_payload(self):
        for stage in ('content', 'full'):
            rp = _review_payload(_sample_full_sow(), stage)
            flat = {field for section in rp.values() for field in section}
            assert 'partner_name' not in flat
            assert 'project_title' not in flat

    def test_full_stage_covers_every_arch_narrative_field(self):
        """Derived from the vocabulary so the slice cannot drift from the
        schema — every architecture/narrative field is present."""
        from app.sub_agents.validation.field_vocabulary import (
            BUNDLE_OWNED_FIELDS_BY_SECTION,
        )
        rp = _review_payload(_sample_full_sow(), 'full')
        expected = {
            field
            for field, section in BUNDLE_OWNED_FIELDS_BY_SECTION.items()
            if section in ('architecture', 'narrative')
        }
        got = {field for section in rp.values() for field in section}
        assert got == expected

    def test_every_value_equals_the_source_sow(self):
        sow = _sample_full_sow()
        for stage in ('content', 'full'):
            rp = _review_payload(sow, stage)
            for section in rp.values():
                for field, value in section.items():
                    assert value == sow[field]

    def test_stage_section_sets_are_disjoint(self):
        """Content and full never share a section — the full pass does not
        re-send approved content."""
        content = set(_REVIEW_SECTIONS_BY_STAGE['content'])
        full = set(_REVIEW_SECTIONS_BY_STAGE['full'])
        assert content.isdisjoint(full)


class TestReviewPayloadEnvelope:
    @staticmethod
    def _body(events: list[Event]) -> dict:
        import json as _json
        return _json.loads(_terminal_event(events).content.parts[0].text)

    async def test_envelope_carries_stage_specific_payload_and_hash(self):
        from app.tools.sow._sow_helpers import sow_data_hash
        loop, _, _ = _build_loop(critic_statuses=['passed'])
        ctx = _ctx_with_sow(_sample_full_sow())
        ctx.session.state[STATE_STAGE] = 'content'

        body = self._body(await _run_loop(loop, ctx))

        assert body['stage'] == 'content'
        assert body['review_payload'] == _review_payload(
            ctx.session.state[STATE_SOW], 'content'
        )
        assert body['sow_data_hash'] == sow_data_hash(
            ctx.session.state[STATE_SOW]
        )

    async def test_full_stage_envelope_excludes_content_sections(self):
        loop, _, _ = _build_loop(critic_statuses=['passed'])
        ctx = _ctx_with_sow(_sample_full_sow())
        ctx.session.state[STATE_STAGE] = 'full'

        body = self._body(await _run_loop(loop, ctx))

        assert set(body['review_payload'].keys()) == {
            'architecture', 'narrative',
        }

    async def test_review_payload_reflects_post_repair_sow(self):
        """The reviser appends an out_of_scope item between rounds — the
        envelope must carry the PATCHED content, not the entry version."""
        loop = QualityLoopAgent(
            name='loop',
            description='test',
            sub_agents=[
                _CriticThatTouchesSow(
                    name='critic', statuses=['blocked', 'passed'],
                ),
                _ReviserMutatesOutOfScope(name='reviser'),
            ],
            max_rounds=3,
        )
        ctx = _ctx_with_sow(_sample_full_sow())
        ctx.session.state[STATE_STAGE] = 'content'

        body = self._body(await _run_loop(loop, ctx))

        oos = body['review_payload']['scope_boundaries']['out_of_scope']
        assert len(oos) == 2, 'review_payload must reflect the repair'
        assert oos[-1] == {'text': 'added-by-repair'}
        # And it is exactly the current corrected state, by construction.
        assert body['review_payload'] == _review_payload(
            ctx.session.state[STATE_SOW], 'content'
        )

    async def test_consistency_with_state_current(self):
        from app.tools.sow._sow_helpers import sow_data_hash
        loop, _, _ = _build_loop(critic_statuses=['passed'])
        ctx = _ctx_with_sow(_sample_full_sow())
        ctx.session.state[STATE_STAGE] = 'full'

        body = self._body(await _run_loop(loop, ctx))

        current = ctx.session.state[STATE_SOW]
        # Every value surfaced equals state['app:sow:current'].
        for section in body['review_payload'].values():
            for field, value in section.items():
                assert value == current[field]
        assert body['sow_data_hash'] == sow_data_hash(current)

    async def test_cache_replay_preserves_review_payload(self):
        """F-05 cache hit must re-emit an envelope with the same
        review_payload (marked cached), not drop it."""
        loop, _, _ = _build_loop(
            # 2nd status would change the result if the critic re-ran —
            # proves the replay came from the cache, not a fresh run.
            critic_statuses=['passed', 'blocked'],
            max_rounds=3,
        )
        ctx = _ctx_with_sow(_sample_full_sow())
        ctx.session.state[STATE_STAGE] = 'full'

        first = self._body(await _run_loop(loop, ctx))
        second = self._body(await _run_loop(loop, ctx))

        assert second.get('cached') is True
        assert second['stage'] == 'full'
        assert second['review_payload'] == first['review_payload']
        assert second['review_payload'] == _review_payload(
            ctx.session.state[STATE_SOW], 'full'
        )
        assert second['sow_data_hash'] == first['sow_data_hash']

    def test_dedupes_repeated_anchors(self):
        """The same id quoted multiple times in different fields counts
        once — sets handle this naturally but pin the contract."""
        bundle = {
            'a': 'FR-01 first',
            'b': 'FR-01 again',
            'c': ['FR-01', 'FR-02'],
        }
        assert _extract_anchor_ids(bundle) == {'FR-01', 'FR-02'}

    def test_ignores_non_string_leaves(self):
        bundle = {
            'count': 5,
            'enabled': True,
            'price': 12.5,
            'ref': 'FR-01',
        }
        assert _extract_anchor_ids(bundle) == {'FR-01'}

    def test_anchor_drop_diff_use_case(self):
        """End-to-end illustration: the loop computes
        ``pre - post`` to find dropped ids. This pins the symmetric
        diff semantics tests in the loop body rely on."""
        pre = _extract_anchor_ids({
            'fr': [
                {'number': 'FR-01'},
                {'number': 'FR-02'},
                {'number': 'FR-03'},
            ],
        })
        post = _extract_anchor_ids({
            'fr': [
                {'number': 'FR-01'},
                {'number': 'FR-03'},  # FR-02 dropped
                {'number': 'FR-04'},  # FR-04 added
            ],
        })
        dropped = pre - post
        added = post - pre
        assert dropped == {'FR-02'}
        assert added == {'FR-04'}
