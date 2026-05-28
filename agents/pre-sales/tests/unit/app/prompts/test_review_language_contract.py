"""Tests for the conversation-language contract.

In real testing the agent presented Content/Architecture Reviews in English
while the conversation was Portuguese. Root cause: the only rule that says
"section content is generated in English for the document and translated
to the conversation language at review time" lives in
``sow-shared/references/language-rules.md``, which the section skills load
and the ``AutoScopedSkillToolset`` prunes before the review happens — so
the rule is not in the root's context when it matters. On top of that the
root's ``<content_review_gate>`` emphasises translating *labels* but is
ambiguous about translating the full text of each item, and the bundles
in ``state['app:sow:<section>']`` are English by design (correct, they
feed the ``.docx``). The fix anchors everything on ``state['app:language']``.

These tests pin the contract:

- ``<communication_rules>`` no longer uses "first message" as an absolute
  rule and instead anchors on ``state['app:language']``.
- ``<content_review_gate>`` / ``<architecture_review_gate>`` require
  translating labels AND the full content of every item.
- Both gates cite ``state['app:language']`` as the authoritative language
  and make explicit that bundles stay English (the translation is only in
  the user-facing speech, never written back to state).
- ``language-rules.md`` drops "most recent message" as an absolute rule and
  routes to the persisted ``app:language``.
- The callable ``build_instruction_provider`` injects the minimal
  ``<conversation_language>`` block from ``state['app:language']`` so the
  root sees the persisted language deterministically (``app:language`` is
  set by ``stage_sow`` before each review — no detection callback).

Assertions are phrasing-tolerant so cosmetic edits don't break the suite.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

# build_instruction_provider is now mandatory (the root agent wires it as
# its instruction). Import at module top so a missing symbol surfaces as a
# hard failure, not a silent skip.
from app.prompts import build_instruction_provider

_APP = Path(__file__).resolve().parents[4] / 'app'
_ROOT_PROMPT = _APP / 'prompts' / 'root_prompt.md'
_LANGUAGE_RULES = (
    _APP / 'skills' / 'sow-shared' / 'references' / 'language-rules.md'
)


def _between(text: str, start: str, end: str) -> str:
    """Return the body of an XML-ish block, asserting both ends are present."""
    s = text.find(start)
    e = text.find(end)
    assert 0 <= s < e, f'block {start!r}..{end!r} not found in order.'
    return text[s + len(start) : e]


@pytest.fixture(scope='module')
def root_prompt() -> str:
    return _ROOT_PROMPT.read_text(encoding='utf-8')


@pytest.fixture(scope='module')
def language_rules() -> str:
    return _LANGUAGE_RULES.read_text(encoding='utf-8')


# ---------------------------------------------------------------------------
# Root prompt: anchor moves to state['app:language']
# ---------------------------------------------------------------------------


def test_communication_rules_anchor_on_app_language(root_prompt: str) -> None:
    """The conversation-language rule must reference state['app:language']
    as authoritative — the old 'first message' rule is too fragile and was
    in direct conflict with language-rules.md's 'most recent message'."""
    block = _between(root_prompt, '<communication_rules>', '</communication_rules>')
    assert "app:language" in block, (
        '<communication_rules> must reference state[app:language] as the '
        'authoritative source.'
    )
    # The absolute "first message" rule is gone — replaced by a sticky
    # anchor that reads state.
    assert not re.search(
        r'detect\s+the\s+language\s+from\s+the\s+user.s\s+first\s+message',
        block,
        re.IGNORECASE,
    ), (
        '<communication_rules> must not use "first message" as the absolute '
        'rule anymore (fragile on short follow-ups).'
    )


def test_communication_rules_protects_against_short_replies(
    root_prompt: str,
) -> None:
    block = _between(root_prompt, '<communication_rules>', '</communication_rules>')
    lowered = block.lower()
    # Either explicit list ("ok", "sim", etc.) or a generic
    # "short continuation replies" wording — both acceptable.
    assert (
        'short continuation' in lowered
        or 'short replies' in lowered
        or ('"ok"' in lowered and ('"sim"' in lowered or '"approved"' in lowered))
    ), (
        '<communication_rules> must explicitly state that short continuation '
        'replies do not change the conversation language.'
    )


def test_communication_rules_allow_explicit_switch(root_prompt: str) -> None:
    block = _between(root_prompt, '<communication_rules>', '</communication_rules>')
    assert re.search(
        r'explicit(ly)?\s+ask|explicit\s+request|explicitly\s+(asks|requests)',
        block,
        re.IGNORECASE,
    ), (
        'The language rule must keep room for an explicit user request to '
        'switch languages.'
    )


# ---------------------------------------------------------------------------
# Review gates: translate labels AND full content; bundles stay English;
# state is not mutated by translation.
# ---------------------------------------------------------------------------


def _both_review_gates(root_prompt: str) -> list[str]:
    return [
        _between(root_prompt, '<content_review_gate>', '</content_review_gate>'),
        _between(
            root_prompt,
            '<architecture_review_gate>',
            '</architecture_review_gate>',
        ),
    ]


def test_review_gates_cite_app_language_as_binding(root_prompt: str) -> None:
    for block in _both_review_gates(root_prompt):
        assert 'app:language' in block, (
            'Each review gate must reference state[app:language] as the '
            'binding language.'
        )


def test_review_gates_require_translating_full_item_content(
    root_prompt: str,
) -> None:
    """The whole point of this fix: not just labels — the full text of each
    item must be rendered in the conversation language."""
    for block in _both_review_gates(root_prompt):
        lowered = block.lower()
        # Tolerant to markdown emphasis ("**the full text**") and small
        # phrasing variations: anchor on "labels … and … (text|content)"
        # with a bounded non-sentence gap so we don't false-positive across
        # unrelated paragraphs.
        assert re.search(
            r'labels?\b[^.\n]{0,10}\band\b[^.\n]{0,60}?\b(text|content)\b',
            lowered,
        ), (
            'Each review gate must instruct translating both labels AND the '
            'full content of every item.'
        )


def test_review_gates_make_bundles_english_explicit(root_prompt: str) -> None:
    for block in _both_review_gates(root_prompt):
        lowered = block.lower()
        assert (
            'bundles' in lowered or 'state[' in lowered
        ) and 'english' in lowered, (
            'Each review gate must state that bundles in state are English '
            'by design (for the .docx).'
        )


def test_review_gates_forbid_writing_translation_back_to_state(
    root_prompt: str,
) -> None:
    for block in _both_review_gates(root_prompt):
        lowered = block.lower()
        assert re.search(
            r'(do\s+not|never)\s+(write|alter|mutate|persist|store).*state',
            lowered,
        ) or 'translation is only' in lowered, (
            'Each review gate must make explicit that the translation is '
            'only in the user-facing speech; state is not mutated.'
        )


def test_review_gates_preserve_stable_ids(root_prompt: str) -> None:
    """FR-NN / NFR-NN / WS-NN must survive translation."""
    for block in _both_review_gates(root_prompt):
        assert re.search(r'preserve.*ids|FR-NN|NFR-NN|WS-NN', block, re.IGNORECASE), (
            'Each review gate must instruct preserving stable ids '
            '(FR-NN/NFR-NN/WS-NN) across translation.'
        )


def test_stage_sow_language_arg_is_conversation_not_document(
    root_prompt: str,
) -> None:
    """The `language` arg passed to stage_sow persists to app:language and
    drives the reviews. The prompt must make explicit it is the CONVERSATION
    language the user writes in — NOT the (English) language of the SOW
    content, the staged bundles, or tool outputs. Passing the document
    language here is exactly what poisons app:language and flips the reviews
    to English."""
    block = _between(root_prompt, '<sow_validation>', '</sow_validation>')
    lowered = block.lower()
    assert 'conversation' in lowered and 'language' in lowered, (
        'The stage_sow instruction must tie the language argument to the '
        'conversation language.'
    )
    # Explicit contrast against the document/bundle/tool-output language.
    assert re.search(
        r'not\b[^.\n]{0,80}\b(bundle|tool output|sow content|document)',
        lowered,
    ), (
        'The stage_sow instruction must warn that the language argument is '
        'NOT the language of the SOW content / bundles / tool outputs.'
    )


# ---------------------------------------------------------------------------
# language-rules.md must align with the root, not compete with it.
# ---------------------------------------------------------------------------


def test_language_rules_drop_most_recent_as_absolute(language_rules: str) -> None:
    """The old 'EXCLUSIVELY by the user's most recent message' rule was the
    other half of the conflict. It is fragile on short replies and must no
    longer be the absolute anchor."""
    assert not re.search(
        r'exclusively\s+by\s+the\s+user.s\s+most\s+recent\s+message',
        language_rules,
        re.IGNORECASE,
    ), (
        "language-rules.md must not declare 'most recent message' as the "
        'absolute anchor — the authoritative source is state[app:language].'
    )


def test_language_rules_anchor_on_persisted_language(language_rules: str) -> None:
    assert 'app:language' in language_rules, (
        'language-rules.md must reference state[app:language] as the '
        'persisted authoritative language.'
    )


def test_language_rules_protect_against_short_replies(
    language_rules: str,
) -> None:
    lowered = language_rules.lower()
    assert (
        'short continuation' in lowered
        or 'short replies' in lowered
    ), (
        'language-rules.md must explicitly warn that short continuation '
        'replies do not change the persisted language.'
    )


def test_language_rules_keep_two_surfaces(language_rules: str) -> None:
    lowered = language_rules.lower()
    assert 'document surface' in lowered and 'conversation surface' in lowered, (
        'language-rules.md must keep the two-surfaces contract intact.'
    )


def test_language_rules_keep_review_translation_rule(
    language_rules: str,
) -> None:
    lowered = language_rules.lower()
    assert 'translated to' in lowered and 'review' in lowered, (
        'language-rules.md must keep the rule that section content is '
        'translated to the conversation language at review time.'
    )


# ---------------------------------------------------------------------------
# Root agent wires the callable instruction provider (state-aware prompt).
# ---------------------------------------------------------------------------


class TestInstructionProvider:
    def _ctx(self, state: dict | None = None):
        from unittest.mock import MagicMock

        ctx = MagicMock(name='ReadonlyContext')
        ctx.state = state or {}
        return ctx

    def test_injects_conversation_language_when_state_set(self):
        provider = build_instruction_provider(company_name='TestCo')
        out = provider(self._ctx({'app:language': 'pt-BR'}))
        assert '<conversation_language>pt-BR</conversation_language>' in out

    def test_uses_auto_when_state_missing(self):
        provider = build_instruction_provider(company_name='TestCo')
        out = provider(self._ctx({}))
        assert '<conversation_language>auto</conversation_language>' in out

    def test_company_name_and_date_still_substituted(self):
        provider = build_instruction_provider(company_name='TestCo')
        out = provider(self._ctx({}))
        # The base instruction's static placeholders are still filled.
        assert 'TestCo' in out
        # Date placeholder is gone (substituted by the date string).
        assert '{todays_date}' not in out
        assert '{company_name}' not in out

    def test_does_not_inject_bundles_or_large_state(self):
        """The injected block must remain MINIMAL — only the language. We
        don't want SOW bundle values, metadata, findings, or quality-loop
        results leaking into the system instruction on every turn.

        We use a unique sentinel as the value of every non-language state
        key. The base prompt naturally contains field NAMES like
        ``partner_name`` (the prompt references state keys by name); the
        guarantee we're asserting is that bundle VALUES are not dumped."""
        provider = build_instruction_provider(company_name='TestCo')
        sentinel = 'ZZZ_INJECTION_SENTINEL_ZZZ'
        big_payload = 'x' * 5000
        big_state = {
            'app:language': 'pt-BR',
            'app:sow:current': {'partner_name': sentinel, 'big': big_payload},
            'app:sow:requirements': {
                'functional_requirements': [{'description': sentinel}]
            },
            'app:sow:metadata': {'project_title': sentinel + '-meta'},
            'app:sow:quality_loop_result': {'status': sentinel + '-status'},
        }
        out = provider(self._ctx(big_state))
        assert 'pt-BR' in out
        assert sentinel not in out, (
            'instruction_provider must not inject bundle values into the '
            'prompt; only the minimal session-state block is allowed.'
        )
        assert big_payload not in out, (
            'instruction_provider must not inject large state payloads.'
        )
