"""Anchor-id extraction helpers — shared between QualityLoopAgent and
section patch tooling.

These were originally defined inside
:mod:`app.sub_agents.quality_loop.agent` (commits A+B+C+D1 of the
quality loop telemetry work). They live in this neutral module so that
the per-section patch engine (:mod:`apply_section_patch`) can reuse the
exact same extraction logic without importing from ``quality_loop`` —
that would create an import cycle (the quality loop already imports
from ``tools.sow``).

The pattern intentionally mirrors the evidence-side anchor regex in
:data:`app.sub_agents.validation.aggregator._ANCHOR_PATTERN`. They are
duplicated rather than imported because the two uses may evolve
independently (one discriminates findings by ids quoted in evidence
prose; this walker pulls every id a structured bundle / SOW dict
actually carries). Keep both regexes in sync until that divergence
happens.
"""

from __future__ import annotations

import re
from typing import Any


ANCHOR_ID_PATTERN: re.Pattern[str] = re.compile(
    r'\b(?:FR|NFR|WS|OOS|A|I|R|T|G|P)-\d{1,4}\b',
    flags=re.IGNORECASE,
)


def extract_anchor_ids(value: Any) -> set[str]:
    """Recursively pull stable item ids from a bundle / SOW value.

    Walks every string inside lists, dicts, and tuples and matches
    against :data:`ANCHOR_ID_PATTERN`. Returns the set of UPPERCASED
    matches so casing drift (``fr-01`` vs ``FR-01``) does not produce
    spurious diffs.

    Returns an empty set for ``None``, non-collection scalars without
    any matching token, or any value the walker cannot traverse.
    """
    ids: set[str] = set()

    def _walk(node: Any) -> None:
        if isinstance(node, str):
            for match in ANCHOR_ID_PATTERN.findall(node):
                ids.add(match.upper())
        elif isinstance(node, dict):
            for child in node.values():
                _walk(child)
        elif isinstance(node, (list, tuple)):
            for child in node:
                _walk(child)

    _walk(value)
    return ids


def diff_anchor_ids(
    before: set[str], after: set[str],
) -> tuple[set[str], set[str]]:
    """Return ``(dropped, added)`` between two anchor-id sets.

    Convenience wrapper used by the section patch engine to detect
    anchor drops in :class:`UpdateItemOp` / :class:`UpdateFieldOp`
    where text-level edits may silently remove an id referenced by
    the manifest. The caller decides how to react (warning vs error).
    """
    dropped = before - after
    added = after - before
    return dropped, added
