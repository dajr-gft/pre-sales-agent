"""Unit tests for root_prompt.md routing between Path A and Path B.

The root prompt is a static markdown file; its routing rules are the
single place that wires the guided-intake skill into the SOW protocol.
These tests pin the routing contract so the file cannot quietly drift
back to "documents-only" or, conversely, drop the regression-protecting
Path B language.

The assertions are deliberately phrasing-tolerant (substring / regex
checks) so cosmetic edits to the prompt do not produce false negatives.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_PROMPT_PATH = (
    Path(__file__).resolve().parents[4] / 'app' / 'prompts' / 'root_prompt.md'
)


@pytest.fixture(scope='module')
def prompt_text() -> str:
    return _PROMPT_PATH.read_text(encoding='utf-8')


# ---------------------------------------------------------------------------
# Path A — guided intake
# ---------------------------------------------------------------------------


def test_prompt_mentions_guided_intake_skill(prompt_text: str) -> None:
    """The root must name ``sow-guided-intake`` so the LLM can route to
    it; mentioning Path A in the abstract is not enough."""
    assert 'sow-guided-intake' in prompt_text, (
        'root_prompt.md must reference the sow-guided-intake skill so the '
        'LLM knows what to call for the guided intake path.'
    )


def test_prompt_declares_two_paths(prompt_text: str) -> None:
    """Both labels must appear so the LLM knows the choice exists."""
    assert re.search(r'Path\s*A', prompt_text)
    assert re.search(r'Path\s*B', prompt_text)


def test_prompt_loads_guided_intake_via_load_skill(prompt_text: str) -> None:
    """The skill must be invoked via ``load_skill`` — the AutoScopedSkill
    pruning depends on that tool call (free-form "ask the user the same
    questions" would skip the toolset entirely)."""
    assert re.search(
        r"load_skill\(\s*['\"]sow-guided-intake['\"]\s*\)", prompt_text
    ), (
        "root_prompt.md must instruct the model to call "
        "load_skill('sow-guided-intake') for Path A; otherwise the toolset "
        "cannot prune the skill afterwards."
    )


def test_prompt_treats_intake_summary_as_handoff(prompt_text: str) -> None:
    """The contract with sow-guided-intake is the ``<intake_summary>``
    block. The root must mention it explicitly so the LLM knows what to
    parse after the interview returns."""
    assert '<intake_summary>' in prompt_text, (
        'root_prompt.md must mention the <intake_summary> handoff so the '
        'LLM knows what artifact to consume after Path A.'
    )


def test_prompt_forbids_root_running_interview_itself(prompt_text: str) -> None:
    """Plan section 6: "The root not implement the interview by
    itself." Pin that rule so the LLM cannot quietly bypass the skill."""
    assert re.search(
        r'do\s+not\s+run\s+the\s+interview\s+yourself|never\s+improvise\s+the\s+guided\s+interview',
        prompt_text,
        re.IGNORECASE,
    ), (
        'root_prompt.md must explicitly forbid the root from running the '
        'guided interview itself — it must always load sow-guided-intake.'
    )


# ---------------------------------------------------------------------------
# Path B — documents (regression)
# ---------------------------------------------------------------------------


def test_prompt_preserves_load_artifacts_step(prompt_text: str) -> None:
    """Path B regression: the root still calls ``load_artifacts`` for
    documents. The plan section 9 explicitly forbids changes to Path B."""
    assert 'load_artifacts' in prompt_text, (
        'root_prompt.md must still call load_artifacts for Path B '
        '(documents-attached flow). Path B regression check.'
    )


def test_prompt_still_extracts_metadata_from_documents(prompt_text: str) -> None:
    """``save_sow_metadata`` is the entry point of the section flow for
    both paths. The instruction to call it after document extraction
    must remain."""
    assert 'save_sow_metadata' in prompt_text


def test_prompt_does_not_force_documents_only(prompt_text: str) -> None:
    """The pre-change prompt said "if no documents, ask for them and do
    NOT run a guided interview". That sentence must be gone now — Path A
    is supported."""
    assert not re.search(
        r'do\s+NOT\s+run\s+a\s+guided\s+interview', prompt_text
    ), (
        'root_prompt.md still carries the documents-only fallback rule. '
        'Path A is supported now; the "do NOT run a guided interview" '
        'clause must be removed.'
    )


# ---------------------------------------------------------------------------
# Ordering — Path A intake summary feeds the section flow
# ---------------------------------------------------------------------------


def test_intake_summary_precedes_save_metadata_in_prompt(prompt_text: str) -> None:
    """The summary must be produced BEFORE save_sow_metadata is called —
    otherwise the root would call the metadata tool with no upstream
    facts."""
    intake_pos = prompt_text.find('<intake_summary>')
    metadata_pos = prompt_text.find('save_sow_metadata')
    assert 0 <= intake_pos < metadata_pos, (
        'root_prompt.md must place the <intake_summary> hand-off before '
        'save_sow_metadata so the metadata call has upstream facts to '
        'extract.'
    )
