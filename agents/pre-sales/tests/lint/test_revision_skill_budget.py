"""Lint — ``sow-revision/SKILL.md`` must carry the per-round patch budget.

The revision_agent without a per-round budget tends to patch every
finding the critic emits in a single round (production case: 19
findings, 57 patches accumulated, loop exhausted). Rewriting that many
top-level fields drifts the SOW far enough that the critic on the next
round flags new defects faster than the reviser resolves old ones, and
the QualityLoopAgent burns its whole round budget without converging.

The budget instruction lives in the central SKILL.md so every revision
round sees it, and is mirrored in the revision_agent's runtime footer
(`_INPUTS_PRESENT_FOOTER`) so it is repeated next to the staged inputs.
This lint pins the anchor phrases in BOTH places — if a future refactor
silently drops the cap, the loop regresses to the production failure
mode that motivated the fix.

The anchors checked here are deliberately the load-bearing pieces of
the contract (the number ``5``, the selection-order labels, the
resolution-mode filter) rather than the full prose. The wording can
evolve as long as the invariants stay visible.
"""

from __future__ import annotations

from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers — read each surface once per test invocation.
# ---------------------------------------------------------------------------


_SKILL_PATH = (
    Path(__file__).resolve().parents[2]
    / 'app' / 'skills' / 'sow-revision' / 'SKILL.md'
)


def _load_skill_body() -> str:
    assert _SKILL_PATH.is_file(), (
        f'sow-revision/SKILL.md not found at {_SKILL_PATH}; the budget '
        'contract has nowhere to live.'
    )
    return _SKILL_PATH.read_text(encoding='utf-8')


def _load_runtime_footer() -> str:
    """Import the runtime footer as a plain string so this lint checks
    BOTH surfaces the LLM sees — the SKILL.md body the provider prepends
    AND the footer the provider appends each turn."""
    from app.sub_agents.revision.agent import _INPUTS_PRESENT_FOOTER

    return _INPUTS_PRESENT_FOOTER


# ---------------------------------------------------------------------------
# SKILL.md — workflow step (1a) carries the binding budget
# ---------------------------------------------------------------------------


class TestSkillBodyBudgetContract:
    def test_budget_heading_is_present(self):
        """The block must be discoverable when the LLM scans the SKILL.md
        for guidance — without the heading the rules are buried in prose
        and easier to overlook on a long prompt."""
        body = _load_skill_body()
        assert 'Per-round patch budget (binding)' in body

    def test_budget_cap_is_five_significant_findings(self):
        """The number is load-bearing. Different caps would change the
        loop's convergence dynamics; pinning the literal makes any future
        loosening obvious in a code review diff."""
        body = _load_skill_body()
        # Anchor on both the count and the severity classification so
        # an unrelated occurrence of "5" elsewhere in the prose cannot
        # accidentally satisfy the assertion.
        assert '5 significant findings' in body

    @pytest.mark.parametrize(
        'phrase',
        [
            # Each phrase corresponds to one tier of the priority order
            # the LLM consults when the budget overflows. Renaming any
            # one regresses the contract (the LLM stops applying the
            # tier consistently).
            'BLOCKER',
            'MAJOR',
            'persistent',
        ],
    )
    def test_budget_selection_order_mentions_each_severity_tier(self, phrase):
        body = _load_skill_body()
        # The selection order is a numbered list of four tiers right
        # under the heading. We only check the phrase is present — the
        # exact wording can evolve.
        assert phrase in body, (
            f"Selection-order phrase {phrase!r} missing — the LLM has no "
            'tiebreaker between equally severe findings.'
        )

    def test_resolution_mode_filter_skips_non_auto_fixable(self):
        """The filter is the other half of the contract: the budget cap
        bounds the WORK; the filter bounds the SCOPE. Without it, the
        reviser tries to patch decision_required findings and either
        invents data (Contract 3 violation) or churns the SOW without
        resolving the underlying ambiguity."""
        body = _load_skill_body()
        # The phrase the LLM scans for + the policy direction.
        assert 'Resolution mode filter (binding)' in body
        # All three non-auto-fixable modes must appear by name so the
        # LLM can deterministically classify each finding.
        for mode in (
            'decision_required',
            'source_conflict',
            'not_fixable_by_agent',
        ):
            assert mode in body, (
                f"resolution_mode {mode!r} missing from the filter — the "
                'LLM may attempt to patch findings of this kind, '
                'invalidating the QualityLoopAgent gate.'
            )

    def test_zero_findings_to_patch_records_noop_round(self):
        """Round-discipline guard: when the filter + cap reduce the work
        to zero, the SKILL must still tell the LLM to log a noop entry.
        Otherwise the QualityLoopAgent sees an empty revision_log and
        cannot distinguish "no work needed" from "the patcher crashed"."""
        body = _load_skill_body()
        assert 'record_revision_log_entries' in body
        assert 'noop_reason' in body


# ---------------------------------------------------------------------------
# Runtime footer — mirror the budget rule next to the staged inputs
# ---------------------------------------------------------------------------


class TestRuntimeFooterMirrorsBudget:
    """The provider appends ``_INPUTS_PRESENT_FOOTER`` after the SKILL
    body on every turn. Pinning the budget reminder there means even a
    truncated context (SKILL body trimmed by the model's context window)
    still carries the rule next to the data it constrains."""

    def test_footer_mentions_per_round_budget(self):
        footer = _load_runtime_footer()
        assert 'Per-round budget (binding)' in footer

    def test_footer_repeats_cap_of_five(self):
        footer = _load_runtime_footer()
        assert 'at most 5' in footer

    def test_footer_repeats_resolution_mode_filter(self):
        footer = _load_runtime_footer()
        assert 'auto_fixable' in footer
        # The instruction must explicitly tell the reviser to skip
        # non-auto_fixable findings, not just acknowledge they exist.
        assert 'Skip' in footer or 'skip' in footer
