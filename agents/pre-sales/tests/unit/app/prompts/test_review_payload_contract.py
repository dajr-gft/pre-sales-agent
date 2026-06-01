"""Regression guards for the review_payload rendering contract.

The quality loop can patch sections AFTER the root stages the SOW; the
root cannot read session state, so the loop now returns the corrected,
stage-specific content in the envelope's ``review_payload``. The root
must render the Content / Architecture Review from that field — not from
the earlier ``stage_sow`` return or its own (pre-repair) draft.

These tests pin that the prompt documents the envelope contract and
points both review gates at ``review_payload``. Substring-based and
phrasing-tolerant.
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
def root_prompt() -> str:
    return _ROOT_PROMPT.read_text(encoding='utf-8')


def test_sow_validation_documents_review_payload(root_prompt: str) -> None:
    block = _between(root_prompt, '<sow_validation>', '</sow_validation>')
    assert 'review_payload' in block, (
        'The <sow_validation> envelope contract must document review_payload.'
    )
    assert 'source of truth' in block.lower(), (
        'review_payload must be declared the source of truth for the review.'
    )


def test_content_gate_renders_from_review_payload(root_prompt: str) -> None:
    block = _between(
        root_prompt, '<content_review_gate>', '</content_review_gate>'
    )
    assert 'review_payload' in block, (
        'The Content Review gate must render from review_payload.'
    )
    # The gate must no longer instruct reading section state directly.
    assert "state['app:sow:<section>']" not in block, (
        'The Content Review gate must not tell the root to read '
        "state['app:sow:<section>'] — the root cannot read state."
    )


def test_architecture_gate_renders_from_review_payload(
    root_prompt: str,
) -> None:
    block = _between(
        root_prompt,
        '<architecture_review_gate>',
        '</architecture_review_gate>',
    )
    assert 'review_payload.architecture' in block
    assert 'review_payload.narrative' in block
    # No direct reads of the architecture/narrative state bundles.
    assert "state['app:sow:architecture']" not in block
    assert "state['app:sow:narrative']" not in block


def test_sow_validation_documents_human_review_items(root_prompt: str) -> None:
    block = _between(root_prompt, '<sow_validation>', '</sow_validation>')
    assert 'human_review_items' in block, (
        'The <sow_validation> envelope contract must document '
        'human_review_items for the needs_human_review path.'
    )


def test_needs_human_review_uses_items_not_state_findings(
    root_prompt: str,
) -> None:
    """The needs_human_review path must base its questions on the
    human_review_items envelope field, not on findings read from state."""
    block = _between(root_prompt, '<sow_validation>', '</sow_validation>')
    # The needs_human_review bullet must point at human_review_items.
    nhr = block[block.find('needs_human_review'):]
    assert 'human_review_items' in nhr
    # It must not instruct translating findings pulled from state.
    assert 'final_report.findings' not in nhr, (
        'needs_human_review must not claim the root can read '
        'final_report.findings from state.'
    )
    # The reopen-approved-content flow is keyed on the decision_type.
    assert 'reopen_approved_content' in block


def test_sow_validation_documents_unresolved_items(root_prompt: str) -> None:
    block = _between(root_prompt, '<sow_validation>', '</sow_validation>')
    assert 'unresolved_items' in block, (
        'The <sow_validation> envelope contract must document '
        'unresolved_items for the exhausted path.'
    )


def test_exhausted_uses_items_not_state_findings(root_prompt: str) -> None:
    """The exhausted path must base its explanation on the unresolved_items
    envelope field, not on findings read from state."""
    block = _between(root_prompt, '<sow_validation>', '</sow_validation>')
    exhausted = _between(
        root_prompt, 'status == "exhausted"', 'status == "no_progress"'
    )
    assert 'unresolved_items' in exhausted
    assert 'final_report.findings' not in block[block.find('status == "exhausted"'):], (
        'exhausted must not claim the root can read final_report.findings '
        'from state.'
    )


def test_exhausted_path_instructs_stop_before_acting(root_prompt: str) -> None:
    """exhausted must present options and halt — never autonomously
    regenerate, re-stage, and re-run the loop (the over-correction bug)."""
    exhausted = _between(
        root_prompt, 'status == "exhausted"', 'status == "no_progress"'
    )
    assert 'STOP' in exhausted, (
        'The exhausted path must tell the root to present options and STOP, '
        'not silently regenerate/re-stage/re-run the loop.'
    )
    # The act-only-on-user-choice guard must be present.
    assert 'on your own this turn' in exhausted
    # Re-staging is gated on a section being regenerated AND saved first —
    # never re-running on an unchanged payload.
    assert 'save that chosen section' in exhausted
    assert 'unchanged payload' in exhausted


def test_needs_human_review_instructs_stop_before_acting(
    root_prompt: str,
) -> None:
    """needs_human_review must also ask and halt this turn."""
    nhr = _between(
        root_prompt, 'status == "needs_human_review"', 'status == "exhausted"'
    )
    assert 'STOP' in nhr


def test_no_progress_instructs_stop_before_acting(root_prompt: str) -> None:
    """no_progress must present options and halt this turn — re-staging only
    after the chosen section is regenerated and saved."""
    block = _between(root_prompt, '<sow_validation>', '</sow_validation>')
    start = block.find('status == "no_progress"')
    end = block.find('status == "unexpected_status"', start)
    assert 0 <= start < end, 'no_progress bullet not found before unexpected.'
    no_progress = block[start:end]
    assert 'STOP' in no_progress
    assert 'on your own this turn' in no_progress
    assert 'save that chosen section' in no_progress
