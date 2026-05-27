"""Per-run telemetry for the A1-vs-A2 architecture comparison.

The root-skills variant (``root_skills_autoscoped``) is compared against
the legacy multi-agent + manifest variant (``multi_agent_manifest``) on
the same document set. This module collects the metrics that can be
derived deterministically from session state — tool-call counters,
skill-scope pruning totals, bundle payload bytes, per-section generation
timing, patch-engine counters — and assembles the canonical metrics row
defined in the plan (§8).

Two entry points:

- :func:`record_tool_call` — called from ``after_tool_callback`` once per
  tool execution. Pure state mutation, no I/O.
- :func:`collect_run_metrics` — called at end of run (by the run harness)
  to assemble the metrics dict from state. Fields that require run-level
  instrumentation the callbacks cannot see (wall-clock durations, LLM
  call count, token usage from the Gemini SDK usage metadata) are
  emitted as ``None`` so the harness can fill them before writing the
  comparison report.

All counters live under the ``app:telemetry:`` state namespace so they
never collide with the SOW bundles or the skill-scope contract.
"""

from __future__ import annotations

import json
import time
from typing import Any

from ..sub_agents.schemas import SOW_BUNDLE_STATE_KEYS

# Sections that have a skill + a save_<section>_bundle tool. Derived from
# the bundle-key contract so adding a section stays a one-line change in
# schemas.py.
_SECTIONS: frozenset[str] = frozenset(SOW_BUNDLE_STATE_KEYS.keys())

# ---------------------------------------------------------------------------
# State-key contract (namespaced under ``app:telemetry:``)
# ---------------------------------------------------------------------------

_PREFIX = 'app:telemetry:'
K_TOOL_CALLS = _PREFIX + 'tool_call_count'
K_SKILLS_LOADED = _PREFIX + 'skills_loaded_count'
K_SKILL_RESOURCES = _PREFIX + 'skill_resources_loaded_count'
K_LOAD_ARTIFACTS = _PREFIX + 'load_artifacts_call_count'
K_BUNDLE_BYTES = _PREFIX + 'bundle_tool_payload_bytes'
K_PATCH_OPS = _PREFIX + 'patch_ops_applied'
K_PATCH_REJECTIONS = _PREFIX + 'patch_rejections'
K_ANCHOR_DROPS = _PREFIX + 'anchor_drops'
K_SECTION_DURATIONS = _PREFIX + 'section_generation_duration_by_section'
# Internal scratch: monotonic timestamp captured at load_skill, consumed
# at save_<section>_bundle. Not part of the reported schema.
_K_SECTION_STARTS = _PREFIX + '_section_started_monotonic'

# Skill-scope prune accumulator written by AutoScopedSkillToolset.
_K_PRUNE_TOTALS = 'app:skill_scope:prune_totals'
# Quality-loop terminal result.
_K_QUALITY_LOOP_RESULT = 'app:sow:quality_loop_result'


def _section_from_skill(skill_name: Any) -> str | None:
    """``'sow-requirements'`` → ``'requirements'`` (when it is a section)."""
    if not isinstance(skill_name, str) or not skill_name.startswith('sow-'):
        return None
    section = skill_name[len('sow-'):].replace('-', '_')
    return section if section in _SECTIONS else None


def _section_from_save_tool(tool_name: str) -> str | None:
    """``'save_requirements_bundle'`` → ``'requirements'``."""
    if tool_name.startswith('save_') and tool_name.endswith('_bundle'):
        section = tool_name[len('save_'):-len('_bundle')]
        return section if section in _SECTIONS else None
    return None


def _inc(state: Any, key: str, amount: int = 1) -> None:
    state[key] = (state.get(key) or 0) + amount


# ---------------------------------------------------------------------------
# Counter updates (called per tool from after_tool_callback)
# ---------------------------------------------------------------------------


def record_tool_call(
    *,
    tool_name: str,
    args: dict[str, Any] | None,
    tool_response: Any,
    state: Any,
    now: float | None = None,
) -> None:
    """Update the per-run telemetry counters for one tool execution.

    Pure state mutation; never raises (a telemetry bug must not break a
    tool call). ``now`` is injectable for deterministic tests.
    """
    try:
        args = args or {}
        clock = now if now is not None else time.monotonic()
        _inc(state, K_TOOL_CALLS)

        if tool_name == 'load_skill':
            _inc(state, K_SKILLS_LOADED)
            section = _section_from_skill(args.get('skill_name'))
            if section:
                starts = dict(state.get(_K_SECTION_STARTS) or {})
                starts[section] = clock
                state[_K_SECTION_STARTS] = starts
            return

        if tool_name == 'load_skill_resource':
            _inc(state, K_SKILL_RESOURCES)
            return

        if tool_name == 'load_artifacts':
            _inc(state, K_LOAD_ARTIFACTS)
            return

        section = _section_from_save_tool(tool_name)
        if section is not None:
            bundle = args.get('bundle')
            try:
                payload_bytes = len(
                    json.dumps(bundle, ensure_ascii=False).encode('utf-8')
                )
            except (TypeError, ValueError):
                payload_bytes = 0
            _inc(state, K_BUNDLE_BYTES, payload_bytes)

            starts = state.get(_K_SECTION_STARTS) or {}
            start = starts.get(section)
            if isinstance(start, (int, float)):
                durations = dict(state.get(K_SECTION_DURATIONS) or {})
                durations[section] = round(clock - start, 3)
                state[K_SECTION_DURATIONS] = durations
            return

        if tool_name.startswith('apply_') and tool_name.endswith('_patch'):
            resp = tool_response if isinstance(tool_response, dict) else {}
            if resp.get('status') == 'error':
                _inc(state, K_PATCH_REJECTIONS)
            else:
                data = resp.get('data')
                if isinstance(data, dict):
                    _inc(state, K_PATCH_OPS, int(data.get('ops_applied') or 0))
                    _inc(
                        state,
                        K_ANCHOR_DROPS,
                        len(data.get('anchor_drops') or []),
                    )
    except Exception:  # noqa: BLE001 — telemetry must never break a tool call
        return


# ---------------------------------------------------------------------------
# End-of-run assembly
# ---------------------------------------------------------------------------


def _significant_findings_from_report(report: Any) -> int | None:
    """Count BLOCKER + MAJOR findings in a ValidationReport-shaped dict."""
    if not isinstance(report, dict):
        return None
    findings = report.get('findings')
    if isinstance(findings, list):
        return sum(
            1
            for f in findings
            if isinstance(f, dict)
            and str(f.get('severity', '')).upper() in ('BLOCKER', 'MAJOR')
        )
    # Fall back to severity counters when findings list is absent.
    blocker = report.get('blocker')
    major = report.get('major')
    if isinstance(blocker, int) or isinstance(major, int):
        return (blocker or 0) + (major or 0)
    return None


def collect_run_metrics(
    state: Any,
    *,
    architecture_variant: str,
    model: str,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Assemble the canonical comparison-metrics row from session state.

    Covers every field the callbacks + state can supply deterministically.
    Fields requiring run-level instrumentation the callbacks cannot see —
    wall-clock durations, LLM call count, token usage — are returned as
    ``None`` for the run harness to fill from its timers and the Gemini
    SDK usage metadata before writing the comparison report.
    """
    prune_totals = state.get(_K_PRUNE_TOTALS) or {}
    ql_result = state.get(_K_QUALITY_LOOP_RESULT) or {}
    final_report = ql_result.get('final_report') if isinstance(ql_result, dict) else {}

    return {
        # --- identity ---
        'run_id': run_id,
        'architecture_variant': architecture_variant,
        'model': model,
        # --- durations (run harness fills; not visible to callbacks) ---
        'total_duration_s': None,
        'generation_duration_s': None,
        'validation_duration_s': None,
        'quality_loop_duration_s': None,
        # --- call counters (from after_tool_callback) ---
        'llm_call_count': None,  # from Gemini SDK usage metadata
        'tool_call_count': state.get(K_TOOL_CALLS) or 0,
        'load_artifacts_call_count': state.get(K_LOAD_ARTIFACTS) or 0,
        'skills_loaded_count': state.get(K_SKILLS_LOADED) or 0,
        'skill_resources_loaded_count': state.get(K_SKILL_RESOURCES) or 0,
        # --- auto-scope pruning (root-skills variant only) ---
        'pruned_messages_count': prune_totals.get('pruned_messages_total') or 0,
        'pruned_bytes_estimate': prune_totals.get('pruned_bytes_total') or 0,
        'bundle_tool_payload_bytes': state.get(K_BUNDLE_BYTES) or 0,
        # --- tokens (run harness fills from SDK usage metadata) ---
        'estimated_input_tokens': None,
        'estimated_output_tokens': None,
        # --- per-section generation timing ---
        'section_generation_duration_by_section': dict(
            state.get(K_SECTION_DURATIONS) or {}
        ),
        # --- quality loop + findings ---
        'quality_loop_rounds': (
            ql_result.get('rounds_used') if isinstance(ql_result, dict) else None
        ),
        'significant_findings_initial': None,  # run harness: first-round report
        'significant_findings_final': _significant_findings_from_report(
            final_report
        ),
        # --- patch engine ---
        'patch_ops_applied': state.get(K_PATCH_OPS) or 0,
        'patch_rejections': state.get(K_PATCH_REJECTIONS) or 0,
        'anchor_drops': state.get(K_ANCHOR_DROPS) or 0,
        # --- terminal status ---
        'final_status': (
            ql_result.get('status') if isinstance(ql_result, dict) else None
        ),
    }
