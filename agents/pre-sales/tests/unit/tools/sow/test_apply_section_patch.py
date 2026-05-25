"""Unit tests for the per-section ``apply_<section>_patch`` engine.

The engine is the heart of the tool-based repair flow. Tests cover:

- Pydantic op validation (malformed shapes rejected with the failing
  op index).
- Allowlist enforcement (collection scoping, identity field blocking,
  item-schema field blocking).
- Transactional semantics (one invalid op in a batch rolls back ALL
  ops).
- Anchor-drop warning surfaces in the tool result.
- Bundle-schema re-validation after mutation.
- Per-section isolation (``apply_delivery_plan_patch`` cannot patch
  the requirements bundle).
"""

from __future__ import annotations

from typing import Any

import pytest

from app.sub_agents.schemas import SOW_BUNDLE_STATE_KEYS
from app.tools.sow._patch_models import (
    PatchError,
    parse_patch_op,
)
from app.tools.sow.apply_section_patch import (
    apply_architecture_patch,
    apply_delivery_plan_patch,
    apply_narrative_patch,
    apply_requirements_patch,
    apply_scope_boundaries_patch,
)


# ---------------------------------------------------------------------------
# Bundle builders — minimal validated shapes the engine accepts.
# ---------------------------------------------------------------------------


def _seed_delivery_plan_bundle() -> dict[str, Any]:
    return {
        'activity_phases': [
            {'name': 'Phase 1', 'description': 'Discovery.', 'tasks': []},
            {'name': 'Phase 2', 'description': 'Build.', 'tasks': []},
        ],
        'deliverables': [
            {
                'number': 'WS-01',
                'activity': 'Phase 1',
                'name': 'Spec',
                'description': 'FR-01 spec.',
                'format': 'Document',
            },
            {
                'number': 'WS-02',
                'activity': 'Phase 1',
                'name': 'Test Plan',
                'description': 'NFR-01 test plan.',
                'format': 'Document',
            },
        ],
        'timeline': [
            {'activity': 'Phase 1', 'timeframe': 'W1', 'outcomes': 'Spec.'},
        ],
        'partner_roles': [{'role': 'PM', 'responsibilities': 'Owns plan.'}],
        'customer_roles': [{'role': 'Sponsor', 'responsibilities': 'Approves.'}],
        'success_criteria': ['Plan accepted.'],
        'objectives': ['Modernize.'],
    }


def _seed_requirements_bundle() -> dict[str, Any]:
    return {
        'functional_requirements': [
            {'number': 'FR-01', 'description': 'Ingest data.'},
            {'number': 'FR-02', 'description': 'Generate reports.'},
        ],
        'non_functional_requirements': [
            {'number': 'NFR-01', 'description': 'TLS 1.3.'},
        ],
    }


def _seed_scope_boundaries_bundle() -> dict[str, Any]:
    return {
        'assumptions': ['Customer provides access.'],
        'out_of_scope': ['Hardware procurement (FR-99 explicitly out).'],
        'risks': [
            {
                'number': 'R-01',
                'description': 'SAP rate limits.',
                'mitigation': 'Backoff.',
            },
        ],
        'handover_disclaimers': [],
        'change_request_policy_text': 'CR requires approval.',
    }


# ---------------------------------------------------------------------------
# PatchOp parsing
# ---------------------------------------------------------------------------


class TestPatchOpParsing:
    def test_update_item_round_trips(self):
        op = parse_patch_op({
            'op': 'update_item',
            'finding_id': 'f-1',
            'collection': 'deliverables',
            'item_id': 'WS-01',
            'fields': {'description': 'updated'},
        })
        assert op.op == 'update_item'
        assert op.item_id == 'WS-01'

    def test_missing_finding_id_rejected(self):
        from pydantic import ValidationError as PyValidationError

        with pytest.raises(PyValidationError):
            parse_patch_op({
                'op': 'update_item',
                'collection': 'deliverables',
                'item_id': 'WS-01',
                'fields': {'description': 'x'},
            })

    def test_unknown_op_kind_rejected(self):
        from pydantic import ValidationError as PyValidationError

        with pytest.raises(PyValidationError):
            parse_patch_op({
                'op': 'frobnicate',
                'finding_id': 'f-1',
                'collection': 'deliverables',
                'item_id': 'WS-01',
                'fields': {'description': 'x'},
            })

    def test_remove_item_requires_coverage_transferred_to_present(self):
        """The field is required on the schema; ``None`` is the
        explicit acknowledgement that no coverage was at stake."""
        op = parse_patch_op({
            'op': 'remove_item',
            'finding_id': 'f-1',
            'collection': 'deliverables',
            'item_id': 'WS-01',
            'coverage_transferred_to': None,
        })
        assert op.coverage_transferred_to is None

        from pydantic import ValidationError as PyValidationError

        with pytest.raises(PyValidationError):
            parse_patch_op({
                'op': 'remove_item',
                'finding_id': 'f-1',
                'collection': 'deliverables',
                'item_id': 'WS-01',
            })


# ---------------------------------------------------------------------------
# Engine happy path
# ---------------------------------------------------------------------------


class TestUpdateItemHappyPath:
    async def test_persists_change_in_state(self, mock_tool_context):
        mock_tool_context.state[
            SOW_BUNDLE_STATE_KEYS['delivery_plan']
        ] = _seed_delivery_plan_bundle()

        result = await apply_delivery_plan_patch(
            ops=[
                {
                    'op': 'update_item',
                    'finding_id': 'f-1',
                    'collection': 'deliverables',
                    'item_id': 'WS-01',
                    'fields': {'description': 'Updated FR-01 spec.'},
                },
            ],
            tool_context=mock_tool_context,
        )

        assert result['status'] == 'success'
        assert result['data']['ops_applied'] == 1
        bundle = mock_tool_context.state[
            SOW_BUNDLE_STATE_KEYS['delivery_plan']
        ]
        assert (
            bundle['deliverables'][0]['description']
            == 'Updated FR-01 spec.'
        )
        # Identity preserved.
        assert bundle['deliverables'][0]['number'] == 'WS-01'

    async def test_returns_distinct_before_after_hashes(
        self, mock_tool_context
    ):
        mock_tool_context.state[
            SOW_BUNDLE_STATE_KEYS['delivery_plan']
        ] = _seed_delivery_plan_bundle()

        result = await apply_delivery_plan_patch(
            ops=[
                {
                    'op': 'update_item',
                    'finding_id': 'f-1',
                    'collection': 'deliverables',
                    'item_id': 'WS-01',
                    'fields': {'description': 'Something different.'},
                },
            ],
            tool_context=mock_tool_context,
        )
        before = result['data']['before_hash']
        after = result['data']['after_hash']
        assert before != after


class TestAddItem:
    async def test_auto_assigns_next_id_when_omitted(self, mock_tool_context):
        mock_tool_context.state[
            SOW_BUNDLE_STATE_KEYS['delivery_plan']
        ] = _seed_delivery_plan_bundle()

        result = await apply_delivery_plan_patch(
            ops=[
                {
                    'op': 'add_item',
                    'finding_id': 'f-1',
                    'collection': 'deliverables',
                    'item': {
                        'activity': 'Phase 2',
                        'name': 'Runbook',
                        'description': 'Ops runbook.',
                        'format': 'Document',
                    },
                },
            ],
            tool_context=mock_tool_context,
        )

        assert result['status'] == 'success'
        bundle = mock_tool_context.state[
            SOW_BUNDLE_STATE_KEYS['delivery_plan']
        ]
        # WS-01 and WS-02 already present → new item must be WS-03.
        assert bundle['deliverables'][-1]['number'] == 'WS-03'

    async def test_explicit_id_preserved(self, mock_tool_context):
        mock_tool_context.state[
            SOW_BUNDLE_STATE_KEYS['delivery_plan']
        ] = _seed_delivery_plan_bundle()

        result = await apply_delivery_plan_patch(
            ops=[
                {
                    'op': 'add_item',
                    'finding_id': 'f-1',
                    'collection': 'deliverables',
                    'item': {
                        'number': 'WS-99',
                        'activity': 'Phase 2',
                        'name': 'Runbook',
                        'description': 'Ops runbook.',
                        'format': 'Document',
                    },
                },
            ],
            tool_context=mock_tool_context,
        )
        assert result['status'] == 'success'
        bundle = mock_tool_context.state[
            SOW_BUNDLE_STATE_KEYS['delivery_plan']
        ]
        assert bundle['deliverables'][-1]['number'] == 'WS-99'

    async def test_after_item_id_places_correctly(self, mock_tool_context):
        mock_tool_context.state[
            SOW_BUNDLE_STATE_KEYS['delivery_plan']
        ] = _seed_delivery_plan_bundle()

        result = await apply_delivery_plan_patch(
            ops=[
                {
                    'op': 'add_item',
                    'finding_id': 'f-1',
                    'collection': 'deliverables',
                    'item': {
                        'activity': 'Phase 1',
                        'name': 'Mid',
                        'description': 'Middle.',
                        'format': 'Document',
                    },
                    'after_item_id': 'WS-01',
                },
            ],
            tool_context=mock_tool_context,
        )
        assert result['status'] == 'success'
        deliverables = mock_tool_context.state[
            SOW_BUNDLE_STATE_KEYS['delivery_plan']
        ]['deliverables']
        # WS-01, NEW, WS-02
        assert deliverables[0]['number'] == 'WS-01'
        assert deliverables[1]['name'] == 'Mid'
        assert deliverables[2]['number'] == 'WS-02'


class TestRemoveItem:
    async def test_removes_with_explicit_none_coverage(self, mock_tool_context):
        mock_tool_context.state[
            SOW_BUNDLE_STATE_KEYS['delivery_plan']
        ] = _seed_delivery_plan_bundle()

        result = await apply_delivery_plan_patch(
            ops=[
                {
                    'op': 'remove_item',
                    'finding_id': 'f-1',
                    'collection': 'deliverables',
                    'item_id': 'WS-02',
                    'coverage_transferred_to': None,
                },
            ],
            tool_context=mock_tool_context,
        )
        assert result['status'] == 'success'
        bundle = mock_tool_context.state[
            SOW_BUNDLE_STATE_KEYS['delivery_plan']
        ]
        assert [d['number'] for d in bundle['deliverables']] == ['WS-01']

    async def test_rejects_when_item_id_not_found(self, mock_tool_context):
        mock_tool_context.state[
            SOW_BUNDLE_STATE_KEYS['delivery_plan']
        ] = _seed_delivery_plan_bundle()

        result = await apply_delivery_plan_patch(
            ops=[
                {
                    'op': 'remove_item',
                    'finding_id': 'f-1',
                    'collection': 'deliverables',
                    'item_id': 'WS-99',
                    'coverage_transferred_to': None,
                },
            ],
            tool_context=mock_tool_context,
        )
        assert result['status'] == 'error'
        assert 'WS-99' in result['error']
        # State untouched.
        bundle = mock_tool_context.state[
            SOW_BUNDLE_STATE_KEYS['delivery_plan']
        ]
        assert len(bundle['deliverables']) == 2


# ---------------------------------------------------------------------------
# Allowlist enforcement — identity / unknown fields / wrong collection
# ---------------------------------------------------------------------------


class TestAllowlistEnforcement:
    async def test_blocks_identity_field_change(self, mock_tool_context):
        mock_tool_context.state[
            SOW_BUNDLE_STATE_KEYS['delivery_plan']
        ] = _seed_delivery_plan_bundle()

        result = await apply_delivery_plan_patch(
            ops=[
                {
                    'op': 'update_item',
                    'finding_id': 'f-1',
                    'collection': 'deliverables',
                    'item_id': 'WS-01',
                    # Attempts to RENAME the identity — must be rejected.
                    'fields': {'number': 'WS-07'},
                },
            ],
            tool_context=mock_tool_context,
        )
        assert result['status'] == 'error'
        assert 'identity' in result['error'].lower() or 'number' in result['error']
        bundle = mock_tool_context.state[
            SOW_BUNDLE_STATE_KEYS['delivery_plan']
        ]
        assert bundle['deliverables'][0]['number'] == 'WS-01'

    async def test_blocks_unknown_field_on_item(self, mock_tool_context):
        mock_tool_context.state[
            SOW_BUNDLE_STATE_KEYS['delivery_plan']
        ] = _seed_delivery_plan_bundle()

        result = await apply_delivery_plan_patch(
            ops=[
                {
                    'op': 'update_item',
                    'finding_id': 'f-1',
                    'collection': 'deliverables',
                    'item_id': 'WS-01',
                    'fields': {'whatever_field': 'x'},
                },
            ],
            tool_context=mock_tool_context,
        )
        assert result['status'] == 'error'
        assert 'whatever_field' in result['error']

    async def test_rejects_unknown_collection(self, mock_tool_context):
        mock_tool_context.state[
            SOW_BUNDLE_STATE_KEYS['delivery_plan']
        ] = _seed_delivery_plan_bundle()

        result = await apply_delivery_plan_patch(
            ops=[
                {
                    'op': 'update_item',
                    'finding_id': 'f-1',
                    'collection': 'requirements',  # not on delivery_plan
                    'item_id': 'FR-01',
                    'fields': {'description': 'x'},
                },
            ],
            tool_context=mock_tool_context,
        )
        assert result['status'] == 'error'
        assert 'requirements' in result['error']

    async def test_string_list_rejects_update_item(self, mock_tool_context):
        mock_tool_context.state[
            SOW_BUNDLE_STATE_KEYS['delivery_plan']
        ] = _seed_delivery_plan_bundle()

        result = await apply_delivery_plan_patch(
            ops=[
                {
                    'op': 'update_item',
                    'finding_id': 'f-1',
                    'collection': 'success_criteria',
                    'item_id': 'whatever',
                    'fields': {},
                },
            ],
            tool_context=mock_tool_context,
        )
        assert result['status'] == 'error'
        assert 'item-level' in result['error'].lower() or 'item' in result['error']

    async def test_update_field_replaces_string_list(self, mock_tool_context):
        mock_tool_context.state[
            SOW_BUNDLE_STATE_KEYS['delivery_plan']
        ] = _seed_delivery_plan_bundle()

        result = await apply_delivery_plan_patch(
            ops=[
                {
                    'op': 'update_field',
                    'finding_id': 'f-1',
                    'field': 'success_criteria',
                    'value': ['A.', 'B.'],
                },
            ],
            tool_context=mock_tool_context,
        )
        assert result['status'] == 'success'
        bundle = mock_tool_context.state[
            SOW_BUNDLE_STATE_KEYS['delivery_plan']
        ]
        assert bundle['success_criteria'] == ['A.', 'B.']

    async def test_update_field_rejects_unknown_field(self, mock_tool_context):
        mock_tool_context.state[
            SOW_BUNDLE_STATE_KEYS['delivery_plan']
        ] = _seed_delivery_plan_bundle()

        result = await apply_delivery_plan_patch(
            ops=[
                {
                    'op': 'update_field',
                    'finding_id': 'f-1',
                    'field': 'nope_field',
                    'value': 'x',
                },
            ],
            tool_context=mock_tool_context,
        )
        assert result['status'] == 'error'
        assert 'nope_field' in result['error']


# ---------------------------------------------------------------------------
# Transactional semantics
# ---------------------------------------------------------------------------


class TestTransactional:
    async def test_invalid_op_rolls_back_batch(self, mock_tool_context):
        mock_tool_context.state[
            SOW_BUNDLE_STATE_KEYS['delivery_plan']
        ] = _seed_delivery_plan_bundle()

        result = await apply_delivery_plan_patch(
            ops=[
                {
                    'op': 'update_item',
                    'finding_id': 'f-1',
                    'collection': 'deliverables',
                    'item_id': 'WS-01',
                    'fields': {'description': 'Updated.'},
                },
                {
                    # The second op references an unknown item — entire
                    # batch must roll back.
                    'op': 'update_item',
                    'finding_id': 'f-2',
                    'collection': 'deliverables',
                    'item_id': 'WS-99',
                    'fields': {'description': 'X.'},
                },
            ],
            tool_context=mock_tool_context,
        )
        assert result['status'] == 'error'
        # First op must NOT have been persisted.
        bundle = mock_tool_context.state[
            SOW_BUNDLE_STATE_KEYS['delivery_plan']
        ]
        assert bundle['deliverables'][0]['description'] == 'FR-01 spec.'

    async def test_pydantic_validation_rolls_back(self, mock_tool_context):
        """An add_item that omits a REQUIRED non-identity field
        (``description``) must surface as bundle-level Pydantic
        validation failure and roll back."""
        mock_tool_context.state[
            SOW_BUNDLE_STATE_KEYS['delivery_plan']
        ] = _seed_delivery_plan_bundle()

        result = await apply_delivery_plan_patch(
            ops=[
                {
                    'op': 'add_item',
                    'finding_id': 'f-1',
                    'collection': 'deliverables',
                    'item': {
                        'activity': 'Phase 2',
                        'name': 'Spec',
                        # No 'description', no 'format' — required fields
                        'format': 'Document',
                    },
                },
            ],
            tool_context=mock_tool_context,
        )
        assert result['status'] == 'error'
        bundle = mock_tool_context.state[
            SOW_BUNDLE_STATE_KEYS['delivery_plan']
        ]
        assert len(bundle['deliverables']) == 2  # not 3

    async def test_too_many_ops_rejected(self, mock_tool_context):
        mock_tool_context.state[
            SOW_BUNDLE_STATE_KEYS['delivery_plan']
        ] = _seed_delivery_plan_bundle()

        ops = [
            {
                'op': 'update_item',
                'finding_id': f'f-{i}',
                'collection': 'deliverables',
                'item_id': 'WS-01',
                'fields': {'description': f'Update {i}.'},
            }
            for i in range(6)
        ]
        result = await apply_delivery_plan_patch(
            ops=ops, tool_context=mock_tool_context,
        )
        assert result['status'] == 'error'
        assert '6' in result['error']

    async def test_empty_ops_rejected(self, mock_tool_context):
        mock_tool_context.state[
            SOW_BUNDLE_STATE_KEYS['delivery_plan']
        ] = _seed_delivery_plan_bundle()

        result = await apply_delivery_plan_patch(
            ops=[], tool_context=mock_tool_context,
        )
        assert result['status'] == 'error'


# ---------------------------------------------------------------------------
# Per-section isolation
# ---------------------------------------------------------------------------


class TestPerSectionIsolation:
    async def test_apply_delivery_plan_patch_cannot_touch_requirements(
        self, mock_tool_context
    ):
        mock_tool_context.state[
            SOW_BUNDLE_STATE_KEYS['requirements']
        ] = _seed_requirements_bundle()
        mock_tool_context.state[
            SOW_BUNDLE_STATE_KEYS['delivery_plan']
        ] = _seed_delivery_plan_bundle()

        result = await apply_delivery_plan_patch(
            ops=[
                {
                    'op': 'update_item',
                    'finding_id': 'f-1',
                    'collection': 'functional_requirements',
                    'item_id': 'FR-01',
                    'fields': {'description': 'x'},
                },
            ],
            tool_context=mock_tool_context,
        )
        assert result['status'] == 'error'
        # Requirements bundle untouched.
        req = mock_tool_context.state[
            SOW_BUNDLE_STATE_KEYS['requirements']
        ]
        assert req['functional_requirements'][0]['description'] == 'Ingest data.'

    async def test_apply_requirements_patch_updates_its_bundle(
        self, mock_tool_context
    ):
        mock_tool_context.state[
            SOW_BUNDLE_STATE_KEYS['requirements']
        ] = _seed_requirements_bundle()

        result = await apply_requirements_patch(
            ops=[
                {
                    'op': 'update_item',
                    'finding_id': 'f-1',
                    'collection': 'functional_requirements',
                    'item_id': 'FR-01',
                    'fields': {'description': 'Updated FR-01.'},
                },
            ],
            tool_context=mock_tool_context,
        )
        assert result['status'] == 'success'
        bundle = mock_tool_context.state[
            SOW_BUNDLE_STATE_KEYS['requirements']
        ]
        assert bundle['functional_requirements'][0]['description'] == 'Updated FR-01.'

    def test_each_tool_has_distinct_name(self):
        assert apply_requirements_patch.__name__ == 'apply_requirements_patch'
        assert apply_delivery_plan_patch.__name__ == 'apply_delivery_plan_patch'
        assert (
            apply_scope_boundaries_patch.__name__
            == 'apply_scope_boundaries_patch'
        )
        assert apply_architecture_patch.__name__ == 'apply_architecture_patch'
        assert apply_narrative_patch.__name__ == 'apply_narrative_patch'


# ---------------------------------------------------------------------------
# Anchor-drop warning
# ---------------------------------------------------------------------------


class TestAnchorDropWarning:
    async def test_update_item_returns_ok_with_warnings_when_anchor_drops(
        self, mock_tool_context
    ):
        mock_tool_context.state[
            SOW_BUNDLE_STATE_KEYS['delivery_plan']
        ] = _seed_delivery_plan_bundle()

        # WS-01.description references FR-01 — replace with text that
        # has no anchor.
        result = await apply_delivery_plan_patch(
            ops=[
                {
                    'op': 'update_item',
                    'finding_id': 'f-1',
                    'collection': 'deliverables',
                    'item_id': 'WS-01',
                    'fields': {'description': 'Generic spec doc.'},
                },
            ],
            tool_context=mock_tool_context,
        )
        assert result['status'] == 'success'
        assert result['data']['status'] == 'ok_with_warnings'
        assert 'FR-01' in result['data']['anchor_drops']

    async def test_update_field_diffs_string_list_anchor_ids(
        self, mock_tool_context
    ):
        mock_tool_context.state[
            SOW_BUNDLE_STATE_KEYS['scope_boundaries']
        ] = _seed_scope_boundaries_bundle()

        # out_of_scope had "FR-99" — drop it via update_field
        result = await apply_scope_boundaries_patch(
            ops=[
                {
                    'op': 'update_field',
                    'finding_id': 'f-1',
                    'field': 'out_of_scope',
                    'value': ['Hardware procurement (generic exclusion).'],
                },
            ],
            tool_context=mock_tool_context,
        )
        assert result['status'] == 'success'
        assert result['data']['status'] == 'ok_with_warnings'
        assert 'FR-99' in result['data']['anchor_drops']

    async def test_no_warning_when_no_anchor_drops(self, mock_tool_context):
        mock_tool_context.state[
            SOW_BUNDLE_STATE_KEYS['delivery_plan']
        ] = _seed_delivery_plan_bundle()

        result = await apply_delivery_plan_patch(
            ops=[
                {
                    'op': 'update_item',
                    'finding_id': 'f-1',
                    'collection': 'deliverables',
                    'item_id': 'WS-01',
                    'fields': {
                        'description': 'Detailed FR-01 spec with extra context.',
                    },
                },
            ],
            tool_context=mock_tool_context,
        )
        assert result['status'] == 'success'
        assert result['data']['status'] == 'ok'
        assert result['data']['anchor_drops'] == []


# ---------------------------------------------------------------------------
# tool_context preconditions
# ---------------------------------------------------------------------------


class TestPreconditions:
    async def test_missing_tool_context_returns_error(self):
        result = await apply_delivery_plan_patch(ops=[], tool_context=None)
        assert result['status'] == 'error'
        assert 'tool_context' in result['error']

    async def test_missing_bundle_in_state_returns_error(
        self, mock_tool_context
    ):
        # State does NOT have the bundle key — repair tool is only
        # meaningful AFTER first-gen wrote the bundle.
        result = await apply_delivery_plan_patch(
            ops=[
                {
                    'op': 'update_item',
                    'finding_id': 'f-1',
                    'collection': 'deliverables',
                    'item_id': 'WS-01',
                    'fields': {'description': 'x'},
                },
            ],
            tool_context=mock_tool_context,
        )
        assert result['status'] == 'error'
        assert 'bundle' in result['error'].lower()
