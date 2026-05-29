"""Regression guards for the Phase 3 single-pass document contract.

The SOW flow used to invoke ``sow_quality_loop`` three times: once for the
Content Review, once for the Architecture Review, and a third defensive
pass in Phase 3 right before ``generate_sow_document``. The third pass was
redundant — no section bundle changes after the Architecture Review, and
``generate_sow_document`` is self-guarding (it rejects non-``full`` stages
and unapproved runs, and re-runs the deterministic quality gates plus the
structural validation before rendering). Phase 3 is now a single
deterministic re-assembly followed by document generation, with no quality
loop and no post-approval Revision Note.

These tests pin that contract on the ``<phase_3_document>`` block.
Assertions are phrasing-tolerant (substring / regex) so cosmetic edits do
not produce false negatives.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT_PROMPT = (
    Path(__file__).resolve().parents[4] / 'app' / 'prompts' / 'root_prompt.md'
)


def _between(text: str, start: str, end: str) -> str:
    """Return the body of an XML-ish block, asserting it is present."""
    s = text.find(start)
    e = text.find(end)
    assert 0 <= s < e, f'block {start!r}..{end!r} not found in order.'
    return text[s + len(start) : e]


@pytest.fixture(scope='module')
def phase_3_block() -> str:
    root_prompt = _ROOT_PROMPT.read_text(encoding='utf-8')
    # The opening tag also appears earlier as an inline `<phase_3_document>`
    # cross-reference; the block *definition* is the tag anchored at the
    # start of its own line (followed by a newline).
    return _between(
        root_prompt, '<phase_3_document>\n', '</phase_3_document>'
    )


def test_phase_3_does_not_invoke_quality_loop(phase_3_block: str) -> None:
    """The redundant third quality-loop pass is gone — Phase 3 must not
    instruct *calling* sow_quality_loop.

    Matches the imperative invocation form used by Steps 3 and 5
    (``Call `sow_quality_loop` ``) so a clarifying negation such as "it
    does not call `sow_quality_loop`" does not produce a false positive.
    """
    assert not re.search(r'Call\s+`sow_quality_loop`', phase_3_block), (
        'Phase 3 must not call sow_quality_loop; the Content and '
        'Architecture Reviews already validated the approved SOW.'
    )


def test_phase_3_still_generates_the_document(phase_3_block: str) -> None:
    assert 'generate_sow_document' in phase_3_block, (
        'Phase 3 must still call generate_sow_document.'
    )


def test_phase_3_keeps_deterministic_reassembly(phase_3_block: str) -> None:
    """The defensive stage_sow(stage="full") stays — but framed as a
    deterministic re-assembly, not a new validation."""
    assert re.search(r'stage_sow\(stage="full"', phase_3_block), (
        'Phase 3 must keep the stage_sow(stage="full") re-assembly so the '
        'staged payload is complete before document generation.'
    )
    assert re.search(r'determinist', phase_3_block, re.IGNORECASE), (
        'Phase 3 must describe the stage_sow call as a deterministic '
        're-assembly, not a semantic validation.'
    )


def test_phase_3_has_no_revision_note(phase_3_block: str) -> None:
    """No revision happens after the final review gate, so there is no
    post-approval Revision Note to present."""
    assert 'Revision Note' not in phase_3_block, (
        'Phase 3 must not present a Revision Note: nothing is revised after '
        'the Architecture Review, so there is no post-approval change to '
        'disclose.'
    )
