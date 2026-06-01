"""Lint — every (skill, category) emitted by the 5 validation skills
must have a mapping in ``sow-revision/references/finding-map.md``.

Why this lint exists
--------------------
The revision_agent's tool surface (``load_sow_reference``) is allowlisted
from finding-map.md at import time. When a validation skill emits a
finding whose ``(skill, category)`` pair is not mapped, the agent has to
fall back to the generic style-guide reference and patches uninformed —
which usually reproduces the finding next round. After the round
budget is spent, the quality loop returns ``exhausted`` and the root agent
asks the user to decide. Mudança 4 of the over-escalation audit closes
this hole by guaranteeing every category the LLM is allowed to emit has
a target reference.

How the lint works
------------------
Tripwire pattern: the expected set of pairs is hardcoded here, derived
from the ``Allowed category values`` lines in each
``app/sub_agents/validation/skills/<skill>/SKILL.md``. When a new
category is added to a SKILL.md, this test fails until the same pair
appears in finding-map.md AND in the list below — forcing the two
files to stay in sync via code review, not goodwill.

The lint does not enforce a particular target reference; that judgement
is contextual and the map can route a category to whichever section
reference makes sense (the only constraint is that *some* mapping
exists so the revision_agent can patch).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


_SKILLS_DIR = Path(__file__).parents[2] / 'app' / 'skills'
_FINDING_MAP_PATH = (
    _SKILLS_DIR / 'sow-revision' / 'references' / 'finding-map.md'
)


# Mirror of the ``Allowed category values`` lines in each validation
# SKILL.md. If a new category is added to a SKILL.md, add it here AND
# add a row to finding-map.md in the same change.
_EXPECTED_SKILL_CATEGORIES: tuple[tuple[str, str], ...] = (
    # coverage
    ('coverage', 'manifest_item_uncovered'),
    # contradictions
    ('contradictions', 'fr_vs_nfr'),
    ('contradictions', 'scope_vs_oos'),
    ('contradictions', 'architecture_vs_stack'),
    ('contradictions', 'activities_vs_deliverables'),
    ('contradictions', 'assumptions_vs_risks'),
    ('contradictions', 'timeline_vs_deliverables'),
    # contractual_exposure
    ('contractual_exposure', 'missing_consequence_clause'),
    ('contractual_exposure', 'missing_timing_anchor'),
    ('contractual_exposure', 'subjective_nfr_target'),
    ('contractual_exposure', 'missing_change_request_gate'),
    ('contractual_exposure', 'missing_handover_boundary'),
    ('contractual_exposure', 'schedule_graph_misalignment'),
    ('contractual_exposure', 'incomplete_parent_contract_reference'),
    # disclosures
    ('disclosures', 'missing_ai_nondeterminism_disclosure'),
    ('disclosures', 'missing_external_api_dependency_disclosure'),
    ('disclosures', 'missing_pii_responsibility_disclosure'),
    ('disclosures', 'missing_production_handover_disclosure'),
    ('disclosures', 'missing_customer_infra_dependency_disclosure'),
    ('disclosures', 'missing_multi_region_authority_disclosure'),
    # semantic_quality
    ('semantic_quality', 'vague_phrasing_outside_nfr'),
    ('semantic_quality', 'self_sufficiency_break'),
    ('semantic_quality', 'redundant_or_overlapping_items'),
    ('semantic_quality', 'naming_drift'),
    ('semantic_quality', 'generic_architecture_labels'),
    ('semantic_quality', 'language_hygiene'),
    ('semantic_quality', 'style_pattern_omission'),
)


# Match table rows: ``| `<skill>` | `<category>` | ... |``. Skill and
# category sit in the first two backtick-quoted cells; we accept any
# whitespace and ignore the rest of the row.
_ROW_PATTERN = re.compile(
    r'^\|\s*`([a-z_]+)`\s*\|\s*`([a-z_*]+)`\s*\|',
    re.MULTILINE,
)


def _load_mapped_pairs() -> set[tuple[str, str]]:
    """Parse the (skill, category) pairs declared in finding-map.md."""
    assert _FINDING_MAP_PATH.is_file(), (
        f'finding-map.md not found at {_FINDING_MAP_PATH}; revision '
        'agent cannot operate without it.'
    )
    text = _FINDING_MAP_PATH.read_text(encoding='utf-8')
    return {
        (skill, category) for skill, category in _ROW_PATTERN.findall(text)
    }


@pytest.mark.parametrize('skill,category', _EXPECTED_SKILL_CATEGORIES)
def test_finding_map_has_entry_for_skill_category(
    skill: str, category: str
):
    """Every category the LLM may emit must have a row in finding-map.md.

    If this fails, the revision_agent cannot route the finding to a
    section reference. The fix is to add a table row to finding-map.md
    naming the target skill + reference that should be loaded before
    patching, then re-run.
    """
    mapped = _load_mapped_pairs()
    assert (skill, category) in mapped, (
        f"finding-map.md has no entry for ({skill!r}, {category!r}). "
        'Without a mapping the revision_agent falls back to the generic '
        'style-guide and patches blind; the loop will exhaust on this '
        'finding and the root will surface it as a user question. Add a '
        'row to sow-revision/references/finding-map.md and either keep '
        'this list in sync or rerun this lint.'
    )


def test_finding_map_does_not_reference_unknown_skill_names():
    """All keyed skills must be one of the five active validation skills
    or the bootstrap helpers the revision_agent has built in.

    Historical leftover entries keyed under skill names that do not
    exist in ``schema.SkillName`` (e.g. ``self_sufficiency``,
    ``language_hygiene``) are silently dead because no finding will ever
    carry that ``skill`` value — but they pollute the allowlist and
    confuse the next reader. Catch them here.
    """
    mapped = _load_mapped_pairs()
    valid_skills = {
        'coverage',
        'contradictions',
        'contractual_exposure',
        'disclosures',
        'semantic_quality',
    }
    bad = {skill for skill, _ in mapped if skill not in valid_skills}
    assert not bad, (
        f'finding-map.md references unknown skill name(s): {sorted(bad)}. '
        'A finding will never carry these skill values, so the entries '
        'are dead. Either rekey to one of the five active skills or '
        'remove the row.'
    )
