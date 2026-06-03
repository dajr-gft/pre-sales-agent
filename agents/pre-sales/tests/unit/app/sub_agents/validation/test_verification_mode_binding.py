"""Unit — the skill instruction provider binds verification mode (PR-3).

The provider built by ``_make_instruction_provider`` reads
``STATE_ROUND_MODE`` / ``STATE_CHANGED_SECTIONS`` (written by the
QualityLoopAgent before each critic run) and appends the
``_VERIFICATION_MODE_GUIDE`` block ONLY in verification mode. Discovery
(round 1) and a missing mode key behave exactly as before — the base
rubric and the runtime payload, no verification block.
"""

from __future__ import annotations

from app.sub_agents.validation.schema import (
    STATE_CHANGED_SECTIONS,
    STATE_ROUND_MODE,
    STATE_SOW,
)
from app.sub_agents.validation.semantic_skills import (
    _RESOLUTION_MODE_GUIDE,
    _make_instruction_provider,
)

_SKILL_BODY = '# CONTRADICTIONS SKILL BODY\n'
_HEADING = '# Verification mode'


class _FakeCtx:
    """Minimal ReadonlyContext stand-in: the provider only reads
    ``ctx.state`` and calls ``.get`` on it."""

    def __init__(self, state: dict):
        self.state = state


def _provider():
    return _make_instruction_provider('contradictions', _SKILL_BODY)


def _state(mode=None, changed=None, sow=None) -> dict:
    state: dict = {}
    if mode is not None:
        state[STATE_ROUND_MODE] = mode
    if changed is not None:
        state[STATE_CHANGED_SECTIONS] = changed
    state[STATE_SOW] = sow if sow is not None else {'project_title': 'P'}
    return state


def test_verification_mode_injects_the_block():
    out = _provider()(_FakeCtx(_state(mode='verification', changed=[])))
    assert _HEADING in out


def test_discovery_mode_omits_the_block():
    out = _provider()(_FakeCtx(_state(mode='discovery')))
    assert _HEADING not in out


def test_absent_mode_omits_the_block():
    # Round-mode key never written (legacy session / first ever run) ->
    # behaves identically to discovery: no verification block.
    out = _provider()(_FakeCtx(_state()))
    assert _HEADING not in out


def test_changed_sections_are_rendered_into_the_block():
    out = _provider()(_FakeCtx(_state(
        mode='verification',
        changed=['requirements', 'scope_boundaries'],
    )))
    assert _HEADING in out
    assert 'requirements, scope_boundaries' in out


def test_empty_changed_sections_render_a_none_placeholder():
    out = _provider()(_FakeCtx(_state(mode='verification', changed=[])))
    assert _HEADING in out
    assert '(none recorded' in out


def test_base_rubric_present_in_both_modes():
    # The resolution-mode rubric and the skill body are unconditional;
    # verification mode is additive, never a replacement.
    discovery = _provider()(_FakeCtx(_state(mode='discovery')))
    verification = _provider()(_FakeCtx(_state(mode='verification', changed=[])))
    for out in (discovery, verification):
        assert _SKILL_BODY in out
        assert _RESOLUTION_MODE_GUIDE in out


def test_block_sits_after_rubric_and_before_runtime_payload():
    out = _provider()(_FakeCtx(_state(mode='verification', changed=['narrative'])))
    rubric_at = out.index(_RESOLUTION_MODE_GUIDE)
    block_at = out.index(_HEADING)
    payload_at = out.index('# Runtime payload')
    assert rubric_at < block_at < payload_at


def test_verification_with_absent_changed_sections_renders_none():
    # Defensive: verification mode set but the changed-sections key never
    # written (e.g. the cache short-circuit path) must not crash prompt
    # assembly — it falls back to the "none recorded" label.
    out = _provider()(_FakeCtx(_state(mode='verification')))
    assert _HEADING in out
    assert '(none recorded' in out
