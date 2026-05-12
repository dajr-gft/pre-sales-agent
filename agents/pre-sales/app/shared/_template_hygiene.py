"""Template hygiene checks: catch content that escaped the template scaffold.

These are generic deterministic checks for two failure classes that have
been observed in shipped SOWs:

1. **Unfilled template placeholders** — the generator left bracketed
   slots literal (``[Activity 1]``, ``[ROLE]``, ``[PREENCHER ...]``)
   instead of either filling them with concrete content or marking them
   with the contract-allowed disclosure token (``[TO BE DEFINED]``).
2. **Decorative non-prose characters** — emojis, box-drawing, block
   elements present in body fields that render into a formal contractual
   document. The DAF/PSF style contract requires plain prose for any
   body text; decoration is only acceptable in metadata fields the
   document never renders.

Each check is generic by construction — no list of "known bad" SOWs,
no project-specific names. Adding a new failing pattern should NOT
require code changes here; if a new SOW ships with a placeholder
pattern, the bracket regex catches it as long as it isn't in the small
intentional whitelist.

The checks live in this module (separate from ``validators.py``) so the
core ``ContentValidator`` orchestration stays readable and the regex
patterns can be unit-tested in isolation.
"""
from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

# Bracket-content tokens the SOW contract permits as intentional disclosure
# of unknown information. Anything else inside square brackets is treated as
# an unfilled slot the generator forgot to handle. The set is intentionally
# small — broadening it weakens the check.
_INTENTIONAL_PLACEHOLDER_TOKENS: frozenset[str] = frozenset({
    'TO BE DEFINED',
    'TBD',
    'A SER DEFINIDO',
    'A DEFINIR',
})

# Captures any [...] block on a single line. Multi-line bracketed content
# (rare in SOW JSON) is intentionally not matched — the generator does not
# emit those.
_BRACKET_RE = re.compile(r'\[([^\[\]\n]+)\]')

# Unicode ranges for emoji, dingbats, symbols, and box-drawing. These have
# no place in a formal contract body.
#
# Ranges chosen to cover the common offenders without snagging accented
# Latin characters (which are valid in pt-BR / es content):
#   U+2500-U+257F  Box Drawing
#   U+2580-U+259F  Block Elements
#   U+2600-U+27BF  Misc Symbols + Dingbats (includes ⚠, ✓, ➜, ★)
#   U+1F300-U+1FAFF  Misc Symbols & Pictographs (modern emoji block)
_DECORATION_RE = re.compile(
    '['
    '\U00002500-\U0000257F'
    '\U00002580-\U0000259F'
    '\U00002600-\U000027BF'
    '\U0001F300-\U0001FAFF'
    ']'
)

# Field paths whose values are NOT rendered into the contract body and may
# legitimately contain markup or symbols. Compared against the dotted path
# produced by the walker (e.g. ``funding_type_short``). Keep tight.
_HYGIENE_EXEMPT_FIELDS: frozenset[str] = frozenset({
    # Reserved for future metadata-only fields. Empty today: every shipped
    # SOW field is body text. If a non-rendering metadata field is added,
    # opt it out here rather than weakening the regex.
})


@dataclass(frozen=True)
class HygieneFinding:
    """One hygiene defect with its location and a short evidence excerpt."""

    kind: str  # 'placeholder' | 'decoration'
    field_path: str
    sample: str


def _walk_strings(
    data: Any, path: tuple[str, ...] = ()
) -> Iterator[tuple[str, str]]:
    """Yield ``(field_path, value)`` for every string leaf reachable from data.

    Path is a dotted concatenation of dict keys and ``[index]`` segments,
    matching the convention used elsewhere in the validators for error
    messages a developer can grep for.
    """
    if isinstance(data, str):
        yield ('.'.join(path) if path else '<root>', data)
        return
    if isinstance(data, dict):
        for key, value in data.items():
            yield from _walk_strings(value, path + (str(key),))
        return
    if isinstance(data, (list, tuple)):
        for index, value in enumerate(data):
            yield from _walk_strings(value, path + (f'[{index}]',))


def _is_intentional_placeholder(inner: str) -> bool:
    """True when bracketed content is on the intentional-disclosure whitelist."""
    return inner.strip().upper() in _INTENTIONAL_PLACEHOLDER_TOKENS


def _is_path_exempt(field_path: str) -> bool:
    """True when the leading path segment is on the metadata exemption list."""
    if not field_path or field_path == '<root>':
        return False
    head = field_path.split('.', 1)[0].split('[', 1)[0]
    return head in _HYGIENE_EXEMPT_FIELDS


def find_unfilled_placeholders(data: Any) -> list[HygieneFinding]:
    """Find every ``[...]`` occurrence whose content isn't on the whitelist."""
    findings: list[HygieneFinding] = []
    for field_path, value in _walk_strings(data):
        if _is_path_exempt(field_path):
            continue
        for match in _BRACKET_RE.finditer(value):
            inner = match.group(1)
            if _is_intentional_placeholder(inner):
                continue
            findings.append(
                HygieneFinding(
                    kind='placeholder',
                    field_path=field_path,
                    sample=match.group(0),
                )
            )
    return findings


def find_decorative_characters(data: Any) -> list[HygieneFinding]:
    """Find emojis, dingbats, box-drawing, and block-element characters."""
    findings: list[HygieneFinding] = []
    for field_path, value in _walk_strings(data):
        if _is_path_exempt(field_path):
            continue
        match = _DECORATION_RE.search(value)
        if match is None:
            continue
        # Small window around the offending character so the message is
        # actionable without dumping the entire field value.
        start = max(0, match.start() - 25)
        end = min(len(value), match.end() + 25)
        excerpt = value[start:end].replace('\n', ' ').strip()
        findings.append(
            HygieneFinding(
                kind='decoration',
                field_path=field_path,
                sample=excerpt,
            )
        )
    return findings
