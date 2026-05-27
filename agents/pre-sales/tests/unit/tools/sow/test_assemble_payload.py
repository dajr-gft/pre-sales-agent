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


def _manifest_nested() -> dict[str, Any]:
    return {
        'project': {
            'title': 'Data Analytics Platform',
            'customer_name': 'Acme Corp',
            'partner_name': 'GFT Technologies',
            'partner_short_name': 'GFT',
            'customer_short_name': 'Acme',
            'date': '2026-04-15',
            'author': 'Test Author',
            'funding_type': 'Google DAF',
            'funding_type_short': 'DAF',
            'start_date': '2026-05-01',
            'end_date': '2026-07-10',
            'engagement_type': 'project',
            'organization_term': 'phases',
        },
    }


def _manifest_flat() -> dict[str, Any]:
    return {
        'project_title': 'Data Analytics Platform',
        'customer_name': 'Acme Corp',
        'partner_name': 'GFT Technologies',
        # F-07: ``funding_type`` is now a required project-metadata key —
        # the assembler rejects payloads where the docx header would
        # render blank. Kept in the flat-shape fixture so the existing
        # "flat shape also works" test still exercises the happy path.
        'funding_type': 'Google DAF',
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
    ctx.state[SOW_BUNDLE_STATE_KEYS['manifest']] = _manifest_nested()
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

    async def test_extracts_nested_project_metadata(self, mock_tool_context):
        _populate_content_state(mock_tool_context)

        result = await assemble_sow_payload(
            stage='content',
            tool_context=mock_tool_context,
        )

        sow = result['data']['sow_data']
        assert sow['project_title'] == 'Data Analytics Platform'
        assert sow['customer_name'] == 'Acme Corp'
        assert sow['project_start_date'] == '2026-05-01'

    async def test_extracts_flat_project_metadata(self, mock_tool_context):
        """Flat manifest shape should also work — defensive against schema drift."""
        mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['manifest']] = _manifest_flat()
        mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['requirements']] = _requirements_bundle()
        mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['delivery_plan']] = _delivery_plan_bundle()
        mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['scope_boundaries']] = _scope_bundle()

        result = await assemble_sow_payload(
            stage='content',
            tool_context=mock_tool_context,
        )

        sow = result['data']['sow_data']
        assert sow['project_title'] == 'Data Analytics Platform'
        # Missing optional fields default to empty string (never KeyError downstream).
        assert sow['date'] == ''
        assert sow['author'] == ''


class TestMetadataEnvelopeSource:
    """Root-skills variant: project metadata comes from the explicit
    ``app:sow:metadata`` envelope rather than the Extraction Manifest."""

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
        # No extraction_manifest in state at all — only the envelope.
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

    async def test_envelope_takes_precedence_over_manifest(
        self, mock_tool_context
    ):
        # Both present: envelope wins.
        _populate_content_state(mock_tool_context)  # writes a manifest
        mock_tool_context.state[SOW_METADATA_STATE_KEY] = self._metadata_envelope()

        result = await assemble_sow_payload(
            stage='content',
            tool_context=mock_tool_context,
        )

        assert result['status'] == 'success'
        sow = result['data']['sow_data']
        # 'Root-Skills SOW' (envelope), not 'Data Analytics Platform' (manifest).
        assert sow['project_title'] == 'Root-Skills SOW'

    async def test_no_metadata_source_rejected(self, mock_tool_context):
        # Bundles present but neither envelope nor manifest.
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

    async def test_non_dict_manifest_rejected(self, mock_tool_context):
        _populate_content_state(mock_tool_context)
        mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['manifest']] = 'not a dict'

        result = await assemble_sow_payload(
            stage='content',
            tool_context=mock_tool_context,
        )

        assert result['status'] == 'error'
        assert 'manifest' in result['error'].lower()


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
    def _state_with_manifest(
        self, ctx, manifest: dict[str, Any]
    ) -> None:
        ctx.state[SOW_BUNDLE_STATE_KEYS['manifest']] = manifest
        ctx.state[SOW_BUNDLE_STATE_KEYS['requirements']] = _requirements_bundle()
        ctx.state[SOW_BUNDLE_STATE_KEYS['delivery_plan']] = _delivery_plan_bundle()
        ctx.state[SOW_BUNDLE_STATE_KEYS['scope_boundaries']] = _scope_bundle()

    async def test_missing_partner_name_rejected(self, mock_tool_context):
        manifest = _manifest_flat()
        del manifest['partner_name']
        self._state_with_manifest(mock_tool_context, manifest)

        result = await assemble_sow_payload(
            stage='content', tool_context=mock_tool_context,
        )

        assert result['status'] == 'error'
        assert 'partner_name' in result['error']
        # Suggestion must name the exact missing field so the discovery
        # repair has a target — generic "metadata is broken" wastes a turn.
        assert 'partner_name' in result['suggestion']

    async def test_missing_customer_name_rejected(self, mock_tool_context):
        manifest = _manifest_flat()
        del manifest['customer_name']
        self._state_with_manifest(mock_tool_context, manifest)

        result = await assemble_sow_payload(
            stage='content', tool_context=mock_tool_context,
        )

        assert result['status'] == 'error'
        assert 'customer_name' in result['error']

    async def test_missing_project_title_rejected(self, mock_tool_context):
        manifest = _manifest_flat()
        del manifest['project_title']
        self._state_with_manifest(mock_tool_context, manifest)

        result = await assemble_sow_payload(
            stage='content', tool_context=mock_tool_context,
        )

        assert result['status'] == 'error'
        assert 'project_title' in result['error']

    async def test_missing_funding_type_rejected(self, mock_tool_context):
        manifest = _manifest_flat()
        del manifest['funding_type']
        self._state_with_manifest(mock_tool_context, manifest)

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
        manifest = _manifest_flat()
        manifest['customer_name'] = '   '
        self._state_with_manifest(mock_tool_context, manifest)

        result = await assemble_sow_payload(
            stage='content', tool_context=mock_tool_context,
        )

        assert result['status'] == 'error'
        assert 'customer_name' in result['error']

    async def test_lists_every_missing_required_field(self, mock_tool_context):
        """A single error must enumerate every blank required key — the
        upstream discovery loop should not need N round trips to find
        them all."""
        manifest = {
            # Only project_title is set; partner_name, customer_name,
            # funding_type all missing.
            'project_title': 'P',
        }
        self._state_with_manifest(mock_tool_context, manifest)

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
        critical keys block assembly. This keeps the existing 'flat
        shape also works' contract intact for projects where date /
        author truly aren't captured upstream."""
        manifest = _manifest_flat()  # has the 4 required keys + nothing else
        self._state_with_manifest(mock_tool_context, manifest)

        result = await assemble_sow_payload(
            stage='content', tool_context=mock_tool_context,
        )

        assert result['status'] == 'success'
        sow = result['data']['sow_data']
        assert sow['date'] == ''
        assert sow['author'] == ''
        assert sow['engagement_type'] == ''


# ---------------------------------------------------------------------------
# F-13 — Identity primitives extraction
#
# Production discovery does NOT write project metadata at the manifest
# top level or under a nested ``project`` dict (the
# ``ExtractionManifest`` schema forbids extra fields at the root and
# has no ``project`` sub-model). Instead it lives inside
# ``extracted_items[*].primitives`` for items with ``category ==
# 'Identity'``. The assembler must read THAT path or it would reject
# every legitimate manifest discovery produces.
# ---------------------------------------------------------------------------


def _identity_item(primitives: dict[str, Any], item_id: str = 'I-001') -> dict:
    """Build a minimal Identity ExtractedItem dict for tests."""
    return {
        'id': item_id,
        'category': 'Identity',
        'value': primitives.get('project_name', 'P'),
        'value_detail': '',
        'primitives': primitives,
        'source': [{'artifact_id': 'A-001', 'anchor': 'page 1'}],
        'confidence': 'stated',
        'cross_refs': [],
        'notes': '',
    }


def _manifest_with_identity(primitives: dict[str, Any]) -> dict[str, Any]:
    """Manifest shaped the way production discovery actually writes it.

    No top-level ``partner_name``, no ``project`` sub-dict — just the
    Identity item carrying primitives under ``extracted_items``.
    """
    return {
        'manifest_version': '1.0',
        'conversation_language': 'pt-BR',
        'inventory': [{'artifact_id': 'A-001', 'kind': 'doc'}],
        'extracted_items': [_identity_item(primitives)],
        'gaps': {
            'hard_gaps': [],
            'pending_decisions': [],
            'ambiguities': [],
            'to_be_defined': [],
        },
        'self_audit': {},
    }


class TestIdentityPrimitivesExtraction:
    async def test_extracts_partner_customer_project_funding_from_identity(
        self, mock_tool_context
    ):
        """The canonical happy path: discovery wrote primitives only,
        and the assembler maps them into the flat sow_data keys."""
        manifest = _manifest_with_identity({
            'partner': 'GFT Technologies',
            'customer': 'Acme Corp',
            'project_name': 'Data Analytics Platform',
            'funding_type': 'DAF',
        })
        mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['manifest']] = manifest
        mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['requirements']] = _requirements_bundle()
        mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['delivery_plan']] = _delivery_plan_bundle()
        mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['scope_boundaries']] = _scope_bundle()

        result = await assemble_sow_payload(
            stage='content', tool_context=mock_tool_context,
        )

        assert result['status'] == 'success', result.get('error')
        sow = result['data']['sow_data']
        assert sow['partner_name'] == 'GFT Technologies'
        assert sow['customer_name'] == 'Acme Corp'
        assert sow['project_title'] == 'Data Analytics Platform'
        assert sow['funding_type'] == 'DAF'

    async def test_engagement_shape_maps_to_engagement_type(
        self, mock_tool_context
    ):
        """``engagement_shape`` is a documented Identity primitive (per
        extraction-rules.md §1) and is what downstream consumers expect
        under the name ``engagement_type``."""
        manifest = _manifest_with_identity({
            'partner': 'GFT', 'customer': 'Acme',
            'project_name': 'Test', 'funding_type': 'DAF',
            'engagement_shape': 'enhance',
        })
        mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['manifest']] = manifest
        mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['requirements']] = _requirements_bundle()
        mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['delivery_plan']] = _delivery_plan_bundle()
        mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['scope_boundaries']] = _scope_bundle()

        result = await assemble_sow_payload(
            stage='content', tool_context=mock_tool_context,
        )

        assert result['status'] == 'success'
        assert result['data']['sow_data']['engagement_type'] == 'enhance'

    async def test_not_stated_primitive_treated_as_missing(
        self, mock_tool_context
    ):
        """``not_stated`` is discovery's "I looked but found nothing"
        sentinel — the assembler must treat it as absent so the F-07
        required-field gate fires and the user is told to re-run
        discovery."""
        manifest = _manifest_with_identity({
            'partner': 'GFT',
            'customer': 'not_stated',  # sentinel
            'project_name': 'Test',
            'funding_type': 'DAF',
        })
        mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['manifest']] = manifest
        mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['requirements']] = _requirements_bundle()
        mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['delivery_plan']] = _delivery_plan_bundle()
        mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['scope_boundaries']] = _scope_bundle()

        result = await assemble_sow_payload(
            stage='content', tool_context=mock_tool_context,
        )

        assert result['status'] == 'error'
        assert 'customer_name' in result['error']

    async def test_multiple_identity_items_coalesce_with_first_value_winning(
        self, mock_tool_context
    ):
        """When discovery produces multiple Identity items (e.g. one
        per artifact), the first non-empty value wins for each
        primitive — matches the skill's own reconciliation rules."""
        items = [
            _identity_item(
                {
                    'partner': 'GFT',
                    'customer': 'not_stated',
                    'project_name': 'Test',
                    'funding_type': 'DAF',
                },
                item_id='I-001',
            ),
            # Second Identity item fills the gap left by 'not_stated'.
            _identity_item(
                {
                    'customer': 'Acme Corp (from second artifact)',
                    'funding_type': 'PSF',  # ignored — first wins
                },
                item_id='I-002',
            ),
        ]
        manifest = _manifest_with_identity({})  # base; replace items
        manifest['extracted_items'] = items

        mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['manifest']] = manifest
        mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['requirements']] = _requirements_bundle()
        mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['delivery_plan']] = _delivery_plan_bundle()
        mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['scope_boundaries']] = _scope_bundle()

        result = await assemble_sow_payload(
            stage='content', tool_context=mock_tool_context,
        )

        assert result['status'] == 'success'
        sow = result['data']['sow_data']
        assert sow['customer_name'] == 'Acme Corp (from second artifact)'
        # First item's DAF wins over second item's PSF.
        assert sow['funding_type'] == 'DAF'

    async def test_non_identity_items_ignored(self, mock_tool_context):
        """Primitives from a Briefing / Integrations item must not
        contaminate the project metadata even if they collide on key
        names."""
        items = [
            _identity_item({
                'partner': 'GFT',
                'customer': 'Acme',
                'project_name': 'Test',
                'funding_type': 'DAF',
            }),
            {
                'id': 'I-002',
                'category': 'Briefing',
                'value': 'Decoy',
                'value_detail': '',
                # Briefing items don't use these keys, but if some bug
                # ever wrote them, the assembler must NOT consult them.
                'primitives': {
                    'partner': 'WRONG_PARTNER',
                    'customer': 'WRONG_CUSTOMER',
                },
                'source': [{'artifact_id': 'A-001', 'anchor': 'page 2'}],
                'confidence': 'stated',
                'cross_refs': [],
                'notes': '',
            },
        ]
        manifest = _manifest_with_identity({})
        manifest['extracted_items'] = items

        mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['manifest']] = manifest
        mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['requirements']] = _requirements_bundle()
        mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['delivery_plan']] = _delivery_plan_bundle()
        mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['scope_boundaries']] = _scope_bundle()

        result = await assemble_sow_payload(
            stage='content', tool_context=mock_tool_context,
        )

        assert result['status'] == 'success'
        sow = result['data']['sow_data']
        assert sow['partner_name'] == 'GFT'
        assert sow['customer_name'] == 'Acme'

    async def test_nested_project_dict_still_works_as_fallback(
        self, mock_tool_context
    ):
        """Legacy fallback path: a manifest that DOES carry a
        ``project`` sub-dict (synthetic, used by older tests) still
        feeds the assembler. We keep this branch alive on purpose so
        test fixtures and any future discovery variant continue
        working without touching the assembler."""
        manifest = _manifest_nested()  # uses the 'project' sub-dict
        mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['manifest']] = manifest
        mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['requirements']] = _requirements_bundle()
        mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['delivery_plan']] = _delivery_plan_bundle()
        mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['scope_boundaries']] = _scope_bundle()

        result = await assemble_sow_payload(
            stage='content', tool_context=mock_tool_context,
        )

        assert result['status'] == 'success'
        assert result['data']['sow_data']['partner_name'] == 'GFT Technologies'

    async def test_identity_primitives_win_over_nested_project_dict(
        self, mock_tool_context
    ):
        """Lookup order is canonical first: Identity primitives beat
        the legacy nested ``project`` sub-dict when both are present.
        This is the migration-safe order — once production discovery
        writes Identity primitives, the legacy nested data (probably
        stale) cannot override it."""
        manifest = _manifest_nested()  # has nested 'project'
        # Add an Identity item with DIFFERENT values to detect which
        # path the extractor chose.
        manifest['extracted_items'] = [
            _identity_item({
                'partner': 'PARTNER_FROM_PRIMITIVES',
                'customer': 'CUSTOMER_FROM_PRIMITIVES',
                'project_name': 'TITLE_FROM_PRIMITIVES',
                'funding_type': 'DAF',
            }),
        ]

        mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['manifest']] = manifest
        mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['requirements']] = _requirements_bundle()
        mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['delivery_plan']] = _delivery_plan_bundle()
        mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['scope_boundaries']] = _scope_bundle()

        result = await assemble_sow_payload(
            stage='content', tool_context=mock_tool_context,
        )

        assert result['status'] == 'success'
        sow = result['data']['sow_data']
        assert sow['partner_name'] == 'PARTNER_FROM_PRIMITIVES'
        assert sow['customer_name'] == 'CUSTOMER_FROM_PRIMITIVES'
        assert sow['project_title'] == 'TITLE_FROM_PRIMITIVES'

    async def test_blank_primitive_falls_through_to_legacy_paths(
        self, mock_tool_context
    ):
        """Whitespace-only Identity primitive → fall through to nested
        project dict if it has the value. Mirrors the discovery
        contract where ``''`` and whitespace also count as 'unknown'."""
        manifest = _manifest_nested()
        manifest['extracted_items'] = [
            _identity_item({
                'partner': '   ',  # blank — must fall through
                'customer': 'Acme Corp',  # wins via primitives
                'project_name': 'not_stated',  # falls through
                'funding_type': 'DAF',
            }),
        ]

        mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['manifest']] = manifest
        mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['requirements']] = _requirements_bundle()
        mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['delivery_plan']] = _delivery_plan_bundle()
        mock_tool_context.state[SOW_BUNDLE_STATE_KEYS['scope_boundaries']] = _scope_bundle()

        result = await assemble_sow_payload(
            stage='content', tool_context=mock_tool_context,
        )

        assert result['status'] == 'success'
        sow = result['data']['sow_data']
        # Blank primitive → nested project sub-dict wins.
        assert sow['partner_name'] == 'GFT Technologies'
        # Real primitive wins even though nested 'project' had its own value.
        assert sow['customer_name'] == 'Acme Corp'
        # not_stated primitive → nested wins.
        assert sow['project_title'] == 'Data Analytics Platform'
