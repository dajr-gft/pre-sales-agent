"""Tools used by the revision sub-agent.

``load_sow_reference`` is an allowlist-protected wrapper around reading
``app/skills/<target_skill>/<reference_path>`` from disk. The allowlist
is derived at import time from ``sow-revision/references/finding-map.md``,
which is the single source of truth for which section reference maps
to which finding category. This means there is no second list of paths
to keep in sync.

The revision agent reads SKILL.md content via its own
``SectionResourcesToolset`` (which exposes ``load_skill_resource``); the
extra tool here exists to give it controlled cross-section reach
without handing it a full ``SkillToolset`` for every other section.
"""

# NOTE: deliberately NOT using ``from __future__ import annotations``.
# See the equivalent comment in ``app/tools/sow/assemble_payload.py`` —
# ADK introspects @safe_tool-wrapped tools via ``typing.get_type_hints``,
# and string annotations resolved against the wrapper's globals fail
# because ``functools.wraps`` cannot copy ``__globals__``.

import re
from pathlib import Path
from typing import Any

import structlog
from google.adk.tools import ToolContext

from ...shared.errors import safe_tool
from ...shared.types import ToolError, ToolSuccess

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Allowlist parsed from finding-map.md at import time
# ---------------------------------------------------------------------------


_SKILLS_DIR = Path(__file__).parents[2] / 'skills'
_FINDING_MAP_PATH = _SKILLS_DIR / 'sow-revision' / 'references' / 'finding-map.md'

# Pattern catches `sow-<name>` paired with `references/<file>.md` on the
# same line. The intervening characters cross at least one pipe-separator
# in the canonical table layout, so we allow anything except newlines and
# backticks between the two captures. Anchored to a single line via the
# newline exclusion so we never bridge unrelated rows.
_PAIR_PATTERN = re.compile(
    r'`(sow-[a-z][a-z0-9-]*)`[^`\n]*?`(references/[^`\n]+\.md)`',
)

# References the revision SKILL.md says to load up front, regardless of
# which findings appear. Adding them here means the agent's prompt can
# call ``load_sow_reference`` for these without the allowlist rejecting
# the request as "not mapped to any finding category".
_ALWAYS_AVAILABLE: tuple[tuple[str, str], ...] = (
    ('sow-shared', 'references/id-stability-rules.md'),
    ('sow-shared', 'references/style-guide.md'),
    ('sow-shared', 'references/language-rules.md'),
    ('sow-revision', 'references/finding-map.md'),
)


def _parse_finding_map(text: str) -> dict[str, set[str]]:
    """Extract ``(target_skill, reference_path)`` pairs from finding-map.md.

    Pure function so unit tests can exercise it on synthetic markdown
    fragments without touching the disk.
    """
    allowlist: dict[str, set[str]] = {}
    for match in _PAIR_PATTERN.finditer(text):
        target_skill, ref_path = match.group(1), match.group(2)
        allowlist.setdefault(target_skill, set()).add(ref_path)
    return allowlist


def _build_default_allowlist() -> dict[str, set[str]]:
    if not _FINDING_MAP_PATH.is_file():
        raise FileNotFoundError(
            f'finding-map.md not found at {_FINDING_MAP_PATH}; '
            'revision agent cannot operate without it.',
        )
    text = _FINDING_MAP_PATH.read_text(encoding='utf-8')
    allowlist = _parse_finding_map(text)
    for skill, ref in _ALWAYS_AVAILABLE:
        allowlist.setdefault(skill, set()).add(ref)
    return allowlist


_ALLOWLIST: dict[str, set[str]] = _build_default_allowlist()


def get_allowlist() -> dict[str, frozenset[str]]:
    """Return an immutable snapshot of the allowlist (for tests / linting)."""
    return {k: frozenset(v) for k, v in _ALLOWLIST.items()}


# ---------------------------------------------------------------------------
# The tool itself
# ---------------------------------------------------------------------------


@safe_tool
async def load_sow_reference(
    target_skill: str,
    reference_path: str,
    tool_context: ToolContext = None,
) -> dict[str, Any]:
    """Read a section reference file mapped from a validation finding.

    The pair (target_skill, reference_path) MUST appear in the finding-map
    or in the always-available bootstrap list. Anything else is rejected
    so the revision agent cannot silently widen its reach.

    Args:
        target_skill: Section skill folder name (e.g. ``"sow-delivery-plan"``).
        reference_path: Reference file relative to the skill folder
            (e.g. ``"references/timeline-rules.md"``).

    Returns:
        ``ToolSuccess`` with ``data={'target_skill', 'reference_path',
        'content'}`` on success; ``ToolError`` with the allowed paths in
        ``suggestion`` on rejection or ``status='error'`` if the file
        is mapped but missing on disk.
    """
    allowed = _ALLOWLIST.get(target_skill, frozenset())
    if reference_path not in allowed:
        logger.warning(
            'load_sow_reference_denied',
            target_skill=target_skill,
            reference_path=reference_path,
            allowed_for_skill=sorted(allowed),
        )
        return ToolError(
            status='error',
            error=(
                f"Reference '{reference_path}' is not allowed for "
                f"'{target_skill}'."
            ),
            retryable=False,
            tool='load_sow_reference',
            suggestion=(
                'Look up the correct path in '
                'sow-revision/references/finding-map.md. Allowed paths for '
                f"'{target_skill}': {sorted(allowed) or 'none'}."
            ),
        )

    full_path = _SKILLS_DIR / target_skill / reference_path
    if not full_path.is_file():
        return ToolError(
            status='error',
            error=(
                f"Reference '{reference_path}' is mapped for "
                f"'{target_skill}' but the file does not exist at "
                f'{full_path}.'
            ),
            retryable=False,
            tool='load_sow_reference',
            suggestion=(
                'finding-map.md is out of sync with the skill folder; '
                'fix the mapping or restore the file.'
            ),
        )

    content = full_path.read_text(encoding='utf-8')
    logger.info(
        'load_sow_reference',
        target_skill=target_skill,
        reference_path=reference_path,
        content_chars=len(content),
    )
    return ToolSuccess(
        status='success',
        data={
            'target_skill': target_skill,
            'reference_path': reference_path,
            'content': content,
        },
    )
