"""Unit tests for ``app.shared._template_hygiene``.

The detectors are deterministic and pure — these tests cover both
detector-level behavior (find_unfilled_placeholders, find_decorative_characters)
and the integration into ``ContentValidator._validate_template_hygiene``.

Examples are abstracted (`[Activity 1]`, `[ROLE]`, generic emoji) so the
tests assert the failure CLASS, not the specific incident that motivated
the check. Adding a new failing pattern from a future SOW should not
require adding a test specific to that pattern.
"""
from __future__ import annotations

import pytest

from app.shared._template_hygiene import (
    HygieneFinding,
    find_decorative_characters,
    find_unfilled_placeholders,
)
from app.shared.validators import ContentValidator


class TestFindUnfilledPlaceholders:
    @pytest.mark.parametrize(
        'placeholder',
        [
            '[Activity 1]',
            '[ROLE]',
            '[DATE OF MSA]',
            '[PROJECT NAME]',
            '[Activity 1 / Workstream 1 / Phase 1]',
            '[PREENCHER aqui]',
        ],
    )
    def test_unmarked_brackets_are_findings(self, placeholder):
        findings = find_unfilled_placeholders({'field': f'Text {placeholder} more.'})
        assert len(findings) == 1
        assert findings[0].kind == 'placeholder'
        assert findings[0].sample == placeholder

    @pytest.mark.parametrize(
        'whitelisted',
        [
            '[TO BE DEFINED]',
            '[TBD]',
            '[A SER DEFINIDO]',
            '[A DEFINIR]',
            # Whitespace and casing variants are tolerated.
            '[ to be defined ]',
            '[Tbd]',
        ],
    )
    def test_whitelisted_markers_are_not_findings(self, whitelisted):
        findings = find_unfilled_placeholders({'field': f'Owner: {whitelisted}'})
        assert findings == []

    def test_no_brackets_no_findings(self):
        findings = find_unfilled_placeholders(
            {'field': 'A normal sentence with no brackets at all.'}
        )
        assert findings == []

    def test_walks_nested_dicts(self):
        data = {
            'level1': {
                'level2': {'leaf': 'Owner: [ROLE]'},
            }
        }
        findings = find_unfilled_placeholders(data)
        assert len(findings) == 1
        assert findings[0].field_path == 'level1.level2.leaf'

    def test_walks_lists_with_indices(self):
        data = {'items': ['one', '[ITEM]', 'three']}
        findings = find_unfilled_placeholders(data)
        assert len(findings) == 1
        assert findings[0].field_path == 'items.[1]'

    def test_walks_lists_of_dicts(self):
        data = {
            'roles': [
                {'role': 'Lead', 'desc': 'Concrete description.'},
                {'role': '[ROLE]', 'desc': 'Filled.'},
            ]
        }
        findings = find_unfilled_placeholders(data)
        assert len(findings) == 1
        assert findings[0].field_path == 'roles.[1].role'

    def test_multiple_placeholders_in_one_string_each_reported(self):
        data = {'field': 'Owner: [ROLE]. Approver: [APPROVER].'}
        findings = find_unfilled_placeholders(data)
        assert len(findings) == 2
        samples = {f.sample for f in findings}
        assert samples == {'[ROLE]', '[APPROVER]'}

    def test_parens_are_not_brackets(self):
        """Round parens (used for inline references like ``(FR-04)``) must not match."""
        data = {'field': 'Cloud Run hosts the API (NFR-02 satisfied).'}
        assert find_unfilled_placeholders(data) == []

    def test_mixed_intentional_and_unfilled_only_unfilled_reported(self):
        data = {
            'field_a': 'Region: [TO BE DEFINED]',  # intentional
            'field_b': 'Owner: [ROLE]',  # unfilled
        }
        findings = find_unfilled_placeholders(data)
        assert len(findings) == 1
        assert findings[0].field_path == 'field_b'


class TestFindDecorativeCharacters:
    @pytest.mark.parametrize(
        'decoration',
        [
            '⚠',      # WARNING SIGN ⚠
            '✅',      # WHITE HEAVY CHECK MARK ✅
            '\U0001f4a1',  # ELECTRIC LIGHT BULB 💡
            '\U0001f6a8',  # POLICE CARS REVOLVING LIGHT 🚨
            '─',      # BOX DRAWINGS LIGHT HORIZONTAL ─
            '█',      # FULL BLOCK █
        ],
    )
    def test_decoration_chars_are_findings(self, decoration):
        data = {'field': f'Plain text {decoration} more text.'}
        findings = find_decorative_characters(data)
        assert len(findings) == 1
        assert findings[0].kind == 'decoration'

    def test_plain_ascii_no_findings(self):
        data = {'field': 'Plain ASCII text without decoration.'}
        assert find_decorative_characters(data) == []

    def test_accented_latin_chars_are_not_decoration(self):
        """pt-BR / es text uses accented Latin chars — must not be flagged."""
        data = {
            'field_pt': 'Implementação não funcional: política de segurança.',
            'field_es': 'Operaciones de migración con configuración mínima.',
        }
        assert find_decorative_characters(data) == []

    def test_currency_symbols_are_not_flagged(self):
        """Currency glyphs that may appear in cost sections must pass."""
        data = {'field': 'Estimated cost: $50,000 / R$ 250.000 / €40k.'}
        assert find_decorative_characters(data) == []

    def test_decoration_in_list_reports_with_index(self):
        data = {'oos': ['ok', '⚠ CRITICAL: change request', 'also ok']}
        findings = find_decorative_characters(data)
        assert len(findings) == 1
        assert findings[0].field_path == 'oos.[1]'

    def test_sample_is_short_excerpt_not_full_field(self):
        long_value = 'x' * 200 + '⚠' + 'y' * 200
        findings = find_decorative_characters({'field': long_value})
        assert len(findings) == 1
        # Sample window is small (~50 chars around the offender), not full text.
        assert len(findings[0].sample) < 80

    def test_multiple_decoration_chars_one_finding_per_field(self):
        """First match per field is enough to surface the defect."""
        data = {'field': '⚠ Warning ✅ done \U0001f4a1 idea.'}
        findings = find_decorative_characters(data)
        assert len(findings) == 1


class TestWalkerEdgeCases:
    def test_empty_dict_no_findings(self):
        assert find_unfilled_placeholders({}) == []
        assert find_decorative_characters({}) == []

    def test_non_string_leaves_ignored(self):
        data = {
            'count': 42,
            'flag': True,
            'ratio': 3.14,
            'maybe': None,
            'text': 'Owner: [ROLE]',
        }
        findings = find_unfilled_placeholders(data)
        assert len(findings) == 1
        assert findings[0].field_path == 'text'

    def test_top_level_string_uses_root_path(self):
        findings = find_unfilled_placeholders('Owner: [ROLE]')
        assert len(findings) == 1
        assert findings[0].field_path == '<root>'


class TestIntegrationWithContentValidator:
    """End-to-end: hygiene findings must surface via ContentValidator.validate."""

    def test_clean_fixture_passes(self, sow_data):
        """The shared fixture has no placeholders or decoration — must pass clean."""
        result = ContentValidator().validate(sow_data)
        assert result.passed, [str(e) for e in result.errors]
        assert not any('placeholder' in e.message.lower() for e in result.errors)
        assert not any('decorative' in e.message.lower() for e in result.errors)

    def test_unfilled_placeholder_blocks_validation(self, sow_data):
        sow_data['executive_summary'] = (
            'Owner: [ROLE]. The project will deliver outcomes for the customer.'
        )
        result = ContentValidator().validate(sow_data)
        assert not result.passed
        assert any(
            'placeholder' in e.message.lower()
            and 'executive_summary' in e.field
            for e in result.errors
        )

    def test_intentional_placeholder_does_not_block(self, sow_data):
        sow_data['project_start_date'] = '[TO BE DEFINED]'
        sow_data['project_end_date'] = '[TBD]'
        result = ContentValidator().validate(sow_data)
        assert result.passed, [str(e) for e in result.errors]

    def test_emoji_in_oos_blocks_validation(self, sow_data):
        sow_data['out_of_scope'][0] = '⚠ CRITICAL: Change Request Policy'
        result = ContentValidator().validate(sow_data)
        assert not result.passed
        assert any(
            'decorative' in e.message.lower() and 'out_of_scope' in e.field
            for e in result.errors
        )

    def test_placeholder_in_nested_role_field_caught(self, sow_data):
        sow_data['partner_roles'][0]['role'] = '[ROLE NAME]'
        result = ContentValidator().validate(sow_data)
        assert not result.passed
        assert any(
            'placeholder' in e.message.lower() and 'partner_roles' in e.field
            for e in result.errors
        )

    def test_hygiene_runs_in_content_stage(self, sow_data):
        """Hygiene checks are stage-agnostic — they don't depend on architecture."""
        sow_data['executive_summary'] = '[Activity 1]'
        result = ContentValidator().validate(sow_data, stage='content')
        assert any('placeholder' in e.message.lower() for e in result.errors)
