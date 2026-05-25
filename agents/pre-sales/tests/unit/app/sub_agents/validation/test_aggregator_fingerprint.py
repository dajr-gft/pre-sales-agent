"""Tests for the fingerprint stability fix.

Production incident: a 3-round quality loop on a content-stage SOW showed
``persistent_blocking_finding_count: 0`` in EVERY round even though the
revision_agent only patched 5 of the ~10 significant findings per round.
With 5 unpatched findings carrying over, at least some round-1 fingerprints
should have re-appeared in round 2. They did not — because the old
discriminator was ``evidence[:240].strip().lower()``, and the critic
re-quoted the same finding with slightly different prose each round,
producing a different fingerprint for the same defect.

The fix derives the discriminator from SOW-internal anchors quoted in
the evidence (``FR-NN``, ``NFR-NN``, ``WS-NN``, ``OOS-NN``, ``A-NN``,
``I-NN``, ``R-NN``, ``T-NN``, ``G-NN``, ``P-NN``). These IDs come from
the staged SOW itself — they are stable across critic re-invocations on
the same defect. When evidence has no anchors (some semantic_quality /
naming_drift cases), a heavily normalised prefix of the prose is used
instead so the fingerprint is still more wording-tolerant than the old
240-char slice.

The tests below pin both halves of that contract — anchor extraction
and the normalised fallback — plus the end-to-end invariant that the
``Finding`` fingerprint stays stable when the critic reworded the same
defect across rounds.
"""

from __future__ import annotations

import pytest

from app.sub_agents.validation.aggregator import (
    _ANCHOR_PATTERN,
    _evidence_discriminator,
    _fingerprint,
)
from app.sub_agents.validation.schema import Finding


# ---------------------------------------------------------------------------
# Anchor extraction
# ---------------------------------------------------------------------------


class TestAnchorPattern:
    """Pin the regex behaviour directly — the rest of the module relies
    on it returning the exact set of anchors quoted in the evidence."""

    @pytest.mark.parametrize(
        ('evidence', 'expected'),
        [
            ('FR-01 contradicts NFR-03', ['FR-01', 'NFR-03']),
            ('the deliverable WS-12 has no activity', ['WS-12']),
            ('OOS-01 / OOS-02 / OOS-03 all overlap', ['OOS-01', 'OOS-02', 'OOS-03']),
            ('A-01 is mentioned but R-02 is not', ['A-01', 'R-02']),
            ('manifest item I-001 is uncovered', ['I-001']),
            ('phase P-2 is missing tasks', ['P-2']),
            ('gap G-04 escalated', ['G-04']),
            # No anchors at all.
            ('the description is too short', []),
            # Multiple of the same anchor — deduplication happens in the
            # discriminator, not the regex.
            ('FR-01 and FR-01 are duplicate', ['FR-01', 'FR-01']),
        ],
    )
    def test_matches_expected_anchors(self, evidence, expected):
        assert _ANCHOR_PATTERN.findall(evidence) == expected

    def test_case_insensitive_match(self):
        """The critic occasionally lowercases ids in flowing prose; the
        anchor list must still recognise them."""
        assert _ANCHOR_PATTERN.findall('fr-01 and nfr-03') == ['fr-01', 'nfr-03']

    @pytest.mark.parametrize(
        'noise',
        [
            'AES-256 encryption',  # cryptographic constant — not a SOW id
            'ISO-9001 compliance',
            'PEP-8 style',
            'gpt-4 model',
            'release v1.2 timeline',  # bare numbers, no prefix
        ],
    )
    def test_does_not_match_non_anchor_tokens(self, noise):
        """The regex is intentionally specific to SOW anchor prefixes to
        avoid false positives that would conflate unrelated findings."""
        assert _ANCHOR_PATTERN.findall(noise) == []


# ---------------------------------------------------------------------------
# Discriminator behaviour
# ---------------------------------------------------------------------------


class TestEvidenceDiscriminator:
    def test_anchor_branch_returns_sorted_unique_tuple(self):
        """When anchors are present we return a sorted-unique tuple so
        the fingerprint is deterministic regardless of quote order."""
        d = _evidence_discriminator(
            'OOS-02 conflicts with FR-01 and again FR-01 mentioned'
        )
        assert d == ('FR-01', 'OOS-02')

    def test_anchor_branch_is_order_invariant(self):
        """Same anchors in different order in the prose → same
        discriminator (the fingerprint must not depend on the critic's
        word order)."""
        a = _evidence_discriminator('FR-01 contradicts NFR-03')
        b = _evidence_discriminator('NFR-03 contradicts FR-01')
        assert a == b

    def test_anchor_branch_is_case_invariant(self):
        a = _evidence_discriminator('FR-01 and NFR-03')
        b = _evidence_discriminator('fr-01 and nfr-03')
        assert a == b

    def test_fallback_normalises_punctuation_case_and_whitespace(self):
        """When no anchors exist we fall back to normalised prose so a
        critic's punctuation / case / whitespace variations across rounds
        do not change the fingerprint."""
        a = _evidence_discriminator(
            "The 'description' is too short, missing context."
        )
        b = _evidence_discriminator(
            "the description is too short missing context"
        )
        # Both normalise to the same alphanumeric-only stream.
        assert a == b

    def test_fallback_truncates_to_cap(self):
        """The fallback prefix is bounded so a long generic finding does
        not blow up the fingerprint key."""
        long_evidence = 'word ' * 200  # 1000 chars worth of repetition
        d = _evidence_discriminator(long_evidence)
        assert isinstance(d, str)
        assert len(d) <= 120

    def test_empty_evidence_returns_empty_string(self):
        assert _evidence_discriminator('') == ''

    def test_anchor_branch_preferred_over_fallback_when_both_available(self):
        """When the evidence has both anchors AND additional prose, the
        anchor tuple wins. The prose is the unstable bit; ditching it is
        the whole point of this fix."""
        with_extra_prose = _evidence_discriminator(
            "FR-01: the description, as written today, conflicts with NFR-03's "
            'target of 99.9 percent because the wording is ambiguous'
        )
        terser = _evidence_discriminator('FR-01 vs NFR-03 conflict')
        assert with_extra_prose == terser == ('FR-01', 'NFR-03')


# ---------------------------------------------------------------------------
# End-to-end fingerprint stability — the production scenario
# ---------------------------------------------------------------------------


def _make_finding(
    *,
    skill: str = 'contradictions',
    category: str = 'activities_vs_deliverables',
    severity: str = 'MAJOR',
    evidence: str = '',
    fields: list[str] | None = None,
    manifest_item_id: str | None = None,
) -> Finding:
    return Finding(
        id='test-001',
        skill=skill,  # type: ignore[arg-type]
        category=category,
        severity=severity,  # type: ignore[arg-type]
        confidence=0.9,
        evidence=evidence,
        recommendation='Reconcile the offending items.',
        fields=fields or ['activity_phases', 'deliverables'],
        manifest_item_id=manifest_item_id,
    )


class TestFingerprintStabilityAcrossRounds:
    """Models the production failure: same defect, two critic invocations,
    different wording. The fingerprint must match in both invocations."""

    def test_same_anchors_in_reworded_evidence_share_fingerprint(self):
        round_one = _make_finding(
            evidence='Activity phase mentions deliverable WS-03 but the '
                     'deliverable WS-03 has no corresponding activity row.'
        )
        round_two = _make_finding(
            evidence='Deliverable WS-03 is referenced from an activity '
                     'phase yet no row maps WS-03 to a phase id.'
        )
        # Different prose, same anchor (WS-03), same skill+category+fields.
        assert _fingerprint(round_one) == _fingerprint(round_two)

    def test_multiple_anchors_in_different_order_share_fingerprint(self):
        round_one = _make_finding(
            evidence='FR-01 contradicts NFR-03 on latency'
        )
        round_two = _make_finding(
            evidence='NFR-03 latency target is incompatible with FR-01'
        )
        assert _fingerprint(round_one) == _fingerprint(round_two)

    def test_different_anchors_produce_different_fingerprints(self):
        """The fix must not over-collapse — two genuinely different
        defects (different anchored entities) must stay distinguishable."""
        a = _make_finding(evidence='FR-01 contradicts NFR-03')
        b = _make_finding(evidence='FR-05 contradicts NFR-07')
        assert _fingerprint(a) != _fingerprint(b)

    def test_different_categories_produce_different_fingerprints(self):
        """Same anchors quoted by different categories are different
        findings — the category stays in the fingerprint key for this
        reason."""
        a = _make_finding(category='activities_vs_deliverables',
                          evidence='WS-03 misaligned')
        b = _make_finding(category='timeline_vs_deliverables',
                          evidence='WS-03 misaligned')
        assert _fingerprint(a) != _fingerprint(b)

    def test_unanchored_finding_with_punctuation_variation_shares_fingerprint(self):
        """The fallback path handles the smaller class of findings that
        truly have no anchors (some semantic_quality / naming_drift
        cases). Punctuation drift between rounds must not break it."""
        a = _make_finding(
            skill='semantic_quality',
            category='naming_drift',
            evidence='The phrase "delivery plan" appears as "delivery-plan" '
                     'in section 3.',
            fields=['executive_summary'],
        )
        b = _make_finding(
            skill='semantic_quality',
            category='naming_drift',
            evidence='the phrase delivery plan appears as delivery plan in '
                     'section 3',
            fields=['executive_summary'],
        )
        assert _fingerprint(a) == _fingerprint(b)

    def test_fingerprint_changes_when_fields_change(self):
        """If the critic decides the same defect actually involves a
        different field, that's a meaningful change — fingerprint should
        differ. ``fields`` is part of the key by design."""
        a = _make_finding(
            evidence='FR-01 issue',
            fields=['functional_requirements'],
        )
        b = _make_finding(
            evidence='FR-01 issue',
            fields=['functional_requirements', 'out_of_scope'],
        )
        assert _fingerprint(a) != _fingerprint(b)

    def test_fingerprint_changes_when_manifest_item_id_changes(self):
        """Coverage findings carry ``manifest_item_id`` — different
        manifest items are different findings even if everything else
        looks the same."""
        a = _make_finding(
            skill='coverage',
            category='manifest_item_uncovered',
            evidence='Item I-001 has no anchor',
            fields=['functional_requirements'],
            manifest_item_id='I-001',
        )
        b = _make_finding(
            skill='coverage',
            category='manifest_item_uncovered',
            evidence='Item I-002 has no anchor',
            fields=['functional_requirements'],
            manifest_item_id='I-002',
        )
        assert _fingerprint(a) != _fingerprint(b)
