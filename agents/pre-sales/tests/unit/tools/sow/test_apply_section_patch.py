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

        # Preserve the FR-01 reference in the updated description so the
        # net-drop refusal does NOT fire (this test pins hashing, not
        # anchor enforcement).
        result = await apply_delivery_plan_patch(
            ops=[
                {
                    'op': 'update_item',
                    'finding_id': 'f-1',
                    'collection': 'deliverables',
                    'item_id': 'WS-01',
                    'fields': {'description': 'FR-01 spec — revised.'},
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
    async def test_removes_with_explicit_coverage_transfer(self, mock_tool_context):
        """Removing an anchored item REQUIRES naming the item that
        absorbs the manifest coverage. ``None`` is no longer a valid
        acknowledgement for anchored ids — see :class:`TestNetDropRefusal`
        for the failure-mode coverage."""
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
                    'coverage_transferred_to': 'WS-01',
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
    """Anchor-drop refusal contract.

    Under the post-Phase-4 contract, anchor drops that escape the
    bundle without an explicit ``remove_item`` (with valid coverage)
    are REFUSED, not warned. The ``ok_with_warnings`` status remains
    reserved for the residual case where the per-collection detector
    sees an anchor leave the touched collection but the bundle as a
    whole still carries the anchor in another collection — that case
    commits successfully but surfaces the per-collection drop for
    diagnostic visibility.

    See :class:`TestNetDropRefusal` for the stricter refusal paths.
    """

    async def test_update_field_diffs_string_list_anchor_ids(
        self, mock_tool_context
    ):
        """``out_of_scope`` quoted ``FR-99`` (a placeholder anchor that
        does NOT exist in any other collection in the seed). Dropping
        it via ``update_field`` now refuses the patch — pin that
        behavior so the warning regression is captured."""
        mock_tool_context.state[
            SOW_BUNDLE_STATE_KEYS['scope_boundaries']
        ] = _seed_scope_boundaries_bundle()

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
        assert result['status'] == 'error'
        assert 'FR-99' in result['error']

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
# Ops telemetry — section_patch_ops_executed event + per-op classification
# ---------------------------------------------------------------------------


class TestOpsExecutedTelemetry:
    """Observability contract for the always-on ops log.

    The ``section_patch_ops_executed`` event is the post-mortem trail
    that answers "which finding made the worker emit which op that
    dropped which anchor?" — it must fire on every patch call (success
    or warning) and carry a stable shape: one dict per op with
    ``op_kind``, ``finding_id``, target descriptors, ``dropped_anchors``,
    and ``removes_manifest_anchored_item``.
    """

    async def test_event_fires_on_every_patch_call(
        self, mock_tool_context, caplog,
    ):
        mock_tool_context.state[
            SOW_BUNDLE_STATE_KEYS['delivery_plan']
        ] = _seed_delivery_plan_bundle()

        import logging
        caplog.set_level(logging.INFO)
        await apply_delivery_plan_patch(
            ops=[
                {
                    'op': 'update_item',
                    'finding_id': 'f-1',
                    'collection': 'deliverables',
                    'item_id': 'WS-01',
                    'fields': {'description': 'Detailed FR-01 spec.'},
                },
            ],
            tool_context=mock_tool_context,
        )

        messages = [r.getMessage() for r in caplog.records]
        assert any('section_patch_ops_executed' in m for m in messages)

    async def test_remove_anchored_item_flagged_manifest_anchored(
        self, mock_tool_context, caplog,
    ):
        """The structural signal this whole pass exists to surface.
        Authorised remove (coverage transferred) commits; telemetry
        carries the manifest-anchored signal."""
        mock_tool_context.state[
            SOW_BUNDLE_STATE_KEYS['delivery_plan']
        ] = _seed_delivery_plan_bundle()

        import logging
        caplog.set_level(logging.INFO)
        result = await apply_delivery_plan_patch(
            ops=[
                {
                    'op': 'remove_item',
                    'finding_id': 'f-9',
                    'collection': 'deliverables',
                    'item_id': 'WS-01',
                    'coverage_transferred_to': 'WS-02',
                },
            ],
            tool_context=mock_tool_context,
        )

        assert result['status'] == 'success'
        # New top-level counter on the existing event surfaces the
        # manifest-anchored remove count without parsing the ops payload.
        # We rely on logger.info call structure: scrape caplog records.
        rec = next(
            r for r in caplog.records
            if 'section_patch_ops_executed' in r.getMessage()
        )
        # structlog renders kwargs into the record dict via the event
        # key — _structured_event_payload below digs them out.
        payload = _structured_event_payload(rec)
        assert payload['manifest_anchored_removes'] == ['WS-01']
        assert len(payload['ops']) == 1
        op_entry = payload['ops'][0]
        assert op_entry['op_kind'] == 'remove_item'
        assert op_entry['finding_id'] == 'f-9'
        assert op_entry['item_id'] == 'WS-01'
        assert op_entry['removes_manifest_anchored_item'] is True
        assert op_entry['dropped_anchors'] == ['WS-01']

    async def test_remove_unanchored_item_not_flagged_manifest_anchored(
        self, mock_tool_context, caplog,
    ):
        """Removing a plain-text bullet (no FR/NFR/WS/R id) is NOT a
        manifest-anchored drop — the classifier must distinguish.
        """
        mock_tool_context.state[
            SOW_BUNDLE_STATE_KEYS['scope_boundaries']
        ] = {
            'assumptions': ['Customer provides access.'],
            'out_of_scope': ['Hardware procurement.'],
            'risks': [],
            'handover_disclaimers': [],
            'change_request_policy_text': '',
        }
        # Remove the assumption via update_field (it's a list[str]).
        import logging
        caplog.set_level(logging.INFO)
        result = await apply_scope_boundaries_patch(
            ops=[
                {
                    'op': 'update_field',
                    'finding_id': 'f-2',
                    'field': 'assumptions',
                    'value': [],
                },
            ],
            tool_context=mock_tool_context,
        )

        assert result['status'] == 'success'
        rec = next(
            r for r in caplog.records
            if 'section_patch_ops_executed' in r.getMessage()
        )
        payload = _structured_event_payload(rec)
        assert payload['manifest_anchored_removes'] == []
        op_entry = payload['ops'][0]
        assert op_entry['removes_manifest_anchored_item'] is False

    async def test_op_payload_is_none_in_default_mode(
        self, mock_tool_context, caplog, monkeypatch,
    ):
        """Default mode (no ``SOW_VERBOSE_LOGGING``) keeps the log lean:
        ``payload`` is ``None`` so the op log row stays at structural
        columns only (op_kind, finding_id, target, drop signals). The
        key is still present so downstream consumers can rely on the
        shape."""
        monkeypatch.delenv('SOW_VERBOSE_LOGGING', raising=False)
        mock_tool_context.state[
            SOW_BUNDLE_STATE_KEYS['delivery_plan']
        ] = _seed_delivery_plan_bundle()

        import logging
        caplog.set_level(logging.INFO)
        await apply_delivery_plan_patch(
            ops=[
                {
                    'op': 'update_item',
                    'finding_id': 'coverage-001',
                    'collection': 'deliverables',
                    'item_id': 'WS-01',
                    'fields': {'description': 'FR-01 detailed spec.'},
                },
            ],
            tool_context=mock_tool_context,
        )

        rec = next(
            r for r in caplog.records
            if 'section_patch_ops_executed' in r.getMessage()
        )
        payload = _structured_event_payload(rec)
        op_entry = payload['ops'][0]
        assert 'payload' in op_entry  # shape-stable: key always present
        assert op_entry['payload'] is None

    async def test_op_payload_captures_update_item_fields_when_verbose(
        self, mock_tool_context, caplog, monkeypatch,
    ):
        """Verbose mode lights up the smoking-gun trail: ``payload``
        carries the ``fields`` the worker is writing into the touched
        item. This is what we'll grep when a coverage finding persists —
        does the worker's text actually anchor the manifest item?"""
        monkeypatch.setenv('SOW_VERBOSE_LOGGING', '1')
        mock_tool_context.state[
            SOW_BUNDLE_STATE_KEYS['delivery_plan']
        ] = _seed_delivery_plan_bundle()

        import logging
        caplog.set_level(logging.INFO)
        await apply_delivery_plan_patch(
            ops=[
                {
                    'op': 'update_item',
                    'finding_id': 'coverage-001',
                    'collection': 'deliverables',
                    'item_id': 'WS-01',
                    'fields': {'description': 'FR-01 detailed spec.'},
                },
            ],
            tool_context=mock_tool_context,
        )

        rec = next(
            r for r in caplog.records
            if 'section_patch_ops_executed' in r.getMessage()
        )
        payload = _structured_event_payload(rec)
        op_entry = payload['ops'][0]
        assert op_entry['payload'] == {
            'fields': {'description': 'FR-01 detailed spec.'},
        }

    async def test_op_payload_captures_add_item_content_when_verbose(
        self, mock_tool_context, caplog, monkeypatch,
    ):
        monkeypatch.setenv('SOW_VERBOSE_LOGGING', '1')
        mock_tool_context.state[
            SOW_BUNDLE_STATE_KEYS['delivery_plan']
        ] = _seed_delivery_plan_bundle()

        import logging
        caplog.set_level(logging.INFO)
        await apply_delivery_plan_patch(
            ops=[
                {
                    'op': 'add_item',
                    'finding_id': 'coverage-002',
                    'collection': 'deliverables',
                    'item': {
                        'number': 'WS-99',
                        'activity': 'Phase 1',
                        'name': 'Extra',
                        'description': 'NFR-01 reinforcement.',
                        'format': 'Document',
                    },
                    'after_item_id': None,
                },
            ],
            tool_context=mock_tool_context,
        )

        rec = next(
            r for r in caplog.records
            if 'section_patch_ops_executed' in r.getMessage()
        )
        payload = _structured_event_payload(rec)
        op_entry = payload['ops'][0]
        assert op_entry['payload']['item']['number'] == 'WS-99'
        assert op_entry['payload']['after_item_id'] is None

    async def test_op_payload_captures_update_field_value_when_verbose(
        self, mock_tool_context, caplog, monkeypatch,
    ):
        monkeypatch.setenv('SOW_VERBOSE_LOGGING', '1')
        mock_tool_context.state[
            SOW_BUNDLE_STATE_KEYS['scope_boundaries']
        ] = {
            'assumptions': ['Customer provides access.'],
            'out_of_scope': [],
            'risks': [],
            'handover_disclaimers': [],
            'change_request_policy_text': '',
        }

        import logging
        caplog.set_level(logging.INFO)
        await apply_scope_boundaries_patch(
            ops=[
                {
                    'op': 'update_field',
                    'finding_id': 'contractual_exposure-001',
                    'field': 'assumptions',
                    'value': ['Customer provides access.', 'New assumption.'],
                },
            ],
            tool_context=mock_tool_context,
        )

        rec = next(
            r for r in caplog.records
            if 'section_patch_ops_executed' in r.getMessage()
        )
        payload = _structured_event_payload(rec)
        op_entry = payload['ops'][0]
        assert op_entry['payload'] == {
            'value': ['Customer provides access.', 'New assumption.'],
        }

    async def test_event_payload_keys_are_stable_across_op_kinds(
        self, mock_tool_context, caplog,
    ):
        """A heterogeneous batch produces uniform per-op dicts — the log
        consumer can parse the payload without branching on op_kind.
        """
        mock_tool_context.state[
            SOW_BUNDLE_STATE_KEYS['delivery_plan']
        ] = _seed_delivery_plan_bundle()

        import logging
        caplog.set_level(logging.INFO)
        await apply_delivery_plan_patch(
            ops=[
                {
                    'op': 'update_item',
                    'finding_id': 'f-1',
                    'collection': 'deliverables',
                    'item_id': 'WS-01',
                    'fields': {'description': 'Updated FR-01 spec.'},
                },
                {
                    'op': 'add_item',
                    'finding_id': 'f-2',
                    'collection': 'deliverables',
                    'item': {
                        'number': 'WS-99',
                        'activity': 'Phase 1',
                        'name': 'Extra',
                        'description': 'NFR-01 reinforcement.',
                        'format': 'Document',
                    },
                    'after_item_id': None,
                },
                {
                    'op': 'update_field',
                    'finding_id': 'f-3',
                    'field': 'success_criteria',
                    'value': ['Plan accepted.', 'Tests pass.'],
                },
            ],
            tool_context=mock_tool_context,
        )

        rec = next(
            r for r in caplog.records
            if 'section_patch_ops_executed' in r.getMessage()
        )
        payload = _structured_event_payload(rec)
        assert len(payload['ops']) == 3
        expected_keys = {
            'op_kind', 'finding_id', 'collection', 'item_id', 'field',
            'payload', 'dropped_anchors', 'removes_manifest_anchored_item',
        }
        for entry in payload['ops']:
            assert expected_keys <= entry.keys(), (
                f'op entry missing keys: {expected_keys - entry.keys()}'
            )


# ---------------------------------------------------------------------------
# Net-drop enforcement — refuses patches that lose anchors without consent
# ---------------------------------------------------------------------------


class TestNetDropRefusal:
    """The patch tool refuses a batch that loses anchor ids from the
    bundle unless the loss is covered by a ``remove_item`` op with
    explicit ``coverage_transferred_to``. This is the structural fix
    for the observed Class-D cascade where worker text edits silently
    dropped manifest-anchored references, the next critic round flagged
    them as uncovered, and the loop spent a round restoring them.
    """

    async def test_update_item_dropping_anchor_with_no_remove_is_refused(
        self, mock_tool_context,
    ):
        mock_tool_context.state[
            SOW_BUNDLE_STATE_KEYS['delivery_plan']
        ] = _seed_delivery_plan_bundle()

        # WS-01.description references FR-01. Updating to text with no
        # anchor strips FR-01 from the bundle if FR-01 is not present
        # anywhere else. The seed has FR-01 only inside WS-01, so the
        # net drop fires and the patch is refused.
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

        assert result['status'] == 'error'
        assert 'FR-01' in result['error']
        # State must be untouched after refusal.
        bundle = mock_tool_context.state[
            SOW_BUNDLE_STATE_KEYS['delivery_plan']
        ]
        assert bundle['deliverables'][0]['description'] == 'FR-01 spec.'

    async def test_update_item_dropping_anchor_with_replacement_in_same_batch_allowed(
        self, mock_tool_context,
    ):
        """Net-drop = empty when another op in the same batch restores
        the anchor. The worker's legitimate "move FR-01 reference from
        WS-01 to WS-02" pattern must keep working.
        """
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
                    'fields': {'description': 'Generic spec doc.'},
                },
                {
                    'op': 'update_item',
                    'finding_id': 'f-1',
                    'collection': 'deliverables',
                    'item_id': 'WS-02',
                    'fields': {'description': 'FR-01 + NFR-01 covered here.'},
                },
            ],
            tool_context=mock_tool_context,
        )

        assert result['status'] == 'success'

    async def test_remove_anchored_item_without_coverage_is_refused(
        self, mock_tool_context,
    ):
        """``coverage_transferred_to: None`` is incoherent for an
        anchored item — the manifest entry needs a new home, not an
        acknowledgement that "no coverage was at stake".
        """
        mock_tool_context.state[
            SOW_BUNDLE_STATE_KEYS['delivery_plan']
        ] = _seed_delivery_plan_bundle()

        result = await apply_delivery_plan_patch(
            ops=[
                {
                    'op': 'remove_item',
                    'finding_id': 'f-9',
                    'collection': 'deliverables',
                    'item_id': 'WS-01',
                    'coverage_transferred_to': None,
                },
            ],
            tool_context=mock_tool_context,
        )

        assert result['status'] == 'error'
        # Message names the offending item and the contract.
        assert 'WS-01' in result['error']
        assert 'coverage_transferred_to' in result['error']

    async def test_remove_anchored_item_with_coverage_is_allowed(
        self, mock_tool_context,
    ):
        mock_tool_context.state[
            SOW_BUNDLE_STATE_KEYS['delivery_plan']
        ] = _seed_delivery_plan_bundle()

        result = await apply_delivery_plan_patch(
            ops=[
                {
                    'op': 'remove_item',
                    'finding_id': 'f-9',
                    'collection': 'deliverables',
                    'item_id': 'WS-01',
                    'coverage_transferred_to': 'WS-02',
                },
            ],
            tool_context=mock_tool_context,
        )

        assert result['status'] == 'success'

    async def test_remove_unanchored_item_with_none_coverage_still_allowed(
        self, mock_tool_context,
    ):
        """The new check only fires for items whose id matches the
        anchor pattern. Removing a plain-text bullet (no id) keeps the
        legacy ``coverage_transferred_to: None`` escape hatch.
        """
        mock_tool_context.state[
            SOW_BUNDLE_STATE_KEYS['scope_boundaries']
        ] = {
            'assumptions': [],
            'out_of_scope': [],
            'risks': [
                # R-01 is anchored, but we won't remove it. We'll
                # remove an entry from a non-anchored collection via
                # update_field below — this test confirms the policy
                # narrows to anchored remove_item.
            ],
            'handover_disclaimers': [],
            'change_request_policy_text': '',
        }
        result = await apply_scope_boundaries_patch(
            ops=[
                {
                    'op': 'update_field',
                    'finding_id': 'f-1',
                    'field': 'assumptions',
                    'value': ['Customer provides AWS S3 access.'],
                },
            ],
            tool_context=mock_tool_context,
        )

        assert result['status'] == 'success'

    async def test_deliverable_dotted_anchor_drop_refused(
        self, mock_tool_context,
    ):
        """``WS03.6`` is now a recognised anchor. Removing it without
        coverage is refused exactly like ``WS-01``.
        """
        mock_tool_context.state[
            SOW_BUNDLE_STATE_KEYS['delivery_plan']
        ] = {
            'activity_phases': [
                {'name': 'Phase 1', 'description': 'D.', 'tasks': []},
            ],
            'deliverables': [
                {
                    'number': 'WS03.6',
                    'activity': 'Phase 1',
                    'name': 'Event publishing',
                    'description': 'Setup.',
                    'format': 'Code',
                },
            ],
            'timeline': [],
            'partner_roles': [],
            'customer_roles': [],
            'success_criteria': [],
            'objectives': [],
        }

        result = await apply_delivery_plan_patch(
            ops=[
                {
                    'op': 'remove_item',
                    'finding_id': 'f-1',
                    'collection': 'deliverables',
                    'item_id': 'WS03.6',
                    'coverage_transferred_to': None,
                },
            ],
            tool_context=mock_tool_context,
        )

        assert result['status'] == 'error'
        assert 'WS03.6' in result['error']
        assert 'coverage_transferred_to' in result['error']


def _structured_event_payload(record) -> dict[str, Any]:
    """Extract the structured kwargs dict from a structlog log record.

    structlog renders the original ``key=value`` pairs into the LogRecord
    via the standard library handler; the most reliable cross-version
    access is via ``record.__dict__`` filtered against the message body.
    """
    # structlog ships the rendered payload via the `event_dict` attr in
    # some versions and via record.msg in others. Cover both.
    if isinstance(record.msg, dict):
        return record.msg
    # Last resort: parse from JSON-renderered message (the project's
    # structlog config uses JSONRenderer; tests should still resolve
    # via __dict__).
    import json
    if isinstance(record.msg, str) and record.msg.startswith('{'):
        return json.loads(record.msg)
    return {
        k: v
        for k, v in record.__dict__.items()
        if not k.startswith('_') and k not in {'msg', 'args', 'levelname'}
    }


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
