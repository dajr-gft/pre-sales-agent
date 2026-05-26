"""Tests for ``app.tools.sow._anchor_utils``.

The walker is shared between ``QualityLoopAgent`` (D1 anchor-drop
telemetry) and the section patch engine (anchor-drop warning in
update_item / update_field). Pin the pattern + extraction so a
silent regex change here is caught by tests rather than by quiet
diagnostic drift in production.
"""

from __future__ import annotations

from app.tools.sow._anchor_utils import (
    ANCHOR_ID_PATTERN,
    diff_anchor_ids,
    extract_anchor_ids,
)


class TestExtractAnchorIds:
    def test_pulls_ids_from_plain_string(self):
        assert extract_anchor_ids('Refers to FR-03 and WS-12 only.') == {
            'FR-03', 'WS-12',
        }

    def test_uppercases_matches(self):
        assert extract_anchor_ids('see fr-01') == {'FR-01'}

    def test_walks_dict_values_recursively(self):
        data = {
            'top': 'Touches FR-01',
            'nested': {'inner': 'And WS-02 too'},
        }
        assert extract_anchor_ids(data) == {'FR-01', 'WS-02'}

    def test_walks_lists_and_tuples(self):
        data = ['mentions A-01', ('also', 'I-10')]
        assert extract_anchor_ids(data) == {'A-01', 'I-10'}

    def test_empty_set_when_no_matches(self):
        assert extract_anchor_ids('no anchors here') == set()

    def test_none_returns_empty(self):
        assert extract_anchor_ids(None) == set()

    def test_pattern_covers_all_known_prefixes(self):
        """The pattern was extracted from quality_loop verbatim — the
        list of supported prefixes is part of the contract with
        downstream consumers (the critic, the manifest)."""
        for prefix in ('FR', 'NFR', 'WS', 'OOS', 'A', 'I', 'R', 'T', 'G', 'P'):
            assert ANCHOR_ID_PATTERN.search(f'{prefix}-01'), prefix

    def test_pattern_covers_dotted_deliverable_format(self):
        """Deliverables are emitted as ``WS<phase>.<seq>`` (``WS01.1``,
        ``WS03.6``) — the dashed form ``WS-NN`` is the test-fixture
        legacy. The regex must accept BOTH so production removes are
        not silent in anchor-drop telemetry.
        """
        assert extract_anchor_ids('Deliverable WS01.1 covers it.') == {'WS01.1'}
        assert extract_anchor_ids('See WS03.6 and WS06.4.') == {'WS03.6', 'WS06.4'}
        # Case-insensitive in source, uppercased in output.
        assert extract_anchor_ids('ws02.3 mentioned') == {'WS02.3'}

    def test_pattern_does_not_match_unrelated_dotted_tokens(self):
        """Defensive: arbitrary ``X.Y`` numerics must not match. Only
        the documented anchor prefixes do."""
        assert extract_anchor_ids('Version 1.5 of GCP product 3.14.') == set()
        # AES-256 inside FR-03 description shouldn't be confused for an anchor.
        assert extract_anchor_ids('AES-256 encryption') == set()


class TestDiffAnchorIds:
    def test_dropped_and_added_are_disjoint_per_side(self):
        before = {'FR-01', 'FR-02', 'WS-03'}
        after = {'FR-01', 'WS-04', 'WS-05'}
        dropped, added = diff_anchor_ids(before, after)
        assert dropped == {'FR-02', 'WS-03'}
        assert added == {'WS-04', 'WS-05'}

    def test_identical_sets_return_empty(self):
        dropped, added = diff_anchor_ids({'FR-01'}, {'FR-01'})
        assert dropped == set()
        assert added == set()
