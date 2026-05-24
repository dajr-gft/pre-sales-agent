"""Lint — the shared ``_RESOLUTION_MODE_GUIDE`` must carry the stage gate.

Mudança 2 of the over-escalation audit: instead of duplicating the
stage-awareness rule across each of the 5 validation ``SKILL.md`` files
(and risking the per-file 200-line gate), the rule lives in the central
``_RESOLUTION_MODE_GUIDE`` constant in
``app/sub_agents/validation/semantic_skills.py`` which is appended to
every skill's instruction at runtime.

If a future refactor accidentally removes the gate or splits the
constant, the validation pipeline silently drops back to the old
behavior (architecture findings emitted at content stage,
re-introducing the false-positive class observed in production).

This lint asserts the rule's anchor phrases are still present in the
constant. Each anchor corresponds to a load-bearing piece of the
contract:

- ``Stage-awareness gate (binding)`` — the heading the LLM sees.
- ``Stage: content`` — the literal stage value the rule binds to.
- ``architecture_description``, ``technology_stack``,
  ``executive_summary``, ``customer_primary_domain`` — the list of
  fields the LLM must not flag at content stage. Spot-check three to
  confirm the bullet list is intact.
- ``"contract, not a defect"`` — the framing sentence that distinguishes
  "intentionally empty" from "missing".

The point of asserting on phrases (instead of regex-matching the whole
block) is that the prose can evolve without breaking the lint as long
as the contract's invariants stay visible.
"""

from __future__ import annotations

import pytest


def _load_guide() -> str:
    """Read the constant via attribute access so refactors that move
    or rename the constant fail the test with a clear message."""
    from app.sub_agents.validation import semantic_skills

    guide = getattr(semantic_skills, '_RESOLUTION_MODE_GUIDE', None)
    assert isinstance(guide, str) and guide, (
        '_RESOLUTION_MODE_GUIDE constant missing or empty in '
        'app.sub_agents.validation.semantic_skills — every validation '
        'skill prompt depends on it for the resolution_mode taxonomy '
        'AND the stage gate.'
    )
    return guide


def test_resolution_mode_guide_contains_stage_gate_heading():
    """The block must be discoverable by the LLM scanning the prompt."""
    guide = _load_guide()
    assert 'Stage-awareness gate (binding)' in guide


def test_resolution_mode_guide_binds_to_content_stage_value():
    """The literal value the LLM compares against must appear at least
    once — without it the conditional clause has no trigger."""
    guide = _load_guide()
    assert 'Stage: content' in guide


@pytest.mark.parametrize(
    'field',
    [
        # Architecture fields — populated only at full stage.
        'architecture_description',
        'architecture_components',
        'architecture_integrations',
        'technology_stack',
        # Narrative fields — populated only at full stage.
        'executive_summary',
        'partner_overview',
        'customer_overview',
        'customer_primary_domain',
    ],
)
def test_resolution_mode_guide_lists_content_stage_empty_field(field: str):
    """Every key that assemble_sow_payload omits at content stage MUST
    appear in the gate. Drift between the assembler and the gate would
    silently reopen the false-positive class.
    """
    guide = _load_guide()
    assert field in guide, (
        f"Stage-awareness gate is missing '{field}' — the LLM may flag "
        'this field as missing at content stage.'
    )


def test_resolution_mode_guide_uses_contract_not_defect_framing():
    """The framing sentence carries the intent — "absent by design",
    not "missing". Without it, the LLM may interpret the rule as a
    suppress-output trick rather than as policy."""
    guide = _load_guide()
    assert 'contract, not a defect' in guide


def test_resolution_mode_guide_keeps_resolution_taxonomy():
    """Sanity: the stage-gate addition did not displace the four-mode
    taxonomy. All four modes must remain documented in the guide."""
    guide = _load_guide()
    for mode in (
        'auto_fixable',
        'decision_required',
        'source_conflict',
        'not_fixable_by_agent',
    ):
        assert mode in guide, (
            f"Resolution mode '{mode}' missing from the guide — every "
            'skill needs all four documented for the LLM to choose '
            'correctly.'
        )


# ---------------------------------------------------------------------------
# Approved placeholders block — extends the guide so the validator does
# not flag user-approved ``[TO BE DEFINED]`` / ``[A DEFINIR]`` literals.
# ---------------------------------------------------------------------------


def test_resolution_mode_guide_contains_approved_placeholders_heading():
    """The heading anchors the block — without it the LLM cannot find
    the rule when scanning the prompt for placeholder guidance."""
    guide = _load_guide()
    assert 'Approved placeholders (binding)' in guide


@pytest.mark.parametrize(
    'marker',
    [
        '[TO BE DEFINED]',
        '[TBD]',
        '[A DEFINIR]',
        '[A SER DEFINIDO]',
        '[POR DEFINIR]',
        '[INSERT X]',
    ],
)
def test_resolution_mode_guide_lists_known_placeholder_marker(marker: str):
    """Every placeholder form the canonical pattern accepts must appear
    in the prose so the LLM has a concrete enumeration to recognise."""
    guide = _load_guide()
    assert marker in guide, (
        f"Placeholder marker {marker!r} missing from the guide — the "
        'LLM may fail to recognise it as a sanctioned deferral and '
        'emit a finding asking the user to fill the field.'
    )


def test_resolution_mode_guide_links_to_approved_deferrals_payload():
    """The block must point the LLM at the ``<approved_deferrals>``
    runtime payload section that the instruction provider injects;
    without that reference the LLM has no way to distinguish approved
    from unapproved placeholders at run time."""
    guide = _load_guide()
    assert '<approved_deferrals>' in guide


def test_resolution_mode_guide_routes_unapproved_placeholders_to_auto_fixable():
    """Guardrail #3: unapproved placeholders must not escalate. The
    rule explicitly says ``auto_fixable`` for unapproved cases so the
    revision_agent either populates or removes the offending field —
    never a user question."""
    guide = _load_guide()
    assert 'UNAPPROVED placeholders' in guide
    assert 'auto_fixable' in guide
