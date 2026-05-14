"""Step 5 of validation_critic — text-only summary (LLM, opcional).

Reads the partial `ValidationReport` from state and returns a structured
``SummaryDraft`` with two text fields. It never alters structural fields;
the assembler enforces that invariant in Python.

The language is taken from session state (`app:language`) when available
so the summary is shown to the user in the conversation language. Defaults
to the source language of the SOW payload otherwise.
"""

from __future__ import annotations

import json

from google.adk.agents import LlmAgent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.models import Gemini
from google.genai import types

from ...config import config
from .schema import STATE_REPORT_PARTIAL, STATE_SUMMARY_DRAFT, SummaryDraft

_LANGUAGE_STATE_KEY = 'app:language'

_BASE_INSTRUCTION = """<role>
You are a concise validation summarizer. You receive a structured
ValidationReport produced by a deterministic aggregator and you return
two short text fields. You never change numbers, severities, or status.
</role>

<rules>
- Respond in the conversation language from state[app:language] when set;
  otherwise default to English.
- Read the report in full. Reference at most three findings, picking the
  highest-severity ones first.
- Keep `summary` under 80 words. Use one short paragraph or 2-3 bullets.
- Keep `next_action` a single sentence — a concrete instruction the
  upstream agent can act on.
- Only ask the user when `overall_status` is `needs_human_review`.
  For `blocked`, tell the upstream agent to apply the concrete fixes and
  re-run validation; do not ask for approval of standard SOW corrections.
- Do not repeat known manual placeholders/deferred fields as user questions
  unless the report explicitly marks them as `requires_human_review`.
- NEVER quote or invent severities, counts, statuses, or finding ids that
  are not in the report. NEVER produce JSON other than the schema.
- NEVER copy the rubric back into the summary.
</rules>

<output_schema>
Return ONLY a JSON object with two string fields: `summary` and
`next_action`. Match the SummaryDraft Pydantic schema.
</output_schema>
"""


def _build_instruction(ctx: ReadonlyContext) -> str:
    state = ctx.state
    partial = state.get(STATE_REPORT_PARTIAL) or {}
    language = state.get(_LANGUAGE_STATE_KEY) or 'auto'
    return (
        _BASE_INSTRUCTION
        + f'\n<conversation_language>{language}</conversation_language>\n'
        + '\n<validation_report>\n'
        + json.dumps(partial, ensure_ascii=False, indent=2)
        + '\n</validation_report>\n'
    )


def _build_model() -> Gemini:
    return Gemini(
        model=config.VALIDATION_SUMMARY_MODEL,
        retry_options=types.HttpRetryOptions(attempts=config.MAX_RETRIES),
    )


validation_summary_agent = LlmAgent(
    name='validation_summary_agent',
    description=(
        'Writes the human-readable summary and next_action lines for the '
        'final ValidationReport. Does not touch structural fields.'
    ),
    model=_build_model(),
    instruction=_build_instruction,
    output_schema=SummaryDraft,
    output_key=STATE_SUMMARY_DRAFT,
    disallow_transfer_to_parent=True,
    disallow_transfer_to_peers=True,
    generate_content_config=types.GenerateContentConfig(
        temperature=config.TEMPERATURE,
        thinking_config=types.ThinkingConfig(
            include_thoughts=False,
            thinking_budget=config.VALIDATION_SUMMARY_THINKING_BUDGET,
        ),
    ),
    include_contents="none"
)

# Backwards-compatible alias for older imports.
validation_summary_skill = validation_summary_agent
