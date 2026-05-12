"""Independent coverage review: every Manifest item should have a SOW anchor.

Why a separate pass
-------------------
``_semantic_review`` runs a comprehensive rubric (contradictions, structural
coherence, semantic gaps) and packs the entire SOW + a truncated Manifest
summary into one prompt. Coverage gaps — items the user/transcript named
that never made it into an FR, deliverable, or success criterion — are a
different problem: they need the FULL Manifest in scope, not a 280-char
summary, and they need the model focused on a single question instead of
juggling the whole rubric.

This module asks one focused question: "For each Manifest item, find at
least one concrete anchor in the SOW. Report items with no anchor."

The output is a list of ``Finding`` objects (same shape as
``_semantic_review.Finding``) with ``category="coverage"`` so the
existing ``validate_sow_content`` summary path renders them alongside
contradictions and semantic findings without code changes downstream.

Failure posture
---------------
Fail-open. Every error path returns the canonical empty-result shape so a
reviewer outage cannot block delivery. Mechanical validation remains
authoritative for the ``passed`` field.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Literal

import structlog
from google.genai import types
from pydantic import BaseModel

from ...config import config
from ._semantic_review import _get_reviewer_client

logger = structlog.get_logger()


_RUBRIC_PATH = Path(__file__).parent / '_coverage_rubric.md'

_MAX_FINDINGS = 8
_MANIFEST_STATE_KEY = 'extraction_manifest'

_rubric_cache: str | None = None


class CoverageFinding(BaseModel):
    """One Manifest item the reviewer judged unanchored in the SOW."""

    id: str
    severity: Literal['BLOCKER', 'MAJOR', 'MINOR']
    category: Literal['coverage']
    evidence: str
    recommendation: str
    fields: list[str]


class _CoverageOutput(BaseModel):
    """Top-level structured output for the coverage reviewer."""

    findings: list[CoverageFinding]


def _load_rubric() -> str:
    """Read the rubric once and cache it in process memory."""
    global _rubric_cache
    if _rubric_cache is None:
        _rubric_cache = _RUBRIC_PATH.read_text(encoding='utf-8')
    return _rubric_cache


def _serialize_manifest_for_prompt(manifest: dict | None) -> str | None:
    """Render the full Manifest as compact JSON for the reviewer prompt.

    Returns ``None`` when no Manifest is available — the caller short-circuits
    in that case. The Manifest is the entire upstream context this pass
    needs; without it, the reviewer has nothing to compare against and
    running the call would burn tokens for no findings.
    """
    if not manifest or not isinstance(manifest, dict):
        return None
    try:
        return json.dumps(manifest, indent=2, ensure_ascii=False)
    except (TypeError, ValueError) as e:
        logger.warning(
            'manifest_serialization_failed',
            error=str(e),
            error_type=type(e).__name__,
        )
        return None


def _build_user_prompt(sow_data: dict, manifest_json: str) -> str:
    """Assemble the user-side prompt: Manifest first, SOW second."""
    return (
        '<extraction_manifest>\n'
        + manifest_json
        + '\n</extraction_manifest>\n\n'
        + '<sow_data>\n'
        + json.dumps(sow_data, indent=2, ensure_ascii=False)
        + '\n</sow_data>'
    )


def _empty_result(reason: str, latency_ms: int = 0) -> dict[str, Any]:
    """Canonical fail-open shape mirroring ``_semantic_review._empty_result``."""
    return {
        'findings': [],
        'review_metadata': {
            'ran': False,
            'model': getattr(config, 'COVERAGE_REVIEW_MODEL', None),
            'latency_ms': latency_ms,
            'fallback_reason': reason,
        },
    }


def _coerce_findings(parsed: Any) -> list[dict[str, Any]]:
    """Normalize the parsed reviewer output to a list of finding dicts."""
    if isinstance(parsed, _CoverageOutput):
        items = parsed.findings
    elif isinstance(parsed, dict):
        raw = parsed.get('findings') or []
        items = []
        for entry in raw:
            try:
                items.append(CoverageFinding.model_validate(entry))
            except Exception:  # noqa: BLE001 — drop malformed entries
                continue
    else:
        return []

    capped = items[:_MAX_FINDINGS]
    return [f.model_dump() for f in capped]


async def manifest_coverage_review(
    sow_data: dict,
    stage: str,
    tool_context: Any | None = None,
) -> dict[str, Any]:
    """Run the independent coverage review pass and return findings + metadata.

    Args:
        sow_data: Already-parsed SOW payload dict.
        stage: ``"content"`` or ``"full"``. Other values short-circuit
            with ``ran=False``.
        tool_context: Optional ADK ``ToolContext``. Used only to read the
            session-state Manifest. Without a Manifest in state the pass
            short-circuits and returns no findings.

    Returns:
        ``{"findings": [...], "review_metadata": {...}}``. Always returns
        this shape; never raises. ``findings`` is empty when the reviewer
        was disabled, the Manifest was missing, the stage is unsupported,
        or the call failed.
    """
    if not getattr(config, 'COVERAGE_REVIEW_ENABLED', True):
        return _empty_result('disabled_by_config')

    if stage not in ('content', 'full'):
        return _empty_result(f'unsupported_stage:{stage}')

    log = logger.bind(component='manifest_coverage_review', stage=stage)
    start = time.perf_counter()

    manifest = None
    if tool_context is not None:
        try:
            state = getattr(tool_context, 'state', None) or {}
            manifest = state.get(_MANIFEST_STATE_KEY)
        except Exception:  # noqa: BLE001 — state access is best-effort
            manifest = None

    manifest_json = _serialize_manifest_for_prompt(manifest)
    if manifest_json is None:
        return _empty_result('no_manifest_available')

    try:
        rubric = _load_rubric()
    except Exception as e:  # noqa: BLE001 — config-time error
        log.warning(
            'rubric_load_failed',
            error=str(e),
            error_type=type(e).__name__,
        )
        return _empty_result(f'rubric_load_failed:{type(e).__name__}')

    user_prompt = _build_user_prompt(sow_data, manifest_json)

    timeout_s = getattr(config, 'COVERAGE_REVIEW_TIMEOUT_S', 30.0)
    model = getattr(
        config, 'COVERAGE_REVIEW_MODEL', 'gemini-3-flash-preview'
    )
    thinking_budget = getattr(config, 'COVERAGE_REVIEW_THINKING_BUDGET', 1024)

    generate_config_kwargs: dict[str, Any] = {
        'system_instruction': rubric,
        'temperature': 0.0,
        'response_mime_type': 'application/json',
        'response_schema': _CoverageOutput,
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
        log.warning(
            'coverage_review_timeout',
            timeout_s=timeout_s,
            latency_ms=elapsed_ms,
        )
        return _empty_result('timeout', latency_ms=elapsed_ms)
    except Exception as e:  # noqa: BLE001 — fail-open on any model error
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        log.warning(
            'coverage_review_call_failed',
            error=str(e),
            error_type=type(e).__name__,
            latency_ms=elapsed_ms,
        )
        return _empty_result(f'{type(e).__name__}', latency_ms=elapsed_ms)

    parsed: Any = getattr(response, 'parsed', None)
    if parsed is None:
        raw_text = getattr(response, 'text', '') or ''
        try:
            parsed = _CoverageOutput.model_validate_json(raw_text)
        except Exception as e:  # noqa: BLE001 — malformed JSON falls back to empty
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            log.warning(
                'coverage_review_malformed_response',
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
        'coverage_review_completed',
        latency_ms=elapsed_ms,
        findings_count=len(findings),
        findings_severities=severity_counts,
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
