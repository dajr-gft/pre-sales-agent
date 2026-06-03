"""Lint: SOW generation references must model deliverable cross-references
in the canonical id form, never the separator-less ``WS01``.

Root cause of the ``noncanonical_timeline_reference`` defect class. The
canonical deliverable id is ``Deliverable.number`` — dashed ``WS-NN`` in the
current template (see ``app/sub_agents/schemas.py`` and
``app/tools/sow/_patch_models.py``, where ``id_prefix='WS'`` yields
``WS-01``). The prompt-facing reference docs, however, modelled the
separator-less form ``WS01``. The generator copied the examples verbatim and
emitted ``(WS01)`` into ``timeline.outcomes`` while the real id was
``WS-01``; the deterministic timeline cross-reference check then re-flagged
that drift every round (smoke 2026-06-02: 5 ``noncanonical_timeline_reference``
findings authored in a single generation).

This lint kills the class at the source: no skill reference may model the
separator-less ``WSNN`` form as a valid id. It enforces the *rule* ("model
the canonical id form"), not a banned string, and is intentionally narrow:

- **Dotted production numbers** (``WS01.1``) are a real ``number`` shape and
  are exempt — a reference may legitimately show them. The rule the prompts
  state is "use the exact ``deliverables[].number``", which is ``WS-NN`` in
  the current template and would be ``WS01.1`` only if a deliverable's
  ``number`` were actually dotted.
- **Contrastive / prohibition mentions** are exempt: a line that also shows
  the canonical dashed form, or that uses disambiguation language ("never",
  "do not", "separator-less", "non-canonical", "dotted", …), is teaching the
  reader NOT to use ``WS01`` and is allowed. The goal is to block *modelling*
  ``WS01`` as valid, not to forbid *explaining* that it is invalid.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_SKILLS_DIR = Path(__file__).resolve().parents[2] / 'app' / 'skills'

# A workstream-id token: optional dash separator, digits, optional dotted
# ``.seq`` suffix.
#   WS-01   -> canonical dashed       (compliant)
#   WS01.1  -> dotted production no.  (exempt — a real ``number`` shape)
#   WS01    -> separator-less         (the regression this lint blocks)
# ``WS-NN`` placeholders (letters, not digits) do not match by design.
_WS_TOKEN = re.compile(r'\bWS(?P<dash>-?)\d+(?P<dot>\.\d+)?\b', re.IGNORECASE)

# A line is an explanatory / contrastive mention (not a modelled example)
# when it shows the canonical dashed id or uses prohibition / disambiguation
# language. Kept deliberately small; widen only with a documented reason.
_EXPLANATORY = re.compile(
    r'WS-\d'
    r"|\b(?:never|do not|don't|must not|cannot|avoid|instead of|"
    r'non-?canonical|separator-less|dotted|legacy|ambiguous|wrong)\b',
    re.IGNORECASE,
)

# Last-resort escape hatch for a reference that must show ``WS01`` for a
# reason the heuristic above cannot see (none today). Keep minimal and
# documented. Each entry is ``(path relative to _SKILLS_DIR, substring the
# offending line must contain)`` so it survives line-number churn.
_ALLOWLIST: set[tuple[str, str]] = set()


def _markdown_files() -> list[Path]:
    return sorted(_SKILLS_DIR.rglob('*.md'))


def _is_allowlisted(rel: str, line: str) -> bool:
    return any(rel == path and needle in line for path, needle in _ALLOWLIST)


def _offending_lines(path: Path) -> list[tuple[int, str]]:
    rel = path.relative_to(_SKILLS_DIR).as_posix()
    offending: list[tuple[int, str]] = []
    for lineno, line in enumerate(
        path.read_text(encoding='utf-8').splitlines(), start=1
    ):
        has_bare = any(
            not m.group('dash') and not m.group('dot')
            for m in _WS_TOKEN.finditer(line)
        )
        if not has_bare:
            continue
        if _EXPLANATORY.search(line) or _is_allowlisted(rel, line):
            continue
        offending.append((lineno, line.strip()))
    return offending


def test_skills_dir_discovered() -> None:
    """Guard against a path bug silently turning the lint into a no-op."""
    files = _markdown_files()
    assert files, f'no skill markdown found under {_SKILLS_DIR}'


@pytest.mark.parametrize(
    'md',
    _markdown_files(),
    ids=lambda p: p.relative_to(_SKILLS_DIR).as_posix(),
)
def test_no_separator_less_workstream_id(md: Path) -> None:
    offending = _offending_lines(md)
    assert not offending, (
        f'{md.relative_to(_SKILLS_DIR).as_posix()} models the separator-less '
        'workstream-id form (e.g. "WS01"). Generation references must model '
        'the canonical deliverables[].number ("WS-NN" in the current '
        'template); the separator-less form is what leaks into '
        'timeline.outcomes as a non-canonical reference. Offending lines: '
        + '; '.join(f'L{n}: {text}' for n, text in offending)
    )
