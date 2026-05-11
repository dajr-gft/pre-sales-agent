"""SDK patches for google.genai.

Currently a single patch: lets ``Part.thought_signature`` carry the Vertex AI
documented bypass token ``skip_thought_signature_validator`` so that
synthetic function-call turns injected from an ``after_model_callback``
(see :mod:`app.callbacks.empty_response_guard`) are accepted by the API.

Background
----------
Gemini 3.x enforces strict validation: every ``functionCall`` part replayed
in the conversation history must carry a ``thought_signature`` proving it
originated from the model. Function calls SYNTHESIZED on the client side
(e.g. the recovery loop) have no such signature. Google documents
``skip_thought_signature_validator`` as a last-resort bypass, but the SDK
serialization step base64-encodes any ``bytes`` value and the Vertex AI
backend only recognizes the LITERAL ASCII string. The result: setting
``Part.thought_signature = b"skip_thought_signature_validator"`` reaches
the wire as base64 and the API rejects with HTTP 400.

This patch wraps :func:`google.genai._common.encode_unserializable_types`
so that the specific base64 form of our sentinel — and only that form —
is replaced with the plaintext bypass right before serialization. Real
signatures returned by the model are opaque, encrypted blobs; collision
with our sentinel is astronomically improbable. So this is surgical:
non-recovery turns are untouched.

References
----------
- Vertex AI docs: https://docs.cloud.google.com/vertex-ai/generative-ai/docs/thought-signatures
- Known SDK base64 bug: https://github.com/langchain-ai/langchain-google/issues/1570
"""
from __future__ import annotations

import base64
from typing import Any

import structlog

logger = structlog.get_logger()

# The literal ASCII string the Vertex AI backend recognizes as the bypass.
THOUGHT_SIGNATURE_BYPASS_PLAINTEXT = 'skip_thought_signature_validator'

# Same string as bytes — what callers should set on
# ``Part.thought_signature`` to opt into the bypass.
THOUGHT_SIGNATURE_BYPASS_BYTES = THOUGHT_SIGNATURE_BYPASS_PLAINTEXT.encode('ascii')

# Pre-computed base64 form produced by the SDK's encoder (URL-safe + padding).
# We compare against this exact string to decide whether to substitute.
_BYPASS_BASE64 = base64.urlsafe_b64encode(THOUGHT_SIGNATURE_BYPASS_BYTES).decode(
    'ascii'
)

# Idempotency guard — apply() may be called more than once (e.g. tests).
_PATCH_INSTALLED = False


def apply() -> None:
    """Install the thought_signature bypass patch on google.genai._common.

    Idempotent. Safe to call multiple times. Logs at INFO on first install
    and at DEBUG on subsequent no-op calls. Logs at WARNING every time the
    patch intercepts a sentinel — useful telemetry to track how often the
    recovery loop fires in production.
    """
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        logger.debug('genai_thought_signature_patch_already_installed')
        return

    import google.genai._common as _common_mod  # local import: defer side effects

    original_encode = _common_mod.encode_unserializable_types

    def _patched_encode(data: Any) -> Any:
        result = original_encode(data)
        if (
            isinstance(result, dict)
            and result.get('thought_signature') == _BYPASS_BASE64
        ):
            logger.warning(
                'thought_signature_bypass_intercepted',
                reason=(
                    'synthetic function_call carrying bypass sentinel — '
                    'recovery loop or other client-injected turn'
                ),
            )
            result['thought_signature'] = THOUGHT_SIGNATURE_BYPASS_PLAINTEXT
        return result

    _common_mod.encode_unserializable_types = _patched_encode
    _PATCH_INSTALLED = True
    logger.info(
        'genai_thought_signature_patch_installed',
        bypass_base64=_BYPASS_BASE64,
    )
