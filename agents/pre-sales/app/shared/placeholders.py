"""Canonical placeholder recognition shared across the validation pipeline.

A "placeholder" is a SOW field whose literal value is a sanctioned
deferral marker such as ``[TO BE DEFINED]`` or ``[A DEFINIR]``. These
markers appear when:

- The user explicitly approved deferring the value during a review
  gate (e.g., "leave the sponsor names as ``[A DEFINIR]`` and we'll
  fill them later");
- A standard contractual field (MSA reference, governing law,
  customer counterpart) is documented as filled in a subsequent
  signature workflow.

The validation pipeline must NOT treat these markers as defects. The
deterministic ``ContentValidator`` would otherwise complain about
description length (``"[A DEFINIR]"`` is 11 characters), and the
semantic skills would otherwise emit findings asking the user to fill
the field that they already approved deferring.

Whether a placeholder is *approved* (vs an unsanctioned fabrication
hiding a real coverage gap) is decided by the validation skills via the
``_RESOLUTION_MODE_GUIDE`` "Approved placeholders" block (contractual
fill-on-signature fields are sanctioned; everything else is an
auto_fixable finding). The helpers in this module are pattern-matching
only; they do not by themselves authorize a placeholder.

Why a module
------------
Centralising the pattern in one file avoids a drift problem: each layer
(deterministic validator, skill prompt, root prompt) was recognising
placeholders differently or — worse — not at all. The regex below is
the single source of truth.
"""

from __future__ import annotations

import re

# Recognised placeholder forms. English ``[TO BE DEFINED]`` and
# Portuguese ``[A DEFINIR]`` / ``[A SER DEFINIDO]`` / ``[POR DEFINIR]``
# are the two we ship today; ``[TBD]`` and ``[INSERT ...]`` are accepted
# because consultancy SOWs occasionally use them. Anything inside the
# brackets matching these tokens (case-insensitive, with internal
# whitespace tolerated) counts. New languages or markers should be added
# here and only here.
_PLACEHOLDER_TOKEN_PATTERN = (
    r'(?:'
    r'TO\s+BE\s+DEFINED'
    r'|TBD'
    r'|A\s+DEFINIR'
    r'|A\s+SER\s+DEFINIDO'
    r'|POR\s+DEFINIR'
    r'|INSERT\s+[^\]]+'
    r')'
)

# Matches a placeholder *anywhere* inside a string. Useful for fields
# that mix prose with a placeholder, e.g. ``"WS-04 led by [A DEFINIR]"``.
PLACEHOLDER_PATTERN = re.compile(
    r'\[\s*' + _PLACEHOLDER_TOKEN_PATTERN + r'\s*\]',
    re.IGNORECASE,
)

# Matches a string whose ONLY substantive content is a placeholder
# (optionally wrapped in surrounding whitespace). Useful for short
# fields where the whole value is the deferral marker, e.g. a
# ``role`` description of ``"[A DEFINIR]"``.
_WHOLE_STRING_PLACEHOLDER_PATTERN = re.compile(
    r'^\s*\[\s*' + _PLACEHOLDER_TOKEN_PATTERN + r'\s*\]\s*$',
    re.IGNORECASE,
)


def is_placeholder(value: object) -> bool:
    """Return True when ``value`` is a string whose entire content is a
    placeholder marker (ignoring leading/trailing whitespace).

    Non-string values return False — this helper is only meaningful for
    text fields the SOW carries. Empty strings return False as well: an
    empty field is a defect, not an intentional deferral.
    """
    if not isinstance(value, str):
        return False
    if not value.strip():
        return False
    return bool(_WHOLE_STRING_PLACEHOLDER_PATTERN.match(value))


def contains_placeholder(value: object) -> bool:
    """Return True when ``value`` is a string containing at least one
    placeholder marker, even amid other prose."""
    if not isinstance(value, str):
        return False
    return bool(PLACEHOLDER_PATTERN.search(value))


def strip_placeholders(value: str) -> str:
    """Return ``value`` with every placeholder marker removed.

    Useful for length-aware checks that would otherwise see
    ``"WS-04 [A DEFINIR]"`` (17 chars) as "long enough" or
    ``"[A DEFINIR]"`` (11 chars) as "too short" when the user has
    explicitly approved the deferral. After stripping the placeholder
    the remaining content reflects the real authored text.
    """
    return PLACEHOLDER_PATTERN.sub('', value).strip()
