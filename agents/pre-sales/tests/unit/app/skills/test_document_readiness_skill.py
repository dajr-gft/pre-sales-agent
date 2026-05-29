"""Unit tests for the sow-document-readiness skill (Path B).

The skill is purely declarative — a SKILL.md instruction body plus one
prose reference. It runs right after ``load_artifacts`` in Path B,
gives the user a short readiness summary, and asks objective questions
about critical gaps. It is conversational only: it writes no state and
calls no tool. The tests below pin the contract the root prompt and the
AutoScopedSkillToolset depend on:

  - The skill loads through ``load_skill_from_dir`` with the expected
    name and a non-empty instruction body.
  - The description self-identifies as the Path B document path (so the
    root routes to it instead of the guided-intake skill).
  - The single advertised reference is present and readable.
  - The skill is part of the root allowlist (``_ROOT_SKILL_NAMES``) so
    ``load_skill('sow-document-readiness')`` works end-to-end.
  - Neither the SKILL.md nor the reference mentions any legacy manifest
    token — this skill recovers the *useful* part of the old document
    flow (gap diagnosis + clarification) without resurrecting the killed
    Extraction Manifest / discovery pipeline.
  - The skill carries the soft-gate + no-state contract the plan pins.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from google.adk.skills import load_skill_from_dir

# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

_SKILL_DIR = (
    Path(__file__).resolve().parents[4]
    / 'app'
    / 'skills'
    / 'sow-document-readiness'
)

_EXPECTED_REFERENCES = ('gap-checklist.md',)

# Legacy vocabulary that must NEVER reappear in this skill. Each token
# was load-bearing in master's killed sow-discovery / Extraction
# Manifest pipeline. The bare word ``manifest`` is included: in this
# skill it could only ever mean the legacy concept. If any of these
# shows up, the old machinery has leaked back in — fix the skill, do not
# loosen the test.
_BANNED_MANIFEST_TOKENS = (
    'manifest',
    'extraction manifest',
    'extraction_manifest',
    'extracted_items',
    'append_extraction_items',
    'initialize_extraction_buffer',
    'finalize_extraction_manifest',
    'save_extraction_manifest',
    'manifest_item_id',
    'coverage_ledger',
    'coverage receipt',
    'discovery_agent',
    'manifest_tools',
)


@pytest.fixture(scope='module')
def loaded_skill():
    return load_skill_from_dir(_SKILL_DIR)


@pytest.fixture(scope='module')
def skill_md_text() -> str:
    return (_SKILL_DIR / 'SKILL.md').read_text(encoding='utf-8')


# ---------------------------------------------------------------------------
# Skill load
# ---------------------------------------------------------------------------


def test_skill_loads_with_expected_name(loaded_skill) -> None:
    """``load_skill_from_dir`` resolves the skill identity from frontmatter.

    Without this, the AutoScopedSkillToolset would not register the
    skill under the name the root prompt references.
    """
    assert loaded_skill.name == 'sow-document-readiness'


def test_skill_has_non_empty_instructions(loaded_skill) -> None:
    """The SKILL.md body is what the LLM sees after ``load_skill``."""
    instructions = loaded_skill.instructions or ''
    assert len(instructions.strip()) > 200, (
        'SKILL.md instruction body is suspiciously short — document '
        'readiness should describe the gap-check workflow.'
    )


def test_skill_description_identifies_path_b_document_flow(loaded_skill) -> None:
    """The description is the root's only ``list_skills`` signal that this
    is the Path B (documents) readiness skill — distinct from the Path A
    guided-intake skill."""
    description = (loaded_skill.description or '').lower()
    assert 'document' in description and 'readiness' in description, (
        'sow-document-readiness description must self-identify as the '
        'document readiness path (Path B) so the root routes correctly.'
    )
    assert 'guided' not in description, (
        'sow-document-readiness must NOT describe itself as the guided '
        'path — that wording belongs to sow-guided-intake (Path A) and '
        'would confuse routing.'
    )


# ---------------------------------------------------------------------------
# References
# ---------------------------------------------------------------------------


@pytest.mark.parametrize('reference_name', _EXPECTED_REFERENCES)
def test_expected_reference_is_loadable(loaded_skill, reference_name: str) -> None:
    """Each reference advertised in SKILL.md must be readable via the
    ``load_skill_resource`` API."""
    content = loaded_skill.resources.get_reference(reference_name)
    assert isinstance(content, str) and content.strip(), (
        f'Reference {reference_name!r} missing or empty — '
        'load_skill_resource would return None at runtime.'
    )


def test_reference_set_matches_expected(loaded_skill) -> None:
    """No surprise references in the folder — the set is part of the
    skill's public contract."""
    listed = set(loaded_skill.resources.list_references())
    assert listed == set(_EXPECTED_REFERENCES), (
        f'Reference set drifted from the documented contract. Got: '
        f'{sorted(listed)}; expected: {sorted(_EXPECTED_REFERENCES)}.'
    )


# ---------------------------------------------------------------------------
# Root allowlist wiring
# ---------------------------------------------------------------------------


def test_skill_is_in_root_allowlist() -> None:
    """The root must whitelist this skill in ``_ROOT_SKILL_NAMES``;
    otherwise ``load_skill('sow-document-readiness')`` fails at runtime."""
    from app.agent import _ROOT_SKILL_NAMES

    assert 'sow-document-readiness' in _ROOT_SKILL_NAMES, (
        'sow-document-readiness must be in app.agent._ROOT_SKILL_NAMES so '
        'load_skill can resolve it for Path B.'
    )


# ---------------------------------------------------------------------------
# Manifest-vocabulary guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize('banned', _BANNED_MANIFEST_TOKENS)
def test_skill_md_has_no_manifest_tokens(skill_md_text: str, banned: str) -> None:
    """The killed Extraction Manifest must NOT leak back into this skill.

    The whole point of sow-document-readiness is to recover the useful
    gap-diagnosis behavior WITHOUT the old manifest/discovery machinery.
    """
    assert banned.lower() not in skill_md_text.lower(), (
        f'sow-document-readiness/SKILL.md mentions banned legacy token '
        f'{banned!r}. This skill must stay a small conversational gap '
        'check — not recreate the Extraction Manifest pipeline.'
    )


@pytest.mark.parametrize('banned', _BANNED_MANIFEST_TOKENS)
@pytest.mark.parametrize('reference_name', _EXPECTED_REFERENCES)
def test_references_have_no_manifest_tokens(
    loaded_skill, reference_name: str, banned: str
) -> None:
    """Same vocabulary guard, applied to the reference file."""
    content = loaded_skill.resources.get_reference(reference_name) or ''
    assert banned.lower() not in content.lower(), (
        f'sow-document-readiness/references/{reference_name} mentions '
        f'banned legacy token {banned!r}. References must stay a simple '
        'prose checklist, not a manifest schema.'
    )


# ---------------------------------------------------------------------------
# Soft-gate / no-state contract
# ---------------------------------------------------------------------------


def test_skill_md_declares_no_state_and_no_save_tool(skill_md_text: str) -> None:
    """The plan pins this skill as purely conversational: it must not
    call save_sow_metadata or save_sow_intake_summary itself — the root
    owns metadata, and intake-summary persistence is Path A only."""
    lowered = skill_md_text.lower()
    assert 'persists nothing' in lowered or 'writes no state' in lowered, (
        'SKILL.md must state that the skill persists nothing — it is '
        'conversational only (no new state bundle).'
    )
    assert 'save_sow_intake_summary' in skill_md_text, (
        'SKILL.md must explicitly fence off save_sow_intake_summary as '
        'Path A only so the model does not call it in Path B.'
    )


def test_skill_md_declares_soft_gate(skill_md_text: str) -> None:
    """The readiness step must never hard-block generation — unresolved
    gaps are carried forward, not treated as a stop condition."""
    lowered = skill_md_text.lower()
    assert 'soft gate' in lowered and 'block' in lowered, (
        'SKILL.md must declare the soft-gate contract (does NOT block '
        'generation on unresolved gaps).'
    )
