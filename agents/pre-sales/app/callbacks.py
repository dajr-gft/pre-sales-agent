"""Agent callbacks for guardrails and observability.

before_tool_callback: runs before every tool call — validates inputs,
    blocks unsafe operations, logs invocations.
after_tool_callback: runs after every tool call — logs results,
    tracks tool usage in session state for downstream decisions.
empty_response_guard: after_model_callback that detects terminal empty
    turns (no text and no function call) and re-enters the agent loop
    via the internal _request_continuation tool, capped at
    _MAX_EMPTY_RETRIES consecutive attempts.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import structlog
from google.adk.agents.callback_context import CallbackContext
from google.adk.models import LlmResponse
from google.adk.tools import ToolContext
from google.genai import Client, types

from ._genai_patches import THOUGHT_SIGNATURE_BYPASS_BYTES
from .config import config
from .tools.sow.confirm_phase import is_architecture_review_approved

logger = structlog.get_logger()

# Maximum JSON input size (chars) to prevent oversized payloads
_MAX_SOW_DATA_CHARS = 500_000

# Tools whose execution requires the user to have approved the
# Architecture Review (Phase 2 Step 4). validate_sow_content is gated
# only when stage='full' (or when stage is omitted — 'full' is the
# default); stage='content' is used in Phase 2 Step 1.5 BEFORE
# architecture exists and must remain unblocked.
_ARCH_REVIEW_GATED_TOOLS = frozenset(
    {'validate_sow_content', 'generate_sow_document'}
)


def _arch_review_gate_blocks(
    tool_name: str,
    args: dict[str, Any],
    tool_context: ToolContext,
) -> dict | None:
    """Return a ToolError dict when the architecture-review gate blocks the call.

    Returns None when the gate passes (call proceeds normally). The dict
    return short-circuits the tool call; ADK uses it as the tool's
    response.
    """
    if tool_name not in _ARCH_REVIEW_GATED_TOOLS:
        return None

    # validate_sow_content with stage='content' is used pre-architecture
    # in Phase 2 Step 1.5 and must NOT be gated.
    if tool_name == 'validate_sow_content':
        stage = (args.get('stage') or 'full').strip().lower()
        if stage != 'full':
            return None

    if is_architecture_review_approved(tool_context.state):
        return None

    logger.warning(
        'arch_review_gate_blocked',
        tool=tool_name,
        stage=args.get('stage') if tool_name == 'validate_sow_content' else None,
    )
    return {
        'status': 'error',
        'error': (
            'Cannot proceed with final document generation: the '
            'Architecture Review phase has not been confirmed. '
            'Required steps:\n'
            '1. Present the Architecture Review to the user in the '
            'conversation language.\n'
            "2. Wait for the user's explicit approval.\n"
            "3. Call `confirm_phase_completion(phase_key="
            "'architecture_review_approved')`.\n"
            '4. Then retry this tool.'
        ),
        'retryable': True,
        'tool': tool_name,
        'suggestion': (
            'Return to Phase 2 Step 4, present the Architecture Review, '
            'wait for approval, and stamp the phase via '
            "confirm_phase_completion before retrying."
        ),
    }


def before_tool_callback(
    tool,
    args: dict[str, Any],
    tool_context: ToolContext,
) -> dict | None:
    """Pre-execution guardrails.

    - Validates sow_data size to prevent OOM on large payloads.
    - Logs every tool invocation for audit trail.
    """
    tool_name = getattr(tool, 'name', str(tool))
    log = logger.bind(tool=tool_name)
    log.info('tool_invoked', args_keys=list(args.keys()))

    # Guard: architecture-review approval is a precondition for
    # final-document tools. Runs FIRST so blocked calls don't pay the
    # cost of the JSON-size and JSON-parse guards below.
    gate_block = _arch_review_gate_blocks(tool_name, args, tool_context)
    if gate_block is not None:
        return gate_block

    # Guard: reject oversized sow_data payloads
    sow_data = args.get('sow_data')
    if isinstance(sow_data, str) and len(sow_data) > _MAX_SOW_DATA_CHARS:
        log.warning(
            'sow_data_too_large',
            size=len(sow_data),
            limit=_MAX_SOW_DATA_CHARS,
        )
        return {
            'status': 'error',
            'error': (
                f'sow_data exceeds maximum size ({len(sow_data):,} chars, '
                f'limit: {_MAX_SOW_DATA_CHARS:,}). Reduce content and retry.'
            ),
        }

    # Guard: validate sow_data is parseable JSON before expensive tools run
    if sow_data and tool_name in (
        'generate_sow_document',
        'validate_sow_content',
    ):
        try:
            json.loads(sow_data)
        except json.JSONDecodeError as e:
            log.warning('sow_data_invalid_json', error=str(e))
            return {
                'status': 'error',
                'error': f'sow_data is not valid JSON: {e}',
            }

    return None


def after_tool_callback(
    tool,
    args: dict[str, Any],
    tool_context: ToolContext,
    tool_response: dict,
) -> dict | None:
    """Post-execution observability and state tracking.

    - Logs tool result status.
    - Tracks which tools have been called in session state so the agent
      (and SKILL.md instructions) can make informed decisions about
      pipeline stage.
    """
    tool_name = getattr(tool, 'name', str(tool))
    status = 'unknown'
    if isinstance(tool_response, dict):
        status = tool_response.get('status', 'unknown')

    logger.info('tool_completed_callback', tool=tool_name, status=status)

    # Track tool call history in session state
    tool_history: list = tool_context.state.get('tool_call_history', [])
    tool_history.append(
        {
            'tool': tool_name,
            'status': status,
        }
    )
    tool_context.state['tool_call_history'] = tool_history

    # Track validation state for pipeline awareness
    if tool_name == 'validate_sow_content' and isinstance(tool_response, dict):
        data = tool_response.get('data', {})
        tool_context.state['last_validation_passed'] = data.get(
            'passed', False
        )
        tool_context.state['last_validation_error_count'] = data.get(
            'error_count', 0
        )

    return None

_STATE_EMPTY_RESPONSE_ATTEMPTS = '_empty_response_attempts'
_MAX_EMPTY_RETRIES = 2
_RECOVERY_TOOL_NAME = '_request_continuation'

# Finish reasons whose "model gave up without rendering anything" failure mode
# maps to the same UX as a plain STOP+empty turn: a blank bubble in the chat.
# When an LLM response carries one of these AND has no renderable content,
# the empty-response guard injects a synthetic call to ``_request_continuation``
# so the agent loop re-prompts the model.
#
# Why each entry is in the set:
# - ``STOP`` / ``None`` / ``FINISH_REASON_UNSPECIFIED``: the model believes
#   it is done. Empty content here is the original failure mode the guard was
#   built for.
# - ``MALFORMED_FUNCTION_CALL``: the model intended to call a tool but emitted
#   invalid JSON; the response is empty (zero tokens). Observed in production
#   logs with ``output_tokens=0`` after a long fix-loop on validate_sow_content.
# - ``UNEXPECTED_TOOL_CALL``: the model attempted a tool the runtime rejected
#   before any user-visible output was produced. Same UX as malformed call.
# - ``OTHER``: Gemini's catch-all for unspecified internal failures.
#
# Why deliberately NOT in the set:
# - ``SAFETY`` / ``PROHIBITED_CONTENT`` / ``SPII`` / ``BLOCKLIST`` /
#   ``IMAGE_SAFETY`` / ``IMAGE_PROHIBITED_CONTENT`` — owned by
#   ``app.guardrails`` and the model's safety settings; recovering here would
#   re-prompt a refusal the safety layer must own.
# - ``RECITATION`` / ``IMAGE_RECITATION`` — typically partial-but-real output,
#   not empty terminal.
# - ``MAX_TOKENS`` — would carry partial content; user sees something.
# - ``LANGUAGE`` / ``NO_IMAGE`` / ``IMAGE_OTHER`` — image-specific or rare
#   edge cases; defaulting to non-recovery preserves their original behavior.
def _build_recoverable_finish_reasons() -> frozenset:
    """Resolve recoverable finish-reason values against the installed SDK.

    Each name is looked up dynamically because the ``FinishReason`` enum has
    grown over SDK versions (e.g. ``UNEXPECTED_TOOL_CALL`` is recent). Names
    absent from this SDK are silently skipped — the guard still works for
    the reasons the SDK does expose.
    """
    names = (
        'STOP',
        'MALFORMED_FUNCTION_CALL',
        'UNEXPECTED_TOOL_CALL',
        'OTHER',
        'FINISH_REASON_UNSPECIFIED',
    )
    out: set = {None}
    for name in names:
        value = getattr(types.FinishReason, name, None)
        if value is not None:
            out.add(value)
    return frozenset(out)


_RECOVERABLE_FINISH_REASONS = _build_recoverable_finish_reasons()

# Hard limit on the apology call: 5 s is generous for a one-sentence Flash
# Lite generation. Beyond this we give up and use the static fallback so the
# user never waits long on a failing path.
_APOLOGY_TIMEOUT_S = 5.0

# Cap snippet length sent to the apology model to keep tokens minimal and
# avoid leaking large user uploads into a recovery-only call.
_APOLOGY_SNIPPET_MAX_CHARS = 800

_APOLOGY_SYSTEM_INSTRUCTION = """You write a single short, polite apology message to a user whose assistant briefly failed to produce a response.

Rules:
- Detect the user's language from the message you receive and write the apology IN THAT LANGUAGE. Do NOT translate to English.
- One sentence, maximum 30 words.
- Briefly acknowledge the issue and ask the user to rephrase or resend the message.
- Do NOT explain causes, mention models, tools, or technical details.
- Do NOT use markdown, code blocks, surrounding quotes, or emojis.
- Output ONLY the apology text. No preamble, no labels."""

# Last-resort English fallback when the localized generation itself fails.
# English is a deliberate compromise here — there is no language signal we
# can trust at this point in the flow.
_FALLBACK_APOLOGY_EN = (
    'I had trouble generating a response. '
    'Could you please rephrase or resend your last message?'
)

_apology_client: Client | None = None


def _get_apology_client() -> Client:
    """Lazy Vertex AI client reused across apology calls.

    Mirrors the pattern in :mod:`app.guardrails` so we do not pay
    TLS/discovery costs per recovery turn. Project and location come
    from the env vars the agent bootstrap sets — falling back to ADC
    defaults if a test imports this module in isolation.
    """
    global _apology_client
    if _apology_client is None:
        _apology_client = Client(
            vertexai=True,
            project=os.environ.get('GOOGLE_CLOUD_PROJECT'),
            location=os.environ.get('GOOGLE_CLOUD_LOCATION', 'global'),
        )
    return _apology_client


def _extract_user_text(callback_context: CallbackContext) -> str:
    """Pull plaintext from the user message that started this invocation.

    Returns an empty string when the context lacks ``user_content`` or
    the content carries no text parts (e.g. file-only upload).
    """
    user_content = getattr(callback_context, 'user_content', None)
    if user_content is None:
        return ''
    parts = getattr(user_content, 'parts', None) or []
    texts = [getattr(p, 'text', None) for p in parts]
    return '\n'.join(t for t in texts if t).strip()


async def _generate_localized_apology(
    callback_context: CallbackContext,
) -> str:
    """Ask Flash Lite for a one-sentence apology in the user's language.

    Falls back to :data:`_FALLBACK_APOLOGY_EN` when:
    - the user content has no text to infer language from,
    - the cheap model call times out, raises, or returns empty text.

    Never raises. The exhausted-apology path must always produce visible
    text; the worst case is one English sentence.
    """
    snippet = _extract_user_text(callback_context)
    if not snippet:
        logger.warning('localized_apology_no_user_text_fallback_en')
        return _FALLBACK_APOLOGY_EN

    if len(snippet) > _APOLOGY_SNIPPET_MAX_CHARS:
        snippet = snippet[:_APOLOGY_SNIPPET_MAX_CHARS].rstrip() + '…'

    try:
        client = _get_apology_client()
        response = await asyncio.wait_for(
            client.aio.models.generate_content(
                model=config.SAFETY_JUDGE_MODEL,
                contents=snippet,
                config=types.GenerateContentConfig(
                    system_instruction=_APOLOGY_SYSTEM_INSTRUCTION,
                    temperature=0.2,
                ),
            ),
            timeout=_APOLOGY_TIMEOUT_S,
        )
    except Exception as e:  # noqa: BLE001 — fallback-only path, never raise
        logger.warning(
            'localized_apology_generation_failed_fallback_en',
            error=str(e),
            error_type=type(e).__name__,
        )
        return _FALLBACK_APOLOGY_EN

    text = (getattr(response, 'text', None) or '').strip()
    if not text:
        logger.warning('localized_apology_empty_response_fallback_en')
        return _FALLBACK_APOLOGY_EN

    return text


def _is_terminal_empty_response(llm_response: LlmResponse) -> bool:
    """True when the response would render as nothing in the UI.

    A turn is terminal-empty when the model's ``finish_reason`` is in
    :data:`_RECOVERABLE_FINISH_REASONS` (``STOP``, ``None``,
    ``MALFORMED_FUNCTION_CALL``, ``UNEXPECTED_TOOL_CALL``, ``OTHER``,
    ``FINISH_REASON_UNSPECIFIED``) AND it produced neither non-empty text
    nor a function call. Function-only turns (``text=None`` + ``function_call``
    present) are the NORMAL tool-calling shape and must pass through.

    Responses already carrying ``error_code`` / ``error_message`` are
    handled by the existing error pipeline, not by this guard. Safety-class
    finish reasons (``SAFETY``, ``PROHIBITED_CONTENT``, ``SPII``, ...) are
    owned by ``app.guardrails`` and the model's safety settings and are
    NOT in :data:`_RECOVERABLE_FINISH_REASONS` — they pass through this
    guard untouched.

    Why the allowlist of finish reasons (instead of "not SAFETY"): only the
    failure modes whose user-visible symptom is a blank chat bubble belong
    here. Reasons like ``MAX_TOKENS`` or ``RECITATION`` carry partial
    content the user can still read; recovering on those would re-prompt
    a turn that already produced something.
    """
    if getattr(llm_response, 'error_code', None) or getattr(
        llm_response, 'error_message', None
    ):
        return False

    finish_reason = getattr(llm_response, 'finish_reason', None)
    if finish_reason not in _RECOVERABLE_FINISH_REASONS:
        return False

    content = getattr(llm_response, 'content', None)
    if content is None:
        return True

    parts = getattr(content, 'parts', None) or []
    if not parts:
        return True

    for part in parts:
        text = (getattr(part, 'text', None) or '').strip()
        if text:
            return False
        if getattr(part, 'function_call', None):
            return False

    return True


def _build_recovery_call_response() -> LlmResponse:
    """Synthesize a model turn that triggers the recovery tool.

    Mirrors the synthetic-response pattern in ``guardrails`` but the
    ``Part`` carries a ``function_call`` instead of text. The agent
    loop executes the tool, feeds the result back to the model, and the
    model gets a fresh turn with explicit instructions to resume.

    The ``thought_signature`` carries the Vertex AI documented bypass
    sentinel — see :mod:`app._genai_patches`. Gemini 3.x rejects replayed
    ``functionCall`` parts that lack a signature; client-synthesized calls
    have none, so we opt into the bypass for this single turn. The patch
    in ``_genai_patches`` is what makes the sentinel reach the wire as the
    literal ASCII string the backend expects.
    """
    return LlmResponse(
        content=types.Content(
            role='model',
            parts=[
                types.Part(
                    function_call=types.FunctionCall(
                        name=_RECOVERY_TOOL_NAME,
                        args={},
                    ),
                    thought_signature=THOUGHT_SIGNATURE_BYPASS_BYTES,
                )
            ],
        ),
        custom_metadata={'empty_response_recovery': True},
    )


def _build_exhausted_apology_response(text: str) -> LlmResponse:
    """Final, single user-visible message after recovery is exhausted.

    ``text`` comes from :func:`_generate_localized_apology` so the message
    arrives in the user's language. Callers are responsible for falling
    back to the static English message when the generator can't run.
    """
    return LlmResponse(
        content=types.Content(
            role='model',
            parts=[types.Part.from_text(text=text)],
        ),
        custom_metadata={'empty_response_recovery_exhausted': True},
    )


async def empty_response_guard(
    callback_context: CallbackContext,
    llm_response: LlmResponse,
) -> LlmResponse | None:
    """after_model_callback that recovers from terminal empty turns.

    Healthy turns pass through unchanged. Terminal empty turns are
    replaced with a synthetic call to ``_request_continuation`` (up to
    ``_MAX_EMPTY_RETRIES`` consecutive attempts) so the agent loop
    re-prompts the model. Once the cap is reached, the user receives
    a single honest apology generated on the fly in their language
    (see :func:`_generate_localized_apology`) and the counter resets.
    """
    state = callback_context.state

    if not _is_terminal_empty_response(llm_response):
        if state.get(_STATE_EMPTY_RESPONSE_ATTEMPTS):
            state[_STATE_EMPTY_RESPONSE_ATTEMPTS] = 0
        return None

    attempts = state.get(_STATE_EMPTY_RESPONSE_ATTEMPTS, 0)
    finish_reason = getattr(llm_response, 'finish_reason', None)

    if attempts < _MAX_EMPTY_RETRIES:
        state[_STATE_EMPTY_RESPONSE_ATTEMPTS] = attempts + 1
        logger.warning(
            'empty_response_recovery_injected',
            attempts=attempts + 1,
            max_retries=_MAX_EMPTY_RETRIES,
            finish_reason=str(finish_reason) if finish_reason else None,
        )
        return _build_recovery_call_response()

    state[_STATE_EMPTY_RESPONSE_ATTEMPTS] = 0
    logger.warning(
        'empty_response_recovery_exhausted',
        attempts=attempts,
        max_retries=_MAX_EMPTY_RETRIES,
        finish_reason=str(finish_reason) if finish_reason else None,
    )
    apology = await _generate_localized_apology(callback_context)
    return _build_exhausted_apology_response(apology)
