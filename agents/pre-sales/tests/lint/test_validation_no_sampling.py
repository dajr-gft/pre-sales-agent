"""Lint — every validation ``SKILL.md`` must enforce no-sampling.

Root-cause fix for quality-loop non-convergence: the 4 semantic skills
used to end with "Cap at 5 findings" (disclosures 4), an MVP-era cap that
forced the critic to SAMPLE — it surfaced ~5 defects of a class per round
regardless of how many existed, so a larger same-class backlog could not
drain within ``max_rounds``. The cap is replaced by a non-sampling rule:
enumerate every VALID finding (within scope/stage, without lowering the
bars), bounded only by a high per-skill safety ceiling.

If a future edit reintroduces a low cap or drops the reinforcement
wording, the critic silently reverts to sampling and the loop stops
converging. This lint pins the rule's anchors in every skill — mirrors
``test_validation_stage_gate.py``.
"""

from __future__ import annotations

import pytest

# The 4 LLM critic skills whose findings the cap throttled.
_SKILLS = (
    'contradictions',
    'contractual_exposure',
    'disclosures',
    'semantic_quality',
)


def _skill_md(name: str) -> str:
    """Read ``<name>/SKILL.md`` via the package's own skills dir so a
    move/rename fails loudly rather than silently skipping the check."""
    from app.sub_agents.validation import semantic_skills

    path = semantic_skills._SKILLS_DIR / name / 'SKILL.md'
    assert path.exists(), f'SKILL.md not found for skill {name!r}: {path}'
    return path.read_text(encoding='utf-8')


@pytest.mark.parametrize('skill', _SKILLS)
def test_skill_has_no_sampling_heading(skill: str):
    assert 'Completeness — do not sample' in _skill_md(skill), (
        f'{skill}: the no-sampling section heading is missing — the LLM '
        'needs a discoverable anchor for the completeness rule.'
    )


@pytest.mark.parametrize('skill', _SKILLS)
@pytest.mark.parametrize(
    'anchor',
    [
        # The reviewer-mandated reinforcement wording. "Exhaustive" must
        # not be read as "hunt for more" — these three lines pin the
        # intended meaning: enumerate valid findings, no dupes, no
        # lowering the bars.
        'Enumerate every valid finding within the current validation scope and stage.',
        'Do not duplicate the same issue with different wording.',
        'Do not lower confidence/severity bars.',
    ],
)
def test_skill_carries_reinforcement_wording(skill: str, anchor: str):
    assert anchor in _skill_md(skill), (
        f'{skill}: missing reinforcement line {anchor!r}. Without it the '
        'model may read "no sampling" as a mandate to invent issues or '
        'relax the bars.'
    )


@pytest.mark.parametrize('skill', _SKILLS)
def test_skill_has_per_skill_safety_ceiling(skill: str):
    md = _skill_md(skill)
    assert 'at most 25 findings for THIS skill (not per category)' in md, (
        f'{skill}: the safety ceiling must be PER SKILL (~25), not per '
        'category — a per-category ceiling could explode token usage.'
    )


@pytest.mark.parametrize('skill', _SKILLS)
def test_skill_no_longer_carries_the_old_sampling_cap(skill: str):
    assert 'Cap at' not in _skill_md(skill), (
        f'{skill}: still carries an old "Cap at N findings" sampling cap. '
        'Replace it with the non-sampling completeness rule.'
    )
