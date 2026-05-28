"""Unit tests for ``stage_sow``.

The tool now owns BOTH assembly and staging: it reads the per-section
bundles + metadata envelope from session state, calls
``build_sow_data_from_state`` internally to build the flat payload,
and persists the result under ``state[STATE_SOW]`` together with the
stage cursor. The model no longer re-emits the payload — that closed
the round-trip drift window where the LLM could drop fields or pass a
stale dict between the assembler and the staging write.

It is also the single mutation point for ``state[STATE_SOW]`` /
``state[STATE_STAGE]`` and is responsible for resetting the
QualityLoopAgent's round-tracking keys when the staged payload moves
across stages (``content`` -> ``full``). Without that reset the
aggregator would inflate ``round_count`` and falsely flag findings as
``persistent`` on the new payload.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.sub_agents.schemas import (
    SOW_BUNDLE_STATE_KEYS,
    SOW_METADATA_STATE_KEY,
)
from app.sub_agents.validation.schema import (
    STATE_PRIOR_BLOCKING_FINGERPRINTS,
    STATE_ROUND_COUNT,
    STATE_SOW,
    STATE_STAGE,
)
from app.tools.sow.stage_sow import stage_sow

pytestmark = pytest.mark.asyncio(loop_scope='module')


# ---------------------------------------------------------------------------
# Builders — minimal canonical bundles the assembler accepts. Duplicated
# locally rather than imported from ``test_assemble_payload`` so the two
# suites can drift independently when their concerns diverge.
# ---------------------------------------------------------------------------


def _metadata_envelope() -> dict[str, Any]:
    return {
        'project_title': 'Data Analytics Platform',
        'customer_name': 'Acme Corp',
        'partner_name': 'GFT Technologies',
        'partner_short_name': 'GFT',
        'customer_short_name': 'Acme',
        'date': '2026-04-15',
        'author': 'Test Author',
        'funding_type': 'Google DAF',
        'funding_type_short': 'DAF',
        'project_start_date': '2026-05-01',
        'project_end_date': '2026-07-10',
        'engagement_type': 'project',
        'organization_term': 'phases',
    }


def _requirements_bundle() -> dict[str, Any]:
    return {
        'functional_requirements': [
            {'number': 'FR-01', 'description': 'Ingest data from SAP.'},
        ],
        'non_functional_requirements': [
            {'number': 'NFR-01', 'description': 'TLS 1.3.'},
        ],
    }


def _delivery_plan_bundle() -> dict[str, Any]:
    return {
        'activity_phases': [
            {'name': 'Phase 1', 'description': 'Discovery.', 'tasks': []}
        ],
        'deliverables': [
            {
                'activity': 'Phase 1',
                'name': 'Doc',
                'description': 'Spec.',
                'format': 'Document',
            },
        ],
        'timeline': [
            {'activity': 'Phase 1', 'timeframe': 'W1-2', 'outcomes': 'Done.'}
        ],
        'partner_roles': [{'role': 'PM', 'responsibilities': 'Owns plan.'}],
        'customer_roles': [
            {'role': 'Sponsor', 'responsibilities': 'Approves.'}
        ],
        'success_criteria': ['Plan accepted.'],
        'objectives': ['Modernize.'],
    }


def _scope_bundle() -> dict[str, Any]:
    return {
        'assumptions': ['Customer provides access.'],
        'out_of_scope': ['Hardware procurement.'],
        'risks': [
            {'description': 'SAP rate limits.', 'mitigation': 'Backoff.'}
        ],
        'handover_disclaimers': ['Knowledge transfer in week 10.'],
        'change_request_policy_text': 'Any change requires written approval.',
    }


def _architecture_bundle() -> dict[str, Any]:
    return {
        'architecture_description': 'Layered architecture.',
        'architecture_components': [
            {'name': 'Cloud Run', 'role': 'API host.'}
        ],
        'architecture_integrations': [
            {'name': 'SAP', 'description': 'Source.'}
        ],
        'technology_stack': [
            {'service': 'BigQuery', 'purpose': 'Warehouse.'}
        ],
    }


def _narrative_bundle() -> dict[str, Any]:
    return {
        'executive_summary': 'Modernizes data.',
        'partner_overview': 'GFT premier partner.',
        'customer_overview': 'Acme manufactures.',
        'customer_primary_domain': 'acme.com',
    }


def _populate_content_state(ctx) -> None:
    ctx.state[SOW_METADATA_STATE_KEY] = _metadata_envelope()
    ctx.state[SOW_BUNDLE_STATE_KEYS['requirements']] = _requirements_bundle()
    ctx.state[SOW_BUNDLE_STATE_KEYS['delivery_plan']] = _delivery_plan_bundle()
    ctx.state[SOW_BUNDLE_STATE_KEYS['scope_boundaries']] = _scope_bundle()


def _populate_full_state(ctx) -> None:
    _populate_content_state(ctx)
    ctx.state[SOW_BUNDLE_STATE_KEYS['architecture']] = _architecture_bundle()
    ctx.state[SOW_BUNDLE_STATE_KEYS['narrative']] = _narrative_bundle()


# ---------------------------------------------------------------------------
# Happy path — assembly + staging happen in one call.
# ---------------------------------------------------------------------------


async def test_writes_assembled_sow_and_returns_hash(mock_tool_context):
    _populate_content_state(mock_tool_context)

    result = await stage_sow(stage='content', tool_context=mock_tool_context)

    assert result['status'] == 'success'
    assert result['data']['stage'] == 'content'
    assert isinstance(result['data']['sow_data_hash'], str)

    # State now carries the assembled flat payload, not whatever the
    # caller passed — the whole point of the refactor.
    staged = mock_tool_context.state[STATE_SOW]
    assert isinstance(staged, dict)
    assert staged['project_title'] == 'Data Analytics Platform'
    assert staged['functional_requirements'][0]['number'] == 'FR-01'
    assert staged['out_of_scope'] == ['Hardware procurement.']
    assert mock_tool_context.state[STATE_STAGE] == 'content'


async def test_return_includes_assembled_sow_for_caller_visibility(
    mock_tool_context,
):
    """The return payload carries the assembled ``sow_data`` so the root
    has the post-assembly content in its conversation context when it
    presents the Content / Architecture Review gates. Without this, the
    root would have to render the review from its memory of the
    ``save_<section>_bundle`` calls — which goes stale the moment the
    quality loop's repair agents patch a bundle. State alone is not
    enough: the root cannot query state directly (only
    ``conversation_language`` is injected). The returned dict is a
    READ-ONLY freshness signal — the caller must not re-emit it as an
    argument to any tool (the round-trip is precisely what the assembly
    move into stage_sow eliminated)."""
    _populate_full_state(mock_tool_context)

    result = await stage_sow(stage='full', tool_context=mock_tool_context)

    returned = result['data']['sow_data']
    # The returned dict is the same shape as what landed in state.
    assert returned == mock_tool_context.state[STATE_SOW]
    # Spot-check both content-stage and full-stage fields so the contract
    # surfaces if a future change accidentally truncates the return.
    assert returned['functional_requirements'][0]['number'] == 'FR-01'
    assert returned['architecture_description'] == 'Layered architecture.'
    assert returned['executive_summary'] == 'Modernizes data.'


async def test_full_stage_assembles_architecture_and_narrative(
    mock_tool_context,
):
    _populate_full_state(mock_tool_context)

    result = await stage_sow(stage='full', tool_context=mock_tool_context)

    assert result['status'] == 'success'
    staged = mock_tool_context.state[STATE_SOW]
    assert staged['architecture_description'] == 'Layered architecture.'
    assert staged['executive_summary'] == 'Modernizes data.'
    assert staged['customer_primary_domain'] == 'acme.com'
    assert mock_tool_context.state[STATE_STAGE] == 'full'


async def test_records_language_when_provided(mock_tool_context):
    _populate_content_state(mock_tool_context)

    await stage_sow(
        stage='content',
        language='pt-BR',
        tool_context=mock_tool_context,
    )
    assert mock_tool_context.state['app:language'] == 'pt-BR'


# ---------------------------------------------------------------------------
# Language preservation — the orchestrator owns ``app:language``; sub-
# agents that re-stage during the quality loop must NOT clobber it.
#
# Production incident: revision_agent called ``stage_sow(..., language='en')``
# between rounds and overrode the user's 'pt-BR', flipping the root's
# subsequent responses to English. The rule: set only when the slot is
# empty; ignore any non-empty argument that disagrees with the value
# already in state.
# ---------------------------------------------------------------------------


class TestLanguagePreservation:
    async def test_existing_language_is_not_overwritten_by_a_different_value(
        self, mock_tool_context
    ):
        """The exact production scenario — root staged 'pt-BR' first,
        reviser tried to re-stage with 'en' a round later."""
        _populate_content_state(mock_tool_context)
        mock_tool_context.state['app:language'] = 'pt-BR'

        await stage_sow(
            stage='content',
            language='en',
            tool_context=mock_tool_context,
        )

        assert mock_tool_context.state['app:language'] == 'pt-BR', (
            "Language was overridden by a re-staging call — the root's "
            'conversation language must survive sub-agent re-stages.'
        )

    async def test_existing_language_is_kept_when_attempt_matches(
        self, mock_tool_context
    ):
        """When the new value equals what is already in state, the no-op
        path is silent — no warning, no rewrite needed."""
        _populate_content_state(mock_tool_context)
        mock_tool_context.state['app:language'] = 'pt-BR'

        await stage_sow(
            stage='content',
            language='pt-BR',
            tool_context=mock_tool_context,
        )

        assert mock_tool_context.state['app:language'] == 'pt-BR'

    async def test_empty_language_argument_never_overrides(
        self, mock_tool_context
    ):
        """The reviser typically omits ``language=`` (default ''). That
        must NOT clear an existing setting — the only writer is the
        first non-empty caller."""
        _populate_content_state(mock_tool_context)
        mock_tool_context.state['app:language'] = 'pt-BR'

        await stage_sow(
            stage='content',
            tool_context=mock_tool_context,
        )

        assert mock_tool_context.state['app:language'] == 'pt-BR'

    async def test_first_writer_seeds_language(self, mock_tool_context):
        """When state has no language yet, a non-empty value is accepted
        — this is the root's first stage at the start of the session."""
        _populate_content_state(mock_tool_context)
        assert 'app:language' not in mock_tool_context.state

        await stage_sow(
            stage='content',
            language='pt-BR',
            tool_context=mock_tool_context,
        )

        assert mock_tool_context.state['app:language'] == 'pt-BR'


# ---------------------------------------------------------------------------
# Error paths — invalid stage MUST be rejected, not silently coerced.
#
# History: the old behaviour fell back to ``'full'`` whenever the input
# was unrecognised (or omitted altogether). The revision_agent's SKILL.md
# called ``stage_sow(patched_sow_data)`` without the keyword, the default
# ``stage='full'`` kicked in, and a content-stage validation got promoted
# to full mid-loop. That reset STATE_ROUND_COUNT /
# STATE_PRIOR_BLOCKING_FINGERPRINTS and re-introduced architecture /
# narrative findings the content-stage validation correctly suppressed.
# These tests pin the new strict-rejection behaviour so any future
# loosening surfaces immediately.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    'bad_stage',
    [
        'garbage',
        '',
        '  ',
        'partial',
        # The literal sentinel the revision provider renders when the
        # stage is absent from state — if the LLM ever echoed it back as
        # the stage argument, we want the tool to reject loudly instead
        # of staging anything.
        '__MISSING__',
    ],
)
async def test_rejects_invalid_stage(mock_tool_context, bad_stage):
    """Anything that does not normalise to ``'content'`` or ``'full'`` is
    rejected. Upper-case / surrounding whitespace IS tolerated because
    ``.strip().lower()`` runs first — so ``'FULL '`` normalises to
    ``'full'`` and stages successfully. The cases pinned here are
    values that cannot survive that normalisation and therefore must
    surface as errors instead of silently falling back to ``'full'``."""
    _populate_full_state(mock_tool_context)

    result = await stage_sow(
        stage=bad_stage,
        tool_context=mock_tool_context,
    )
    assert result['status'] == 'error'
    assert 'content' in result['error'] and 'full' in result['error']
    # State must NOT have been written — the caller has to retry with a
    # valid stage. Silent fallback is the failure mode we are killing.
    assert STATE_SOW not in mock_tool_context.state
    assert STATE_STAGE not in mock_tool_context.state


async def test_rejects_missing_tool_context():
    result = await stage_sow(stage='content')
    assert result['status'] == 'error'
    assert 'tool_context' in result['error']


# ---------------------------------------------------------------------------
# Assembly precondition errors — propagated from build_sow_data_from_state
# as ToolError with actionable suggestions, and state is NOT mutated.
# ---------------------------------------------------------------------------


class TestAssemblyPreconditions:
    async def test_missing_bundle_returns_error_without_mutating_state(
        self, mock_tool_context
    ):
        _populate_content_state(mock_tool_context)
        del mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['requirements']]

        result = await stage_sow(
            stage='content', tool_context=mock_tool_context,
        )

        assert result['status'] == 'error'
        assert SOW_BUNDLE_STATE_KEYS['requirements'] in result['suggestion']
        # The failure path must leave STATE_SOW / STATE_STAGE untouched —
        # a half-staged session would mislead the quality loop into
        # validating a stale payload.
        assert STATE_SOW not in mock_tool_context.state
        assert STATE_STAGE not in mock_tool_context.state

    async def test_missing_metadata_envelope_returns_error(
        self, mock_tool_context
    ):
        mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['requirements']] = (
            _requirements_bundle()
        )
        mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['delivery_plan']] = (
            _delivery_plan_bundle()
        )
        mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['scope_boundaries']] = (
            _scope_bundle()
        )

        result = await stage_sow(
            stage='content', tool_context=mock_tool_context,
        )

        assert result['status'] == 'error'
        assert 'metadata' in result['error'].lower()
        assert 'save_sow_metadata' in result['suggestion']
        assert STATE_SOW not in mock_tool_context.state

    async def test_sentinel_in_bundle_returns_error(self, mock_tool_context):
        """When a section worker emits the ``MISSING_INPUT`` sentinel
        (its aborted-because-upstream-missing fallback), stage_sow must
        refuse to stage anything — burning a critic round on a SOW the
        orchestrator already knows is incomplete is exactly what the
        sentinel exists to prevent."""
        _populate_content_state(mock_tool_context)
        mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['scope_boundaries']] = {
            'assumptions': [],
            'out_of_scope': [],
            'risks': [],
            'handover_disclaimers': [],
            'change_request_policy_text': 'MISSING_INPUT',
        }

        result = await stage_sow(
            stage='content', tool_context=mock_tool_context,
        )

        assert result['status'] == 'error'
        assert 'MISSING_INPUT' in result['error']
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
        # Bundles for FULL stage need to be present (we are staging full).
        _populate_full_state(mock_tool_context)
        # Simulate prior content-stage validation completed: stage='content'
        # in state plus round_count from the aggregator.
        mock_tool_context.state[STATE_STAGE] = 'content'
        mock_tool_context.state[STATE_ROUND_COUNT] = 3
        mock_tool_context.state[STATE_PRIOR_BLOCKING_FINGERPRINTS] = [
            'fp-1',
            'fp-2',
        ]

        await stage_sow(
            stage='full',
            tool_context=mock_tool_context,
        )

        assert mock_tool_context.state[STATE_ROUND_COUNT] == 0
        assert mock_tool_context.state[STATE_PRIOR_BLOCKING_FINGERPRINTS] == []
        assert mock_tool_context.state[STATE_STAGE] == 'full'

    async def test_full_to_content_also_resets(self, mock_tool_context):
        """Direction-agnostic reset — any change between the two valid
        stages clears the tracking keys."""
        _populate_content_state(mock_tool_context)
        mock_tool_context.state[STATE_STAGE] = 'full'
        mock_tool_context.state[STATE_ROUND_COUNT] = 5
        mock_tool_context.state[STATE_PRIOR_BLOCKING_FINGERPRINTS] = ['fp']

        await stage_sow(
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
        _populate_full_state(mock_tool_context)
        mock_tool_context.state[STATE_STAGE] = 'full'
        mock_tool_context.state[STATE_ROUND_COUNT] = 2
        mock_tool_context.state[STATE_PRIOR_BLOCKING_FINGERPRINTS] = [
            'fp-keep'
        ]

        await stage_sow(
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
        _populate_content_state(mock_tool_context)

        await stage_sow(
            stage='content',
            tool_context=mock_tool_context,
        )

        assert STATE_ROUND_COUNT not in mock_tool_context.state
        assert STATE_PRIOR_BLOCKING_FINGERPRINTS not in mock_tool_context.state
        assert mock_tool_context.state[STATE_STAGE] == 'content'

    async def test_assembly_failure_does_not_reset_round_state(
        self, mock_tool_context
    ):
        """Atomicity check: if assembly fails, no state mutation must
        happen — neither STATE_SOW, nor STATE_STAGE, nor the round
        tracking keys. A failed stage call must be a true no-op."""
        # Set up a prior stage and counters as if a content-stage loop
        # were in flight. Then attempt to re-stage as 'full' without the
        # architecture / narrative bundles in state.
        _populate_content_state(mock_tool_context)
        mock_tool_context.state[STATE_STAGE] = 'content'
        mock_tool_context.state[STATE_ROUND_COUNT] = 2
        mock_tool_context.state[STATE_PRIOR_BLOCKING_FINGERPRINTS] = ['fp']

        result = await stage_sow(
            stage='full',
            tool_context=mock_tool_context,
        )

        assert result['status'] == 'error'
        # No reset — the counters from the in-flight content stage must
        # survive a failed full-stage attempt.
        assert mock_tool_context.state[STATE_ROUND_COUNT] == 2
        assert mock_tool_context.state[STATE_PRIOR_BLOCKING_FINGERPRINTS] == [
            'fp'
        ]
        assert mock_tool_context.state[STATE_STAGE] == 'content'
