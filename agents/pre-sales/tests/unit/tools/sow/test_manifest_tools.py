"""Unit tests for ``app.tools.sow.manifest_tools``.

Covers the three tools the discovery skill drives the buffer with:

- ``initialize_extraction_buffer`` — inventory validation, overwrite behavior.
- ``append_extraction_items`` — per-item schema validation, artifact-id
  resolution, ID uniqueness, status precedence (ok / partial / error),
  buffer persistence across calls.
- ``finalize_extraction_manifest`` — full Pydantic cross-validation,
  manifest persistence in session state, buffer clearing on success.
- ``validate_extraction_manifest`` — standalone schema check.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.tools.sow.manifest_tools import (
    _MANIFEST_STATE_KEY,
    append_extraction_items,
    finalize_extraction_manifest,
    initialize_extraction_buffer,
    load_extraction_manifest,
    validate_extraction_manifest,
)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _make_inventory_entry(artifact_id: str = 'A1') -> dict[str, Any]:
    return {
        'id': artifact_id,
        'name': f'{artifact_id}.csv',
        'type': 'txt',
        'phase_0_hypothesis': 'capability matrix',
        'source_language': 'pt',
    }


def _make_item(idx: int, artifact_id: str = 'A1') -> dict[str, Any]:
    return {
        'id': f'I-{idx:03d}',
        'category': 'Integrations',
        'value': f'System {idx}',
        'value_detail': f'Detail for system {idx}',
        'primitives': {
            'system_name': f'System{idx}',
            'direction': 'not_stated',
            'operations': 'not_stated',
            'data_class': 'not_stated',
            'protocol': 'not_stated',
            'ownership': 'not_stated',
            'criticality': 'not_stated',
        },
        'source': [{'artifact_id': artifact_id, 'anchor': f'row {idx}'}],
        'confidence': 'stated',
    }


def _identity_item(idx: int = 1) -> dict[str, Any]:
    """An Identity item that satisfies the engagement_shape cross-check used at finalize time."""
    return {
        'id': f'ID-{idx:03d}',
        'category': 'Identity',
        'value': 'Acme Data Platform',
        'value_detail': 'A modernization engagement.',
        'primitives': {
            'engagement_shape': 'project',
        },
        'source': [{'artifact_id': 'A1', 'anchor': 'cover page'}],
        'confidence': 'stated',
    }


@pytest.fixture
async def initialized_context(mock_tool_context):
    await initialize_extraction_buffer(
        conversation_language='pt-BR',
        inventory=[
            _make_inventory_entry('A1'),
            _make_inventory_entry('A2'),
        ],
        tool_context=mock_tool_context,
    )
    return mock_tool_context


# ---------------------------------------------------------------------------
# initialize_extraction_buffer
# ---------------------------------------------------------------------------


class TestInitializeBuffer:
    async def test_first_call_initializes_state(self, mock_tool_context):
        result = await initialize_extraction_buffer(
            conversation_language='pt-BR',
            inventory=[_make_inventory_entry('A1')],
            tool_context=mock_tool_context,
        )
        assert result['status'] == 'ok'
        assert result['inventory_count'] == 1
        assert result['buffer_initialized'] is True
        assert result['warnings'] == []

        buffer = mock_tool_context.state['extraction_buffer']
        assert buffer['conversation_language'] == 'pt-BR'
        assert len(buffer['inventory']) == 1
        assert buffer['extracted_items'] == []

    async def test_invalid_entry_blocks_initialization(self, mock_tool_context):
        bad_entry = _make_inventory_entry('A1')
        del bad_entry['source_language']  # required field
        result = await initialize_extraction_buffer(
            conversation_language='pt-BR',
            inventory=[bad_entry],
            tool_context=mock_tool_context,
        )
        assert result['status'] == 'error'
        assert result['buffer_initialized'] is False
        assert 'extraction_buffer' not in mock_tool_context.state
        assert result['errors'][0]['raw_id'] == 'A1'

    async def test_duplicate_inventory_ids_rejected(self, mock_tool_context):
        result = await initialize_extraction_buffer(
            conversation_language='pt-BR',
            inventory=[
                _make_inventory_entry('A1'),
                _make_inventory_entry('A1'),
            ],
            tool_context=mock_tool_context,
        )
        assert result['status'] == 'error'
        assert result['buffer_initialized'] is False
        # Second occurrence carries the duplicate_id error.
        dup = next(
            err
            for err in result['errors']
            if err['errors'][0]['type'] == 'duplicate_id'
        )
        assert dup['raw_id'] == 'A1'

    async def test_overwrite_emits_warning_and_drops_prior_items(
        self, initialized_context
    ):
        # Populate the buffer first.
        await append_extraction_items(
            items=[_make_item(1)], tool_context=initialized_context
        )
        assert (
            len(initialized_context.state['extraction_buffer']['extracted_items'])
            == 1
        )

        result = await initialize_extraction_buffer(
            conversation_language='pt-BR',
            inventory=[_make_inventory_entry('A9')],
            tool_context=initialized_context,
        )
        assert result['status'] == 'ok'
        assert len(result['warnings']) == 1
        assert 'Overwrote' in result['warnings'][0]
        assert (
            initialized_context.state['extraction_buffer']['extracted_items']
            == []
        )


# ---------------------------------------------------------------------------
# append_extraction_items — happy path
# ---------------------------------------------------------------------------


class TestAppendHappyPath:
    async def test_single_valid_item_appended(self, initialized_context):
        result = await append_extraction_items(
            items=[_make_item(1)],
            tool_context=initialized_context,
        )
        assert result['status'] == 'ok'
        assert result['items_appended_this_call'] == 1
        assert result['total_items_in_buffer'] == 1
        assert result['errors_per_item'] == []

    async def test_multiple_valid_items_all_persisted(
        self, initialized_context
    ):
        items = [_make_item(i) for i in range(1, 25)]
        result = await append_extraction_items(
            items=items, tool_context=initialized_context
        )
        assert result['status'] == 'ok'
        assert result['items_appended_this_call'] == 24
        assert result['total_items_in_buffer'] == 24
        buffer = initialized_context.state['extraction_buffer']
        assert len(buffer['extracted_items']) == 24

    async def test_state_persists_across_calls(self, initialized_context):
        await append_extraction_items(
            items=[_make_item(i) for i in range(1, 4)],
            tool_context=initialized_context,
        )
        result = await append_extraction_items(
            items=[_make_item(i) for i in range(4, 7)],
            tool_context=initialized_context,
        )
        assert result['status'] == 'ok'
        assert result['total_items_in_buffer'] == 6


# ---------------------------------------------------------------------------
# append_extraction_items — validation errors
# ---------------------------------------------------------------------------


class TestAppendValidation:
    async def test_buffer_not_initialized_returns_error(
        self, mock_tool_context
    ):
        result = await append_extraction_items(
            items=[_make_item(1)], tool_context=mock_tool_context
        )
        assert result['status'] == 'error'
        assert result['items_appended_this_call'] == 0
        assert result['total_items_in_buffer'] == 0
        assert (
            result['errors_per_item'][0]['errors'][0]['type']
            == 'buffer_not_initialized'
        )

    async def test_invalid_schema_routed_to_errors_per_item(
        self, initialized_context
    ):
        bad = _make_item(1)
        bad['category'] = 'NotARealCategory'
        result = await append_extraction_items(
            items=[bad], tool_context=initialized_context
        )
        assert result['status'] == 'error'
        assert result['items_appended_this_call'] == 0
        assert len(result['errors_per_item']) == 1
        assert result['errors_per_item'][0]['raw_id'] == 'I-001'

    async def test_unknown_artifact_id_rejected(self, initialized_context):
        item = _make_item(1, artifact_id='A99')  # not in inventory
        result = await append_extraction_items(
            items=[item], tool_context=initialized_context
        )
        assert result['status'] == 'error'
        err = result['errors_per_item'][0]['errors'][0]
        assert err['type'] == 'unknown_artifact'
        assert 'A99' in err['msg']

    async def test_duplicate_id_against_buffer_rejected(
        self, initialized_context
    ):
        await append_extraction_items(
            items=[_make_item(1)], tool_context=initialized_context
        )
        result = await append_extraction_items(
            items=[_make_item(1)], tool_context=initialized_context
        )
        assert result['status'] == 'error'
        err = result['errors_per_item'][0]['errors'][0]
        assert err['type'] == 'duplicate_id'

    async def test_duplicate_id_within_single_call_rejected(
        self, initialized_context
    ):
        result = await append_extraction_items(
            items=[_make_item(1), _make_item(1)],
            tool_context=initialized_context,
        )
        # First copy is appended; second is rejected as duplicate-in-call.
        assert result['items_appended_this_call'] == 1
        assert result['status'] == 'partial'
        err_types = {
            e['errors'][0]['type'] for e in result['errors_per_item']
        }
        assert 'duplicate_id_in_call' in err_types


# ---------------------------------------------------------------------------
# append_extraction_items — status precedence
# ---------------------------------------------------------------------------


class TestAppendStatusPrecedence:
    async def test_all_valid_returns_ok(self, initialized_context):
        items = [_make_item(i) for i in range(1, 4)]
        result = await append_extraction_items(
            items=items, tool_context=initialized_context
        )
        assert result['status'] == 'ok'

    async def test_some_valid_some_invalid_returns_partial(
        self, initialized_context
    ):
        valid = [_make_item(i) for i in range(1, 4)]
        bad = _make_item(99)
        bad['category'] = 'NotARealCategory'
        result = await append_extraction_items(
            items=valid + [bad], tool_context=initialized_context
        )
        assert result['status'] == 'partial'
        assert result['items_appended_this_call'] == 3
        assert len(result['errors_per_item']) == 1

    async def test_all_invalid_returns_error(self, initialized_context):
        bad1 = _make_item(1)
        bad1['category'] = 'NotARealCategory'
        bad2 = _make_item(2)
        bad2['category'] = 'AlsoNotReal'
        result = await append_extraction_items(
            items=[bad1, bad2], tool_context=initialized_context
        )
        assert result['status'] == 'error'
        assert result['items_appended_this_call'] == 0
        assert len(result['errors_per_item']) == 2


# ---------------------------------------------------------------------------
# append_extraction_items — response shape
# ---------------------------------------------------------------------------


class TestAppendResponseShape:
    async def test_response_keys_are_stable(self, initialized_context):
        result = await append_extraction_items(
            items=[_make_item(1)], tool_context=initialized_context
        )
        assert set(result.keys()) == {
            'status',
            'items_appended_this_call',
            'total_items_in_buffer',
            'errors_per_item',
        }

    async def test_recovery_after_errors_produces_complete_buffer(
        self, initialized_context
    ):
        """First call rejects an invalid item; second call appends the gap."""
        bad = _make_item(1)
        bad['category'] = 'NotARealCategory'
        first = await append_extraction_items(
            items=[bad, _make_item(2), _make_item(3)],
            tool_context=initialized_context,
        )
        assert first['status'] == 'partial'
        assert first['total_items_in_buffer'] == 2

        # Resubmit only the corrected item — successful items are NOT resent.
        fixed = _make_item(1)
        second = await append_extraction_items(
            items=[fixed], tool_context=initialized_context
        )
        assert second['status'] == 'ok'
        assert second['total_items_in_buffer'] == 3


# ---------------------------------------------------------------------------
# finalize_extraction_manifest
# ---------------------------------------------------------------------------


def _valid_self_audit() -> dict[str, Any]:
    return {
        'all_artifacts_contributed': True,
        'all_required_categories_covered': True,
        'contradictions_resolved_or_flagged': True,
        'user_interview_turns': 0,
    }


def _valid_gaps() -> dict[str, Any]:
    return {
        'hard_gaps': [],
        'pending_decisions': [],
        'ambiguities': [],
        'to_be_defined': [],
    }


class TestFinalize:
    async def test_finalize_persists_manifest_and_clears_buffer(
        self, initialized_context
    ):
        # Both inventory entries (A1, A2) must contribute at least one
        # item — that's the all_artifacts_contributed cross-check.
        await append_extraction_items(
            items=[
                _identity_item(),
                _make_item(2, artifact_id='A1'),
                _make_item(3, artifact_id='A2'),
            ],
            tool_context=initialized_context,
        )
        result = await finalize_extraction_manifest(
            gaps=_valid_gaps(),
            self_audit=_valid_self_audit(),
            tool_context=initialized_context,
        )
        assert result['status'] == 'ok'
        assert result['manifest_persisted'] is True
        assert result['items_count'] == 3

        # Buffer cleared on success.
        assert initialized_context.state['extraction_buffer'] is None

        # Manifest persisted to session state under the dedicated key.
        stored = initialized_context.state[_MANIFEST_STATE_KEY]
        assert stored['manifest_version'] == '1.0'
        assert len(stored['extracted_items']) == 3
        assert len(stored['inventory']) == 2

    async def test_finalize_writes_conversation_language_to_app_language(
        self, initialized_context
    ):
        """Phase 2 section workers run BEFORE ``stage_sow`` (the other
        writer of ``app:language``), so the manifest tool must publish
        the conversation language as soon as discovery finalises. Without
        this write, requirements/delivery/scope agents would have to
        infer the output language from manifest prose, which is fragile
        in mixed-language sessions."""
        await append_extraction_items(
            items=[
                _identity_item(),
                _make_item(2, artifact_id='A1'),
                _make_item(3, artifact_id='A2'),
            ],
            tool_context=initialized_context,
        )

        # Precondition: nobody has written app:language yet.
        assert initialized_context.state.get('app:language') is None

        result = await finalize_extraction_manifest(
            gaps=_valid_gaps(),
            self_audit=_valid_self_audit(),
            tool_context=initialized_context,
        )

        assert result['status'] == 'ok'
        # initialized_context fixture seeded conversation_language='pt-BR'.
        assert initialized_context.state['app:language'] == 'pt-BR'

    async def test_finalize_does_not_write_app_language_on_validation_error(
        self, initialized_context
    ):
        """If the manifest fails validation, the buffer stays put AND no
        partial state should leak — ``app:language`` must remain absent
        so a later successful finalize is the sole writer."""
        # Missing Identity item → engagement_shape cross-check fails.
        await append_extraction_items(
            items=[_make_item(1)], tool_context=initialized_context
        )

        result = await finalize_extraction_manifest(
            gaps=_valid_gaps(),
            self_audit=_valid_self_audit(),
            tool_context=initialized_context,
        )

        assert result['status'] == 'error'
        assert initialized_context.state.get('app:language') is None

    async def test_finalize_without_buffer_returns_error(
        self, mock_tool_context
    ):
        result = await finalize_extraction_manifest(
            gaps=_valid_gaps(),
            self_audit=_valid_self_audit(),
            tool_context=mock_tool_context,
        )
        assert result['status'] == 'error'
        assert result['manifest_persisted'] is False
        assert (
            result['errors'][0]['type'] == 'buffer_not_initialized'
        )

    async def test_finalize_keeps_buffer_on_validation_error(
        self, initialized_context
    ):
        # No Identity items → engagement_shape cross-check fails at finalize.
        await append_extraction_items(
            items=[_make_item(1)], tool_context=initialized_context
        )
        result = await finalize_extraction_manifest(
            gaps=_valid_gaps(),
            self_audit=_valid_self_audit(),
            tool_context=initialized_context,
        )
        assert result['status'] == 'error'
        assert result['manifest_persisted'] is False
        # Manifest must NOT be persisted on validation failure.
        assert _MANIFEST_STATE_KEY not in initialized_context.state
        # Buffer preserved so the model can append the missing Identity item.
        assert (
            initialized_context.state['extraction_buffer'] is not None
        )
        assert len(
            initialized_context.state['extraction_buffer']['extracted_items']
        ) == 1


# ---------------------------------------------------------------------------
# validate_extraction_manifest (standalone)
# ---------------------------------------------------------------------------


class TestValidateStandalone:
    async def test_valid_manifest_returns_valid_true(self):
        manifest = {
            'manifest_version': '1.0',
            'created_at': '2026-05-06T00:00:00Z',
            'conversation_language': 'pt-BR',
            'inventory': [_make_inventory_entry('A1')],
            'extracted_items': [_identity_item()],
            'gaps': _valid_gaps(),
            'self_audit': _valid_self_audit(),
        }
        result = await validate_extraction_manifest(manifest=manifest)
        assert result == {'valid': True, 'errors': []}

    async def test_invalid_manifest_returns_errors_list(self):
        result = await validate_extraction_manifest(
            manifest={'manifest_version': '1.0'}
        )
        assert result['valid'] is False
        assert isinstance(result['errors'], list)
        assert len(result['errors']) > 0


# ---------------------------------------------------------------------------
# load_extraction_manifest
# ---------------------------------------------------------------------------


class TestLoadManifest:
    async def test_missing_manifest_returns_not_found(self, mock_tool_context):
        # State has no manifest entry yet.
        result = await load_extraction_manifest(tool_context=mock_tool_context)
        assert result == {'status': 'not_found', 'manifest': None}

    async def test_load_returns_manifest_finalize_persisted(
        self, initialized_context
    ):
        await append_extraction_items(
            items=[
                _identity_item(),
                _make_item(2, artifact_id='A1'),
                _make_item(3, artifact_id='A2'),
            ],
            tool_context=initialized_context,
        )
        await finalize_extraction_manifest(
            gaps=_valid_gaps(),
            self_audit=_valid_self_audit(),
            tool_context=initialized_context,
        )
        result = await load_extraction_manifest(
            tool_context=initialized_context
        )
        assert result['status'] == 'ok'
        assert result['manifest'] is not None
        assert result['manifest'] == initialized_context.state[
            _MANIFEST_STATE_KEY
        ]
        assert len(result['manifest']['extracted_items']) == 3
