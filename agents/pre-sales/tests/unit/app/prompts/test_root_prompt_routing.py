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


def test_prompt_treats_intake_state_key_as_handoff(prompt_text: str) -> None:
    """The contract with sow-guided-intake is the persisted state key.

    The root must reference ``state['app:sow:intake_summary']`` so the
    LLM consumes the persisted summary instead of parsing chat text."""
    assert "app:sow:intake_summary" in prompt_text, (
        "root_prompt.md must reference state['app:sow:intake_summary'] so "
        'the LLM consumes the persisted summary after Path A.'
    )


def test_prompt_references_save_intake_tool(prompt_text: str) -> None:
    """The skill persists via ``save_sow_intake_summary``; the root must
    know that is the signal the interview finished."""
    assert 'save_sow_intake_summary' in prompt_text, (
        'root_prompt.md must reference save_sow_intake_summary as the '
        'Path A persistence step.'
    )


def test_prompt_has_marker_contract_block(prompt_text: str) -> None:
    """The two markers carry distinct downstream behavior; the prompt
    must publish that contract so the LLM dispatches correctly."""
    assert '<intake_summary_contract>' in prompt_text
    assert '[TO BE DEFINED]' in prompt_text
    assert '(inferred)' in prompt_text


def test_prompt_forbids_treating_tbd_as_infer(prompt_text: str) -> None:
    """Core failure mode: do NOT treat [TO BE DEFINED] as 'please infer'.
    The prompt must say so explicitly."""
    assert re.search(
        r"do\s+NOT\s+treat\s+[`'\"]*\[TO BE DEFINED\][`'\"]*\s+as\s+an\s+instruction\s+to\s+infer",
        prompt_text,
        re.IGNORECASE,
    ), (
        'root_prompt.md must explicitly forbid treating [TO BE DEFINED] '
        'as an inference instruction.'
    )


def test_prompt_forbids_printing_summary(prompt_text: str) -> None:
    """UX rule: the persisted summary is not echoed to the user."""
    assert re.search(
        r'do\s+NOT\s+print\s+the\s+(persisted\s+)?summary',
        prompt_text,
        re.IGNORECASE,
    ), (
        'root_prompt.md must forbid printing the persisted intake summary '
        'to the user (UX rule).'
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


# ---------------------------------------------------------------------------
# Path B — document readiness (Step 0b)
# ---------------------------------------------------------------------------


def test_prompt_loads_document_readiness_via_load_skill(prompt_text: str) -> None:
    """Path B must run the readiness pass through ``load_skill`` so the
    AutoScopedSkillToolset can prune it afterwards — a free-form gap
    check in the root's own turn would skip the toolset entirely."""
    assert re.search(
        r"load_skill\(\s*['\"]sow-document-readiness['\"]\s*\)", prompt_text
    ), (
        "root_prompt.md must instruct the model to call "
        "load_skill('sow-document-readiness') for the Path B readiness "
        "pass (Step 0b)."
    )


def test_prompt_runs_readiness_after_load_artifacts(prompt_text: str) -> None:
    """The readiness skill must run AFTER documents are loaded but BEFORE
    metadata is persisted, so it can see the documents and shape the
    answers that feed save_sow_metadata.

    Anchored on the unique Step 0b ``load_skill`` call and the unique
    "Persist metadata" (Step 1) heading rather than bare token positions,
    because the skill name and the tool name also appear in the prose of
    earlier sections (capabilities / user-facing contract)."""
    readiness_call_pos = prompt_text.find("load_skill('sow-document-readiness')")
    assert readiness_call_pos != -1, (
        "root_prompt.md must contain the Step 0b "
        "load_skill('sow-document-readiness') call."
    )
    # load_artifacts (Step 0) precedes the readiness call.
    assert 0 <= prompt_text.find('load_artifacts') < readiness_call_pos, (
        'root_prompt.md must call load_artifacts before the '
        'sow-document-readiness pass (Step 0 before Step 0b).'
    )
    # The readiness call precedes Step 1 metadata persistence.
    assert readiness_call_pos < prompt_text.find('Persist metadata'), (
        'root_prompt.md must run the sow-document-readiness pass (Step 0b) '
        'before Step 1 — Persist metadata.'
    )


def test_prompt_declares_readiness_soft_gate(prompt_text: str) -> None:
    """The readiness step must be a soft gate: it must not hard-block
    generation when gaps remain unresolved."""
    assert re.search(r'soft\s*gate', prompt_text, re.IGNORECASE), (
        'root_prompt.md must declare Step 0b as a soft gate (does not '
        'block generation on unresolved gaps).'
    )


def test_prompt_forbids_intake_summary_in_path_b(prompt_text: str) -> None:
    """Path B must not borrow the Path A persistence tool — that would
    blur the two paths. The Step 0b instruction fences it off."""
    assert re.search(
        r'do\s+NOT\s+call\s+`?save_sow_intake_summary`?\s+in\s+Path\s*B',
        prompt_text,
        re.IGNORECASE,
    ), (
        'root_prompt.md must explicitly forbid calling '
        'save_sow_intake_summary in Path B (it is Path A only).'
    )


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


def test_intake_persist_precedes_save_metadata_in_prompt(prompt_text: str) -> None:
    """The intake summary must be persisted BEFORE save_sow_metadata is
    called — otherwise the root would call the metadata tool with no
    upstream facts."""
    intake_pos = prompt_text.find('save_sow_intake_summary')
    metadata_pos = prompt_text.find('save_sow_metadata')
    assert 0 <= intake_pos < metadata_pos, (
        'root_prompt.md must place the save_sow_intake_summary hand-off '
        'before save_sow_metadata so the metadata call has upstream facts '
        'to extract.'
    )
