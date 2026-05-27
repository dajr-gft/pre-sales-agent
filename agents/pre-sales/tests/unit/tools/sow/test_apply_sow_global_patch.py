"""Unit tests for ``apply_sow_global_patch`` — Phase 4 reviser restriction.

The tool replaces the reviser's pre-Phase-4 ``stage_sow`` write surface.
Coverage focuses on the blocklist contract (the whole point of the
refactor):

- Every bundle-owned field rejected with the owning section named.
- Every manifest-derived field rejected with the manifest origin named.
- Unknown / brand-new fields rejected (no top-level key creation).
- The only fields that COULD be patched are flat-SOW fields that exist
  AND are not in either blocklist — in practice the current schema has
  none such, which is the design intent (the reviser is nearly noop
  by construction after Phase 3 routes every structural finding to a
  section repair agent).
- Side effects scoped: STATE_STAGE / revision log untouched.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.sub_agents.validation.schema import STATE_SOW, STATE_STAGE
from app.tools.sow.apply_sow_global_patch import apply_sow_global_patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seeded_sow() -> dict[str, Any]:
    """Realistic flat-SOW payload covering every category of field.

    Includes bundle-owned (functional_requirements, deliverables, …),
    manifest-derived (partner_name, customer_name, …), and a synthetic
    extra field (``allowed_text_field``) the tests use as a positive-
    path example of a non-blocked field. In production the synthetic
    field would not exist; tests inject it so we can prove the
    blocklist is the *only* thing standing between the reviser and a
    mutation.
    """
    return {
        # Bundle-owned (delivery_plan)
        'deliverables': [{'number': 'WS-01', 'name': 'doc'}],
        # Bundle-owned (requirements)
        'functional_requirements': [{'number': 'FR-01', 'description': 'x'}],
        # Bundle-owned (scope_boundaries)
        'assumptions': ['Customer provides access.'],
        # Bundle-owned (architecture)
        'architecture_description': 'Layered.',
        # Bundle-owned (narrative)
        'executive_summary': 'A platform.',
        # Manifest-derived
        'partner_name': 'GFT Technologies',
        'customer_name': 'Acme',
        'project_title': 'Data Platform',
        # Synthetic non-blocked field for positive-path coverage.
        'allowed_text_field': 'starting value',
    }


def _ctx_with_sow(mock_tool_context, sow: dict[str, Any] | None = None):
    mock_tool_context.state[STATE_SOW] = sow if sow is not None else _seeded_sow()
    return mock_tool_context


# ---------------------------------------------------------------------------
# Preconditions
# ---------------------------------------------------------------------------


class TestPreconditions:
    async def test_missing_tool_context_returns_error(self):
        result = await apply_sow_global_patch(
            field='allowed_text_field', value='new', tool_context=None,
        )
        assert result['status'] == 'error'
        assert 'tool_context' in result['error']

    async def test_blank_field_rejected(self, mock_tool_context):
        ctx = _ctx_with_sow(mock_tool_context)
        result = await apply_sow_global_patch(
            field='', value='x', tool_context=ctx,
        )
        assert result['status'] == 'error'

    async def test_missing_staged_sow_returns_error(self, mock_tool_context):
        # No STATE_SOW in state.
        result = await apply_sow_global_patch(
            field='allowed_text_field', value='x', tool_context=mock_tool_context,
        )
        assert result['status'] == 'error'
        assert 'not a dict' in result['error'] or STATE_SOW in result['error']


# ---------------------------------------------------------------------------
# Blocklist — bundle-owned fields
# ---------------------------------------------------------------------------


# Every field listed in _FIELD_TO_SECTION should be blocked. Pin a
# representative slice; the lint test below ensures coverage of the
# whole table.
_BUNDLE_FIELD_BY_SECTION = (
    ('functional_requirements', 'requirements'),
    ('non_functional_requirements', 'requirements'),
    ('deliverables', 'delivery_plan'),
    ('activity_phases', 'delivery_plan'),
    ('timeline', 'delivery_plan'),
    ('partner_roles', 'delivery_plan'),
    ('customer_roles', 'delivery_plan'),
    ('success_criteria', 'delivery_plan'),
    ('objectives', 'delivery_plan'),
    ('assumptions', 'scope_boundaries'),
    ('out_of_scope', 'scope_boundaries'),
    ('risks', 'scope_boundaries'),
    ('handover_disclaimers', 'scope_boundaries'),
    ('change_request_policy_text', 'scope_boundaries'),
    ('architecture_description', 'architecture'),
    ('architecture_components', 'architecture'),
    ('architecture_integrations', 'architecture'),
    ('technology_stack', 'architecture'),
    ('executive_summary', 'narrative'),
    ('partner_overview', 'narrative'),
    ('customer_overview', 'narrative'),
    ('customer_primary_domain', 'narrative'),
)


class TestBundleFieldBlocklist:
    @pytest.mark.parametrize('field,owner', _BUNDLE_FIELD_BY_SECTION)
    async def test_each_bundle_field_blocked(
        self, mock_tool_context, field, owner,
    ):
        ctx = _ctx_with_sow(mock_tool_context)
        result = await apply_sow_global_patch(
            field=field, value='whatever', tool_context=ctx,
        )
        assert result['status'] == 'error'
        # Error message names the owning section so the LLM can route
        # the finding correctly on retry (or surface a diagnostic and stop).
        assert owner in result['error'] or owner in result.get('suggestion', '')

    def test_test_table_covers_every_bundle_owned_field(self):
        """Lint guard: every entry in ``_FIELD_TO_SECTION`` must appear
        in ``_BUNDLE_FIELD_BY_SECTION`` so a future addition to the
        routing table cannot silently lose blocklist coverage."""
        from app.sub_agents.quality_loop.agent import _FIELD_TO_SECTION

        covered = {f for f, _ in _BUNDLE_FIELD_BY_SECTION}
        missing = set(_FIELD_TO_SECTION) - covered
        assert not missing, (
            f'_BUNDLE_FIELD_BY_SECTION must list every routed field; '
            f'missing: {sorted(missing)}'
        )

    async def test_bundle_field_rejection_does_not_mutate_state(
        self, mock_tool_context,
    ):
        original = _seeded_sow()
        ctx = _ctx_with_sow(mock_tool_context, dict(original))
        await apply_sow_global_patch(
            field='deliverables',
            value=[{'number': 'WS-99', 'name': 'evil'}],
            tool_context=ctx,
        )
        # State unchanged.
        assert ctx.state[STATE_SOW]['deliverables'] == original['deliverables']


# ---------------------------------------------------------------------------
# Blocklist — manifest-derived fields
# ---------------------------------------------------------------------------


_MANIFEST_FIELDS = (
    'partner_name',
    'customer_name',
    'partner_short_name',
    'customer_short_name',
    'project_title',
    'date',
    'author',
    'funding_type',
    'funding_type_short',
    'project_start_date',
    'project_end_date',
    'engagement_type',
    'organization_term',
)


class TestManifestFieldBlocklist:
    @pytest.mark.parametrize('field', _MANIFEST_FIELDS)
    async def test_each_manifest_field_blocked(self, mock_tool_context, field):
        sow = _seeded_sow()
        sow.setdefault(field, 'existing')
        ctx = _ctx_with_sow(mock_tool_context, sow)
        result = await apply_sow_global_patch(
            field=field, value='hijacked', tool_context=ctx,
        )
        assert result['status'] == 'error'
        assert 'metadata' in result['error'].lower() or 'save_sow_metadata' in (
            result.get('suggestion', '').lower()
        )

    def test_test_table_matches_assemble_payload_metadata_keys(self):
        from app.tools.sow.assemble_payload import _PROJECT_METADATA_KEYS

        assert set(_MANIFEST_FIELDS) == set(_PROJECT_METADATA_KEYS)


# ---------------------------------------------------------------------------
# Unknown field — refuse to create top-level keys
# ---------------------------------------------------------------------------


class TestUnknownFieldRejection:
    async def test_brand_new_top_level_key_rejected(self, mock_tool_context):
        ctx = _ctx_with_sow(mock_tool_context)
        result = await apply_sow_global_patch(
            field='brand_new_key',
            value='whatever',
            tool_context=ctx,
        )
        assert result['status'] == 'error'
        assert 'does not exist' in result['error'] or 'brand_new_key' in result['error']
        assert 'brand_new_key' not in ctx.state[STATE_SOW]


# ---------------------------------------------------------------------------
# Happy path — non-blocked existing field
# ---------------------------------------------------------------------------


class TestHappyPath:
    async def test_allowed_field_overwrites_value(self, mock_tool_context):
        ctx = _ctx_with_sow(mock_tool_context)
        result = await apply_sow_global_patch(
            field='allowed_text_field',
            value='new value',
            tool_context=ctx,
        )
        assert result['status'] == 'success'
        assert ctx.state[STATE_SOW]['allowed_text_field'] == 'new value'

    async def test_success_returns_before_after_hashes(self, mock_tool_context):
        ctx = _ctx_with_sow(mock_tool_context)
        result = await apply_sow_global_patch(
            field='allowed_text_field',
            value='different value',
            tool_context=ctx,
        )
        assert 'before_hash' in result['data']
        assert 'after_hash' in result['data']
        assert result['data']['before_hash'] != result['data']['after_hash']

    async def test_success_does_not_touch_stage_or_revision_log(
        self, mock_tool_context,
    ):
        ctx = _ctx_with_sow(mock_tool_context)
        ctx.state[STATE_STAGE] = 'content'
        ctx.state['app:sow:revision_log'] = [{'finding_id': 'pre-existing'}]

        await apply_sow_global_patch(
            field='allowed_text_field', value='x', tool_context=ctx,
        )

        # Phase 4 contract: this tool is the ONLY writer of staged SOW
        # fields; it must not touch the stage cursor or the revision log
        # (the log is owned by record_revision_log_entries; the stage is
        # owned by the root via stage_sow).
        assert ctx.state[STATE_STAGE] == 'content'
        assert ctx.state['app:sow:revision_log'] == [
            {'finding_id': 'pre-existing'}
        ]
