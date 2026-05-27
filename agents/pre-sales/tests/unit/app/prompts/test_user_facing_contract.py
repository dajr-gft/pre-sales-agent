"""Unit tests for the user-facing conversation contract.

After Approach 2 the root agent narrated its internal pipeline to the
user ("Vou consultar as diretrizes…", "Os requisitos foram
estruturados…", "Estou corrigindo a formatação…"). The fix is a WRITTEN
UX contract, not a word blacklist: the root prompt gains a
``<user_facing_contract>`` block, ``<output_discipline>`` stops demanding
visible text every turn, and the cross-cutting echo lives once in
``sow-shared/references/language-rules.md`` so every section skill
inherits it without per-skill duplication.

These tests pin that the contract EXISTS (positive assertions) and that
the two specific narration-inducing instructions are gone (targeted
regression guards). Assertions are phrasing-tolerant (substring / regex)
so cosmetic edits do not produce false negatives.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_APP = Path(__file__).resolve().parents[4] / 'app'
_ROOT_PROMPT = _APP / 'prompts' / 'root_prompt.md'
_LANGUAGE_RULES = (
    _APP / 'skills' / 'sow-shared' / 'references' / 'language-rules.md'
)
_INTAKE_SKILL = _APP / 'skills' / 'sow-guided-intake' / 'SKILL.md'


def _between(text: str, start: str, end: str) -> str:
    """Return the body of an XML-ish block, asserting it is present."""
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


@pytest.fixture(scope='module')
def intake_skill() -> str:
    return _INTAKE_SKILL.read_text(encoding='utf-8')


# ---------------------------------------------------------------------------
# Root prompt — the contract exists (positive, not a blacklist)
# ---------------------------------------------------------------------------


def test_root_has_user_facing_contract_block(root_prompt: str) -> None:
    """The single place that defines when the agent speaks vs. stays
    silent. The old flow had this as sow-discovery's <user_visible_contract>;
    Approach 2 dropped it and the regression followed."""
    assert (
        '<user_facing_contract>' in root_prompt
        and '</user_facing_contract>' in root_prompt
    ), 'root_prompt.md must declare a <user_facing_contract> block.'


def test_contract_lists_speaking_touchpoints(root_prompt: str) -> None:
    """The contract is positive (WHEN to speak), not just a list of banned
    words — the review gates are the load-bearing touch-points."""
    block = _between(
        root_prompt, '<user_facing_contract>', '</user_facing_contract>'
    )
    assert 'Content Review' in block, 'contract must name the Content Review touch-point.'
    assert 'Architecture Review' in block, (
        'contract must name the Architecture Review touch-point.'
    )
    assert re.search(r'gate', block, re.IGNORECASE)


def test_contract_forbids_narrating_internal_work(root_prompt: str) -> None:
    block = _between(
        root_prompt, '<user_facing_contract>', '</user_facing_contract>'
    )
    assert re.search(
        r'never\s+(echoed|narrate)|internal reasoning, never|never\s+tell\s+the\s+user',
        block,
        re.IGNORECASE,
    ), 'contract must state that internal work is never narrated to the user.'


def test_contract_has_consultant_translation_rule(root_prompt: str) -> None:
    """The durable rule: translate an internal finding into a project
    question. This is what makes the fix more than a word filter."""
    block = _between(
        root_prompt, '<user_facing_contract>', '</user_facing_contract>'
    )
    assert re.search(r'\bwrong\b', block, re.IGNORECASE) and re.search(
        r'\bright\b', block, re.IGNORECASE
    ), 'contract must show the wrong/right consultant-translation example.'


# ---------------------------------------------------------------------------
# Root prompt — narration-inducing instructions removed (regression guards)
# ---------------------------------------------------------------------------


def test_root_no_longer_instructs_acknowledge_before_tool_call(
    root_prompt: str,
) -> None:
    """Primary regression: "acknowledge what you are about to do … before
    the first tool call" seeded the "Vou consultar…" narration."""
    assert not re.search(
        r'acknowledge\s+what\s+you\s+are\s+about\s+to\s+do',
        root_prompt,
        re.IGNORECASE,
    ), (
        'root_prompt.md must NOT instruct the agent to acknowledge what it '
        'is about to do before tool calls — that seeds pipeline narration.'
    )


def test_output_discipline_allows_silent_tool_turns(root_prompt: str) -> None:
    """The old <output_discipline> said "immediately produce the visible
    output the current phase requires", which the model read as "narrate
    every step". The fix marks internal-step turns as silent."""
    block = _between(root_prompt, '<output_discipline>', '</output_discipline>')
    assert 'silent' in block.lower(), (
        'output_discipline must state that internal-step turns are silent '
        '(tool calls, no user-facing text).'
    )


# ---------------------------------------------------------------------------
# Cross-cutting echo — lives once in the shared reference
# ---------------------------------------------------------------------------


def test_language_rules_carry_user_facing_surface_rule(
    language_rules: str,
) -> None:
    """The cross-cutting rule lives once in language-rules.md (already
    loaded by every section skill), so it is inherited without copying a
    big contract into each SKILL.md."""
    lowered = language_rules.lower()
    assert 'internal work' in lowered or 'internal reasoning' in lowered, (
        'language-rules.md must carry the cross-cutting user-facing-surface '
        'rule so section skills inherit it without per-skill duplication.'
    )
    assert re.search(r'never\s+narrat|never\s+echo|not\s+narrat', lowered), (
        'language-rules.md must state internal work is not narrated.'
    )


# ---------------------------------------------------------------------------
# Guided intake — post-handoff confirmation no longer narrates the pipeline
# ---------------------------------------------------------------------------


def test_intake_handoff_does_not_narrate_generation(intake_skill: str) -> None:
    """The old example modeled narration: "vou começar a gerar a SOW
    agora". The confirmation must be consultive, not a pipeline preview."""
    assert 'vou começar a gerar' not in intake_skill.lower(), (
        'sow-guided-intake/SKILL.md must not model pipeline narration in '
        'its post-handoff confirmation example.'
    )


# ---------------------------------------------------------------------------
# Reviewer round 2 — escalation wording, double confirmation, Manifest debt
# ---------------------------------------------------------------------------


def test_validation_escalation_translates_findings(root_prompt: str) -> None:
    """needs_human_review / exhausted were the leak path: the agent relayed
    final_report.summary / severities / "validator" wording. The fix is a
    cross-status rule to translate findings into project questions."""
    block = _between(root_prompt, '<sow_validation>', '</sow_validation>')
    lowered = block.lower()
    assert 'consultant-style' in lowered, (
        'sow_validation must instruct translating findings into '
        'consultant-style questions/decisions about the project.'
    )
    assert re.search(r'never\s+relay|do\s+not\s+relay', lowered), (
        'sow_validation must forbid relaying final_report.summary / '
        'next_action / severities / validator wording verbatim.'
    )


def test_path_a_root_does_not_double_confirm(root_prompt: str) -> None:
    """The intake skill already sends the hand-off confirmation; the root
    must continue silently instead of adding a second one (Path A)."""
    assert re.search(
        r'continue\s+silently\s+to\s+step\s+1|do\s+NOT\s+add\s+another',
        root_prompt,
        re.IGNORECASE,
    ), (
        "root_prompt.md Step 0' must avoid a double confirmation on Path A "
        '(continue silently after the intake hand-off sentence).'
    )


def test_language_rules_have_no_manifest_residue(language_rules: str) -> None:
    """Approach 2 killed the Extraction Manifest; the cross-cutting
    language reference must not still anchor inference to "the Manifest"."""
    assert 'manifest' not in language_rules.lower(), (
        'language-rules.md still references the (removed) Manifest. Use '
        '"upstream project context / user-confirmed content" instead.'
    )


# ---------------------------------------------------------------------------
# Reviewer round 3 — failure paths must not leak internal status / mechanics
# ---------------------------------------------------------------------------


def test_unexpected_status_does_not_expose_observed_status(
    root_prompt: str,
) -> None:
    """The unexpected_status path used to surface `observed_status` (an
    internal status name) to the user."""
    block = _between(root_prompt, '<sow_validation>', '</sow_validation>')
    assert re.search(
        r'do\s+not\s+expose\s+`?observed_status', block, re.IGNORECASE
    ), (
        'sow_validation unexpected_status path must NOT surface '
        'observed_status verbatim to the user.'
    )


def test_no_progress_hides_internal_loop_mechanics(root_prompt: str) -> None:
    """no_progress used to tell the user the "automatic correction loop"
    "could not converge" and named the revision step — internal mechanics."""
    block = _between(root_prompt, '<sow_validation>', '</sow_validation>')
    lowered = block.lower()
    assert 'internal loop mechanics' in lowered, (
        'no_progress must instruct the agent to hide internal loop '
        'mechanics from the user.'
    )
    assert 'automatic correction loop' not in lowered, (
        'no_progress must not tell the user about the "automatic '
        'correction loop" — that is internal mechanics.'
    )
