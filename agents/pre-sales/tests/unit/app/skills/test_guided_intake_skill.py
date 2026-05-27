"""Unit tests for the sow-guided-intake skill.

The skill is purely declarative — three markdown reference files and a
SKILL.md instruction body. The tests below pin the contract the root
prompt and the AutoScopedSkillToolset depend on:

  - The skill loads through ``load_skill_from_dir`` with the expected
    name and a non-empty instruction body.
  - The three references advertised in the SKILL.md are present and
    readable as text.
  - The skill is part of the root allowlist (``_ROOT_SKILL_NAMES``) so
    ``load_skill('sow-guided-intake')`` works end-to-end at runtime.
  - The skill body does NOT mention any of the legacy manifest tokens
    (extraction manifest, append_extraction_items, coverage_ledger,
    etc.); the manifest was killed for this branch and any reappearance
    here would mean the discovery skill was accidentally restored.
  - The skill body publicly declares the ``<intake_summary>`` handoff
    contract — that is what the root parses after the interview.
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
    / 'sow-guided-intake'
)

_EXPECTED_REFERENCES = (
    'intake-blocks.md',
    'inference-policy.md',
    'intake-summary-format.md',
)

# Legacy manifest vocabulary that must NEVER reappear in the guided
# intake skill. Each token below was load-bearing in master's
# sow-discovery (now killed). If any of them shows up here, the manifest
# pipeline has leaked into the guided intake — fix the skill, do not
# loosen the test.
_BANNED_MANIFEST_TOKENS = (
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
    assert loaded_skill.name == 'sow-guided-intake'


def test_skill_has_non_empty_instructions(loaded_skill) -> None:
    """The SKILL.md body is what the LLM sees after ``load_skill``.

    An empty body would mean the file was parsed but contained only
    frontmatter — the skill would have no behavior.
    """
    instructions = loaded_skill.instructions or ''
    assert len(instructions.strip()) > 200, (
        'SKILL.md instruction body is suspiciously short — guided intake '
        'should describe the interview workflow.'
    )


def test_skill_description_mentions_guided_intake_path(loaded_skill) -> None:
    """The description is shown in ``list_skills`` and is the root's only
    signal that this is the guided-intake path (Path A).
    """
    description = (loaded_skill.description or '').lower()
    assert 'guided' in description and (
        'intake' in description or 'interview' in description
    ), (
        'sow-guided-intake description must self-identify as the guided '
        'interview path so the root can route to it correctly.'
    )


# ---------------------------------------------------------------------------
# References
# ---------------------------------------------------------------------------


@pytest.mark.parametrize('reference_name', _EXPECTED_REFERENCES)
def test_expected_reference_is_loadable(loaded_skill, reference_name: str) -> None:
    """Each reference advertised in SKILL.md must be readable via the
    ``Resources.get_reference`` API the toolset wraps as
    ``load_skill_resource``.
    """
    content = loaded_skill.resources.get_reference(reference_name)
    assert isinstance(content, str) and content.strip(), (
        f'Reference {reference_name!r} missing or empty — load_skill_resource '
        'would return None at runtime and the skill instructions would dead-end.'
    )


def test_reference_set_matches_expected(loaded_skill) -> None:
    """No surprise references in the folder.

    The set is part of the public contract this skill carries; an
    accidental extra file (e.g. an editor backup) would be picked up by
    ``_load_dir`` and shown in ``list_references`` to the LLM.
    """
    listed = set(loaded_skill.resources.list_references())
    assert listed == set(_EXPECTED_REFERENCES), (
        f'Reference set drifted from the documented contract. Got: '
        f'{sorted(listed)}; expected: {sorted(_EXPECTED_REFERENCES)}.'
    )


# ---------------------------------------------------------------------------
# Root allowlist wiring
# ---------------------------------------------------------------------------


def test_skill_is_in_root_allowlist() -> None:
    """The root must whitelist this skill in ``_ROOT_SKILL_NAMES``.

    Without it, ``load_skill('sow-guided-intake')`` would fail at
    runtime even though the folder exists on disk.
    """
    from app.agent import _ROOT_SKILL_NAMES

    assert 'sow-guided-intake' in _ROOT_SKILL_NAMES, (
        'sow-guided-intake must be in app.agent._ROOT_SKILL_NAMES so '
        'load_skill can resolve it; see Phase 2 of the guided-intake plan.'
    )


# ---------------------------------------------------------------------------
# Manifest-vocabulary guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize('banned', _BANNED_MANIFEST_TOKENS)
def test_skill_md_has_no_manifest_tokens(skill_md_text: str, banned: str) -> None:
    """The killed Extraction Manifest must NOT leak back into this skill.

    The master branch's sow-discovery built a manifest with these tokens.
    The Approach 2 branch killed the manifest; if any token reappears
    here, the simplification has been quietly undone.
    """
    haystack = skill_md_text.lower()
    assert banned.lower() not in haystack, (
        f'sow-guided-intake/SKILL.md mentions banned manifest token '
        f'{banned!r}. This skill must NOT recreate the Extraction Manifest '
        'pipeline — only emit a structured <intake_summary> block.'
    )


@pytest.mark.parametrize('banned', _BANNED_MANIFEST_TOKENS)
@pytest.mark.parametrize('reference_name', _EXPECTED_REFERENCES)
def test_references_have_no_manifest_tokens(
    loaded_skill, reference_name: str, banned: str
) -> None:
    """Same vocabulary guard, applied to every reference file."""
    content = loaded_skill.resources.get_reference(reference_name) or ''
    assert banned.lower() not in content.lower(), (
        f'sow-guided-intake/references/{reference_name} mentions banned '
        f'manifest token {banned!r}. References must stay focused on the '
        'guided intake interview + <intake_summary> handoff.'
    )


# ---------------------------------------------------------------------------
# Intake-summary handoff contract
# ---------------------------------------------------------------------------


def test_skill_md_declares_intake_summary_handoff(skill_md_text: str) -> None:
    """The root prompt parses ``<intake_summary>``; the skill must say so.

    Without this declaration, the LLM may end the interview with free
    prose and the root cannot tell that the handoff happened.
    """
    assert '<intake_summary>' in skill_md_text, (
        'sow-guided-intake/SKILL.md must reference the <intake_summary> '
        'tag — it is the documented handoff contract to the root.'
    )


def test_summary_format_reference_declares_block_shape(loaded_skill) -> None:
    """The summary-format reference is the field-level contract.

    It must spell out the canonical labels the root expects (Customer,
    Project, Funding, Problem / Goal, ...) so the LLM does not silently
    rename or drop them.
    """
    content = loaded_skill.resources.get_reference('intake-summary-format.md') or ''
    for required_label in (
        '<intake_summary>',
        'Customer:',
        'Project:',
        'Funding:',
        'Problem / Goal:',
        'Solution Direction:',
        'Integrations / Systems:',
        'Timeline:',
        'Open Items:',
    ):
        assert required_label in content, (
            f'intake-summary-format.md must declare label {required_label!r} '
            'so the root prompt parses a consistent handoff.'
        )


def test_inference_policy_distinguishes_required_and_inference_eligible(
    loaded_skill,
) -> None:
    """The policy must publish both the ``[TO BE DEFINED]`` rule and the
    ``(inferred)`` marker, otherwise the root cannot tell hard gaps from
    deferred decisions when it parses the summary.
    """
    content = loaded_skill.resources.get_reference('inference-policy.md') or ''
    assert '[TO BE DEFINED]' in content
    assert '(inferred)' in content
    assert 'inference-eligible' in content.lower()
    assert 'required' in content.lower()


def test_intake_blocks_reference_lists_five_blocks(loaded_skill) -> None:
    """The plan calls for five blocks (Identity, Briefing, Integrations,
    Scope & Team, Targets). The block tracker is the single place the
    skill enumerates them — drift would break the exit gate.
    """
    content = loaded_skill.resources.get_reference('intake-blocks.md') or ''
    headers = (
        'Block 1 — Identity',
        'Block 2 — Project Briefing',
        'Block 3 — Integrations and Systems',
        'Block 4 — Scope and Team',
        'Block 5 — Targets and Constraints',
    )
    for header in headers:
        assert header in content, (
            f'intake-blocks.md must contain block header {header!r}; the '
            'five-block contract is part of the plan acceptance criteria.'
        )
