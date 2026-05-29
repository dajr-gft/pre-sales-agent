"""Regression guard for the full-stage content-conflict escalation.

The full-stage (Architecture Review) validation does not re-validate or
auto-rewrite the content sections the user already approved at the
Content Review. The one exception is a genuine cross-section conflict
whose only correct fix is to change approved content (architecture is
right, content is wrong): instead of silently dropping it, the loop
escalates as needs_human_review and the orchestrator tells the user that
resolving it requires reopening the Content Review.

This test pins that the ``<sow_validation>`` block carries that
instruction. Phrasing-tolerant (substring checks).
"""
from __future__ import annotations

from pathlib import Path

import pytest

_ROOT_PROMPT = (
    Path(__file__).resolve().parents[4] / 'app' / 'prompts' / 'root_prompt.md'
)


def _between(text: str, start: str, end: str) -> str:
    s = text.find(start)
    e = text.find(end)
    assert 0 <= s < e, f'block {start!r}..{end!r} not found in order.'
    return text[s + len(start) : e]


@pytest.fixture(scope='module')
def sow_validation_block() -> str:
    root_prompt = _ROOT_PROMPT.read_text(encoding='utf-8')
    return _between(root_prompt, '<sow_validation>', '</sow_validation>')


def test_needs_human_review_handles_content_conflict(
    sow_validation_block: str,
) -> None:
    """The needs_human_review policy must cover the case where a full-stage
    finding traces back to approved content and require reopening the
    Content Review before any content edit."""
    block = sow_validation_block
    assert 'reopening' in block.lower() or 'reopen' in block.lower(), (
        'The full-stage escalation must tell the user that the fix means '
        'reopening the already-approved Content Review.'
    )
    assert 'Content Review' in block
    # The flow back into content must re-stage the content stage.
    assert 'stage_sow(stage="content")' in block, (
        'On approval, the escalation must route back through '
        'stage_sow(stage="content") to fix the affected content section.'
    )
