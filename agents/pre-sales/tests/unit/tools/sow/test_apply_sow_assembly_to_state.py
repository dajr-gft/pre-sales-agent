"""Tests for ``apply_sow_assembly_to_state``.

The helper is the QualityLoopAgent's path to re-materialise a SOW from
in-state bundles between repair rounds — same job as
``assemble_sow_payload`` + ``stage_sow`` back-to-back, but without
requiring a ToolContext (the loop runs as a ``BaseAgent`` outside the
tool surface). The guardrails matter as much as the happy path:

- ``app:language`` must survive untouched (production incident: the
  reviser flipped pt-BR to en between rounds via ``stage_sow``).
- ``STATE_REVISION_LOG`` must survive untouched (it accumulates across
  the entire loop lifetime; touching it from here would lose
  telemetry).
- ``STATE_ROUND_COUNT`` and ``STATE_PRIOR_BLOCKING_FINGERPRINTS`` must
  survive untouched — same-stage repair never resets them; only
  ``stage_sow`` does, and only when the stage actually changes.

The negative paths re-raise ``AssemblyError`` with the same structured
attributes the ADK tool wrapper consumes, so the loop can map them onto
its own diagnostic events.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.sub_agents.schemas import SOW_BUNDLE_STATE_KEYS
from app.sub_agents.validation.schema import (
    STATE_PRIOR_BLOCKING_FINGERPRINTS,
    STATE_ROUND_COUNT,
    STATE_SOW,
    STATE_STAGE,
)
from app.tools.sow._sow_helpers import apply_sow_assembly_to_state
from app.tools.sow.assemble_payload import AssemblyError


_LANGUAGE_KEY = 'app:language'
_REVISION_LOG_KEY = 'app:sow:revision_log'


# ---------------------------------------------------------------------------
# Bundle builders — minimal valid shapes the assembler accepts.
# ---------------------------------------------------------------------------


def _manifest() -> dict[str, Any]:
    return {
        'project': {
            'title': 'Data Platform',
            'customer_name': 'Acme',
            'partner_name': 'GFT',
            'funding_type': 'Google DAF',
        },
    }


def _requirements() -> dict[str, Any]:
    return {
        'functional_requirements': [
            {'number': 'FR-01', 'description': 'Ingest data.'},
        ],
        'non_functional_requirements': [
            {'number': 'NFR-01', 'description': 'TLS 1.3.'},
        ],
    }


def _delivery_plan() -> dict[str, Any]:
    return {
        'activity_phases': [
            {'name': 'Phase 1', 'description': 'Discovery.', 'tasks': []},
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
            {'activity': 'Phase 1', 'timeframe': 'W1', 'outcomes': 'Spec.'},
        ],
        'partner_roles': [
            {'role': 'PM', 'responsibilities': 'Owns plan.'},
        ],
        'customer_roles': [
            {'role': 'Sponsor', 'responsibilities': 'Approves.'},
        ],
        'success_criteria': ['Plan accepted.'],
        'objectives': ['Modernize.'],
    }


def _scope_boundaries() -> dict[str, Any]:
    return {
        'assumptions': ['Customer provides access.'],
        'out_of_scope': ['Hardware procurement.'],
        'risks': [{'description': 'SAP limits.', 'mitigation': 'Backoff.'}],
        'handover_disclaimers': ['KT week 10.'],
        'change_request_policy_text': 'CR requires approval.',
    }


def _architecture() -> dict[str, Any]:
    return {
        'architecture_description': 'Layered.',
        'architecture_components': [{'name': 'CR', 'role': 'API.'}],
        'architecture_integrations': [{'name': 'SAP', 'description': 'Src.'}],
        'technology_stack': [{'service': 'BQ', 'purpose': 'Warehouse.'}],
    }


def _narrative() -> dict[str, Any]:
    return {
        'executive_summary': 'A platform.',
        'partner_overview': 'GFT.',
        'customer_overview': 'Acme.',
        'customer_primary_domain': 'Retail',
    }


def _populated_state(stage: str = 'content') -> dict[str, Any]:
    state: dict[str, Any] = {
        SOW_BUNDLE_STATE_KEYS['manifest']: _manifest(),
        SOW_BUNDLE_STATE_KEYS['requirements']: _requirements(),
        SOW_BUNDLE_STATE_KEYS['delivery_plan']: _delivery_plan(),
        SOW_BUNDLE_STATE_KEYS['scope_boundaries']: _scope_boundaries(),
    }
    if stage == 'full':
        state[SOW_BUNDLE_STATE_KEYS['architecture']] = _architecture()
        state[SOW_BUNDLE_STATE_KEYS['narrative']] = _narrative()
    return state


# ---------------------------------------------------------------------------
# Happy path — writes STATE_SOW + STATE_STAGE, returns the same dict
# ---------------------------------------------------------------------------


class TestHappyPath:
    @pytest.mark.parametrize('stage', ['content', 'full'])
    def test_writes_state_sow_and_returns_same_dict(self, stage):
        state = _populated_state(stage)

        result = apply_sow_assembly_to_state(state, stage)

        assert state[STATE_SOW] is result
        assert state[STATE_STAGE] == stage
        # Spot-check the content was assembled, not just stubbed.
        assert result['functional_requirements'][0]['number'] == 'FR-01'

    def test_full_stage_includes_architecture_and_narrative(self):
        state = _populated_state('full')

        result = apply_sow_assembly_to_state(state, 'full')

        assert 'architecture_description' in result
        assert 'executive_summary' in result

    def test_content_stage_omits_architecture_and_narrative(self):
        """Content stage assembly must NOT carry forward arch / narrative
        keys even if a prior full-stage bundle is hanging around in
        state — those keys belong to a later phase and inflating them
        early triggers stage-gate findings."""
        state = _populated_state('content')
        # Add stale architecture / narrative — assembler should ignore.
        state[SOW_BUNDLE_STATE_KEYS['architecture']] = _architecture()
        state[SOW_BUNDLE_STATE_KEYS['narrative']] = _narrative()

        result = apply_sow_assembly_to_state(state, 'content')

        assert 'architecture_description' not in result
        assert 'executive_summary' not in result

    def test_normalises_case_and_whitespace_on_stage(self):
        """Defensive parity with ``stage_sow``: ``'  CONTENT  '`` is
        accepted because the helper does the same ``.strip().lower()``."""
        state = _populated_state('content')

        result = apply_sow_assembly_to_state(state, '  CONTENT  ')

        assert state[STATE_STAGE] == 'content'
        assert result['functional_requirements']


# ---------------------------------------------------------------------------
# Guardrails — sibling state keys must survive the helper untouched
# ---------------------------------------------------------------------------


class TestSideEffectGuardrails:
    """The helper exists specifically so the QualityLoopAgent can re-
    materialise the SOW without triggering the wide blast radius of
    ``stage_sow`` (which manages round counters + language). Each test
    seeds one sibling key and asserts the helper leaves it alone."""

    def test_does_not_touch_app_language(self):
        """Production incident replay: the reviser called stage_sow with
        ``language='en'`` between rounds and overrode the user's
        'pt-BR'. This helper must NOT have an equivalent code path."""
        state = _populated_state('content')
        state[_LANGUAGE_KEY] = 'pt-BR'

        apply_sow_assembly_to_state(state, 'content')

        assert state[_LANGUAGE_KEY] == 'pt-BR'

    def test_does_not_touch_revision_log(self):
        """The revision log accumulates across the entire loop; clearing
        it here would lose telemetry the root reads after the loop ends
        to compose the Revision Note."""
        state = _populated_state('content')
        seeded_log = [
            {'finding_id': 'x-1', 'action': 'refinement'},
            {'finding_id': 'x-2', 'action': 'addition'},
        ]
        state[_REVISION_LOG_KEY] = seeded_log

        apply_sow_assembly_to_state(state, 'content')

        assert state[_REVISION_LOG_KEY] is seeded_log

    def test_does_not_reset_round_count_on_same_stage_repair(self):
        """Same-stage repair is the intended use — the round counter
        must keep ticking so persistence / no-progress detection stays
        coherent. (Different-stage transitions are handled by
        ``stage_sow``, not this helper.)"""
        state = _populated_state('content')
        state[STATE_STAGE] = 'content'
        state[STATE_ROUND_COUNT] = 3

        apply_sow_assembly_to_state(state, 'content')

        assert state[STATE_ROUND_COUNT] == 3

    def test_does_not_reset_prior_blocking_fingerprints(self):
        """The aggregator owns this list — the loop relies on it to
        flag findings as ``persistent``. Wiping it mid-loop would mark
        every finding as ``new`` next round and starve the no-progress
        detector of signal."""
        state = _populated_state('content')
        state[STATE_STAGE] = 'content'
        state[STATE_PRIOR_BLOCKING_FINGERPRINTS] = ['fp-a', 'fp-b']

        apply_sow_assembly_to_state(state, 'content')

        assert state[STATE_PRIOR_BLOCKING_FINGERPRINTS] == ['fp-a', 'fp-b']


# ---------------------------------------------------------------------------
# Failure paths — AssemblyError carries the structured attributes the
# tool wrapper and the loop both consume
# ---------------------------------------------------------------------------


class TestAssemblyErrors:
    def test_unknown_stage_raises(self):
        state = _populated_state('content')

        with pytest.raises(AssemblyError) as exc:
            apply_sow_assembly_to_state(state, 'garbage')

        # Unknown stage produces no structured attrs — just the reason.
        assert 'garbage' in exc.value.reason
        assert exc.value.missing_keys == []

    def test_missing_bundle_raises_with_missing_keys_attribute(self):
        state = _populated_state('content')
        # Wipe one bundle.
        del state[SOW_BUNDLE_STATE_KEYS['delivery_plan']]

        with pytest.raises(AssemblyError) as exc:
            apply_sow_assembly_to_state(state, 'content')

        assert SOW_BUNDLE_STATE_KEYS['delivery_plan'] in exc.value.missing_keys

    def test_sentinel_in_bundle_raises_with_sentinel_keys_attribute(self):
        """The MISSING_INPUT sentinel a section worker emits when its
        upstream is missing must surface as a sentinel error here too —
        same shape the assemble_sow_payload tool reports."""
        state = _populated_state('content')
        state[SOW_BUNDLE_STATE_KEYS['delivery_plan']] = {
            'activity_phases': [{'name': 'MISSING_INPUT', 'description': '', 'tasks': []}],
            'deliverables': [],
            'timeline': [],
            'partner_roles': [],
            'customer_roles': [],
            'success_criteria': [],
        }

        with pytest.raises(AssemblyError) as exc:
            apply_sow_assembly_to_state(state, 'content')

        assert SOW_BUNDLE_STATE_KEYS['delivery_plan'] in exc.value.sentinel_keys

    def test_failure_does_not_mutate_state(self):
        """When assembly fails, ``STATE_SOW`` / ``STATE_STAGE`` MUST NOT
        be partially written — a caller seeing the exception expects
        state to be exactly as it was before the call."""
        state = _populated_state('content')
        del state[SOW_BUNDLE_STATE_KEYS['delivery_plan']]
        state['unrelated_key'] = 'untouched'

        with pytest.raises(AssemblyError):
            apply_sow_assembly_to_state(state, 'content')

        assert STATE_SOW not in state
        assert STATE_STAGE not in state
        assert state['unrelated_key'] == 'untouched'


class TestLegacyBundleMigration:
    """Phase 0.5 added required ``number`` ids to ``Deliverable`` and
    ``Risk``. Sessions that wrote bundles to state before the deploy
    must still re-assemble — the migration path runs
    ``ensure_collection_numbers`` on the raw bundle dict before
    :func:`build_sow_data_from_state` validates anything."""

    def test_migrates_deliverables_without_number(self):
        state = _populated_state('content')
        # Wipe ``number`` from the seeded deliverable to simulate a
        # legacy bundle.
        for d in state[SOW_BUNDLE_STATE_KEYS['delivery_plan']]['deliverables']:
            d.pop('number', None)

        result = apply_sow_assembly_to_state(state, 'content')

        # Migration path injected the id, both in the in-state bundle
        # and in the assembled flat SOW.
        injected_numbers = [
            d['number']
            for d in state[SOW_BUNDLE_STATE_KEYS['delivery_plan']]['deliverables']
        ]
        assert all(n.startswith('WS-') for n in injected_numbers)
        assert result['deliverables'][0]['number'] == injected_numbers[0]

    def test_migrates_risks_without_number(self):
        state = _populated_state('content')
        for r in state[SOW_BUNDLE_STATE_KEYS['scope_boundaries']]['risks']:
            r.pop('number', None)

        result = apply_sow_assembly_to_state(state, 'content')

        injected = [
            r['number']
            for r in state[SOW_BUNDLE_STATE_KEYS['scope_boundaries']]['risks']
        ]
        assert all(n.startswith('R-') for n in injected)
        assert result['risks'][0]['number'] == injected[0]


def _populated_state_with_numbers(stage: str = 'content') -> dict[str, Any]:
    return _populated_state(stage)


class TestFlatSowCarriesNumbers:
    """Downstream impact pin: the assembled flat ``sow_data`` carries
    the ``number`` field for each deliverable and risk. The DOCX
    template renders these ids in the final document — confirm here
    that assembly does not strip them."""

    def test_deliverable_numbers_appear_in_assembled_sow(self):
        state = _populated_state('content')
        # Seed an explicit number so the test is deterministic.
        state[SOW_BUNDLE_STATE_KEYS['delivery_plan']]['deliverables'][0][
            'number'
        ] = 'WS-42'

        result = apply_sow_assembly_to_state(state, 'content')

        assert result['deliverables'][0]['number'] == 'WS-42'

    def test_risk_numbers_appear_in_assembled_sow(self):
        state = _populated_state('content')
        state[SOW_BUNDLE_STATE_KEYS['scope_boundaries']]['risks'][0][
            'number'
        ] = 'R-07'

        result = apply_sow_assembly_to_state(state, 'content')

        assert result['risks'][0]['number'] == 'R-07'
