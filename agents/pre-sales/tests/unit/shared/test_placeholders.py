"""Tests for the canonical placeholder recognition module.

The pipeline (deterministic validator, manifest prefilter, semantic
skill prompts, root prompt) all rely on these helpers staying stable
across versions. Any change to the recognised forms or the API surface
must be made here AND propagated to the consumers.
"""

from __future__ import annotations

import pytest

from app.shared.placeholders import (
    collect_approved_deferrals,
    contains_placeholder,
    is_placeholder,
    strip_placeholders,
)


# ---------------------------------------------------------------------------
# Pattern recognition
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    'value',
    [
        '[TO BE DEFINED]',
        '[to be defined]',
        '[To Be Defined]',
        '[TBD]',
        '[tbd]',
        '[A DEFINIR]',
        '[a definir]',
        '[A SER DEFINIDO]',
        '[a ser definido]',
        '[POR DEFINIR]',
        '[por definir]',
        '[INSERT customer name]',
        '[INSERT sponsor]',
        '[insert MSA reference]',
        # Internal whitespace tolerated
        '[  TO BE DEFINED  ]',
        '[ A DEFINIR ]',
        # Leading / trailing whitespace tolerated
        '  [A DEFINIR]  ',
    ],
)
def test_is_placeholder_recognises_canonical_forms(value: str):
    """Each accepted placeholder form returns True from ``is_placeholder``."""
    assert is_placeholder(value) is True


@pytest.mark.parametrize(
    'value',
    [
        '',
        '   ',
        'A DEFINIR',  # no brackets
        '[NOT A PLACEHOLDER]',
        '[Something else]',
        'TBD',  # bare, no brackets
        '[future work]',
        # Partial text — placeholder present but not the WHOLE string
        'WS-04 owned by [A DEFINIR]',
        'See [INSERT name] for details',
    ],
)
def test_is_placeholder_rejects_non_whole_string(value: str):
    """``is_placeholder`` is strict — only fields whose entire value is
    a placeholder qualify. Mixed prose returns False (use
    ``contains_placeholder`` for substring matches)."""
    assert is_placeholder(value) is False


@pytest.mark.parametrize(
    'value',
    [None, 123, [], {}, True, 0.5],
)
def test_is_placeholder_rejects_non_strings(value: object):
    """Non-string inputs never qualify — defensive against accidentally
    passing a structured field."""
    assert is_placeholder(value) is False


def test_contains_placeholder_finds_substring():
    """``contains_placeholder`` matches anywhere in the string."""
    assert contains_placeholder('WS-04 owned by [A DEFINIR]') is True
    assert contains_placeholder('See [INSERT name] later') is True
    assert contains_placeholder('plain prose') is False


def test_contains_placeholder_rejects_non_strings():
    assert contains_placeholder(None) is False
    assert contains_placeholder(42) is False


# ---------------------------------------------------------------------------
# strip_placeholders
# ---------------------------------------------------------------------------


def test_strip_placeholders_removes_marker_and_trims():
    assert strip_placeholders('[A DEFINIR]') == ''
    assert strip_placeholders('  [A DEFINIR]  ') == ''
    assert strip_placeholders('WS-04 [A DEFINIR]') == 'WS-04'
    assert strip_placeholders('Owner: [TO BE DEFINED]; backup: [TBD]') == (
        'Owner: ; backup:'
    )
    assert strip_placeholders('plain prose') == 'plain prose'


def test_strip_placeholders_preserves_useful_content():
    """A description with a placeholder in the middle still keeps the
    real prose; length-aware checks downstream get the right signal."""
    field = 'Engineer responsible for [A DEFINIR] integration work'
    stripped = strip_placeholders(field)
    assert 'Engineer responsible for' in stripped
    assert 'integration work' in stripped
    assert 'A DEFINIR' not in stripped


# ---------------------------------------------------------------------------
# collect_approved_deferrals
# ---------------------------------------------------------------------------


def test_collect_approved_deferrals_includes_to_be_defined_items():
    manifest = {
        'gaps': {
            'to_be_defined': [
                {'item': 'Sponsor names for the Customer team', 'source_gap_id': 'G-001'},
                {'item': 'Production go-live date', 'source_gap_id': 'G-002'},
            ],
            'hard_gaps': [],
        },
    }
    result = collect_approved_deferrals(manifest)
    assert result == [
        'Sponsor names for the Customer team',
        'Production go-live date',
    ]


def test_collect_approved_deferrals_includes_non_blocking_hard_gaps():
    manifest = {
        'gaps': {
            'to_be_defined': [],
            'hard_gaps': [
                {
                    'id': 'G-001',
                    'description': 'Specific governance template to adopt',
                    'blocks_sow_generation': False,
                    'user_response': '[TO BE DEFINED]',
                },
            ],
        },
    }
    result = collect_approved_deferrals(manifest)
    assert result == ['Specific governance template to adopt']


def test_collect_approved_deferrals_skips_blocking_hard_gaps():
    """Guardrail #3: a hard gap flagged as blocking is NOT an approved
    deferral — the SOW shouldn't carry a placeholder for it. The collector
    must not surface such gaps to the validator as legitimate placeholders."""
    manifest = {
        'gaps': {
            'to_be_defined': [],
            'hard_gaps': [
                {
                    'id': 'G-CRIT',
                    'description': 'Engagement shape (assessment / greenfield / migration)',
                    'blocks_sow_generation': True,
                    'user_response': '[TO BE DEFINED]',
                },
            ],
        },
    }
    assert collect_approved_deferrals(manifest) == []


def test_collect_approved_deferrals_deduplicates():
    manifest = {
        'gaps': {
            'to_be_defined': [
                {'item': 'Customer team names'},
                {'item': 'Customer team names'},  # exact duplicate
                {'item': '  Customer team names  '},  # whitespace duplicate
            ],
            'hard_gaps': [
                {
                    'description': 'Customer team names',
                    'blocks_sow_generation': False,
                },
            ],
        },
    }
    assert collect_approved_deferrals(manifest) == ['Customer team names']


def test_collect_approved_deferrals_handles_malformed_manifest():
    """Defensive: this helper runs on every validation round, must never
    raise. Various malformed manifests return an empty list silently."""
    assert collect_approved_deferrals(None) == []
    assert collect_approved_deferrals('not a dict') == []
    assert collect_approved_deferrals({}) == []
    assert collect_approved_deferrals({'gaps': None}) == []
    assert collect_approved_deferrals({'gaps': 'wrong type'}) == []
    assert collect_approved_deferrals({'gaps': {}}) == []
    assert collect_approved_deferrals(
        {'gaps': {'to_be_defined': 'not a list', 'hard_gaps': None}}
    ) == []
    # Non-dict entries inside the lists are skipped, not raised on
    assert collect_approved_deferrals(
        {
            'gaps': {
                'to_be_defined': ['not a dict', {'item': 'kept'}],
                'hard_gaps': [None, {'description': 'kept too', 'blocks_sow_generation': False}],
            },
        }
    ) == ['kept', 'kept too']
