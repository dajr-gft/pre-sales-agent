"""Tests for the canonical placeholder recognition module.

The pipeline (deterministic validator, semantic skill prompts, root
prompt) all rely on these helpers staying stable across versions. Any
change to the recognised forms or the API surface must be made here AND
propagated to the consumers.
"""

from __future__ import annotations

import pytest

from app.shared.placeholders import (
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
