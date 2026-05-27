"""Unit tests for ``assemble_sow_payload``.

The tool is the only place where bundles from per-section sub-agents
are translated into the flat ``sow_data`` schema that ``stage_sow`` and
``generate_sow_document`` expect. Bugs here silently corrupt the SOW,
so coverage focuses on both stages and every "missing bundle" path.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.sub_agents.schemas import (
    SOW_BUNDLE_STATE_KEYS,
    SOW_METADATA_STATE_KEY,
)
from app.tools.sow.assemble_payload import assemble_sow_payload


# ---------------------------------------------------------------------------
# Builders — return canonical bundle dicts the assembler can consume.
# ---------------------------------------------------------------------------


def _metadata_envelope() -> dict[str, Any]:
    """The ``app:sow:metadata`` envelope written by ``save_sow_metadata``."""
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
        'activity_phases': [{'name': 'Phase 1', 'description': 'Discovery.', 'tasks': []}],
        'deliverables': [
            {'activity': 'Phase 1', 'name': 'Doc', 'description': 'Spec.', 'format': 'Document'},
        ],
        'timeline': [{'activity': 'Phase 1', 'timeframe': 'W1-2', 'outcomes': 'Spec done.'}],
        'partner_roles': [{'role': 'PM', 'responsibilities': 'Owns plan.'}],
        'customer_roles': [{'role': 'Sponsor', 'responsibilities': 'Approves.'}],
        'success_criteria': ['Plan accepted.'],
        'objectives': ['Modernize.'],
    }


def _scope_bundle() -> dict[str, Any]:
    return {
        'assumptions': ['Customer provides access.'],
        'out_of_scope': ['Hardware procurement.'],
        'risks': [{'description': 'SAP rate limits.', 'mitigation': 'Backoff.'}],
        'handover_disclaimers': ['Knowledge transfer in week 10.'],
        'change_request_policy_text': 'Any change requires written approval.',
    }


def _architecture_bundle() -> dict[str, Any]:
    return {
        'architecture_description': 'Layered architecture.',
        'architecture_components': [{'name': 'Cloud Run', 'role': 'API host.'}],
        'architecture_integrations': [{'name': 'SAP', 'description': 'Source.'}],
        'technology_stack': [{'service': 'BigQuery', 'purpose': 'Warehouse.'}],
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
# Happy paths
# ---------------------------------------------------------------------------


class TestContentStage:
    async def test_returns_success_with_content_keys(self, mock_tool_context):
        _populate_content_state(mock_tool_context)

        result = await assemble_sow_payload(
            stage='content',
            tool_context=mock_tool_context,
        )

        assert result['status'] == 'success'
        data = result['data']
        assert data['stage'] == 'content'
        sow = data['sow_data']
        assert sow['functional_requirements'][0]['number'] == 'FR-01'
        assert sow['activity_phases'][0]['name'] == 'Phase 1'
        assert sow['out_of_scope'] == ['Hardware procurement.']

    async def test_omits_architecture_and_narrative_keys(self, mock_tool_context):
        _populate_content_state(mock_tool_context)

        result = await assemble_sow_payload(
            stage='content',
            tool_context=mock_tool_context,
        )

        sow = result['data']['sow_data']
        for key in (
            'architecture_description',
            'architecture_components',
            'technology_stack',
            'executive_summary',
            'partner_overview',
            'customer_overview',
        ):
            assert key not in sow, f'{key} must be absent in content stage'

    async def test_succeeds_when_arch_and_narrative_missing(self, mock_tool_context):
        """Content stage must NOT require architecture or narrative bundles."""
        _populate_content_state(mock_tool_context)

        result = await assemble_sow_payload(
            stage='content',
            tool_context=mock_tool_context,
        )

        assert result['status'] == 'success'

    async def test_populates_project_metadata_from_envelope(
        self, mock_tool_context
    ):
        _populate_content_state(mock_tool_context)

        result = await assemble_sow_payload(
            stage='content',
            tool_context=mock_tool_context,
        )

        sow = result['data']['sow_data']
        assert sow['project_title'] == 'Data Analytics Platform'
        assert sow['customer_name'] == 'Acme Corp'
        assert sow['project_start_date'] == '2026-05-01'


class TestMetadataEnvelopeSource:
    """Project metadata comes from the explicit ``app:sow:metadata``
    envelope written by ``save_sow_metadata``."""

    def _metadata_envelope(self) -> dict[str, Any]:
        return {
            'partner_name': 'GFT Technologies',
            'customer_name': 'Acme Corp',
            'partner_short_name': 'GFT',
            'customer_short_name': 'Acme',
            'project_title': 'Root-Skills SOW',
            'date': '2026-05-27',
            'author': 'Root Agent',
            'funding_type': 'Google DAF',
            'funding_type_short': 'DAF',
            'project_start_date': '2026-06-01',
            'project_end_date': '2026-08-01',
            'engagement_type': 'project',
            'organization_term': 'phases',
        }

    async def test_assembles_from_envelope_without_manifest(
        self, mock_tool_context
    ):
        mock_tool_context.state[SOW_METADATA_STATE_KEY] = self._metadata_envelope()
        mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['requirements']] = _requirements_bundle()
        mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['delivery_plan']] = _delivery_plan_bundle()
        mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['scope_boundaries']] = _scope_bundle()

        result = await assemble_sow_payload(
            stage='content',
            tool_context=mock_tool_context,
        )

        assert result['status'] == 'success'
        sow = result['data']['sow_data']
        assert sow['project_title'] == 'Root-Skills SOW'
        assert sow['partner_name'] == 'GFT Technologies'
        assert sow['project_start_date'] == '2026-06-01'

    async def test_no_metadata_envelope_rejected(self, mock_tool_context):
        # Bundles present but no metadata envelope.
        mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['requirements']] = _requirements_bundle()
        mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['delivery_plan']] = _delivery_plan_bundle()
        mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['scope_boundaries']] = _scope_bundle()

        result = await assemble_sow_payload(
            stage='content',
            tool_context=mock_tool_context,
        )

        assert result['status'] == 'error'
        assert 'metadata' in result['error'].lower()


class TestFullStage:
    async def test_returns_all_section_keys(self, mock_tool_context):
        _populate_full_state(mock_tool_context)

        result = await assemble_sow_payload(
            stage='full',
            tool_context=mock_tool_context,
        )

        assert result['status'] == 'success'
        sow = result['data']['sow_data']
        assert sow['architecture_description'] == 'Layered architecture.'
        assert sow['technology_stack'][0]['service'] == 'BigQuery'
        assert sow['executive_summary'] == 'Modernizes data.'
        assert sow['customer_primary_domain'] == 'acme.com'

    async def test_content_keys_are_subset_of_full(self, mock_tool_context):
        """Full assembly must preserve every key the content assembly produced."""
        _populate_content_state(mock_tool_context)
        content = await assemble_sow_payload(
            stage='content', tool_context=mock_tool_context,
        )

        _populate_full_state(mock_tool_context)
        full = await assemble_sow_payload(
            stage='full', tool_context=mock_tool_context,
        )

        for key, value in content['data']['sow_data'].items():
            assert key in full['data']['sow_data']
            assert full['data']['sow_data'][key] == value


# ---------------------------------------------------------------------------
# Missing-bundle paths — error message must tell the caller exactly what's wrong.
# ---------------------------------------------------------------------------


class TestMissingBundles:
    async def test_content_stage_missing_requirements(self, mock_tool_context):
        _populate_content_state(mock_tool_context)
        del mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['requirements']]

        result = await assemble_sow_payload(
            stage='content',
            tool_context=mock_tool_context,
        )

        assert result['status'] == 'error'
        assert SOW_BUNDLE_STATE_KEYS['requirements'] in result['suggestion']

    async def test_content_stage_missing_delivery_plan(self, mock_tool_context):
        _populate_content_state(mock_tool_context)
        del mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['delivery_plan']]

        result = await assemble_sow_payload(
            stage='content',
            tool_context=mock_tool_context,
        )

        assert result['status'] == 'error'
        assert SOW_BUNDLE_STATE_KEYS['delivery_plan'] in result['suggestion']

    async def test_full_stage_fails_without_architecture(self, mock_tool_context):
        _populate_content_state(mock_tool_context)
        mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['narrative']] = _narrative_bundle()
        # architecture intentionally absent

        result = await assemble_sow_payload(
            stage='full',
            tool_context=mock_tool_context,
        )

        assert result['status'] == 'error'
        assert SOW_BUNDLE_STATE_KEYS['architecture'] in result['suggestion']

    async def test_full_stage_fails_without_narrative(self, mock_tool_context):
        _populate_content_state(mock_tool_context)
        mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['architecture']] = _architecture_bundle()
        # narrative intentionally absent

        result = await assemble_sow_payload(
            stage='full',
            tool_context=mock_tool_context,
        )

        assert result['status'] == 'error'
        assert SOW_BUNDLE_STATE_KEYS['narrative'] in result['suggestion']


# ---------------------------------------------------------------------------
# Argument and state-shape validation
# ---------------------------------------------------------------------------


class TestArgValidation:
    async def test_unknown_stage_rejected(self, mock_tool_context):
        _populate_full_state(mock_tool_context)

        result = await assemble_sow_payload(
            stage='partial',  # type: ignore[arg-type]
            tool_context=mock_tool_context,
        )

        assert result['status'] == 'error'
        assert 'partial' in result['error']

    async def test_missing_tool_context_returns_error(self):
        result = await assemble_sow_payload(stage='content')
        assert result['status'] == 'error'


# ---------------------------------------------------------------------------
# MISSING_INPUT sentinel detection (stage-aware)
#
# When a section worker is invoked without its required upstream state
# (manifest / prior bundles), its instruction provider switches to a
# "STOP and emit empty bundle" footer that writes the literal string
# ``"MISSING_INPUT"`` in scalar required fields. The assembler must
# short-circuit on this sentinel for the bundles relevant to the
# current stage — and ONLY those — so a content-stage assembly is not
# blocked by an absent architecture/narrative (which simply have not
# been generated yet).
# ---------------------------------------------------------------------------


class TestMissingInputSentinel:
    async def test_content_stage_rejects_sentinel_in_scope_boundaries(
        self, mock_tool_context
    ):
        """A worker that aborted with MISSING_INPUT must not flow through
        to ``sow_quality_loop`` — burning a critic round on a SOW the
        orchestrator already knows is incomplete."""
        _populate_content_state(mock_tool_context)
        mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['scope_boundaries']] = {
            'assumptions': [],
            'out_of_scope': [],
            'risks': [],
            'handover_disclaimers': [],
            'change_request_policy_text': 'MISSING_INPUT',
        }

        result = await assemble_sow_payload(
            stage='content',
            tool_context=mock_tool_context,
        )

        assert result['status'] == 'error'
        assert 'MISSING_INPUT' in result['error']
        assert SOW_BUNDLE_STATE_KEYS['scope_boundaries'] in result['suggestion']

    async def test_content_stage_rejects_sentinel_nested_in_requirements(
        self, mock_tool_context
    ):
        """The sentinel can appear nested inside list items (e.g. an
        FR.description) — the recursive walk must catch it."""
        _populate_content_state(mock_tool_context)
        mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['requirements']] = {
            'functional_requirements': [
                {'number': 'FR-01', 'description': 'MISSING_INPUT'},
            ],
            'non_functional_requirements': [],
        }

        result = await assemble_sow_payload(
            stage='content',
            tool_context=mock_tool_context,
        )

        assert result['status'] == 'error'
        assert 'MISSING_INPUT' in result['error']

    async def test_content_stage_ignores_sentinel_in_architecture(
        self, mock_tool_context
    ):
        """architecture is NOT part of CONTENT_STAGE_KEYS. A stray
        sentinel there must not affect a content-stage assembly —
        otherwise the very first ``assemble(stage='content')`` after a
        failed Step D would never recover."""
        _populate_content_state(mock_tool_context)
        # Architecture bundle present but corrupted; not required for content stage.
        mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['architecture']] = {
            'architecture_description': 'MISSING_INPUT',
            'architecture_components': [],
            'architecture_integrations': [],
            'technology_stack': [],
        }

        result = await assemble_sow_payload(
            stage='content',
            tool_context=mock_tool_context,
        )

        assert result['status'] == 'success'

    async def test_full_stage_rejects_sentinel_in_narrative(
        self, mock_tool_context
    ):
        _populate_full_state(mock_tool_context)
        mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['narrative']] = {
            'executive_summary': 'MISSING_INPUT',
            'partner_overview': 'MISSING_INPUT',
            'customer_overview': 'MISSING_INPUT',
            'customer_primary_domain': None,
        }

        result = await assemble_sow_payload(
            stage='full',
            tool_context=mock_tool_context,
        )

        assert result['status'] == 'error'
        assert 'MISSING_INPUT' in result['error']
        assert SOW_BUNDLE_STATE_KEYS['narrative'] in result['suggestion']

    async def test_sentinel_message_lists_only_affected_bundles(
        self, mock_tool_context
    ):
        """When several bundles are clean and one is poisoned, only the
        poisoned key should appear in the suggestion — so the root knows
        exactly which section to re-invoke."""
        _populate_full_state(mock_tool_context)
        mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['architecture']] = {
            'architecture_description': 'MISSING_INPUT',
            'architecture_components': [],
            'architecture_integrations': [],
            'technology_stack': [],
        }

        result = await assemble_sow_payload(
            stage='full',
            tool_context=mock_tool_context,
        )

        assert result['status'] == 'error'
        assert SOW_BUNDLE_STATE_KEYS['architecture'] in result['suggestion']
        assert SOW_BUNDLE_STATE_KEYS['narrative'] not in result['suggestion']
        assert SOW_BUNDLE_STATE_KEYS['requirements'] not in result['suggestion']


# ---------------------------------------------------------------------------
# F-07 — project-metadata validation
#
# ``_extract_project_metadata`` emits the empty string for keys absent
# from the manifest so the docx template never KeyErrors at render time.
# That makes the renderer resilient but lets a discovery-side bug leak
# a SOW header that reads "Partner: " with nothing after. F-07 makes the
# assembler reject such payloads before they reach ``stage_sow``.
# ---------------------------------------------------------------------------


class TestProjectMetadataValidation:
    def _state_with_envelope(
        self, ctx, envelope: dict[str, Any]
    ) -> None:
        ctx.state[SOW_METADATA_STATE_KEY] = envelope
        ctx.state[SOW_BUNDLE_STATE_KEYS['requirements']] = _requirements_bundle()
        ctx.state[SOW_BUNDLE_STATE_KEYS['delivery_plan']] = _delivery_plan_bundle()
        ctx.state[SOW_BUNDLE_STATE_KEYS['scope_boundaries']] = _scope_bundle()

    def _required_only(self) -> dict[str, str]:
        """A minimal envelope carrying just the four required keys."""
        return {
            'partner_name': 'GFT Technologies',
            'customer_name': 'Acme Corp',
            'project_title': 'Data Analytics Platform',
            'funding_type': 'Google DAF',
        }

    async def test_missing_partner_name_rejected(self, mock_tool_context):
        envelope = self._required_only()
        del envelope['partner_name']
        self._state_with_envelope(mock_tool_context, envelope)

        result = await assemble_sow_payload(
            stage='content', tool_context=mock_tool_context,
        )

        assert result['status'] == 'error'
        assert 'partner_name' in result['error']
        # Suggestion must name the exact missing field so the caller has a
        # target — generic "metadata is broken" wastes a turn.
        assert 'partner_name' in result['suggestion']

    async def test_missing_customer_name_rejected(self, mock_tool_context):
        envelope = self._required_only()
        del envelope['customer_name']
        self._state_with_envelope(mock_tool_context, envelope)

        result = await assemble_sow_payload(
            stage='content', tool_context=mock_tool_context,
        )

        assert result['status'] == 'error'
        assert 'customer_name' in result['error']

    async def test_missing_project_title_rejected(self, mock_tool_context):
        envelope = self._required_only()
        del envelope['project_title']
        self._state_with_envelope(mock_tool_context, envelope)

        result = await assemble_sow_payload(
            stage='content', tool_context=mock_tool_context,
        )

        assert result['status'] == 'error'
        assert 'project_title' in result['error']

    async def test_missing_funding_type_rejected(self, mock_tool_context):
        envelope = self._required_only()
        del envelope['funding_type']
        self._state_with_envelope(mock_tool_context, envelope)

        result = await assemble_sow_payload(
            stage='content', tool_context=mock_tool_context,
        )

        assert result['status'] == 'error'
        assert 'funding_type' in result['error']

    async def test_whitespace_only_value_treated_as_missing(
        self, mock_tool_context
    ):
        """``"   "`` would render an essentially-blank header; the
        assembler must strip and reject the same as ``""``."""
        envelope = self._required_only()
        envelope['customer_name'] = '   '
        self._state_with_envelope(mock_tool_context, envelope)

        result = await assemble_sow_payload(
            stage='content', tool_context=mock_tool_context,
        )

        assert result['status'] == 'error'
        assert 'customer_name' in result['error']

    async def test_lists_every_missing_required_field(self, mock_tool_context):
        """A single error must enumerate every blank required key — the
        caller should not need N round trips to find them all."""
        # Only project_title is set; partner_name, customer_name,
        # funding_type all missing.
        self._state_with_envelope(mock_tool_context, {'project_title': 'P'})

        result = await assemble_sow_payload(
            stage='content', tool_context=mock_tool_context,
        )

        assert result['status'] == 'error'
        for key in ('partner_name', 'customer_name', 'funding_type'):
            assert key in result['error']
        # The fully-populated key MUST NOT show up in the error list.
        assert 'project_title' not in result['error'].split('fields are empty:')[1]

    async def test_optional_fields_still_default_to_empty_string(
        self, mock_tool_context
    ):
        """``date``, ``author``, etc. remain optional — only the four
        critical keys block assembly."""
        # Envelope with just the 4 required keys + nothing else.
        self._state_with_envelope(mock_tool_context, self._required_only())

        result = await assemble_sow_payload(
            stage='content', tool_context=mock_tool_context,
        )

        assert result['status'] == 'success'
        sow = result['data']['sow_data']
        assert sow['date'] == ''
        assert sow['author'] == ''
        assert sow['engagement_type'] == ''
