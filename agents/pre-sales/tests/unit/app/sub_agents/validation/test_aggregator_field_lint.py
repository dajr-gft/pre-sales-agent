"""Contract tests for the aggregator's field-vocabulary lint pass.

The lint runs AFTER calibration and is the FINAL word: structural
impossibility (no writer for the cited fields) beats any policy claim
that the category is "mechanically fixable". Three classes of action
this suite pins:

- **Manifest-only fields** → ``not_fixable_by_agent`` + human review,
  even when ``(skill, category)`` is in the policy auto-fix table.
- **Orphan fields** → ``not_fixable_by_agent`` + human review.
- **Mixed (writable + noise)** → fields trimmed to the writable
  subset; resolution mode preserved so the router still dispatches.

The tests speak in CLASS terms (manifest-only / orphan-only / mixed)
and pull representative field names from the canonical vocabulary at
runtime — no specific schema field is hardcoded. Adding or renaming a
single field cannot make these tests silently lose coverage.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from app.sub_agents.validation.aggregator import (
    _POLICY_FORCED_AUTO_FIXABLE,
    _lint_field_vocabulary,
    validation_aggregator_agent,
)
from app.sub_agents.validation.field_vocabulary import (
    BUNDLE_OWNED_FIELDS,
    MANIFEST_DERIVED_FIELDS,
)
from app.sub_agents.validation.schema import (
    STATE_DET_RESULT,
    STATE_REPORT_PARTIAL,
    STATE_STAGE,
    DeterministicResult,
    Finding,
    ValidationReport,
    skill_findings_state_key,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _finding(
    *,
    fid: str = 'sq-001',
    skill: str = 'semantic_quality',
    category: str = 'naming_drift',
    severity: str = 'MAJOR',
    confidence: float = 0.9,
    evidence: str = "SOW field cites an entity not present in the sources",
    recommendation: str = 'Remove the ungrounded item.',
    fields: tuple[str, ...] = (),
    resolution_mode: str = 'auto_fixable',
    requires_human_review: bool = False,
) -> dict[str, Any]:
    return {
        'id': fid,
        'skill': skill,
        'category': category,
        'severity': severity,
        'confidence': confidence,
        'evidence': evidence,
        'recommendation': recommendation,
        'fields': list(fields),
        'persistent': False,
        'resolution_mode': resolution_mode,
        'requires_human_review': requires_human_review,
        'model_used': 'test-model',
    }


def _make_ctx(state: dict[str, Any]) -> MagicMock:
    ctx = MagicMock(name='InvocationContext')
    ctx.session.state = state
    ctx.invocation_id = 'inv-test'
    ctx.branch = 'test'
    return ctx


def _state_with(skill_findings: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    state: dict[str, Any] = {
        STATE_DET_RESULT: DeterministicResult(
            passed=True, error_count=0,
        ).model_dump(),
        STATE_STAGE: 'full',
    }
    for name in (
        'contradictions',
        'contractual_exposure',
        'disclosures',
        'semantic_quality',
    ):
        state[skill_findings_state_key(name)] = {
            'findings': skill_findings.get(name, []),
        }
    return state


async def _run(state: dict[str, Any]) -> ValidationReport:
    ctx = _make_ctx(state)
    async for _ in validation_aggregator_agent._run_async_impl(ctx):
        pass
    return ValidationReport.model_validate(state[STATE_REPORT_PARTIAL])


def _sample_bundle_field() -> str:
    return next(iter(BUNDLE_OWNED_FIELDS))


def _sample_manifest_field() -> str:
    return next(iter(MANIFEST_DERIVED_FIELDS))


# ---------------------------------------------------------------------------
# Unit-level — _lint_field_vocabulary in isolation
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_lint_reclassifies_manifest_only_finding():
    """A finding citing only manifest-derived fields cannot be patched."""
    f = Finding.model_validate(
        _finding(
            fields=(_sample_manifest_field(),),
            resolution_mode='auto_fixable',
            requires_human_review=False,
        )
    )

    [out] = _lint_field_vocabulary([f]).findings

    assert out.resolution_mode == 'not_fixable_by_agent'
    assert out.requires_human_review is True
    # Fields are NOT trimmed for manifest-only — the reviewer can still
    # see what the critic flagged, just routed to human-review now.
    assert list(out.fields) == [_sample_manifest_field()]


@pytest.mark.unit
def test_lint_reclassifies_orphan_only_finding():
    """A finding citing only unknown fields has no writer in the pipeline."""
    f = Finding.model_validate(
        _finding(
            fields=('project_identity', 'mystery_key'),
            resolution_mode='auto_fixable',
        )
    )

    [out] = _lint_field_vocabulary([f]).findings

    assert out.resolution_mode == 'not_fixable_by_agent'
    assert out.requires_human_review is True
    # Preserve original prose so the human review surface keeps context.
    assert list(out.fields) == ['project_identity', 'mystery_key']


@pytest.mark.unit
def test_lint_trims_mixed_fields_to_writable_subset():
    """Mixed claims keep the writable fields and drop the rest."""
    bundle_field = _sample_bundle_field()
    manifest_field = _sample_manifest_field()
    f = Finding.model_validate(
        _finding(
            fields=(bundle_field, manifest_field, 'unknown_thing'),
            resolution_mode='auto_fixable',
        )
    )

    [out] = _lint_field_vocabulary([f]).findings

    assert list(out.fields) == [bundle_field]
    # Mode preserved — the writable subset is patchable, so the router
    # will dispatch to the section that owns ``bundle_field``.
    assert out.resolution_mode == 'auto_fixable'
    assert out.requires_human_review is False


@pytest.mark.unit
def test_lint_does_not_touch_empty_fields_findings():
    """Empty fields = textual finding; router uses (skill, category) fallback."""
    f = Finding.model_validate(
        _finding(
            fields=(),
            resolution_mode='auto_fixable',
            requires_human_review=False,
        )
    )

    [out] = _lint_field_vocabulary([f]).findings

    assert list(out.fields) == []
    assert out.resolution_mode == 'auto_fixable'
    assert out.requires_human_review is False


@pytest.mark.unit
def test_lint_does_not_touch_all_writable_findings():
    """Happy path — fully writable claim flows through unchanged."""
    bundle_field = _sample_bundle_field()
    f = Finding.model_validate(
        _finding(
            fields=(bundle_field,),
            resolution_mode='auto_fixable',
        )
    )

    [out] = _lint_field_vocabulary([f]).findings

    assert list(out.fields) == [bundle_field]
    assert out.resolution_mode == 'auto_fixable'
    assert out.requires_human_review is False


# ---------------------------------------------------------------------------
# Integration — lint precedence over the policy auto-fix table
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_manifest_only_finding_for_policy_category_routes_to_human_review():
    """`(semantic_quality, naming_drift)` is in the policy auto-fix
    table. A finding emitting that category with ALL fields metadata-
    derived is still structurally unfixable (no section writes the
    administrative metadata). The lint must override the policy claim —
    the agent cannot conjure a writer that does not exist.
    """
    assert ('semantic_quality', 'naming_drift') in _POLICY_FORCED_AUTO_FIXABLE

    state = _state_with(
        {
            'semantic_quality': [
                _finding(
                    fid='sq-001',
                    skill='semantic_quality',
                    category='naming_drift',
                    severity='MAJOR',
                    fields=(_sample_manifest_field(),),
                    resolution_mode='auto_fixable',
                ),
            ]
        }
    )

    report = await _run(state)

    # Lint wins over the policy table.
    assert report.findings[0].resolution_mode == 'not_fixable_by_agent'
    assert report.findings[0].requires_human_review is True
    assert report.overall_status == 'needs_human_review'


@pytest.mark.unit
async def test_orphan_finding_for_policy_category_routes_to_human_review():
    """Same precedence rule for orphan fields — the policy table's
    ``auto_fixable`` claim cannot stand when no writer owns the field.
    """
    state = _state_with(
        {
            'semantic_quality': [
                _finding(
                    fid='sq-002',
                    skill='semantic_quality',
                    category='naming_drift',
                    severity='MAJOR',
                    fields=('project_identity',),
                    resolution_mode='auto_fixable',
                ),
            ]
        }
    )

    report = await _run(state)

    assert report.findings[0].resolution_mode == 'not_fixable_by_agent'
    assert report.findings[0].requires_human_review is True


@pytest.mark.unit
async def test_writable_finding_for_policy_category_remains_auto_fixable():
    """Sanity guard — when fields ARE writable, policy still fires."""
    bundle_field = _sample_bundle_field()
    state = _state_with(
        {
            'semantic_quality': [
                _finding(
                    fid='sq-003',
                    skill='semantic_quality',
                    category='naming_drift',
                    severity='MAJOR',
                    fields=(bundle_field,),
                    # LLM emitted decision_required; policy must force
                    # auto_fixable; lint must NOT undo that because the
                    # finding has a writable target.
                    resolution_mode='decision_required',
                ),
            ]
        }
    )

    report = await _run(state)

    assert report.findings[0].resolution_mode == 'auto_fixable'


@pytest.mark.unit
async def test_mixed_fields_finding_keeps_only_writable_after_lint():
    """End-to-end: mixed claim → trimmed → routed to writable section."""
    bundle_field = _sample_bundle_field()
    state = _state_with(
        {
            'semantic_quality': [
                _finding(
                    fid='sq-004',
                    skill='semantic_quality',
                    category='naming_drift',
                    severity='MAJOR',
                    fields=(bundle_field, 'unknown', _sample_manifest_field()),
                ),
            ]
        }
    )

    report = await _run(state)

    [linted] = report.findings
    assert list(linted.fields) == [bundle_field]
    assert linted.resolution_mode == 'auto_fixable'


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_lint_emits_reclassification_log_for_manifest_only(caplog):
    """Structured log carries the action + reason so observability
    can count manifest-only findings without scraping prose.
    """
    f = Finding.model_validate(
        _finding(fields=(_sample_manifest_field(),))
    )

    import logging
    caplog.set_level(logging.WARNING)
    _lint_field_vocabulary([f])

    # structlog renders the event key via the message arg.
    messages = [r.getMessage() for r in caplog.records]
    assert any('finding_field_lint_applied' in m for m in messages)


@pytest.mark.unit
def test_lint_emits_trim_log_for_mixed_fields(caplog):
    """Trim action telemetry is INFO (not WARNING) — degraded but
    recoverable; the loop keeps going."""
    bundle_field = _sample_bundle_field()
    f = Finding.model_validate(
        _finding(fields=(bundle_field, 'mystery', _sample_manifest_field())),
    )

    import logging
    caplog.set_level(logging.INFO)
    _lint_field_vocabulary([f])

    messages = [r.getMessage() for r in caplog.records]
    assert any('finding_field_lint_applied' in m for m in messages)


# ---------------------------------------------------------------------------
# LintReport counters + drift sets — observability contract
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_lint_report_counts_each_action_class():
    """Aggregator emits class counts per round for dashboarding."""
    bundle = _sample_bundle_field()
    manifest = _sample_manifest_field()
    findings = [
        Finding.model_validate(_finding(fid='m-1', fields=(manifest,))),
        Finding.model_validate(_finding(fid='m-2', fields=(manifest,))),
        Finding.model_validate(_finding(fid='o-1', fields=('rogue_a',))),
        Finding.model_validate(_finding(fid='mix-1', fields=(bundle, 'rogue_b'))),
        Finding.model_validate(_finding(fid='clean-1', fields=(bundle,))),
        Finding.model_validate(_finding(fid='empty-1', fields=())),
    ]

    report = _lint_field_vocabulary(findings)

    assert report.reclassified_manifest_only == 2
    assert report.reclassified_orphan_only == 1
    assert report.fields_trimmed == 1
    # ``clean-1`` and ``empty-1`` are both untouched.
    assert report.untouched == 2
    assert len(report.findings) == 6


@pytest.mark.unit
def test_lint_report_collects_distinct_drift_field_names():
    """The drift sets are the actionable observability signal — they
    name exactly which orphan/manifest tokens the critic emitted this
    round, sorted for diff-stability across runs.
    """
    manifest_a, manifest_b = list(MANIFEST_DERIVED_FIELDS)[:2]
    findings = [
        Finding.model_validate(_finding(fid='m-1', fields=(manifest_a,))),
        Finding.model_validate(_finding(fid='m-2', fields=(manifest_a, manifest_b))),
        Finding.model_validate(_finding(fid='o-1', fields=('project_identity',))),
        Finding.model_validate(_finding(fid='o-2', fields=('project_identity', 'mystery_key'))),
    ]

    report = _lint_field_vocabulary(findings)

    assert report.unique_manifest_fields_seen == sorted([manifest_a, manifest_b])
    assert report.unique_orphan_fields_seen == sorted(
        ['project_identity', 'mystery_key']
    )


@pytest.mark.unit
def test_lint_report_breaks_down_actions_by_skill():
    """Per-skill action breakdown points at which SKILL.md to harden."""
    bundle = _sample_bundle_field()
    manifest = _sample_manifest_field()
    findings = [
        Finding.model_validate(
            _finding(fid='c-1', skill='coverage', fields=(manifest,))
        ),
        Finding.model_validate(
            _finding(
                fid='cn-1', skill='contradictions',
                category='fr_vs_nfr', fields=(bundle, 'rogue'),
            )
        ),
        Finding.model_validate(
            _finding(
                fid='cn-2', skill='contradictions',
                category='fr_vs_nfr', fields=('orphan_only',),
            )
        ),
    ]

    report = _lint_field_vocabulary(findings)

    assert report.actions_by_skill == {
        'coverage': {'reclassified_manifest_only': 1},
        'contradictions': {
            'fields_trimmed': 1,
            'reclassified_orphan_only': 1,
        },
    }


@pytest.mark.unit
async def test_aggregator_emits_vocabulary_summary_event(caplog):
    """The aggregator emits ``validation_field_vocabulary_summary`` once
    per round so downstream observability can chart drift.
    """
    import logging
    caplog.set_level(logging.INFO)

    state = _state_with(
        {
            'coverage': [
                _finding(
                    fid='coverage-001',
                    fields=(_sample_manifest_field(),),
                ),
                _finding(
                    fid='coverage-002',
                    fields=('project_identity',),
                ),
            ]
        }
    )
    await _run(state)

    messages = [r.getMessage() for r in caplog.records]
    assert any('validation_field_vocabulary_summary' in m for m in messages)
    assert any('validation_raw_skill_emission' in m for m in messages)
