"""Unit tests for ``app.callbacks``.

``before_tool_callback`` and ``after_tool_callback`` are the agent's
guardrails. Their behavior is specified and tested without invoking the
actual ADK runtime — we feed them a MagicMock ``ToolContext`` with a real
dict ``state`` (matching the SDK's interface).
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from google.adk.models import LlmResponse
from google.genai import types

from app import callbacks as _callbacks_module
from app.callbacks import (
    _FALLBACK_APOLOGY_EN,
    _MAX_EMPTY_RETRIES,
    _MAX_SOW_DATA_CHARS,
    _RECOVERY_TOOL_NAME,
    _STATE_EMPTY_RESPONSE_ATTEMPTS,
    after_tool_callback,
    before_tool_callback,
    empty_response_guard,
)


def _mock_tool(name: str) -> MagicMock:
    t = MagicMock()
    t.name = name
    return t


def _approve_arch_review(ctx) -> None:
    """Bypass the architecture-review gate for tests targeting other guards.

    The gated tools (validate_sow_content with stage='full',
    generate_sow_document) refuse to run until the user has explicitly
    approved the Architecture Review. Tests that exercise the size or
    JSON guards downstream must first satisfy that precondition.
    """
    ctx.state['phase.architecture_review_approved'] = True


class TestBeforeToolCallback:
    def test_normal_args_allowed(self, mock_tool_context):
        out = before_tool_callback(
            _mock_tool('some_tool'),
            {'sow_data': '{"a": 1}'},
            mock_tool_context,
        )
        assert out is None

    def test_oversized_sow_data_blocked(self, mock_tool_context):
        _approve_arch_review(mock_tool_context)
        big = 'x' * (_MAX_SOW_DATA_CHARS + 1)
        out = before_tool_callback(
            _mock_tool('generate_sow_document'),
            {'sow_data': big},
            mock_tool_context,
        )
        assert out is not None
        assert out['status'] == 'error'
        assert 'exceeds maximum size' in out['error']

    def test_boundary_size_allowed(self, mock_tool_context):
        _approve_arch_review(mock_tool_context)
        exact = 'x' * _MAX_SOW_DATA_CHARS
        out = before_tool_callback(
            _mock_tool('generate_sow_document'),
            {'sow_data': exact},
            mock_tool_context,
        )
        # At-limit values are accepted
        assert out is None or out.get('status') != 'error' or 'exceeds' not in out.get('error', '')

    @pytest.mark.parametrize(
        'tool_name',
        ['generate_sow_document', 'validate_sow_content'],
    )
    def test_invalid_json_blocked_for_sow_tools(
        self, mock_tool_context, tool_name
    ):
        _approve_arch_review(mock_tool_context)
        out = before_tool_callback(
            _mock_tool(tool_name),
            {'sow_data': '{not valid'},
            mock_tool_context,
        )
        assert out is not None
        assert out['status'] == 'error'
        assert 'not valid JSON' in out['error']

    def test_invalid_json_ignored_for_other_tools(self, mock_tool_context):
        """Tools unrelated to sow_data schema shouldn't trip on bad JSON."""
        out = before_tool_callback(
            _mock_tool('some_other_tool'),
            {'sow_data': '{not valid'},
            mock_tool_context,
        )
        assert out is None

    def test_valid_json_allowed(self, mock_tool_context):
        _approve_arch_review(mock_tool_context)
        out = before_tool_callback(
            _mock_tool('generate_sow_document'),
            {'sow_data': json.dumps({'key': 'value'})},
            mock_tool_context,
        )
        assert out is None

    def test_non_string_sow_data_skips_size_guard(self, mock_tool_context):
        """Size guard is string-only — dict payloads pass straight through it.

        Note: the JSON guard is NOT string-guarded — ADK always passes strings
        for tool args today, so this is a non-issue in production.
        """
        out = before_tool_callback(
            _mock_tool('some_unrelated_tool'),  # avoids JSON guard
            {'sow_data': {'already': 'dict'}},
            mock_tool_context,
        )
        assert out is None

    def test_no_sow_data_key_allowed(self, mock_tool_context):
        out = before_tool_callback(
            _mock_tool('another_tool'),
            {'query': 'hello'},
            mock_tool_context,
        )
        assert out is None

    def test_tool_without_name_attr_uses_str(self, mock_tool_context):
        class ToolishObject:
            def __str__(self):
                return 'fallback_name'

        out = before_tool_callback(
            ToolishObject(),
            {},
            mock_tool_context,
        )
        assert out is None


class TestAfterToolCallback:
    def test_appends_to_history(self, mock_tool_context):
        after_tool_callback(
            _mock_tool('my_tool'),
            {},
            mock_tool_context,
            {'status': 'success'},
        )
        history = mock_tool_context.state['tool_call_history']
        assert len(history) == 1
        assert history[0] == {'tool': 'my_tool', 'status': 'success'}

    def test_history_accumulates_across_calls(self, mock_tool_context):
        after_tool_callback(
            _mock_tool('t1'), {}, mock_tool_context, {'status': 'success'}
        )
        after_tool_callback(
            _mock_tool('t2'), {}, mock_tool_context, {'status': 'error'}
        )
        history = mock_tool_context.state['tool_call_history']
        assert [h['tool'] for h in history] == ['t1', 't2']
        assert [h['status'] for h in history] == ['success', 'error']

    def test_unknown_status_logged_as_unknown(self, mock_tool_context):
        after_tool_callback(
            _mock_tool('t'), {}, mock_tool_context, {}  # no status key
        )
        history = mock_tool_context.state['tool_call_history']
        assert history[0]['status'] == 'unknown'

    def test_non_dict_response_logged_as_unknown(self, mock_tool_context):
        after_tool_callback(
            _mock_tool('t'), {}, mock_tool_context, 'just a string'
        )
        history = mock_tool_context.state['tool_call_history']
        assert history[0]['status'] == 'unknown'

    def test_validate_sow_content_updates_validation_state(
        self, mock_tool_context
    ):
        after_tool_callback(
            _mock_tool('validate_sow_content'),
            {},
            mock_tool_context,
            {
                'status': 'success',
                'data': {'passed': True, 'error_count': 0},
            },
        )
        assert mock_tool_context.state['last_validation_passed'] is True
        assert mock_tool_context.state['last_validation_error_count'] == 0

    def test_validate_sow_content_captures_failure_details(
        self, mock_tool_context
    ):
        after_tool_callback(
            _mock_tool('validate_sow_content'),
            {},
            mock_tool_context,
            {
                'status': 'success',
                'data': {'passed': False, 'error_count': 3},
            },
        )
        assert mock_tool_context.state['last_validation_passed'] is False
        assert mock_tool_context.state['last_validation_error_count'] == 3

    def test_other_tools_dont_touch_validation_state(
        self, mock_tool_context
    ):
        after_tool_callback(
            _mock_tool('generate_sow_document'),
            {},
            mock_tool_context,
            {'status': 'success'},
        )
        assert 'last_validation_passed' not in mock_tool_context.state

    def test_returns_none(self, mock_tool_context):
        """Callback must return None so ADK uses the original tool response."""
        out = after_tool_callback(
            _mock_tool('t'), {}, mock_tool_context, {'status': 'success'}
        )
        assert out is None

    def test_validate_sow_content_non_dict_response_safe(
        self, mock_tool_context
    ):
        """Non-dict response must not raise, just skip validation tracking."""
        after_tool_callback(
            _mock_tool('validate_sow_content'),
            {},
            mock_tool_context,
            'plain string',
        )
        assert 'last_validation_passed' not in mock_tool_context.state


class TestIntegration:
    """Combined before/after flow against a single context."""

    def test_full_cycle_records_state(self, mock_tool_context):
        _approve_arch_review(mock_tool_context)
        tool = _mock_tool('validate_sow_content')
        before = before_tool_callback(
            tool,
            {'sow_data': json.dumps({'k': 'v'})},
            mock_tool_context,
        )
        assert before is None

        after_tool_callback(
            tool,
            {'sow_data': json.dumps({'k': 'v'})},
            mock_tool_context,
            {
                'status': 'success',
                'data': {'passed': True, 'error_count': 0},
            },
        )

        assert len(mock_tool_context.state['tool_call_history']) == 1
        assert mock_tool_context.state['last_validation_passed'] is True


def _make_callback_context(
    initial_state: dict | None = None,
    user_text: str | None = None,
) -> MagicMock:
    """A MagicMock that exposes ``state`` as a real dict, like ADK's Context.

    ``user_content`` defaults to None so tests that don't care about the
    user's last message can't accidentally trigger language detection.
    Pass ``user_text`` to simulate a user message of a given language.
    """
    ctx = MagicMock(name='CallbackContext')
    ctx.state = dict(initial_state or {})
    if user_text:
        ctx.user_content = types.Content(
            role='user',
            parts=[types.Part.from_text(text=user_text)],
        )
    else:
        ctx.user_content = None
    return ctx


def _mock_generator(monkeypatch, text: str = 'mocked apology in user language'):
    """Replace _generate_localized_apology with an AsyncMock returning ``text``."""
    from unittest.mock import AsyncMock
    mock = AsyncMock(return_value=text)
    monkeypatch.setattr(
        _callbacks_module, '_generate_localized_apology', mock
    )
    return mock


def _empty_response() -> LlmResponse:
    """A turn the UI would render as nothing: no content at all."""
    return LlmResponse(content=None)


def _empty_parts_response() -> LlmResponse:
    """A turn with an empty parts list — still nothing to render."""
    return LlmResponse(
        content=types.Content(role='model', parts=[]),
    )


def _whitespace_only_response() -> LlmResponse:
    """A turn whose only part contains whitespace — still nothing useful."""
    return LlmResponse(
        content=types.Content(
            role='model',
            parts=[types.Part.from_text(text='   \n\t')],
        ),
    )


def _text_response(text: str = 'Hello world') -> LlmResponse:
    """A healthy turn with substantive user-facing text."""
    return LlmResponse(
        content=types.Content(
            role='model',
            parts=[types.Part.from_text(text=text)],
        ),
    )


def _function_call_response(name: str = 'some_tool') -> LlmResponse:
    """A normal tool-calling turn: function call without text. NOT empty."""
    return LlmResponse(
        content=types.Content(
            role='model',
            parts=[
                types.Part(
                    function_call=types.FunctionCall(name=name, args={})
                )
            ],
        ),
    )


def _extract_function_call_name(response: LlmResponse) -> str | None:
    parts = (response.content.parts if response.content else None) or []
    for part in parts:
        fc = getattr(part, 'function_call', None)
        if fc is not None:
            return fc.name
    return None


def _extract_text(response: LlmResponse) -> str:
    parts = (response.content.parts if response.content else None) or []
    return ''.join((getattr(p, 'text', None) or '') for p in parts)


class TestEmptyResponseGuardPassThrough:
    """Healthy responses must pass through unchanged."""

    async def test_text_response_returns_none(self):
        ctx = _make_callback_context()
        out = await empty_response_guard(ctx, _text_response())
        assert out is None

    async def test_text_response_resets_attempt_counter(self):
        ctx = _make_callback_context({_STATE_EMPTY_RESPONSE_ATTEMPTS: 1})
        await empty_response_guard(ctx, _text_response())
        assert ctx.state[_STATE_EMPTY_RESPONSE_ATTEMPTS] == 0

    async def test_function_call_turn_returns_none(self):
        """text=None + function_call is the NORMAL tool-calling shape."""
        ctx = _make_callback_context()
        out = await empty_response_guard(ctx, _function_call_response())
        assert out is None

    async def test_function_call_turn_does_not_touch_counter(self):
        """A pre-tool turn must not be conflated with a terminal empty turn."""
        ctx = _make_callback_context({_STATE_EMPTY_RESPONSE_ATTEMPTS: 1})
        await empty_response_guard(ctx, _function_call_response())
        assert ctx.state[_STATE_EMPTY_RESPONSE_ATTEMPTS] == 0 or \
            ctx.state[_STATE_EMPTY_RESPONSE_ATTEMPTS] == 1

    async def test_response_with_error_code_returns_none(self):
        ctx = _make_callback_context()
        resp = LlmResponse(content=None, error_code='MODEL_ERROR')
        out = await empty_response_guard(ctx, resp)
        assert out is None

    async def test_response_with_error_message_returns_none(self):
        ctx = _make_callback_context()
        resp = LlmResponse(content=None, error_message='boom')
        out = await empty_response_guard(ctx, resp)
        assert out is None

    async def test_response_with_safety_finish_reason_returns_none(self):
        """SAFETY refusals are owned by scope_guardrail / safety settings."""
        safety = getattr(types.FinishReason, 'SAFETY', None)
        if safety is None:
            pytest.skip('SDK version does not expose FinishReason.SAFETY')
        ctx = _make_callback_context()
        resp = LlmResponse(content=None, finish_reason=safety)
        out = await empty_response_guard(ctx, resp)
        assert out is None


class TestEmptyResponseGuardRecovery:
    """Terminal-empty turns must be replaced with the recovery call."""

    @pytest.mark.parametrize(
        'response_factory',
        [
            _empty_response,
            _empty_parts_response,
            _whitespace_only_response,
        ],
    )
    async def test_first_empty_returns_recovery_call(self, response_factory):
        ctx = _make_callback_context()
        out = await empty_response_guard(ctx, response_factory())

        assert out is not None
        assert isinstance(out, LlmResponse)
        assert _extract_function_call_name(out) == _RECOVERY_TOOL_NAME
        assert ctx.state[_STATE_EMPTY_RESPONSE_ATTEMPTS] == 1

    async def test_counter_increments_across_consecutive_empties(self):
        ctx = _make_callback_context()
        for expected in range(1, _MAX_EMPTY_RETRIES + 1):
            out = await empty_response_guard(ctx, _empty_response())
            assert _extract_function_call_name(out) == _RECOVERY_TOOL_NAME
            assert ctx.state[_STATE_EMPTY_RESPONSE_ATTEMPTS] == expected

    async def test_recovery_response_carries_metadata_flag(self):
        ctx = _make_callback_context()
        out = await empty_response_guard(ctx, _empty_response())
        assert out.custom_metadata is not None
        assert out.custom_metadata.get('empty_response_recovery') is True

    async def test_recovery_part_carries_bypass_sentinel(self):
        """The synthetic function_call must carry the thought_signature
        bypass bytes; the SDK serialization patch turns those into the
        literal string Vertex AI expects."""
        from app._genai_patches import THOUGHT_SIGNATURE_BYPASS_BYTES

        ctx = _make_callback_context()
        out = await empty_response_guard(ctx, _empty_response())
        parts = out.content.parts or []
        assert parts, 'recovery response must have at least one part'
        assert parts[0].thought_signature == THOUGHT_SIGNATURE_BYPASS_BYTES

    async def test_recovery_part_survives_sdk_encoding_to_plaintext(self):
        """End-to-end: after the SDK encodes the synthetic Part, the
        thought_signature must arrive as the literal bypass plaintext —
        not as base64 of the sentinel."""
        from app._genai_patches import (
            THOUGHT_SIGNATURE_BYPASS_PLAINTEXT,
            apply,
        )
        # Idempotent — patch is applied at agent bootstrap, but tests can
        # be run without importing app.agent, so apply explicitly here.
        apply()

        ctx = _make_callback_context()
        out = await empty_response_guard(ctx, _empty_response())
        encoded = out.content.model_dump(mode='json', exclude_none=True)

        import google.genai._common as _common_mod
        wire = _common_mod.encode_unserializable_types(encoded['parts'][0])
        assert (
            wire['thought_signature']
            == THOUGHT_SIGNATURE_BYPASS_PLAINTEXT
        ), f'expected plaintext bypass, got {wire["thought_signature"]!r}'


class TestEmptyResponseGuardExhaustion:
    """After MAX_EMPTY_RETRIES the user must see one honest apology
    generated in their language by the Flash Lite helper."""

    async def test_exhausted_returns_apology_text_response(self, monkeypatch):
        _mock_generator(monkeypatch, text='mocked-localized-apology')
        ctx = _make_callback_context(
            {_STATE_EMPTY_RESPONSE_ATTEMPTS: _MAX_EMPTY_RETRIES}
        )
        out = await empty_response_guard(ctx, _empty_response())

        assert out is not None
        # No function call — this is a user-facing terminal message.
        assert _extract_function_call_name(out) is None
        assert _extract_text(out) == 'mocked-localized-apology'

    async def test_exhausted_resets_counter(self, monkeypatch):
        _mock_generator(monkeypatch)
        ctx = _make_callback_context(
            {_STATE_EMPTY_RESPONSE_ATTEMPTS: _MAX_EMPTY_RETRIES}
        )
        await empty_response_guard(ctx, _empty_response())
        assert ctx.state[_STATE_EMPTY_RESPONSE_ATTEMPTS] == 0

    async def test_exhausted_response_carries_metadata_flag(self, monkeypatch):
        _mock_generator(monkeypatch)
        ctx = _make_callback_context(
            {_STATE_EMPTY_RESPONSE_ATTEMPTS: _MAX_EMPTY_RETRIES}
        )
        out = await empty_response_guard(ctx, _empty_response())
        assert out.custom_metadata is not None
        assert (
            out.custom_metadata.get('empty_response_recovery_exhausted')
            is True
        )

    async def test_exhausted_apology_text_comes_from_generator(
        self, monkeypatch
    ):
        """The localized text from _generate_localized_apology must be the
        literal user-visible content — no hard-coded bilingual blob."""
        mock = _mock_generator(
            monkeypatch, text='Désolé, pouvez-vous reformuler ?'
        )
        ctx = _make_callback_context(
            {_STATE_EMPTY_RESPONSE_ATTEMPTS: _MAX_EMPTY_RETRIES},
            user_text='Bonjour, pouvez-vous générer un SOW pour Acme ?',
        )
        out = await empty_response_guard(ctx, _empty_response())
        assert _extract_text(out) == 'Désolé, pouvez-vous reformuler ?'
        # Generator was invoked once with the same callback context
        mock.assert_awaited_once_with(ctx)


class TestEmptyResponseGuardRecoveryFollowedByHealthy:
    """After successful recovery the counter resets so a future incident
    starts a fresh budget of retries."""

    async def test_recovery_then_healthy_resets_counter(self):
        ctx = _make_callback_context()
        # 1st empty: counter -> 1, recovery injected
        await empty_response_guard(ctx, _empty_response())
        assert ctx.state[_STATE_EMPTY_RESPONSE_ATTEMPTS] == 1

        # Healthy text response: counter resets
        await empty_response_guard(
            ctx, _text_response('Here is the content review.')
        )
        assert ctx.state[_STATE_EMPTY_RESPONSE_ATTEMPTS] == 0

    async def test_full_cycle_recovery_then_failure_then_apology(
        self, monkeypatch
    ):
        _mock_generator(monkeypatch, text='localized apology')
        ctx = _make_callback_context()

        # Empty turn #1: recovery
        out1 = await empty_response_guard(ctx, _empty_response())
        assert _extract_function_call_name(out1) == _RECOVERY_TOOL_NAME

        # Empty turn #2: recovery
        out2 = await empty_response_guard(ctx, _empty_response())
        assert _extract_function_call_name(out2) == _RECOVERY_TOOL_NAME

        # Empty turn #3: apology (counter was at MAX)
        out3 = await empty_response_guard(ctx, _empty_response())
        assert _extract_function_call_name(out3) is None
        assert _extract_text(out3) == 'localized apology'


class TestLocalizedApologyGenerator:
    """``_generate_localized_apology`` must never raise and must always
    fall back to the static English message on any error path."""

    async def test_no_user_content_falls_back_to_en(self):
        ctx = _make_callback_context()  # user_content=None by default
        result = await _callbacks_module._generate_localized_apology(ctx)
        assert result == _FALLBACK_APOLOGY_EN

    async def test_empty_user_text_falls_back_to_en(self):
        ctx = _make_callback_context(user_text='')  # empty string
        result = await _callbacks_module._generate_localized_apology(ctx)
        assert result == _FALLBACK_APOLOGY_EN

    async def test_client_exception_falls_back_to_en(self, monkeypatch):
        """Any exception from the model call must produce the EN fallback,
        never raise."""
        from unittest.mock import AsyncMock, MagicMock

        bad_client = MagicMock()
        bad_client.aio.models.generate_content = AsyncMock(
            side_effect=RuntimeError('vertex unavailable')
        )
        monkeypatch.setattr(
            _callbacks_module, '_get_apology_client', lambda: bad_client
        )

        ctx = _make_callback_context(user_text='Olá, preciso de ajuda.')
        result = await _callbacks_module._generate_localized_apology(ctx)
        assert result == _FALLBACK_APOLOGY_EN

    async def test_empty_model_response_falls_back_to_en(self, monkeypatch):
        from unittest.mock import AsyncMock, MagicMock

        empty_response = MagicMock()
        empty_response.text = ''  # model returned nothing
        client = MagicMock()
        client.aio.models.generate_content = AsyncMock(
            return_value=empty_response
        )
        monkeypatch.setattr(
            _callbacks_module, '_get_apology_client', lambda: client
        )

        ctx = _make_callback_context(user_text='Hello there')
        result = await _callbacks_module._generate_localized_apology(ctx)
        assert result == _FALLBACK_APOLOGY_EN

    async def test_localized_text_returned_when_model_succeeds(
        self, monkeypatch
    ):
        from unittest.mock import AsyncMock, MagicMock

        success_response = MagicMock()
        success_response.text = 'Desculpe, pode repetir a mensagem?'
        client = MagicMock()
        client.aio.models.generate_content = AsyncMock(
            return_value=success_response
        )
        monkeypatch.setattr(
            _callbacks_module, '_get_apology_client', lambda: client
        )

        ctx = _make_callback_context(
            user_text='Bom dia, quero gerar um SOW para o cliente X.'
        )
        result = await _callbacks_module._generate_localized_apology(ctx)
        assert result == 'Desculpe, pode repetir a mensagem?'

    async def test_long_user_text_is_truncated_before_call(
        self, monkeypatch
    ):
        """Snippet must be capped before reaching the model so we never
        pay tokens for a huge user upload on the recovery path."""
        from unittest.mock import AsyncMock, MagicMock

        captured: dict[str, str] = {}

        async def fake_generate(model, contents, config):
            captured['contents'] = contents
            r = MagicMock()
            r.text = 'apology'
            return r

        client = MagicMock()
        client.aio.models.generate_content = AsyncMock(
            side_effect=fake_generate
        )
        monkeypatch.setattr(
            _callbacks_module, '_get_apology_client', lambda: client
        )

        huge = 'A' * 5000
        ctx = _make_callback_context(user_text=huge)
        await _callbacks_module._generate_localized_apology(ctx)

        sent = captured['contents']
        assert len(sent) < len(huge)
        assert sent.endswith('…')
