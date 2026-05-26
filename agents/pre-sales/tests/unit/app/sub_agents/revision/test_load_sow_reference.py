"""Unit tests for ``load_sow_reference`` and its finding-map allowlist parser.

Coverage focuses on the security boundary: the tool must reject any
(skill, reference) pair that does not appear in ``finding-map.md`` (or
in the small bootstrap list the SKILL.md hardcodes as always-needed).
Drift between the map and the actual allowlist would let the revision
agent silently widen its read surface, so we also assert that every
allowlisted file exists on disk.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.sub_agents.revision import tools as revision_tools
from app.sub_agents.revision.tools import (
    _ALWAYS_AVAILABLE,
    _parse_finding_map,
    get_allowlist,
    load_sow_reference,
)


# ---------------------------------------------------------------------------
# Pure-function parser tests — no disk I/O
# ---------------------------------------------------------------------------


class TestParseFindingMap:
    def test_extracts_direct_mapping_row(self):
        text = (
            '| `contradictions` | `fr_vs_nfr` | `sow-requirements` '
            '| `references/anti-patterns.md` |\n'
        )
        allowlist = _parse_finding_map(text)

        assert allowlist == {'sow-requirements': {'references/anti-patterns.md'}}

    def test_extracts_field_dependent_row(self):
        text = (
            '| `partner_roles`, `customer_roles` | `sow-delivery-plan` '
            '| `references/roles-rules.md` |\n'
        )
        allowlist = _parse_finding_map(text)

        assert allowlist == {'sow-delivery-plan': {'references/roles-rules.md'}}

    def test_extracts_fallback_text_pair(self):
        """The 'vague_phrasing_outside_nfr' row mentions sow-shared inline."""
        text = (
            '| `semantic_quality` | `vague_phrasing_outside_nfr` | field-dependent '
            '| see field-dependent table; default `sow-shared` / '
            '`references/style-guide.md` → "General writing rules" |\n'
        )
        allowlist = _parse_finding_map(text)

        assert 'sow-shared' in allowlist
        assert 'references/style-guide.md' in allowlist['sow-shared']

    def test_multiple_references_per_skill_accumulate(self):
        text = (
            '| `contradictions` | `fr_vs_nfr` | `sow-requirements` '
            '| `references/anti-patterns.md` |\n'
            '| `semantic_quality` | `generic_capability` | `sow-requirements` '
            '| `references/fr-patterns.md` |\n'
        )
        allowlist = _parse_finding_map(text)

        assert allowlist['sow-requirements'] == {
            'references/anti-patterns.md',
            'references/fr-patterns.md',
        }

    def test_empty_text_returns_empty(self):
        assert _parse_finding_map('') == {}

    def test_lines_without_pairs_are_ignored(self):
        text = (
            '# Header\n'
            'Some prose that does not contain mappings.\n'
            '| skill | category | Target | Reference |\n'
            '|---|---|---|---|\n'
        )
        assert _parse_finding_map(text) == {}


# ---------------------------------------------------------------------------
# Allowlist derived from the real finding-map.md
# ---------------------------------------------------------------------------


class TestRealAllowlist:
    """Smoke-test the production allowlist against the on-disk skills."""

    def test_each_known_section_skill_is_present(self):
        allowlist = get_allowlist()
        for skill in (
            'sow-requirements',
            'sow-delivery-plan',
            'sow-scope-boundaries',
            'sow-architecture',
            'sow-narrative',
            'sow-shared',
        ):
            assert skill in allowlist, f'{skill} missing from allowlist'

    def test_always_available_entries_present(self):
        allowlist = get_allowlist()
        for skill, ref in _ALWAYS_AVAILABLE:
            assert ref in allowlist[skill], (
                f'{skill}/{ref} should always be available'
            )

    def test_every_allowed_path_exists_on_disk(self):
        """Detect drift: a mapping entry whose target file was renamed."""
        skills_dir = Path(__file__).resolve().parents[5] / 'app' / 'skills'
        allowlist = get_allowlist()
        missing: list[str] = []
        for skill, refs in allowlist.items():
            for ref in refs:
                full = skills_dir / skill / ref
                if not full.is_file():
                    missing.append(str(full))
        assert not missing, (
            'Allowlisted references missing on disk; finding-map.md drifted '
            f'from skill folders. Missing: {missing}'
        )


# ---------------------------------------------------------------------------
# Tool behaviour
# ---------------------------------------------------------------------------


def _ctx() -> MagicMock:
    """Minimal ToolContext stand-in — load_sow_reference only checks presence."""
    ctx = MagicMock(name='ToolContext')
    ctx.state = {}
    return ctx


class TestLoadSowReferenceHappyPath:
    async def test_returns_file_content_for_allowed_pair(self):
        # finding-map.md maps fr_vs_nfr → sow-requirements/anti-patterns.md.
        result = await load_sow_reference(
            target_skill='sow-requirements',
            reference_path='references/anti-patterns.md',
            tool_context=_ctx(),
        )
        assert result['status'] == 'success'
        assert result['data']['target_skill'] == 'sow-requirements'
        assert result['data']['reference_path'] == 'references/anti-patterns.md'
        assert len(result['data']['content']) > 0

    async def test_always_available_finding_map_loadable(self):
        result = await load_sow_reference(
            target_skill='sow-revision',
            reference_path='references/finding-map.md',
            tool_context=_ctx(),
        )
        assert result['status'] == 'success'
        assert '# Finding' in result['data']['content']


class TestLoadSowReferenceDenied:
    async def test_unknown_skill_rejected(self):
        result = await load_sow_reference(
            target_skill='not-a-skill',
            reference_path='references/anything.md',
            tool_context=_ctx(),
        )
        assert result['status'] == 'error'
        assert 'not allowed' in result['error']

    async def test_unmapped_reference_rejected(self):
        """Reference exists in the folder but is not in finding-map.md."""
        result = await load_sow_reference(
            target_skill='sow-architecture',
            reference_path='references/reasoning-rules.md',
            tool_context=_ctx(),
        )
        # reasoning-rules.md is in the skill folder but not in finding-map.md.
        # The tool must reject — opening it would silently widen the surface.
        assert result['status'] == 'error'
        assert 'not allowed' in result['error']

    async def test_traversal_path_rejected(self):
        """Defensive: rejecting unmapped paths blocks ../ traversal attempts."""
        result = await load_sow_reference(
            target_skill='sow-shared',
            reference_path='../sow-architecture/SKILL.md',
            tool_context=_ctx(),
        )
        assert result['status'] == 'error'


class TestLoadSowReferenceMissingFile:
    async def test_mapped_but_missing_file_returns_error(self, tmp_path, monkeypatch):
        """Simulate finding-map drift: allowlisted path with no file."""
        monkeypatch.setattr(revision_tools, '_SKILLS_DIR', tmp_path)
        monkeypatch.setattr(
            revision_tools,
            '_ALLOWLIST',
            {'sow-requirements': {'references/anti-patterns.md'}},
        )

        result = await load_sow_reference(
            target_skill='sow-requirements',
            reference_path='references/anti-patterns.md',
            tool_context=_ctx(),
        )
        assert result['status'] == 'error'
        assert 'does not exist' in result['error']
