"""Unit tests for ``app.tools.sow._sow_helpers``."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.tools.sow._sow_helpers import (
    QUALITY_GATES,
    load_logo,
    sow_data_hash,
    sow_data_preview,
    validate_quality_gates,
)


class TestQualityGates:
    def test_all_gates_pass_on_complete_payload(self, sow_data):
        assert validate_quality_gates(sow_data) == []

    @pytest.mark.parametrize(
        'field,label',
        [
            ('out_of_scope', 'Out-of-Scope'),
            ('assumptions', 'Assumptions'),
            ('deliverables', 'Deliverables'),
            ('functional_requirements', 'Functional Requirements'),
            ('non_functional_requirements', 'Non-Functional Requirements'),
            ('success_criteria', 'Success Criteria'),
        ],
    )
    def test_below_threshold_for_each_gate(self, sow_data, field, label):
        sow_data[field] = []
        errors = validate_quality_gates(sow_data)
        assert any(label in e for e in errors), errors

    def test_gate_thresholds_match_constants(self, sow_data):
        """Gate limits live in QUALITY_GATES — this test pins that map."""
        assert QUALITY_GATES['out_of_scope'] == ('Out-of-Scope', 20)
        assert QUALITY_GATES['deliverables'] == ('Deliverables', 10)
        assert QUALITY_GATES['functional_requirements'] == (
            'Functional Requirements',
            10,
        )
        assert QUALITY_GATES['success_criteria'] == ('Success Criteria', 5)

    def test_risks_below_3_when_present(self, sow_data):
        sow_data['risks'] = [
            {'description': 'x', 'mitigation': 'y'},
            {'description': 'a', 'mitigation': 'b'},
        ]
        errors = validate_quality_gates(sow_data)
        assert any('Risks' in e for e in errors)

    def test_risks_empty_ok(self, sow_data):
        sow_data['risks'] = []
        errors = validate_quality_gates(sow_data)
        assert not any('Risks' in e for e in errors)

    def test_risks_missing_key_ok(self, sow_data):
        sow_data.pop('risks', None)
        errors = validate_quality_gates(sow_data)
        assert not any('Risks' in e for e in errors)

    def test_psf_without_consumption_plan_fails(self, sow_data_psf):
        sow_data_psf.pop('consumption_plan')
        errors = validate_quality_gates(sow_data_psf)
        assert any('Consumption Plan' in e for e in errors)

    def test_psf_with_consumption_plan_table_ok(self, sow_data_psf):
        """Accept either consumption_plan or consumption_plan_table."""
        sow_data_psf['consumption_plan_table'] = sow_data_psf.pop(
            'consumption_plan'
        )
        errors = validate_quality_gates(sow_data_psf)
        assert not any('Consumption Plan' in e for e in errors)

    def test_daf_without_plan_ok(self, sow_data):
        errors = validate_quality_gates(sow_data)
        assert not any('Consumption Plan' in e for e in errors)


class TestSowDataHash:
    def test_dict_and_json_produce_same_hash(self, sow_data):
        h1 = sow_data_hash(sow_data)
        h2 = sow_data_hash(json.dumps(sow_data))
        assert h1 == h2

    def test_key_order_insensitive(self):
        a = {'a': 1, 'b': 2, 'c': [1, 2, 3]}
        b = {'c': [1, 2, 3], 'a': 1, 'b': 2}
        assert sow_data_hash(a) == sow_data_hash(b)

    def test_returns_12_char_hex(self, sow_data):
        h = sow_data_hash(sow_data)
        assert len(h) == 12
        int(h, 16)  # valid hex

    def test_different_content_different_hash(self):
        a = sow_data_hash({'x': 1})
        b = sow_data_hash({'x': 2})
        assert a != b

    def test_unhashable_on_error(self):
        """Circular refs in a dict raise → sentinel returned, never raises."""

        class Unserializable:
            pass

        assert sow_data_hash({'x': Unserializable()}) == 'unhashable'

    def test_unhashable_on_invalid_json_string(self):
        assert sow_data_hash('{not-valid-json') == 'unhashable'


class TestSowDataPreview:
    def test_truncates_long_strings(self):
        data = {'note': 'x' * 500}
        out = sow_data_preview(data)
        assert '…' in out

    def test_lists_reduced_to_count_and_first(self):
        data = {'items': [{'name': 'first'}, {'name': 'second'}]}
        out = sow_data_preview(data)
        parsed = json.loads(out)
        assert parsed['items']['_count'] == 2
        assert parsed['items']['_first']['name'] == 'first'

    def test_empty_list_renders_count_only(self):
        out = sow_data_preview({'items': []})
        parsed = json.loads(out)
        assert parsed == {'items': {'_count': 0}}

    def test_dict_renders_keys_only(self):
        out = sow_data_preview({'nested': {'a': 1, 'b': 2, 'c': 3}})
        parsed = json.loads(out)
        assert set(parsed['nested']['_keys']) == {'a', 'b', 'c'}

    def test_accepts_json_string_input(self):
        s = json.dumps({'k': 'v'})
        out = sow_data_preview(s)
        assert 'k' in out

    def test_non_dict_top_level(self):
        assert 'not_a_dict' in sow_data_preview('[1, 2, 3]')

    def test_max_chars_applied(self):
        data = {'bigfield': 'x' * 5000}
        out = sow_data_preview(data, max_chars=100)
        assert len(out) <= 100 + len('…<truncated>')
        assert out.endswith('…<truncated>')

    def test_failure_returns_sentinel(self):
        with patch(
            'app.tools.sow._sow_helpers._json.dumps',
            side_effect=RuntimeError('boom'),
        ):
            out = sow_data_preview({'x': 1})
        assert out.startswith('<preview_failed')

    def test_nested_list_item_is_sampled(self):
        data = {'items': ['first item text', 'second']}
        out = sow_data_preview(data)
        parsed = json.loads(out)
        assert parsed['items']['_first'].startswith('first item')


class TestLoadLogo:
    """`load_logo` must never crash the caller — it returns a placeholder on error."""

    def test_missing_file_returns_placeholder(self):
        doc = MagicMock()
        result = load_logo(doc, Path('/does/not/exist.png'), 'partner', 41)
        assert result == '[Partner Logo]'

    def test_none_path_returns_placeholder(self):
        doc = MagicMock()
        result = load_logo(doc, None, 'customer', 43)  # type: ignore[arg-type]
        assert result == '[Customer Logo]'

    def test_unsupported_extension_returns_placeholder(self, tmp_path):
        bad = tmp_path / 'logo.bmp'
        bad.write_bytes(b'\x00')
        doc = MagicMock()
        result = load_logo(doc, bad, 'partner', 41)
        assert result == '[Partner Logo]'

    @pytest.mark.parametrize(
        'ext', ['.png', '.jpg', '.jpeg', '.svg', '.gif', '.webp']
    )
    def test_supported_extensions_try_to_load(self, tmp_path, ext):
        img = tmp_path / f'logo{ext}'
        img.write_bytes(b'\x00')
        doc = MagicMock()
        with patch(
            'app.tools.sow._sow_helpers.InlineImage'
        ) as InlineImageMock:
            InlineImageMock.return_value = 'an_image'
            result = load_logo(doc, img, 'customer', 43)
        assert result == 'an_image'

    def test_inline_image_failure_returns_placeholder(self, tmp_path):
        img = tmp_path / 'logo.png'
        img.write_bytes(b'\x00')
        doc = MagicMock()
        with patch(
            'app.tools.sow._sow_helpers.InlineImage',
            side_effect=RuntimeError('corrupt'),
        ):
            result = load_logo(doc, img, 'partner', 41)
        assert result == '[Partner Logo]'

    def test_uppercase_extension_still_accepted(self, tmp_path):
        img = tmp_path / 'logo.PNG'
        img.write_bytes(b'\x00')
        doc = MagicMock()
        with patch(
            'app.tools.sow._sow_helpers.InlineImage', return_value='ok'
        ):
            result = load_logo(doc, img, 'partner', 41)
        assert result == 'ok'
