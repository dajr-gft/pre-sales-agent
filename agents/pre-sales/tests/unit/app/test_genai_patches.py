"""Unit tests for ``app._genai_patches``.

The patch makes :func:`google.genai._common.encode_unserializable_types`
replace the URL-safe base64 of our bypass sentinel with the literal ASCII
string the Vertex AI API expects. Tests verify three properties:

1. The patch is idempotent (apply() twice is safe).
2. The patch substitutes ONLY when the encoded value matches our sentinel
   exactly — real-looking signatures pass through untouched.
3. The substitution produces the documented bypass plaintext.
"""
from __future__ import annotations

import base64

import pytest

from app import _genai_patches


# Apply the patch once for the whole module. Idempotency is exercised in
# test_apply_is_idempotent below.
@pytest.fixture(scope='module', autouse=True)
def _apply_patch():
    _genai_patches.apply()


def _import_patched_encoder():
    """Import the patched function lazily so the fixture has run first."""
    import google.genai._common as _common_mod
    return _common_mod.encode_unserializable_types


class TestSentinelInterception:
    def test_bypass_bytes_replaced_with_plaintext(self):
        encode = _import_patched_encoder()
        out = encode({
            'thought_signature': _genai_patches.THOUGHT_SIGNATURE_BYPASS_BYTES,
            'name': 'recovery_tool',
        })
        assert (
            out['thought_signature']
            == _genai_patches.THOUGHT_SIGNATURE_BYPASS_PLAINTEXT
        )
        assert out['thought_signature'] == 'skip_thought_signature_validator'
        assert out['name'] == 'recovery_tool'

    def test_nested_dict_intercepts_too(self):
        """encode_unserializable_types recurses; nested sentinels must
        also be substituted."""
        encode = _import_patched_encoder()
        out = encode({
            'parts': [
                {
                    'function_call': {'name': 'foo', 'args': {}},
                    'thought_signature': (
                        _genai_patches.THOUGHT_SIGNATURE_BYPASS_BYTES
                    ),
                }
            ]
        })
        nested = out['parts'][0]
        assert (
            nested['thought_signature']
            == _genai_patches.THOUGHT_SIGNATURE_BYPASS_PLAINTEXT
        )


class TestRealSignaturesUntouched:
    def test_arbitrary_bytes_pass_through_as_base64(self):
        """Bytes that aren't our sentinel must still be encoded as base64
        — the patch must not touch real model-generated signatures."""
        encode = _import_patched_encoder()
        fake_sig = b'\x00\x01\x02\x03fake-encrypted-blob\xff'
        out = encode({'thought_signature': fake_sig})
        expected_b64 = base64.urlsafe_b64encode(fake_sig).decode('ascii')
        assert out['thought_signature'] == expected_b64

    def test_different_string_bytes_not_intercepted(self):
        """Bytes that look similar but aren't exactly the sentinel must
        not be substituted."""
        encode = _import_patched_encoder()
        almost = b'skip_thought_signature_validator!'  # extra char
        out = encode({'thought_signature': almost})
        assert out['thought_signature'] != 'skip_thought_signature_validator'
        assert (
            out['thought_signature']
            == base64.urlsafe_b64encode(almost).decode('ascii')
        )

    def test_unrelated_bytes_field_not_touched_by_sentinel_check(self):
        """A field named differently must NOT be substituted even if its
        value happens to encode to our sentinel base64."""
        encode = _import_patched_encoder()
        out = encode({
            'some_other_field': _genai_patches.THOUGHT_SIGNATURE_BYPASS_BYTES,
        })
        # Other fields still get base64-encoded, never plaintext-substituted.
        assert out['some_other_field'] != 'skip_thought_signature_validator'


class TestApplyIdempotency:
    def test_apply_is_idempotent(self):
        """Calling apply() repeatedly must not stack patches."""
        before = _genai_patches._PATCH_INSTALLED
        _genai_patches.apply()
        _genai_patches.apply()
        encode = _import_patched_encoder()
        # Confirm the encoder still works correctly after multiple apply()s.
        out = encode({
            'thought_signature': _genai_patches.THOUGHT_SIGNATURE_BYPASS_BYTES,
        })
        assert (
            out['thought_signature']
            == _genai_patches.THOUGHT_SIGNATURE_BYPASS_PLAINTEXT
        )
        assert before is True  # patch was already installed via fixture


class TestSentinelConsistency:
    def test_bytes_and_plaintext_match(self):
        """The bytes constant and plaintext constant must decode/encode
        to each other — a typo would silently break the bypass."""
        assert (
            _genai_patches.THOUGHT_SIGNATURE_BYPASS_BYTES.decode('ascii')
            == _genai_patches.THOUGHT_SIGNATURE_BYPASS_PLAINTEXT
        )

    def test_base64_matches_sdk_encoding(self):
        """The pre-computed _BYPASS_BASE64 must match what the SDK encoder
        produces for our sentinel — otherwise the substitution check never
        triggers."""
        expected = base64.urlsafe_b64encode(
            _genai_patches.THOUGHT_SIGNATURE_BYPASS_BYTES
        ).decode('ascii')
        assert _genai_patches._BYPASS_BASE64 == expected
