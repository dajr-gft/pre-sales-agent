"""Lint — the shared ``_VERIFICATION_MODE_GUIDE`` must carry the
verification-mode admission rule (PR-3).

Rounds 2+ of the quality loop run AFTER a repair. The critic kept
re-auditing the whole document every round and opening new lateral
fronts — an "infinite audit" that stops the loop from converging even
while net progress is real. The fix is a prompt-level block, injected
only in verification mode, that narrows the round to "confirm the repair
+ catch regressions" without re-mining the document, while refusing to
let a genuine critical BLOCKER disappear.

If a future refactor removes or guts the block, the critic silently
reverts to re-auditing every round. This lint pins the rule's anchor
phrases so the prose can evolve without breaking the contract's
invariants — mirrors ``test_validation_stage_gate.py``.
"""

from __future__ import annotations

import pytest


def _load_guide() -> str:
    """Read the constant via attribute access so refactors that move or
    rename it fail with a clear message."""
    from app.sub_agents.validation import semantic_skills

    guide = getattr(semantic_skills, '_VERIFICATION_MODE_GUIDE', None)
    assert isinstance(guide, str) and guide, (
        '_VERIFICATION_MODE_GUIDE constant missing or empty in '
        'app.sub_agents.validation.semantic_skills — verification mode '
        'depends on it to narrow rounds 2+ to repair-checking.'
    )
    return guide


def test_guide_has_discoverable_heading():
    """The LLM scanning the prompt needs a heading to find the block."""
    assert 'Verification mode' in _load_guide()


def test_guide_keeps_changed_sections_placeholder():
    """The provider fills ``{changed_sections}`` per round from
    STATE_CHANGED_SECTIONS — without the placeholder the critic loses the
    repair-regression anchor."""
    assert '{changed_sections}' in _load_guide()


@pytest.mark.parametrize(
    'anchor',
    [
        # 1 — unresolved / recurring issues always re-enter.
        'UNRESOLVED',
        # 2 — repair-related regressions (changed section OR same family).
        'REGRESSION RELATED TO THE REPAIR',
        'changed sections',
        # the same-family route, keyed on (skill, category).
        'skill',
        'category',
        # 3 — lateral MAJOR/MINOR can be deferred to a later discovery.
        'FULLY-LATERAL issue of MAJOR or MINOR',
        # 4 — lateral BLOCKER must never be silently dropped.
        'FULLY-LATERAL issue of BLOCKER',
    ],
)
def test_guide_enumerates_admission_rule(anchor: str):
    guide = _load_guide()
    assert anchor in guide, (
        f'Verification-mode admission rule is missing the anchor '
        f'{anchor!r}. Each anchor pins one branch of the rule; dropping '
        'one re-opens the infinite-audit failure mode or risks hiding a '
        'real blocker.'
    )


def test_guide_forbids_dropping_a_genuine_blocker():
    """The dangerous half of the rule: lateral MAJOR/MINOR may be
    deferred, but a genuine critical BLOCKER must always be reported.
    Without this the audit just trades severity and a real blocker can
    vanish."""
    guide = _load_guide().lower()
    assert 'never silently drop it' in guide or 'never suppressed' in guide, (
        'The block must state that a genuine critical BLOCKER is never '
        'silently dropped — deferring lateral findings must never apply '
        'to a real blocker.'
    )


def test_guide_states_it_is_not_a_fresh_audit():
    """The intent framing — verification confirms a repair, it does not
    re-audit. Without it the LLM may treat the block as cosmetic."""
    guide = _load_guide()
    assert 'NOT a fresh audit' in guide or 'not an audit' in guide.lower()


def test_guide_does_not_bake_in_case_specific_identifiers():
    """Class-level rule, not an incident patch: no specific vendor /
    customer / product names belong in the shipped prompt (same guardrail
    as the inference patterns lint)."""
    guide = _load_guide()
    forbidden_tokens = (
        'Apigee',
        'Pub/Sub',
        'Santander',
        'BTG',
        'JBS',
        'Salesforce',
        'Acme',
        'OneTrust',
        'Confluence',
        'Vertex AI',
        'GCP',
    )
    found = [tok for tok in forbidden_tokens if tok in guide]
    assert not found, (
        f'Verification-mode block references incident-specific tokens '
        f'{found!r}. The rule must read in terms of categories '
        '(changed section / family / severity), not the triggering '
        'incident.'
    )
