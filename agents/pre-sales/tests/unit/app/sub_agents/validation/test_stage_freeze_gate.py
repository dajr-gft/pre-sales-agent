"""Guards for the stage-awareness freeze gate shared by every skill.

The validation critic runs twice per SOW: once at ``stage="content"``
(requirements + delivery_plan + scope_boundaries) and once at
``stage="full"`` (all five sections). The shared
``_RESOLUTION_MODE_GUIDE`` carries a binding gate that freezes the
sections NOT under review in each pass:

- at ``content`` the architecture / narrative keys are empty by design,
  so findings on them are suppressed;
- at ``full`` the content keys were already approved at the Content
  Review, so findings that would route a repair back into them are
  suppressed — the second pass focuses on architecture / narrative plus
  the legitimate cross-section check, which reads content as evidence
  but anchors its ``fields`` on architecture / technology_stack.

These tests pin that both halves of the gate enumerate exactly the
fields owned by the frozen sections, derived from the canonical
``BUNDLE_OWNED_FIELDS_BY_SECTION`` so the gate cannot silently drift
from the schema vocabulary. Assertions are substring-based and
phrasing-tolerant.
"""
from __future__ import annotations

import pytest

from app.sub_agents.validation.field_vocabulary import (
    BUNDLE_OWNED_FIELDS_BY_SECTION,
)
from app.sub_agents.validation.semantic_skills import _RESOLUTION_MODE_GUIDE

_CONTENT_SECTIONS = frozenset(
    {'requirements', 'delivery_plan', 'scope_boundaries'}
)
_SECOND_PART_SECTIONS = frozenset({'architecture', 'narrative'})


def _fields_for(sections: frozenset[str]) -> list[str]:
    return sorted(
        field
        for field, section in BUNDLE_OWNED_FIELDS_BY_SECTION.items()
        if section in sections
    )


@pytest.mark.parametrize('field', _fields_for(_SECOND_PART_SECTIONS))
def test_content_pass_freezes_every_architecture_narrative_field(
    field: str,
) -> None:
    """The content-stage half of the gate must list every architecture /
    narrative field so the critic never flags them while they are empty."""
    assert field in _RESOLUTION_MODE_GUIDE, (
        f'Stage-awareness gate must enumerate {field!r} as frozen at '
        'Stage: content (it is empty by design until the full stage).'
    )


@pytest.mark.parametrize('field', _fields_for(_CONTENT_SECTIONS))
def test_full_pass_freezes_every_content_owned_field(field: str) -> None:
    """The full-stage half of the gate must list every content-owned field
    so the second pass does not re-validate content already approved at the
    Content Review."""
    assert field in _RESOLUTION_MODE_GUIDE, (
        f'Stage-awareness gate must enumerate {field!r} as read-only at '
        'Stage: full (it was approved at the Content Review).'
    )


def test_full_pass_declares_a_content_freeze() -> None:
    guide = _RESOLUTION_MODE_GUIDE
    assert 'Stage: full' in guide
    assert 'READ-ONLY' in guide, (
        'The full-stage gate must mark the content sections as read-only.'
    )


def test_cross_section_exception_anchors_on_architecture() -> None:
    """The one allowed cross-section finding (Architecture × Stack × Scope)
    may read content as evidence but must anchor ``fields`` on architecture /
    technology_stack so the repair never routes into approved content."""
    guide = _RESOLUTION_MODE_GUIDE
    assert 'technology_stack' in guide
    # The exception must be explicit about keeping content out of `fields`.
    assert 'evidence' in guide.lower() and 'fields' in guide, (
        'The cross-section exception must instruct citing content in '
        'evidence, not in `fields`.'
    )
