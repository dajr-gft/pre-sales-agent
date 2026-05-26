"""Step 3 of validation_critic — five semantic skills running in parallel.

Each skill is an `LlmAgent` whose ``instruction`` is the body of its
`SKILL.md` plus a runtime payload (SOW + manifest residual + stage)
resolved per invocation. Every skill writes its findings into a
**dedicated** state key — `app:skill_findings:{name}` — so the
`ParallelAgent` runs without any chance of a write race.

Anti-monolith gates (`SKILL.md` ≤ 200 lines) are enforced at startup so
a bloated prompt cannot quietly regress the architecture.
"""

from __future__ import annotations

import json
from pathlib import Path

import structlog
from google.adk.agents import LlmAgent, ParallelAgent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.models import Gemini
from google.genai import types

from ...config import config
from .schema import (
    SKILL_NAMES,
    STATE_MANIFEST_RESIDUAL,
    STATE_SOW,
    STATE_STAGE,
    SkillFindings,
    skill_findings_state_key,
)

logger = structlog.get_logger()

_SKILLS_DIR = Path(__file__).parent / 'skills'
_MAX_SKILL_LINES = 200


def _read_with_gate(path: Path) -> str:
    """Read a SKILL.md and fail fast if it breaches the size gate.

    The gate is an architectural constraint from the MVP plan: each skill
    must stay focused. Crash at startup so the issue surfaces in CI, not
    in production telemetry.
    """
    if not path.exists():
        raise FileNotFoundError(f'Required skill file not found: {path}')
    text = path.read_text(encoding='utf-8')
    line_count = text.count('\n') + 1
    if line_count > _MAX_SKILL_LINES:
        raise RuntimeError(
            f'Anti-monolith gate breached: {path} has {line_count} '
            f'lines (limit: {_MAX_SKILL_LINES}). Trim the SKILL.md.'
        )
    return text


def _trim_manifest(items: list[dict] | None) -> list[dict]:
    """Keep the residual lean so each skill's prompt stays focused."""
    out: list[dict] = []
    for item in items or []:
        out.append(
            {
                'item_id': item.get('item_id'),
                'category': item.get('category'),
                'priority': item.get('priority'),
                'value': item.get('value'),
                'value_detail': item.get('value_detail'),
            }
        )
    return out


def _make_instruction_provider(skill_name: str, skill_body: str):
    """Closure that resolves SKILL.md body + runtime payload from state."""

    def _provider(ctx: ReadonlyContext) -> str:
        state = ctx.state
        sow = state.get(STATE_SOW) or {}
        residual = _trim_manifest(state.get(STATE_MANIFEST_RESIDUAL) or [])
        stage = state.get(STATE_STAGE) or 'full'

        payload = (
            '\n\n---\n\n'
            '# Runtime payload\n\n'
            f'Stage: `{stage}`.\n\n'
            '<sow_data>\n'
            f'{json.dumps(sow, ensure_ascii=False, indent=2)}\n'
            '</sow_data>\n\n'
            '<manifest_residual>\n'
            f'{json.dumps(residual, ensure_ascii=False, indent=2)}\n'
            '</manifest_residual>\n\n'
            'Return ONLY a JSON object matching the schema: '
            f'`{{"findings": [Finding, ...]}}` with `skill="{skill_name}"` '
            'on every finding. Return `{"findings": []}` if nothing applies.'
        )
        return skill_body + payload

    return _provider


def _make_model() -> Gemini:
    return Gemini(
        model=config.VALIDATION_SKILL_MODEL,
        retry_options=types.HttpRetryOptions(attempts=config.MAX_RETRIES),
    )


def _make_skill_agent(skill_name: str) -> LlmAgent:
    """Build the LlmAgent that runs one semantic skill."""
    skill_md = _read_with_gate(_SKILLS_DIR / skill_name / 'SKILL.md')
    return LlmAgent(
        name=f'{skill_name}_skill_agent',
        description=f'Semantic validation skill: {skill_name}.',
        model=_make_model(),
        instruction=_make_instruction_provider(skill_name, skill_md),
        output_schema=SkillFindings,
        output_key=skill_findings_state_key(skill_name),
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
        generate_content_config=types.GenerateContentConfig(
            temperature=config.TEMPERATURE,
            thinking_config=types.ThinkingConfig(
                include_thoughts=False,
                thinking_budget=config.VALIDATION_SKILL_THINKING_BUDGET,
            ),
        ),
        include_contents="none"
    )


coverage_skill_agent = _make_skill_agent('coverage')
contradictions_skill_agent = _make_skill_agent('contradictions')
contractual_exposure_skill_agent = _make_skill_agent('contractual_exposure')
disclosures_skill_agent = _make_skill_agent('disclosures')
semantic_quality_skill_agent = _make_skill_agent('semantic_quality')


semantic_skills_parallel = ParallelAgent(
    name='semantic_skills',
    description=(
        'Runs the five semantic validation skills concurrently. Each skill '
        'writes into its own state key — no race condition possible.'
    ),
    sub_agents=[
        coverage_skill_agent,
        contradictions_skill_agent,
        contractual_exposure_skill_agent,
        disclosures_skill_agent,
        semantic_quality_skill_agent,
    ],
)


__all__ = [
    'coverage_skill_agent',
    'contradictions_skill_agent',
    'contractual_exposure_skill_agent',
    'disclosures_skill_agent',
    'semantic_quality_skill_agent',
    'semantic_skills_parallel',
    'SKILL_NAMES',
]
