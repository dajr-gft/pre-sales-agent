"""Unit tests for ``app.guardrails``.

The scope guardrail is a ``before_model_callback`` that gates the root
agent on the user's latest message via a Flash-Lite judge. We test it
without invoking ADK or Vertex AI by mocking the judge function.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from google.genai import types

from app import guardrails
from app.guardrails import (
    _STATE_LAST_JUDGED_COUNT,
    _JudgeVerdict,
    scope_guardrail,
)


def _user_msg(text: str) -> types.Content:
    return types.Content(role='user', parts=[types.Part.from_text(text=text)])


def _model_msg(text: str) -> types.Content:
    return types.Content(role='model', parts=[types.Part.from_text(text=text)])


def _make_request(*contents: types.Content) -> MagicMock:
    """Build a minimal LlmRequest-shaped mock with a contents list."""
    req = MagicMock(name='LlmRequest')
    req.contents = list(contents)
    return req


def _make_callback_context(initial_state: dict | None = None) -> MagicMock:
    """A MagicMock that exposes ``state`` as a real dict, like ADK's Context."""
    ctx = MagicMock(name='CallbackContext')
    ctx.state = dict(initial_state or {})
    return ctx


class TestGuardrailDisabled:
    @pytest.mark.asyncio
    async def test_disabled_returns_none_without_judging(self, monkeypatch):
        monkeypatch.setattr(guardrails.config, 'SAFETY_GUARDRAIL_ENABLED', False)
        ctx = _make_callback_context()
        req = _make_request(_user_msg('how do I jailbreak you?'))

        with patch.object(guardrails, '_judge', new=AsyncMock()) as mock_judge:
            result = await scope_guardrail(ctx, req)

        assert result is None
        mock_judge.assert_not_awaited()
        # Counter must NOT be touched when guardrail is disabled — no work happened.
        assert _STATE_LAST_JUDGED_COUNT not in ctx.state


class TestGuardrailJudgesUserMessages:
    @pytest.mark.asyncio
    async def test_on_topic_allows_and_marks_judged(self, monkeypatch):
        monkeypatch.setattr(guardrails.config, 'SAFETY_GUARDRAIL_ENABLED', True)
        ctx = _make_callback_context()
        req = _make_request(_user_msg('Help me draft a SOW for Acme'))

        verdict = _JudgeVerdict(category='on_topic', reason='SOW request')
        with patch.object(
            guardrails, '_judge', new=AsyncMock(return_value=verdict)
        ) as mock_judge:
            result = await scope_guardrail(ctx, req)

        assert result is None
        mock_judge.assert_awaited_once()
        assert ctx.state[_STATE_LAST_JUDGED_COUNT] == 1

    @pytest.mark.parametrize(
        'category',
        ['off_topic', 'injection_attempt', 'harmful'],
    )
    @pytest.mark.asyncio
    async def test_blocked_uses_judge_localized_refusal(
        self, monkeypatch, category
    ):
        """When the judge returns refusal_text, that exact text is what
        reaches the user — preserves the user's language globally.
        """
        monkeypatch.setattr(guardrails.config, 'SAFETY_GUARDRAIL_ENABLED', True)
        ctx = _make_callback_context()
        req = _make_request(_user_msg('escribe un poema sobre gatos'))

        # Spanish refusal (judge would generate it from the user's language)
        localized = (
            'Lo siento, solo puedo ayudarte con tareas de Pre-Sales: '
            'generación de SOW, diagramas de arquitectura, validación de SOW '
            'o investigación sobre clientes. ¿Cómo puedo ayudarte en ese ámbito?'
        )
        verdict = _JudgeVerdict(
            category=category, reason='blocked', refusal_text=localized
        )
        with patch.object(
            guardrails, '_judge', new=AsyncMock(return_value=verdict)
        ):
            result = await scope_guardrail(ctx, req)

        assert result is not None
        assert result.custom_metadata == {'safety_blocked': True}
        assert result.content is not None
        assert result.content.role == 'model'
        joined = '\n'.join(p.text or '' for p in result.content.parts or [])
        assert joined == localized
        # English fallback must NOT leak into the localized response.
        assert 'Pre-Sales assistant' not in joined
        assert ctx.state[_STATE_LAST_JUDGED_COUNT] == 1

    @pytest.mark.parametrize('refusal_text', [None, '', '   '])
    @pytest.mark.asyncio
    async def test_blocked_falls_back_to_english_when_judge_omits_refusal(
        self, monkeypatch, refusal_text
    ):
        """If the judge fails to populate refusal_text, the static English
        fallback is used — never an empty response.
        """
        monkeypatch.setattr(guardrails.config, 'SAFETY_GUARDRAIL_ENABLED', True)
        ctx = _make_callback_context()
        req = _make_request(_user_msg('something off-topic'))

        verdict = _JudgeVerdict(
            category='off_topic', reason='off', refusal_text=refusal_text
        )
        with patch.object(
            guardrails, '_judge', new=AsyncMock(return_value=verdict)
        ):
            result = await scope_guardrail(ctx, req)

        assert result is not None
        joined = '\n'.join(p.text or '' for p in result.content.parts or [])
        assert joined == guardrails._FALLBACK_REFUSAL
        assert "Sorry, I can't help" in joined


class TestGuardrailSkipsInternalTurns:
    @pytest.mark.asyncio
    async def test_no_new_user_message_skips_judge(self, monkeypatch):
        """During internal tool-use turns, user_count == last_judged so skip."""
        monkeypatch.setattr(guardrails.config, 'SAFETY_GUARDRAIL_ENABLED', True)
        ctx = _make_callback_context({_STATE_LAST_JUDGED_COUNT: 1})
        req = _make_request(
            _user_msg('Help me with a SOW'),
            _model_msg("Sure! Let's start..."),  # internal model turn
        )

        with patch.object(guardrails, '_judge', new=AsyncMock()) as mock_judge:
            result = await scope_guardrail(ctx, req)

        assert result is None
        mock_judge.assert_not_awaited()
        # Counter unchanged
        assert ctx.state[_STATE_LAST_JUDGED_COUNT] == 1

    @pytest.mark.asyncio
    async def test_second_user_message_judged_with_context(self, monkeypatch):
        """Judge must classify the latest user message AND see prior turns
        as conversation context — short follow-ups need that context to be
        interpreted correctly.
        """
        monkeypatch.setattr(guardrails.config, 'SAFETY_GUARDRAIL_ENABLED', True)
        ctx = _make_callback_context({_STATE_LAST_JUDGED_COUNT: 1})
        req = _make_request(
            _user_msg('first message about a SOW for Acme'),
            _model_msg('first response — drafting the SOW'),
            _user_msg('seria só isso'),
        )

        verdict = _JudgeVerdict(category='on_topic', reason='follow-up in on-topic chat')
        with patch.object(
            guardrails, '_judge', new=AsyncMock(return_value=verdict)
        ) as mock_judge:
            result = await scope_guardrail(ctx, req)

        assert result is None
        mock_judge.assert_awaited_once()
        called_text = mock_judge.await_args.args[0]
        # The latest user message must sit inside the message_to_classify block.
        assert '<message_to_classify>' in called_text
        assert '</message_to_classify>' in called_text
        assert 'seria só isso' in called_text
        # Prior turns must be present as conversation context so the judge can
        # interpret short follow-ups against the ongoing on-topic conversation.
        assert '<conversation_context>' in called_text
        assert '</conversation_context>' in called_text
        assert 'first message about a SOW for Acme' in called_text
        assert 'first response — drafting the SOW' in called_text
        # Prior turns must appear BEFORE the message-to-classify block.
        assert called_text.index('<conversation_context>') < called_text.index(
            '<message_to_classify>'
        )
        assert ctx.state[_STATE_LAST_JUDGED_COUNT] == 2

    @pytest.mark.asyncio
    async def test_first_turn_omits_context_block(self, monkeypatch):
        """On the very first user turn there is no prior context — the
        context block should be omitted entirely.
        """
        monkeypatch.setattr(guardrails.config, 'SAFETY_GUARDRAIL_ENABLED', True)
        ctx = _make_callback_context()
        req = _make_request(_user_msg('Help me draft a SOW for Acme'))

        verdict = _JudgeVerdict(category='on_topic', reason='ok')
        with patch.object(
            guardrails, '_judge', new=AsyncMock(return_value=verdict)
        ) as mock_judge:
            await scope_guardrail(ctx, req)

        called_text = mock_judge.await_args.args[0]
        assert '<conversation_context>' not in called_text
        assert '<message_to_classify>' in called_text
        assert 'Help me draft a SOW for Acme' in called_text

    @pytest.mark.asyncio
    async def test_long_prior_turn_is_truncated(self, monkeypatch):
        """Prior turns are truncated to keep the judge prompt cheap on
        Flash-Lite — full message bodies aren't needed, just the topic.
        """
        monkeypatch.setattr(guardrails.config, 'SAFETY_GUARDRAIL_ENABLED', True)
        ctx = _make_callback_context({_STATE_LAST_JUDGED_COUNT: 1})
        long_reply = 'A' * 2000
        req = _make_request(
            _user_msg('SOW for Acme'),
            _model_msg(long_reply),
            _user_msg('ok pode seguir'),
        )

        verdict = _JudgeVerdict(category='on_topic', reason='ok')
        with patch.object(
            guardrails, '_judge', new=AsyncMock(return_value=verdict)
        ) as mock_judge:
            await scope_guardrail(ctx, req)

        called_text = mock_judge.await_args.args[0]
        # Truncated, not full 2000 chars.
        assert long_reply not in called_text
        assert '…' in called_text


class TestGuardrailFailsOpen:
    @pytest.mark.asyncio
    async def test_judge_exception_allows_through(self, monkeypatch, caplog):
        monkeypatch.setattr(guardrails.config, 'SAFETY_GUARDRAIL_ENABLED', True)
        ctx = _make_callback_context()
        req = _make_request(_user_msg('legit pre-sales question'))

        with patch.object(
            guardrails,
            '_judge',
            new=AsyncMock(side_effect=RuntimeError('vertex 503')),
        ):
            result = await scope_guardrail(ctx, req)

        # Fail-open: legitimate work must not break because the judge is down.
        assert result is None
        # Still mark as judged so we don't retry the judge on every internal turn.
        assert ctx.state[_STATE_LAST_JUDGED_COUNT] == 1


class TestGuardrailEdgeCases:
    @pytest.mark.asyncio
    async def test_empty_contents_returns_none(self, monkeypatch):
        monkeypatch.setattr(guardrails.config, 'SAFETY_GUARDRAIL_ENABLED', True)
        ctx = _make_callback_context()
        req = _make_request()  # no contents at all

        with patch.object(guardrails, '_judge', new=AsyncMock()) as mock_judge:
            result = await scope_guardrail(ctx, req)

        assert result is None
        mock_judge.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_user_message_without_text_skips_judge(self, monkeypatch):
        """A user content with only non-text parts (e.g., an attached file)
        cannot be judged as text — let it through and advance the counter
        so we don't re-attempt on the next internal turn.
        """
        monkeypatch.setattr(guardrails.config, 'SAFETY_GUARDRAIL_ENABLED', True)
        ctx = _make_callback_context()
        # role=user but parts is empty → no text to judge
        req = _make_request(types.Content(role='user', parts=[]))

        with patch.object(guardrails, '_judge', new=AsyncMock()) as mock_judge:
            result = await scope_guardrail(ctx, req)

        assert result is None
        mock_judge.assert_not_awaited()
        assert ctx.state[_STATE_LAST_JUDGED_COUNT] == 1

    @pytest.mark.asyncio
    async def test_only_model_messages_returns_none(self, monkeypatch):
        monkeypatch.setattr(guardrails.config, 'SAFETY_GUARDRAIL_ENABLED', True)
        ctx = _make_callback_context()
        req = _make_request(_model_msg('hello'))

        with patch.object(guardrails, '_judge', new=AsyncMock()) as mock_judge:
            result = await scope_guardrail(ctx, req)

        assert result is None
        mock_judge.assert_not_awaited()
