"""Lint guard: no Extraction Manifest terminology in the 5 section skills
or in the cross-cutting ``sow-shared`` references.

Approach 2 removed the Extraction Manifest object. Section skills now read
from the upstream project context (the persisted intake_summary for
Path A; source documents loaded through artifacts for the current
generation step in Path B). Any leftover mention of "Manifest" /
``manifest.extracted_items`` / ``manifest.gaps`` here is a model-facing
instruction that no longer maps to anything in state, and risks the LLM
hallucinating an input that does not exist.

The Phase 1 staged migration (PR-0 through PR-E) is complete:
``_PENDING_MIGRATION_ALLOWLIST`` is empty and the guard is absolute over
all six in-scope scopes. The allowlist mechanism is kept in place for
future staged migrations that may need to re-allowlist a scope
temporarily; an empty allowlist is the steady-state.

Out of scope (NOT scanned by this guard): ``sow-revision``,
``app/sub_agents/validation/**``, ``app/sub_agents/revision/**``,
``app/sub_agents/quality_loop/**``, the patch engine and the semantic
critic. Those still use ``manifest-derived`` / ``manifest-anchored``
terminology in a different, active sense and are intentionally deferred
to a separate cleanup front.
"""
from __future__ import annotations

from pathlib import Path

import pytest

_SKILLS_ROOT = Path(__file__).resolve().parents[4] / 'app' / 'skills'

# Scopes the guard inspects. ``sow-shared`` is treated specially: only
# its ``references/`` folder is scanned, because the cross-cutting rules
# live there.
_IN_SCOPE_SCOPES = (
    'sow-shared',
    'sow-requirements',
    'sow-delivery-plan',
    'sow-architecture',
    'sow-scope-boundaries',
    'sow-narrative',
)

# Skills still allowed to mention Manifest. Empty in steady-state — the
# Phase 1 staged migration (PR-0..PR-E) is complete and the guard is
# absolute over every scope in ``_IN_SCOPE_SCOPES``. Re-populate this
# set only if a future staged migration needs to temporarily allowlist
# a scope while it is being rewritten.
_PENDING_MIGRATION_ALLOWLIST: frozenset[str] = frozenset()

# Tokens that must NOT appear in any in-scope, non-allowlisted file.
# Substring match, case-insensitive. The bare word ``manifest`` is on
# the list deliberately: in the in-scope files the word is always the
# legacy concept, and banning the substring catches every cased and
# possessive variant ("Manifest", "Manifest's", "the Manifest") in one
# rule. The longer entries below are documentation — they remain even
# though the substring rule subsumes them, so a future reader sees
# exactly which legacy artifacts are off-limits.
_BANNED_MANIFEST_TOKENS = (
    'manifest',
    'extraction manifest',
    'extraction_manifest',
    'extracted_items',
    'manifest_item',
    'manifest_item_id',
    'manifest.gaps',
    'manifest.extracted_items',
    'append_extraction_items',
    'initialize_extraction_buffer',
    'finalize_extraction_manifest',
    'save_extraction_manifest',
    'coverage_ledger',
    'coverage receipt',
    'discovery_agent',
    'manifest_tools',
)


def _files_in_lint_scope(scope: str) -> list[Path]:
    """Files the guard inspects for the given scope.

    For ``sow-shared`` we only scan ``references/*.md`` — the cross-cutting
    rules. For section skills we scan ``SKILL.md`` plus every markdown
    file under ``references/``.
    """
    scope_dir = _SKILLS_ROOT / scope
    if scope == 'sow-shared':
        return sorted((scope_dir / 'references').glob('*.md'))
    files: list[Path] = []
    skill_md = scope_dir / 'SKILL.md'
    if skill_md.exists():
        files.append(skill_md)
    refs_dir = scope_dir / 'references'
    if refs_dir.exists():
        files.extend(sorted(refs_dir.glob('*.md')))
    return files


@pytest.mark.parametrize('scope', _IN_SCOPE_SCOPES)
def test_no_manifest_terminology_in_scope(scope: str) -> None:
    """Scan every in-scope file under ``scope`` for banned manifest tokens.

    Skipped while ``scope`` is in the staged-migration allowlist; once a
    PR migrates the scope and removes it from the allowlist, this test
    starts enforcing the ban on that scope's files.
    """
    if scope in _PENDING_MIGRATION_ALLOWLIST:
        pytest.skip(
            f'{scope!r} is allowlisted while the staged migration is in '
            f'flight. The allowlist shrinks one entry per PR (PR-A..PR-E '
            f'in the manifest-residue cleanup plan); PR-E empties it.'
        )
    files = _files_in_lint_scope(scope)
    assert files, (
        f'No lint-scope files found for {scope!r} — directory layout has '
        f'changed; update _files_in_lint_scope before relaxing this guard.'
    )
    offenders: list[str] = []
    for file in files:
        body = file.read_text(encoding='utf-8').lower()
        for banned in _BANNED_MANIFEST_TOKENS:
            if banned in body:
                offenders.append(
                    f'{file.relative_to(_SKILLS_ROOT)}: {banned!r}'
                )
    assert not offenders, (
        f'Extraction Manifest terminology leaked back into {scope!r}:\n'
        + '\n'.join(f'  - {o}' for o in offenders)
        + '\n\nApproach 2 has no Manifest object at runtime. Reference '
        'the upstream project context (the persisted intake_summary for '
        'Path A; source documents loaded through artifacts for the '
        'current generation step in Path B) instead. See '
        'app/skills/sow-shared/references/style-guide.md for the '
        'cross-cutting wording.'
    )


def test_allowlist_only_names_known_scopes() -> None:
    """Catch typos in ``_PENDING_MIGRATION_ALLOWLIST``.

    Without this check, a typo'd scope name would silently skip nothing
    (the typo never matches a real scope) AND would never trigger the
    "unmigrated scope still pending" reminder — easy to miss during the
    staged migration.
    """
    unknown = _PENDING_MIGRATION_ALLOWLIST - set(_IN_SCOPE_SCOPES)
    assert not unknown, (
        f'Allowlist names unknown scopes {sorted(unknown)!r}. The guard '
        f'only understands: {", ".join(_IN_SCOPE_SCOPES)}.'
    )
