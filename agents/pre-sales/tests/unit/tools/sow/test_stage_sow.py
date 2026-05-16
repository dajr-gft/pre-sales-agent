"""Unit tests for ``stage_sow``.

The tool is the single mutation point for ``state[STATE_SOW]`` /
``state[STATE_STAGE]`` and is also responsible for resetting the
QualityLoopAgent's round-tracking keys when the staged payload moves
across stages (``content`` -> ``full``). Without that reset the
aggregator would inflate ``round_count`` and falsely flag findings as
``persistent`` on the new payload.
"""

from __future__ import annotations

import pytest

from app.sub_agents.validation.schema import (
    STATE_PRIOR_BLOCKING_FINGERPRINTS,
    STATE_ROUND_COUNT,
    STATE_SOW,
    STATE_STAGE,
)
from app.tools.sow.stage_sow import stage_sow

pytestmark = pytest.mark.asyncio(loop_scope='module')


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_writes_sow_stage_and_returns_hash(mock_tool_context):
    payload = {'project_title': 'P', 'functional_requirements': []}

    result = await stage_sow(
        sow_data=payload, stage='content', tool_context=mock_tool_context
    )

    assert result['status'] == 'success'
    assert mock_tool_context.state[STATE_SOW] == payload
    assert mock_tool_context.state[STATE_STAGE] == 'content'
    assert result['data']['stage'] == 'content'
    assert isinstance(result['data']['sow_data_hash'], str)


async def test_records_language_when_provided(mock_tool_context):
    await stage_sow(
        sow_data={'project_title': 'P'},
        stage='content',
        language='pt-BR',
        tool_context=mock_tool_context,
    )
    assert mock_tool_context.state['app:language'] == 'pt-BR'


async def test_invalid_stage_falls_back_to_full(mock_tool_context):
    await stage_sow(
        sow_data={'project_title': 'P'},
        stage='garbage',
        tool_context=mock_tool_context,
    )
    assert mock_tool_context.state[STATE_STAGE] == 'full'


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


async def test_rejects_missing_tool_context():
    result = await stage_sow(sow_data={'project_title': 'P'}, stage='content')
    assert result['status'] == 'error'
    assert 'tool_context' in result['error']


async def test_rejects_non_dict_payload(mock_tool_context):
    result = await stage_sow(
        sow_data='not a dict',  # type: ignore[arg-type]
        stage='content',
        tool_context=mock_tool_context,
    )
    assert result['status'] == 'error'
    assert 'dict' in result['error']
    # And state must NOT have been mutated on rejection.
    assert STATE_SOW not in mock_tool_context.state


# ---------------------------------------------------------------------------
# F-02 — stage-change reset of round_count + prior blocking fingerprints
# ---------------------------------------------------------------------------


class TestStageChangeResetsRoundState:
    """The aggregator increments STATE_ROUND_COUNT monotonically across
    rounds within a single staged payload. When the orchestrator re-stages
    with a different stage value (typically content -> full), those
    counters refer to a SOW that no longer exists. Carrying them forward
    poisons persistence detection and inflates telemetry. F-02 fixes
    this by having stage_sow zero both keys whenever the stage changes.
    """

    async def test_content_to_full_resets_round_count(self, mock_tool_context):
        # Simulate prior content-stage validation completed: stage='content'
        # in state plus round_count from the aggregator.
        mock_tool_context.state[STATE_STAGE] = 'content'
        mock_tool_context.state[STATE_ROUND_COUNT] = 3
        mock_tool_context.state[STATE_PRIOR_BLOCKING_FINGERPRINTS] = [
            'fp-1',
            'fp-2',
        ]

        await stage_sow(
            sow_data={'project_title': 'P'},
            stage='full',
            tool_context=mock_tool_context,
        )

        assert mock_tool_context.state[STATE_ROUND_COUNT] == 0
        assert mock_tool_context.state[STATE_PRIOR_BLOCKING_FINGERPRINTS] == []
        assert mock_tool_context.state[STATE_STAGE] == 'full'

    async def test_full_to_content_also_resets(self, mock_tool_context):
        """Direction-agnostic reset — any change between the two valid
        stages clears the tracking keys."""
        mock_tool_context.state[STATE_STAGE] = 'full'
        mock_tool_context.state[STATE_ROUND_COUNT] = 5
        mock_tool_context.state[STATE_PRIOR_BLOCKING_FINGERPRINTS] = ['fp']

        await stage_sow(
            sow_data={'project_title': 'P'},
            stage='content',
            tool_context=mock_tool_context,
        )

        assert mock_tool_context.state[STATE_ROUND_COUNT] == 0
        assert mock_tool_context.state[STATE_PRIOR_BLOCKING_FINGERPRINTS] == []

    async def test_same_stage_re_stage_preserves_round_state(
        self, mock_tool_context
    ):
        """Within the same stage the aggregator's monotonic counter is
        the source of truth for "this finding has survived N rounds".
        Re-staging with the same stage (e.g. defensive call before the
        Phase 3 quality loop) must NOT reset it — otherwise the
        persistence signal is lost."""
        mock_tool_context.state[STATE_STAGE] = 'full'
        mock_tool_context.state[STATE_ROUND_COUNT] = 2
        mock_tool_context.state[STATE_PRIOR_BLOCKING_FINGERPRINTS] = [
            'fp-keep'
        ]

        await stage_sow(
            sow_data={'project_title': 'P'},
            stage='full',
            tool_context=mock_tool_context,
        )

        assert mock_tool_context.state[STATE_ROUND_COUNT] == 2
        assert mock_tool_context.state[STATE_PRIOR_BLOCKING_FINGERPRINTS] == [
            'fp-keep'
        ]

    async def test_first_call_does_not_seed_round_state(
        self, mock_tool_context
    ):
        """The very first stage_sow has no previous stage in state — it
        must NOT pre-create the round-tracking keys. The aggregator owns
        their initialization; doing it here would race the aggregator's
        first-run semantics."""
        # No STATE_STAGE in state; both round-tracking keys absent too.
        await stage_sow(
            sow_data={'project_title': 'P'},
            stage='content',
            tool_context=mock_tool_context,
        )

        assert STATE_ROUND_COUNT not in mock_tool_context.state
        assert STATE_PRIOR_BLOCKING_FINGERPRINTS not in mock_tool_context.state
        assert mock_tool_context.state[STATE_STAGE] == 'content'
