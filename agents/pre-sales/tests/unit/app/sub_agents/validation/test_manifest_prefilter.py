"""Tests for ``ManifestPrefilterAgent`` and its helpers.

The prefilter is the only Python step in the validation pipeline that
prunes the manifest before the LLM skills run. A bug in
``_is_intentionally_deferred`` (the helper that filters items the
discovery agent already flagged as deferred) shipped silently because
the old check compared a non-existent ``item_id`` field — the function
always returned False. Coverage findings were therefore emitted for
items the user had explicitly approved deferring to ``[A DEFINIR]``,
forcing yet another over-escalation round.

These tests cover the fixed semantics: fuzzy-match the extracted
item's ``value`` / ``value_detail`` against the gap's human-readable
description. Both directions (description in item, item in description)
are tried so the prefilter survives the normal divergence between an
extractor's canonical short value and a discovery interviewer's
sentence.
"""

from __future__ import annotations

from app.sub_agents.validation.manifest_prefilter import (
    _is_intentionally_deferred,
)


# ---------------------------------------------------------------------------
# Negative cases — empty / malformed inputs never raise.
# ---------------------------------------------------------------------------


def test_empty_manifest_returns_false():
    assert _is_intentionally_deferred({'value': 'foo'}, {}) is False
    assert _is_intentionally_deferred({'value': 'foo'}, {'gaps': None}) is False
    assert _is_intentionally_deferred(
        {'value': 'foo'}, {'gaps': 'not a dict'}
    ) is False


def test_item_without_value_returns_false():
    """If the item has no extractable value, there's nothing to match
    against the gap descriptions — return False rather than raising."""
    manifest = {
        'gaps': {
            'hard_gaps': [{'description': 'Anything'}],
        },
    }
    assert _is_intentionally_deferred({}, manifest) is False
    assert _is_intentionally_deferred(
        {'value': '', 'value_detail': '   '}, manifest
    ) is False


# ---------------------------------------------------------------------------
# Hard gaps — both blocking and non-blocking pull the item out so the
# coverage skill doesn't re-emit a finding the discovery agent already
# escalated to the user.
# ---------------------------------------------------------------------------


def test_blocking_hard_gap_matches_when_description_contains_item_value():
    item = {'item_id': 'I-001', 'value': 'Customer SLA target'}
    manifest = {
        'gaps': {
            'hard_gaps': [
                {
                    'id': 'G-001',
                    'description': 'Customer SLA target for production uptime',
                    'blocks_sow_generation': True,
                },
            ],
        },
    }
    assert _is_intentionally_deferred(item, manifest) is True


def test_non_blocking_hard_gap_also_pulls_item_out():
    """A non-blocking hard_gap means the SOW will carry a placeholder
    for that field. Coverage shouldn't flag it as uncovered — the
    placeholder IS the (sanctioned) anchor."""
    item = {'item_id': 'I-002', 'value': 'Specific governance template'}
    manifest = {
        'gaps': {
            'hard_gaps': [
                {
                    'id': 'G-002',
                    'description': 'Specific governance template to adopt',
                    'blocks_sow_generation': False,
                    'user_response': '[A DEFINIR]',
                },
            ],
        },
    }
    assert _is_intentionally_deferred(item, manifest) is True


def test_to_be_defined_entry_matches_via_item_field():
    item = {'item_id': 'I-003', 'value': 'Sponsor names'}
    manifest = {
        'gaps': {
            'to_be_defined': [
                {
                    'item': 'Sponsor names for the Customer team',
                    'source_gap_id': 'G-003',
                },
            ],
        },
    }
    assert _is_intentionally_deferred(item, manifest) is True


# ---------------------------------------------------------------------------
# Fuzzy matching — both directions tried so the prefilter is tolerant of
# the standard rephrasing between a discovery interview sentence and an
# extractor's canonical short value.
# ---------------------------------------------------------------------------


def test_match_when_item_value_contains_description():
    """Item carries a longer phrasing; gap description is a shorter
    canonical form."""
    item = {
        'item_id': 'I-004',
        'value': 'Production deployment region for the EU workload',
    }
    manifest = {
        'gaps': {
            'hard_gaps': [
                {
                    'description': 'Production deployment region',
                    'blocks_sow_generation': False,
                },
            ],
        },
    }
    assert _is_intentionally_deferred(item, manifest) is True


def test_match_via_value_detail_when_value_does_not_match():
    """The match falls back to ``value_detail`` when ``value`` is too
    terse to overlap with the gap description."""
    item = {
        'item_id': 'I-005',
        'value': 'Region',
        'value_detail': 'EU region for production deployment',
    }
    manifest = {
        'gaps': {
            'hard_gaps': [
                {
                    'description': 'EU region for production deployment',
                    'blocks_sow_generation': False,
                },
            ],
        },
    }
    assert _is_intentionally_deferred(item, manifest) is True


def test_no_match_when_item_is_unrelated_to_any_gap():
    item = {'item_id': 'I-006', 'value': 'BigQuery dataset partitioning'}
    manifest = {
        'gaps': {
            'hard_gaps': [
                {
                    'description': 'Specific governance template',
                    'blocks_sow_generation': False,
                },
            ],
            'to_be_defined': [
                {'item': 'Sponsor names'},
            ],
        },
    }
    assert _is_intentionally_deferred(item, manifest) is False


# ---------------------------------------------------------------------------
# Defensive — gaps with malformed entries don't crash the helper.
# ---------------------------------------------------------------------------


def test_skips_non_dict_gap_entries():
    item = {'item_id': 'I-007', 'value': 'foo'}
    manifest = {
        'gaps': {
            'hard_gaps': [None, 'not a dict', {'description': 'foo bar', 'blocks_sow_generation': False}],
            'to_be_defined': ['bad', None, {'item': 'unrelated'}],
        },
    }
    # Match should still succeed against the well-formed entry.
    assert _is_intentionally_deferred(item, manifest) is True


def test_empty_gap_descriptions_do_not_match_any_item():
    """A gap entry with an empty description must not produce a false
    positive — early-return on empty haystacks is the guard."""
    item = {'item_id': 'I-008', 'value': 'foo'}
    manifest = {
        'gaps': {
            'hard_gaps': [
                {'description': '', 'blocks_sow_generation': False},
                {'description': '   ', 'blocks_sow_generation': False},
            ],
            'to_be_defined': [
                {'item': ''},
            ],
        },
    }
    assert _is_intentionally_deferred(item, manifest) is False
