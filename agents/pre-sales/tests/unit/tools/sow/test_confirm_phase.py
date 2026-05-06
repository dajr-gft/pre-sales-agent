"""Unit tests for the phase-confirmation runtime gate.

Covers the eight acceptance criteria:

1. ``confirm_phase_completion('inference_summary_confirmed')`` works
   without prerequisites.
2. ``confirm_phase_completion('content_review_approved')`` returns a
   ToolError when the prior phase is missing, and works after it.
3. ``confirm_phase_completion('architecture_review_approved')`` returns
   a ToolError when the prior phase is missing, and works after the
   full sequence.
4. An invalid phase_key returns a ToolError with ``retryable=False``.
5. ``validate_sow_content(stage='content')`` is never blocked by the
   architecture-review gate.
6. ``validate_sow_content(stage='full')`` and ``generate_sow_document``
   stay blocked until ``architecture_review_approved`` is set, and the
   error message points to ``confirm_phase_completion``.
7. The ``is_architecture_review_approved`` accessor reads the new
   phase-based state key.
8. Idempotency: re-confirming the same phase keeps the state True (the
   second call succeeds; behaviour is documented as idempotent — the
   stamp is a write, not a counter).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.callbacks import before_tool_callback
from app.tools.sow.confirm_phase import (
    PHASE_KEYS,
    PHASE_STATE_PREFIX,
    confirm_phase_completion,
    is_architecture_review_approved,
)


def _mock_tool(name: str) -> MagicMock:
    t = MagicMock()
    t.name = name
    return t


def _state_key(phase_key: str) -> str:
    return f'{PHASE_STATE_PREFIX}{phase_key}'


# ---------------------------------------------------------------------------
# Acceptance criteria #1, #2, #3 — ordered confirmation
# ---------------------------------------------------------------------------


class TestConfirmPhaseCompletionOrder:
    async def test_first_phase_has_no_prerequisites(self, mock_tool_context):
        result = await confirm_phase_completion(
            phase_key='inference_summary_confirmed',
            tool_context=mock_tool_context,
        )
        assert result['status'] == 'success'
        assert (
            result['data']['phase_confirmed']
            == 'inference_summary_confirmed'
        )
        assert result['data']['all_phases_confirmed'] is False
        assert (
            mock_tool_context.state[_state_key('inference_summary_confirmed')]
            is True
        )

    async def test_content_review_blocked_without_manifest_confirmation(
        self, mock_tool_context
    ):
        result = await confirm_phase_completion(
            phase_key='content_review_approved',
            tool_context=mock_tool_context,
        )
        assert result['status'] == 'error'
        assert result['retryable'] is True
        assert 'inference_summary_confirmed' in result['error']
        # Nothing was persisted on the failed call.
        assert (
            _state_key('content_review_approved')
            not in mock_tool_context.state
        )

    async def test_content_review_succeeds_after_manifest_confirmation(
        self, mock_tool_context
    ):
        await confirm_phase_completion(
            phase_key='inference_summary_confirmed',
            tool_context=mock_tool_context,
        )
        result = await confirm_phase_completion(
            phase_key='content_review_approved',
            tool_context=mock_tool_context,
        )
        assert result['status'] == 'success'
        assert (
            mock_tool_context.state[_state_key('content_review_approved')]
            is True
        )

    async def test_architecture_review_blocked_without_content_review(
        self, mock_tool_context
    ):
        # Confirm only the first phase — content_review_approved is missing.
        await confirm_phase_completion(
            phase_key='inference_summary_confirmed',
            tool_context=mock_tool_context,
        )
        result = await confirm_phase_completion(
            phase_key='architecture_review_approved',
            tool_context=mock_tool_context,
        )
        assert result['status'] == 'error'
        assert result['retryable'] is True
        assert 'content_review_approved' in result['error']
        assert (
            _state_key('architecture_review_approved')
            not in mock_tool_context.state
        )

    async def test_full_workflow_in_order_sets_all_keys_and_flag(
        self, mock_tool_context
    ):
        for phase_key in PHASE_KEYS:
            result = await confirm_phase_completion(
                phase_key=phase_key,
                tool_context=mock_tool_context,
            )
            assert result['status'] == 'success', phase_key

        # Final state: every phase is True and the all-confirmed flag flips.
        for phase_key in PHASE_KEYS:
            assert (
                mock_tool_context.state[_state_key(phase_key)] is True
            ), phase_key
        assert result['data']['all_phases_confirmed'] is True


# ---------------------------------------------------------------------------
# Acceptance criterion #4 — invalid phase_key
# ---------------------------------------------------------------------------


class TestInvalidPhaseKey:
    async def test_unknown_phase_key_returns_non_retryable_error(
        self, mock_tool_context
    ):
        result = await confirm_phase_completion(
            phase_key='foo',
            tool_context=mock_tool_context,
        )
        assert result['status'] == 'error'
        assert result['retryable'] is False
        assert "'foo'" in result['error']
        # No state mutation on rejection.
        assert mock_tool_context.state == {}

    async def test_empty_phase_key_returns_error(self, mock_tool_context):
        result = await confirm_phase_completion(
            phase_key='',
            tool_context=mock_tool_context,
        )
        assert result['status'] == 'error'
        assert result['retryable'] is False


# ---------------------------------------------------------------------------
# Acceptance criteria #5 + #6 — gate behaviour in before_tool_callback
# ---------------------------------------------------------------------------


class TestArchitectureReviewGate:
    def test_validate_full_blocked_without_approval(self, mock_tool_context):
        out = before_tool_callback(
            _mock_tool('validate_sow_content'),
            {'sow_data': '{"k": "v"}', 'stage': 'full'},
            mock_tool_context,
        )
        assert out is not None
        assert out['status'] == 'error'
        assert 'Architecture Review' in out['error']
        assert 'confirm_phase_completion' in out['error']
        assert 'architecture_review_approved' in out['error']

    def test_validate_full_blocked_when_stage_omitted(
        self, mock_tool_context
    ):
        """stage defaults to 'full' — calls without it must be gated."""
        out = before_tool_callback(
            _mock_tool('validate_sow_content'),
            {'sow_data': '{"k": "v"}'},
            mock_tool_context,
        )
        assert out is not None
        assert out['status'] == 'error'

    def test_generate_sow_document_blocked_without_approval(
        self, mock_tool_context
    ):
        out = before_tool_callback(
            _mock_tool('generate_sow_document'),
            {'sow_data': '{"k": "v"}'},
            mock_tool_context,
        )
        assert out is not None
        assert out['status'] == 'error'
        assert 'confirm_phase_completion' in out['error']

    def test_validate_content_stage_never_blocked(self, mock_tool_context):
        out = before_tool_callback(
            _mock_tool('validate_sow_content'),
            {'sow_data': '{"k": "v"}', 'stage': 'content'},
            mock_tool_context,
        )
        assert out is None

    def test_other_tools_not_affected(self, mock_tool_context):
        out = before_tool_callback(
            _mock_tool('load_extraction_manifest'),
            {},
            mock_tool_context,
        )
        assert out is None

    def test_gate_passes_after_approval(self, mock_tool_context):
        mock_tool_context.state[_state_key('architecture_review_approved')] = (
            True
        )
        out = before_tool_callback(
            _mock_tool('generate_sow_document'),
            {'sow_data': '{"k": "v"}'},
            mock_tool_context,
        )
        assert out is None

    def test_validate_full_passes_after_approval(self, mock_tool_context):
        mock_tool_context.state[_state_key('architecture_review_approved')] = (
            True
        )
        out = before_tool_callback(
            _mock_tool('validate_sow_content'),
            {'sow_data': '{"k": "v"}', 'stage': 'full'},
            mock_tool_context,
        )
        assert out is None


# ---------------------------------------------------------------------------
# Acceptance criterion #7 — is_architecture_review_approved accessor
# ---------------------------------------------------------------------------


class TestArchitectureReviewAccessor:
    def test_returns_false_on_empty_state(self):
        assert is_architecture_review_approved({}) is False

    def test_returns_false_when_other_phases_set(self):
        state = {
            _state_key('inference_summary_confirmed'): True,
            _state_key('content_review_approved'): True,
        }
        assert is_architecture_review_approved(state) is False

    async def test_returns_true_after_full_workflow(self, mock_tool_context):
        for phase_key in PHASE_KEYS:
            await confirm_phase_completion(
                phase_key=phase_key,
                tool_context=mock_tool_context,
            )
        assert is_architecture_review_approved(mock_tool_context.state) is True


# ---------------------------------------------------------------------------
# Acceptance criterion #8 — idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    """Re-stamping a phase is a write, not a counter.

    Behaviour: calling ``confirm_phase_completion`` with the same key
    twice in a row succeeds both times and leaves the state ``True``.
    Documented as accepted behaviour — the stamp is monotonic.
    """

    async def test_re_confirming_same_phase_succeeds(self, mock_tool_context):
        first = await confirm_phase_completion(
            phase_key='inference_summary_confirmed',
            tool_context=mock_tool_context,
        )
        second = await confirm_phase_completion(
            phase_key='inference_summary_confirmed',
            tool_context=mock_tool_context,
        )
        assert first['status'] == 'success'
        assert second['status'] == 'success'
        assert (
            mock_tool_context.state[_state_key('inference_summary_confirmed')]
            is True
        )

    async def test_re_confirming_architecture_keeps_gate_open(
        self, mock_tool_context, sow_data
    ):
        import json

        # Walk the workflow once.
        for phase_key in PHASE_KEYS:
            await confirm_phase_completion(
                phase_key=phase_key,
                tool_context=mock_tool_context,
            )
        # Re-stamp the final phase.
        await confirm_phase_completion(
            phase_key='architecture_review_approved',
            tool_context=mock_tool_context,
        )

        out = before_tool_callback(
            _mock_tool('generate_sow_document'),
            {'sow_data': json.dumps(sow_data)},
            mock_tool_context,
        )
        # Gate stays open.
        assert out is None


# ---------------------------------------------------------------------------
# Pipeline cleanup — no overrides leak into Phase 3
# ---------------------------------------------------------------------------


class TestNoStateOverridesInPhase3:
    """Sanity check: the architecture-review module no longer exports
    any helper that mutates ``sow_data`` in the Phase 3 pipeline.

    If somebody re-introduces an override path, this test fails before
    the integration tests do.
    """

    def test_module_does_not_export_override_helpers(self):
        import importlib

        from app.tools import sow as sow_pkg

        forbidden = (
            'apply_arch_review_state_overrides',
            'serialize_arch_overrides',
            'present_architecture_review',
            'confirm_architecture_approved',
            'ARCH_REVIEW_FIELDS',
            'ARCH_REVIEW_STATE_PREFIX',
            'ARCH_REVIEW_PRESENTED_KEY',
            'ARCH_REVIEW_APPROVED_KEY',
        )

        # The package itself must not re-export any override symbol.
        for name in forbidden:
            assert not hasattr(sow_pkg, name), name

        # If the legacy ``architecture_review`` submodule still exists,
        # it must not expose override symbols either. A missing module is
        # an even stronger guarantee and is therefore acceptable.
        try:
            arch_review = importlib.import_module(
                'app.tools.sow.architecture_review'
            )
        except ImportError:
            return

        for name in forbidden:
            assert not hasattr(arch_review, name), name

    async def test_validate_sow_content_does_not_consume_state(
        self, mock_tool_context, sow_data
    ):
        """Approve the gate, drift the architecture text, and check
        that no override silently rewrites it before the validator
        runs. The validator either passes or fails on the literal model
        payload — no state-side rewrite is permitted.
        """
        import json

        from app.tools.sow.validate_sow_content import validate_sow_content

        # Approve the architecture phase so the gate lets us through.
        mock_tool_context.state[
            _state_key('architecture_review_approved')
        ] = True

        # Stash a fake "approved" architecture in state. If overrides
        # were still wired in, this string would replace the model's
        # payload before validation.
        state_only_marker = 'STATE_ONLY_MARKER_THAT_MUST_NOT_LEAK'
        mock_tool_context.state['arch_review.architecture_description'] = (
            state_only_marker
        )

        sow_data['architecture_description'] = (
            sow_data['architecture_description'] + ' (model payload)'
        )

        result = await validate_sow_content(
            sow_data=json.dumps(sow_data),
            funding_type='DAF',
            stage='full',
            tool_context=mock_tool_context,
        )

        # The marker must not appear in the validator's output — it lives
        # only in state, which the tool no longer consults.
        assert state_only_marker not in str(result)
