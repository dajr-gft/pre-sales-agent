"""Independent semantic review of a SOW payload via a separate Gemini call.

Why this lives inside the validator tool, not as a separate skill or sub-agent
--------------------------------------------------------------------------
The deterministic ``ContentValidator`` catches structural defects (ID format,
counts, cross-reference shape). It does NOT catch contradictions across
sections, naming drift, or vague language that the upstream context made
concrete material available for. Those are semantic properties that emerge
only when the draft is read holistically.

A single LLM call here, on a fresh context that never saw the generation
reasoning, is the operational equivalent of a reviewer with fresh eyes — but
implemented as a function, not as an orchestrated sub-agent. The agent keeps
calling ``validate_sow_content`` exactly where it does today; the tool's
output now carries semantic findings alongside mechanical issues, and the
existing fix-and-retry loop covers both.

Failure posture
---------------
This module is fail-open. Every error path returns an empty findings list
plus a ``review_metadata`` block whose ``ran=False`` and ``fallback_reason``
spell out why. A SOW must NEVER fail to deliver because the reviewer is
unavailable — mechanical validation remains authoritative for the ``passed``
field.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, Literal

import google.auth
import structlog
from google.genai import Client, types
from pydantic import BaseModel

from ...config import config

logger = structlog.get_logger()

_, _project_id = google.auth.default()
os.environ.setdefault('GOOGLE_CLOUD_PROJECT', _project_id or '')
os.environ.setdefault('GOOGLE_CLOUD_LOCATION', 'global')
os.environ.setdefault('GOOGLE_GENAI_USE_VERTEXAI', 'True')


_RUBRIC_PATH = Path(__file__).parent / '_review_rubric.md'

_MAX_FINDINGS = 20
_MAX_MANIFEST_ITEMS_PER_CATEGORY = 8
_MANIFEST_STATE_KEY = 'extraction_manifest'

_rubric_cache: str | None = None
_reviewer_client: Client | None = None


class Finding(BaseModel):
    """One semantic finding returned by the reviewer model."""

    id: str
    severity: Literal['BLOCKER', 'MAJOR', 'MINOR']
    category: Literal[
        'structural', 'contradiction', 'semantic', 'self_sufficiency'
    ]
    evidence: str
    recommendation: str
    fields: list[str]


class _ReviewerOutput(BaseModel):
    """Top-level structured output for the reviewer model."""

    findings: list[Finding]


def _load_rubric() -> str:
    """Read the rubric once and cache it in process memory."""
    global _rubric_cache
    if _rubric_cache is None:
        _rubric_cache = _RUBRIC_PATH.read_text(encoding='utf-8')
    return _rubric_cache


def _get_reviewer_client() -> Client:
    """Lazy-init a Vertex AI genai Client reused across reviewer calls."""
    global _reviewer_client
    if _reviewer_client is None:
        _reviewer_client = Client(
            vertexai=True,
            project=os.environ.get('GOOGLE_CLOUD_PROJECT', '') or None,
            location=os.environ.get('GOOGLE_CLOUD_LOCATION', 'global'),
        )
    return _reviewer_client


def _build_manifest_summary(manifest: dict | None) -> str:
    """Render a compact text summary of the Manifest for the reviewer prompt.

    Returns an empty string when no Manifest is available — the reviewer
    operates on the SOW alone in that case. Never raises: a malformed
    Manifest produces an empty summary plus a logged warning.
    """
    if not manifest or not isinstance(manifest, dict):
        return ''

    try:
        items = manifest.get('extracted_items') or []
        by_category: dict[str, list[str]] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            category = str(item.get('category') or 'Uncategorized')
            text = (
                item.get('content')
                or item.get('text')
                or item.get('summary')
                or ''
            )
            if not text:
                continue
            by_category.setdefault(category, []).append(str(text))

        sections: list[str] = []
        for category in sorted(by_category):
            entries = by_category[category][:_MAX_MANIFEST_ITEMS_PER_CATEGORY]
            if not entries:
                continue
            sections.append(f'## {category}')
            for entry in entries:
                trimmed = entry.strip().replace('\n', ' ')
                if len(trimmed) > 280:
                    trimmed = trimmed[:280].rstrip() + '…'
                sections.append(f'- {trimmed}')

        gaps = manifest.get('gaps') or {}
        pending = gaps.get('pending_decisions') if isinstance(gaps, dict) else None
        if pending:
            sections.append('## Pending decisions (open at SOW time)')
            for entry in pending[:_MAX_MANIFEST_ITEMS_PER_CATEGORY]:
                if isinstance(entry, dict):
                    desc = entry.get('description') or entry.get('question') or ''
                else:
                    desc = str(entry)
                if desc:
                    sections.append(f'- {str(desc).strip()[:280]}')

        return '\n'.join(sections).strip()
    except Exception as e:  # noqa: BLE001 — summary is best-effort
        logger.warning(
            'manifest_summary_failed',
            error=str(e),
            error_type=type(e).__name__,
        )
        return ''


def _build_user_prompt(
    sow_data: dict,
    stage: str,
    manifest_summary: str,
) -> str:
    """Assemble the user-side prompt for the reviewer call.

    Order: stage hint → manifest summary (if any) → the SOW JSON. The SOW
    JSON is pretty-printed for readability in the model's context.
    """
    parts: list[str] = []

    if stage == 'content':
        parts.append(
            'Stage: CONTENT — architecture has not been generated yet. Skip '
            'Architecture × Stack × Scope contradiction checks; focus on '
            'requirements, scope, deliverables, assumptions, activities, '
            'roles, timeline, success criteria, and risks.'
        )
    else:
        parts.append(
            'Stage: FULL — content and architecture are both present. Apply '
            'the full rubric, including Architecture × Stack × Scope checks.'
        )

    if manifest_summary:
        parts.append(
            '<extraction_manifest_summary>\n'
            + manifest_summary
            + '\n</extraction_manifest_summary>'
        )
    else:
        parts.append(
            '<extraction_manifest_summary>'
            '\n(no Manifest available — review the SOW on its own merits)\n'
            '</extraction_manifest_summary>'
        )

    parts.append('<sow_data>')
    parts.append(json.dumps(sow_data, indent=2, ensure_ascii=False))
    parts.append('</sow_data>')

    return '\n\n'.join(parts)


def _empty_result(reason: str, latency_ms: int = 0) -> dict[str, Any]:
    """Return the standard fail-open shape: no findings, reason captured."""
    return {
        'findings': [],
        'review_metadata': {
            'ran': False,
            'model': getattr(config, 'SEMANTIC_REVIEW_MODEL', None),
            'latency_ms': latency_ms,
            'fallback_reason': reason,
        },
    }


def _coerce_findings(parsed: Any) -> list[dict[str, Any]]:
    """Normalize the parsed reviewer output to a list of finding dicts.

    Caps at ``_MAX_FINDINGS`` to defend the agent's correction loop from a
    runaway response. Drops malformed entries silently — fail-open posture.
    """
    if isinstance(parsed, _ReviewerOutput):
        items = parsed.findings
    elif isinstance(parsed, dict):
        raw = parsed.get('findings') or []
        items = []
        for entry in raw:
            try:
                items.append(Finding.model_validate(entry))
            except Exception:  # noqa: BLE001 — drop malformed
                continue
    else:
        return []

    capped = items[:_MAX_FINDINGS]
    return [f.model_dump() for f in capped]


async def semantic_review(
    sow_data: dict,
    stage: str,
    tool_context: Any | None = None,
) -> dict[str, Any]:
    """Run the independent semantic review pass and return findings + metadata.

    Args:
        sow_data: Already-parsed SOW payload dict (the same dict that
            ``ContentValidator.validate`` consumed).
        stage: ``"content"`` or ``"full"``. Other values short-circuit
            with ``ran=False``.
        tool_context: Optional ADK ``ToolContext``. Used only to read the
            session-state Manifest for the prompt summary.

    Returns:
        ``{"findings": [...], "review_metadata": {...}}``. Always returns
        this shape; never raises. ``findings`` is empty when the reviewer
        was disabled, the stage is unsupported, or the call failed.
    """
    if not getattr(config, 'SEMANTIC_REVIEW_ENABLED', True):
        return _empty_result('disabled_by_config')

    if stage not in ('content', 'full'):
        return _empty_result(f'unsupported_stage:{stage}')

    log = logger.bind(component='semantic_review', stage=stage)
    start = time.perf_counter()

    try:
        rubric = _load_rubric()
    except Exception as e:  # noqa: BLE001 — config-time error
        log.warning(
            'rubric_load_failed',
            error=str(e),
            error_type=type(e).__name__,
        )
        return _empty_result(f'rubric_load_failed:{type(e).__name__}')

    manifest = None
    if tool_context is not None:
        try:
            state = getattr(tool_context, 'state', None) or {}
            manifest = state.get(_MANIFEST_STATE_KEY)
        except Exception:  # noqa: BLE001 — state access is best-effort
            manifest = None

    manifest_summary = _build_manifest_summary(manifest)
    user_prompt = _build_user_prompt(sow_data, stage, manifest_summary)

    timeout_s = getattr(config, 'SEMANTIC_REVIEW_TIMEOUT_S', 90.0)
    model = getattr(config, 'SEMANTIC_REVIEW_MODEL', 'gemini-flash-latest')
    thinking_budget = getattr(config, 'SEMANTIC_REVIEW_THINKING_BUDGET', 0)

    generate_config_kwargs: dict[str, Any] = {
        'system_instruction': rubric,
        'temperature': 0.0,
        'response_mime_type': 'application/json',
        'response_schema': _ReviewerOutput,
    }
    if thinking_budget and thinking_budget > 0:
        generate_config_kwargs['thinking_config'] = types.ThinkingConfig(
            include_thoughts=False,
            thinking_budget=thinking_budget,
        )

    try:
        client = _get_reviewer_client()
        response = await asyncio.wait_for(
            client.aio.models.generate_content(
                model=model,
                contents=user_prompt,
                config=types.GenerateContentConfig(**generate_config_kwargs),
            ),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        log.warning('semantic_review_timeout', timeout_s=timeout_s, latency_ms=elapsed_ms)
        return _empty_result('timeout', latency_ms=elapsed_ms)
    except Exception as e:  # noqa: BLE001 — fail-open on any model error
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        log.warning(
            'semantic_review_call_failed',
            error=str(e),
            error_type=type(e).__name__,
            latency_ms=elapsed_ms,
        )
        return _empty_result(f'{type(e).__name__}', latency_ms=elapsed_ms)

    parsed: Any = getattr(response, 'parsed', None)
    if parsed is None:
        raw_text = getattr(response, 'text', '') or ''
        try:
            parsed = _ReviewerOutput.model_validate_json(raw_text)
        except Exception as e:  # noqa: BLE001 — malformed JSON falls back to empty
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            log.warning(
                'semantic_review_malformed_response',
                error=str(e),
                error_type=type(e).__name__,
                latency_ms=elapsed_ms,
            )
            return _empty_result('malformed_response', latency_ms=elapsed_ms)

    findings = _coerce_findings(parsed)
    elapsed_ms = int((time.perf_counter() - start) * 1000)

    severity_counts = {'BLOCKER': 0, 'MAJOR': 0, 'MINOR': 0}
    for f in findings:
        sev = f.get('severity')
        if sev in severity_counts:
            severity_counts[sev] += 1

    log.info(
        'semantic_review_completed',
        latency_ms=elapsed_ms,
        findings_count=len(findings),
        findings_severities=severity_counts,
        manifest_used=bool(manifest_summary),
        model=model,
    )

    return {
        'findings': findings,
        'review_metadata': {
            'ran': True,
            'model': model,
            'latency_ms': elapsed_ms,
            'fallback_reason': None,
            'severity_counts': severity_counts,
        },
    }
