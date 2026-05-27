"""Root agent prompt builders.

The agent's instruction is exposed in two forms:

- :func:`build_instruction` — legacy static-string builder. Substitutes
  ``{company_name}`` / ``{todays_date}`` and returns the resulting string.
  Kept for parity with tests and callers that need a deterministic
  string.
- :func:`build_instruction_provider` — the runtime instruction provider
  the root agent actually uses. Returns a callable that ADK invokes per
  turn with a ``ReadonlyContext``; the callable reads
  ``state['app:language']`` and injects a minimal ``<session_state>``
  block at the end of the prompt, so the model sees the authoritative
  conversation language deterministically. Without this, writing
  "read state['app:language']" in the prompt is decorative — the model
  has no other way to query state.

Only the conversation language is injected — never bundles, metadata,
findings, or any other state, so the system instruction stays compact.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Callable

from google.adk.agents.readonly_context import ReadonlyContext

_PROMPT_PATH = Path(__file__).parent / 'root_prompt.md'

if not _PROMPT_PATH.exists():
    raise FileNotFoundError(
        f"Root agent prompt not found at '{_PROMPT_PATH}'. "
        'Ensure prompts/root_prompt.md exists in the project.'
    )

ROOT_PROMPT: str = _PROMPT_PATH.read_text(encoding='utf-8')

_LANGUAGE_STATE_KEY = 'app:language'


class _PreservingDict(dict):
    """Dict that preserves unknown {placeholders} during format_map."""

    def __missing__(self, key: str) -> str:
        return '{' + key + '}'


def build_instruction(company_name: str) -> str:
    """Assemble the root agent instruction by injecting runtime variables.

    Substitutes ``{company_name}`` and ``{todays_date}`` only. Used by
    :func:`build_instruction_provider` as the static base.
    """
    variables = {
        'todays_date': date.today().strftime('%d/%m/%Y'),
        'company_name': company_name,
    }
    return ROOT_PROMPT.format_map(_PreservingDict(variables))


def build_instruction_provider(
    company_name: str,
) -> Callable[[ReadonlyContext], str]:
    """Return the callable ADK invokes per turn to inject session state.

    The returned function appends a minimal ``<session_state>`` block to
    the static base instruction::

        <session_state>
          <conversation_language>pt-BR</conversation_language>
        </session_state>

    The value comes from ``ctx.state['app:language']`` — or ``'auto'`` when
    the key is not set yet. The block is intentionally minimal: only the
    conversation language. Bundles, metadata, findings, and quality-loop
    results stay out of the system instruction.

    The static base is computed once at provider build time so that
    ``{todays_date}`` reflects the day the agent was instantiated (same
    semantics the legacy static-string instruction had).
    """
    base = build_instruction(company_name=company_name)

    def _provider(ctx: ReadonlyContext) -> str:
        language = ctx.state.get(_LANGUAGE_STATE_KEY) or 'auto'
        return (
            base
            + '\n\n<session_state>\n'
            + f'  <conversation_language>{language}</conversation_language>\n'
            + '</session_state>\n'
        )

    return _provider


__all__ = ['ROOT_PROMPT', 'build_instruction', 'build_instruction_provider']
