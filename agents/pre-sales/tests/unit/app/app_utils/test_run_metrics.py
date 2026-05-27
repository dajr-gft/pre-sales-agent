"""Unit tests for the A/B comparison telemetry (run_metrics).

Coverage: the per-tool counter updates (record_tool_call) and the
end-of-run assembly (collect_run_metrics), driven against a plain dict
state — the same shape ADK's session state presents.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.app_utils.run_metrics import (
    K_ANCHOR_DROPS,
    K_BUNDLE_BYTES,
    K_LOAD_ARTIFACTS,
    K_PATCH_OPS,
    K_PATCH_REJECTIONS,
    K_SECTION_DURATIONS,
    K_SKILL_RESOURCES,
    K_SKILLS_LOADED,
    K_TOOL_CALLS,
    collect_run_metrics,
    record_tool_call,
)


def _rec(state: dict[str, Any], tool_name: str, **kw) -> None:
    record_tool_call(
        tool_name=tool_name,
        args=kw.get('args'),
        tool_response=kw.get('tool_response'),
        state=state,
        now=kw.get('now'),
    )


class TestRecordToolCall:
    def test_tool_call_count_increments_for_every_tool(self):
        state: dict[str, Any] = {}
        _rec(state, 'load_artifacts', args={})
        _rec(state, 'assemble_sow_payload', args={})
        _rec(state, 'stage_sow', args={})
        assert state[K_TOOL_CALLS] == 3

    def test_skill_and_resource_and_artifact_counters(self):
        state: dict[str, Any] = {}
        _rec(state, 'load_artifacts', args={})
        _rec(state, 'load_skill', args={'skill_name': 'sow-requirements'})
        _rec(state, 'load_skill_resource',
             args={'skill_name': 'sow-requirements', 'file_path': 'references/x.md'})
        _rec(state, 'load_skill', args={'skill_name': 'sow-narrative'})
        assert state[K_LOAD_ARTIFACTS] == 1
        assert state[K_SKILLS_LOADED] == 2
        assert state[K_SKILL_RESOURCES] == 1

    def test_bundle_payload_bytes_accumulate(self):
        state: dict[str, Any] = {}
        _rec(state, 'save_requirements_bundle',
             args={'bundle': {'functional_requirements': []}},
             tool_response={'status': 'success'})
        first = state[K_BUNDLE_BYTES]
        assert first > 0
        _rec(state, 'save_narrative_bundle',
             args={'bundle': {'executive_summary': 'x' * 100}},
             tool_response={'status': 'success'})
        assert state[K_BUNDLE_BYTES] > first

    def test_section_duration_measured_between_load_skill_and_save(self):
        state: dict[str, Any] = {}
        _rec(state, 'load_skill',
             args={'skill_name': 'sow-delivery-plan'}, now=10.0)
        _rec(state, 'save_delivery_plan_bundle',
             args={'bundle': {}}, tool_response={'status': 'success'}, now=14.25)
        assert state[K_SECTION_DURATIONS] == {'delivery_plan': 4.25}

    def test_save_without_prior_load_skill_records_no_duration(self):
        state: dict[str, Any] = {}
        _rec(state, 'save_requirements_bundle',
             args={'bundle': {}}, tool_response={'status': 'success'}, now=3.0)
        # Bytes still counted, but no duration without a start timestamp.
        assert state[K_BUNDLE_BYTES] >= 0
        assert state.get(K_SECTION_DURATIONS, {}) == {}

    def test_patch_success_accumulates_ops_and_anchor_drops(self):
        state: dict[str, Any] = {}
        _rec(state, 'apply_requirements_patch', args={'ops': []},
             tool_response={'status': 'success',
                            'data': {'ops_applied': 3, 'anchor_drops': ['FR-02']}})
        _rec(state, 'apply_delivery_plan_patch', args={'ops': []},
             tool_response={'status': 'success',
                            'data': {'ops_applied': 2, 'anchor_drops': []}})
        assert state[K_PATCH_OPS] == 5
        assert state[K_ANCHOR_DROPS] == 1

    def test_patch_error_counts_as_rejection(self):
        state: dict[str, Any] = {}
        _rec(state, 'apply_requirements_patch', args={'ops': []},
             tool_response={'status': 'error', 'error': 'bad op'})
        assert state[K_PATCH_REJECTIONS] == 1
        assert state.get(K_PATCH_OPS, 0) == 0

    def test_non_section_skill_does_not_start_a_timer(self):
        state: dict[str, Any] = {}
        # sow-shared is a consultative skill, not a section.
        _rec(state, 'load_skill', args={'skill_name': 'sow-shared'}, now=1.0)
        _rec(state, 'save_requirements_bundle',
             args={'bundle': {}}, tool_response={'status': 'success'}, now=2.0)
        assert state.get(K_SECTION_DURATIONS, {}) == {}

    def test_never_raises_on_malformed_input(self):
        state: dict[str, Any] = {}
        # bundle is not JSON-serialisable; must not raise, bytes stay 0.
        _rec(state, 'save_requirements_bundle',
             args={'bundle': {1, 2, 3}}, tool_response={'status': 'success'})
        assert state[K_TOOL_CALLS] == 1
        assert state.get(K_BUNDLE_BYTES, 0) == 0


class TestCollectRunMetrics:
    def test_assembles_counters_and_prune_and_quality_loop(self):
        state: dict[str, Any] = {
            'app:skill_scope:prune_totals': {
                'pruned_messages_total': 8,
                'pruned_bytes_total': 4096,
                'prune_event_count': 4,
            },
            'app:sow:quality_loop_result': {
                'status': 'passed',
                'rounds_used': 2,
                'final_report': {
                    'findings': [
                        {'severity': 'BLOCKER'},
                        {'severity': 'MAJOR'},
                        {'severity': 'MINOR'},
                    ],
                },
            },
        }
        _rec(state, 'load_skill', args={'skill_name': 'sow-requirements'}, now=1.0)
        _rec(state, 'save_requirements_bundle',
             args={'bundle': {'functional_requirements': []}},
             tool_response={'status': 'success'}, now=2.0)

        m = collect_run_metrics(
            state, architecture_variant='root_skills_autoscoped',
            model='gemini-x', run_id='run-1',
        )
        assert m['architecture_variant'] == 'root_skills_autoscoped'
        assert m['model'] == 'gemini-x'
        assert m['run_id'] == 'run-1'
        assert m['tool_call_count'] == 2
        assert m['skills_loaded_count'] == 1
        assert m['pruned_messages_count'] == 8
        assert m['pruned_bytes_estimate'] == 4096
        assert m['quality_loop_rounds'] == 2
        assert m['final_status'] == 'passed'
        # BLOCKER + MAJOR are significant; MINOR is not.
        assert m['significant_findings_final'] == 2
        assert 'requirements' in m['section_generation_duration_by_section']

    def test_empty_state_yields_zeroed_schema(self):
        m = collect_run_metrics(
            {}, architecture_variant='multi_agent_manifest', model='gemini-x',
        )
        assert m['tool_call_count'] == 0
        assert m['pruned_messages_count'] == 0
        assert m['bundle_tool_payload_bytes'] == 0
        assert m['section_generation_duration_by_section'] == {}
        assert m['quality_loop_rounds'] is None
        assert m['final_status'] is None
        # Harness-filled fields are present as None (schema completeness).
        assert m['total_duration_s'] is None
        assert m['estimated_input_tokens'] is None
        assert m['llm_call_count'] is None

    def test_significant_findings_falls_back_to_severity_counts(self):
        state = {
            'app:sow:quality_loop_result': {
                'status': 'exhausted',
                'rounds_used': 5,
                'final_report': {'blocker': 1, 'major': 3, 'minor': 7},
            }
        }
        m = collect_run_metrics(
            state, architecture_variant='x', model='y',
        )
        assert m['significant_findings_final'] == 4
