"""Unit tests for ``app.shared.validators``.

``ContentValidator`` is the deterministic gate between LLM output and
document generation. Each individual check is unit-tested in isolation so
regressions point directly at the rule that broke.
"""
from __future__ import annotations

from copy import deepcopy

import pytest

from app.shared.validators import (
    ContentValidator,
    Severity,
    ValidationIssue,
    ValidationResult,
)


class TestSeverityEnum:
    def test_has_error_and_warning(self):
        assert Severity.ERROR.value == 'error'
        assert Severity.WARNING.value == 'warning'

    def test_string_equivalence(self):
        assert Severity.ERROR == 'error'
        assert Severity.WARNING == 'warning'


class TestValidationIssue:
    def test_str_formats_error_prefix(self):
        issue = ValidationIssue(severity='error', field='x', message='bad')
        assert str(issue) == '[ERROR] x: bad'

    def test_str_formats_warning_prefix(self):
        issue = ValidationIssue(severity='warning', field='x', message='meh')
        assert str(issue) == '[WARN] x: meh'

    def test_suggestion_rendered_when_present(self):
        issue = ValidationIssue(
            severity='error', field='x', message='bad', suggestion='do y'
        )
        assert str(issue) == '[ERROR] x: bad -> do y'

    def test_empty_suggestion_not_rendered(self):
        issue = ValidationIssue(severity='error', field='x', message='bad')
        assert '->' not in str(issue)


class TestValidationResult:
    def test_passed_when_no_errors(self):
        r = ValidationResult(issues=[
            ValidationIssue(severity='warning', field='a', message='m'),
        ])
        assert r.passed is True

    def test_not_passed_with_errors(self):
        r = ValidationResult(issues=[
            ValidationIssue(severity='error', field='a', message='m'),
        ])
        assert r.passed is False

    def test_errors_and_warnings_partitioned(self):
        r = ValidationResult(issues=[
            ValidationIssue(severity='error', field='a', message='e1'),
            ValidationIssue(severity='warning', field='b', message='w1'),
            ValidationIssue(severity='error', field='c', message='e2'),
        ])
        assert len(r.errors) == 2
        assert len(r.warnings) == 1
        assert {i.message for i in r.errors} == {'e1', 'e2'}

    def test_to_dict_serialization(self):
        r = ValidationResult(issues=[
            ValidationIssue(
                severity='error', field='a', message='m', suggestion='s'
            ),
        ])
        out = r.to_dict()
        assert out == {
            'passed': False,
            'error_count': 1,
            'warning_count': 0,
            'issues': [
                {
                    'severity': 'error',
                    'field': 'a',
                    'message': 'm',
                    'suggestion': 's',
                }
            ],
        }


class TestFunctionalRequirements:
    def test_valid_fr_passes(self, sow_data):
        result = ContentValidator().validate(sow_data)
        assert not any(
            e.field == 'functional_requirements' for e in result.errors
        )

    @pytest.mark.parametrize(
        'bad_id', ['FR1', 'FR-1', 'fr-01', 'INVALID', '', 'NFR-01']
    )
    def test_invalid_fr_id_triggers_error(self, sow_data, bad_id):
        sow_data['functional_requirements'][0]['number'] = bad_id
        result = ContentValidator().validate(sow_data)
        assert any(
            e.field == 'functional_requirements' and 'FR-XX' in e.message
            for e in result.errors
        )

    @pytest.mark.parametrize('good_id', ['FR-01', 'FR-99', 'FR-001', 'FR-999'])
    def test_accepts_two_and_three_digit_fr_ids(self, sow_data, good_id):
        sow_data['functional_requirements'][0]['number'] = good_id
        result = ContentValidator().validate(sow_data)
        assert not any(
            e.field == 'functional_requirements' and 'FR-XX' in e.message
            for e in result.errors
        )

    def test_short_description_emits_warning(self, sow_data):
        sow_data['functional_requirements'][0]['description'] = 'too short'
        result = ContentValidator().validate(sow_data)
        assert any(
            w.field == 'functional_requirements' and 'too short' in w.message
            for w in result.warnings
        )

    def test_no_frs_no_errors(self, sow_data_minimal):
        """Empty FR list must not raise FR format errors (empty is empty)."""
        result = ContentValidator().validate(sow_data_minimal)
        assert not any(
            'FR-XX' in e.message for e in result.errors
        )


class TestNonFunctionalRequirements:
    @pytest.mark.parametrize(
        'bad_id', ['NFR1', 'NFR-1', 'nfr-01', 'BAD-01', 'FR-01']
    )
    def test_invalid_nfr_id_triggers_error(self, sow_data, bad_id):
        sow_data['non_functional_requirements'][0]['number'] = bad_id
        result = ContentValidator().validate(sow_data)
        assert any(
            'NFR-XX' in e.message for e in result.errors
        )

    @pytest.mark.parametrize('good_id', ['NFR-01', 'NFR-99', 'NFR-100'])
    def test_valid_nfr_ids(self, sow_data, good_id):
        sow_data['non_functional_requirements'][0]['number'] = good_id
        result = ContentValidator().validate(sow_data)
        assert not any('NFR-XX' in e.message for e in result.errors)


class TestRoleDescriptions:
    def test_short_role_description_emits_warning(self, sow_data):
        sow_data['partner_roles'][0]['responsibilities'] = 'too short'
        result = ContentValidator().validate(sow_data)
        assert any(
            w.field == 'partner_roles' and 'too short' in w.message
            for w in result.warnings
        )

    def test_customer_roles_are_also_checked(self, sow_data):
        sow_data['customer_roles'][0]['responsibilities'] = 'brief'
        result = ContentValidator().validate(sow_data)
        assert any(
            w.field == 'customer_roles' for w in result.warnings
        )

    def test_exactly_100_chars_does_not_warn(self, sow_data):
        sow_data['partner_roles'][0]['responsibilities'] = 'x' * 100
        result = ContentValidator().validate(sow_data)
        # 100 is the lower bound → no warning
        assert not any(
            w.field == 'partner_roles' and 'too short' in w.message
            for w in result.warnings
        )


class TestArchitectureDescription:
    def test_valid_passes(self, sow_data):
        result = ContentValidator().validate(sow_data)
        assert not any(
            i.field == 'architecture_description'
            for i in result.issues
        )

    def test_short_description_warns(self, sow_data):
        sow_data['architecture_description'] = ' '.join(['word'] * 100)
        result = ContentValidator().validate(sow_data)
        assert any(
            i.field == 'architecture_description'
            and i.severity == 'warning'
            for i in result.issues
        )

    def test_very_short_description_is_error(self, sow_data):
        sow_data['architecture_description'] = 'too short'
        result = ContentValidator().validate(sow_data)
        assert any(
            i.field == 'architecture_description'
            and i.severity == 'error'
            for i in result.issues
        )

    def test_architecture_check_skipped_in_content_stage(self, sow_data):
        """Content stage runs BEFORE architecture exists → skip arch checks."""
        sow_data['architecture_description'] = 'too short'
        result = ContentValidator().validate(sow_data, stage='content')
        assert not any(
            i.field == 'architecture_description' for i in result.issues
        )


class TestAssumptionConsequences:
    def test_consequences_present_no_warning(self, sow_data):
        result = ContentValidator().validate(sow_data)
        assert not any(
            w.field == 'assumptions'
            and 'consequence' in w.message.lower()
            for w in result.warnings
        )

    def test_over_40_percent_missing_consequences_warns(self, sow_data):
        # Rewrite all but one to lack consequence keywords
        sow_data['assumptions'] = [
            'Customer must provide API credentials.'
        ] * 10 + [
            'Customer must provide test data. Failure will result in timeline extension.'
        ]
        result = ContentValidator().validate(sow_data)
        assert any(
            w.field == 'assumptions'
            and 'consequence' in w.message.lower()
            for w in result.warnings
        )

    def test_all_well_formed_no_warning(self, sow_data):
        sow_data['assumptions'] = [
            'Customer must X. Failure will result in timeline extension.',
            'Customer must Y. This may result in additional cost.',
            'Customer must Z. Impact: change request required.',
        ]
        result = ContentValidator().validate(sow_data)
        assert not any(
            'consequence' in w.message.lower() for w in result.warnings
        )

    def test_empty_assumptions_no_warning(self, sow_data):
        sow_data['assumptions'] = []
        result = ContentValidator().validate(sow_data)
        assert not any(
            w.field == 'assumptions'
            and 'consequence' in w.message.lower()
            for w in result.warnings
        )


class TestTimelineConsistency:
    def test_matching_phases_no_warning(self, sow_data):
        result = ContentValidator().validate(sow_data)
        assert not any(
            w.field == 'timeline' for w in result.warnings
        )

    def test_phase_missing_from_timeline_warns(self, sow_data):
        sow_data['timeline'] = sow_data['timeline'][:1]  # drop phases 2 & 3
        result = ContentValidator().validate(sow_data)
        assert any(
            w.field == 'timeline' for w in result.warnings
        )

    def test_silent_when_either_is_empty(self, sow_data):
        sow_data['timeline'] = []
        result = ContentValidator().validate(sow_data)
        assert not any(w.field == 'timeline' for w in result.warnings)


class TestTechStackConsistency:
    def test_skipped_when_either_empty(self, sow_data):
        sow_data.pop('technology_stack', None)
        result = ContentValidator().validate(sow_data)
        assert not any(
            w.field == 'technology_stack' for w in result.warnings
        )

    def test_in_stack_not_in_arch_warns(self, sow_data):
        sow_data['technology_stack'] = [
            {'service': 'Cloud Run', 'purpose': 'API'},
            {'service': 'Firestore', 'purpose': 'DB'},  # not in components
        ]
        sow_data['architecture_components'] = [
            {'name': 'Cloud Run', 'role': 'API'},
        ]
        result = ContentValidator().validate(sow_data)
        assert any(
            w.field == 'technology_stack' for w in result.warnings
        )

    def test_in_arch_not_in_stack_warns(self, sow_data):
        sow_data['technology_stack'] = [
            {'service': 'Cloud Run', 'purpose': 'API'},
        ]
        sow_data['architecture_components'] = [
            {'name': 'Cloud Run', 'role': 'API'},
            {'name': 'Dataflow', 'role': 'ETL'},  # not in stack
        ]
        result = ContentValidator().validate(sow_data)
        assert any(
            w.field == 'architecture_components' for w in result.warnings
        )

    def test_case_insensitive(self, sow_data):
        sow_data['technology_stack'] = [
            {'service': 'cloud run', 'purpose': 'x'},
        ]
        sow_data['architecture_components'] = [
            {'name': 'Cloud Run', 'role': 'x'},
        ]
        result = ContentValidator().validate(sow_data)
        assert not any(
            w.field in ('technology_stack', 'architecture_components')
            for w in result.warnings
        )


class TestDeliverableCoverage:
    def test_all_phases_covered_no_warning(self, sow_data):
        result = ContentValidator().validate(sow_data)
        assert not any(
            w.field == 'deliverables'
            and 'no deliverables' in w.message.lower()
            for w in result.warnings
        )

    def test_phase_with_no_deliverables_warns(self, sow_data):
        sow_data['deliverables'] = [
            {
                'activity': 'Phase 1',
                'name': 'D',
                'description': 'desc',
                'format': 'Document',
            }
        ]
        result = ContentValidator().validate(sow_data)
        assert any(
            w.field == 'deliverables'
            and 'no deliverables' in w.message.lower()
            for w in result.warnings
        )

    def test_partial_match_counts_as_covered(self, sow_data):
        """'Phase 1' (deliverable.activity) matches 'Phase 1: Discovery' (phase.name)."""
        # already the fixture default → no warning expected
        result = ContentValidator().validate(sow_data)
        assert not any(
            w.field == 'deliverables'
            and 'no deliverables' in w.message.lower()
            for w in result.warnings
        )

    def test_skipped_when_either_empty(self, sow_data):
        sow_data['activity_phases'] = []
        result = ContentValidator().validate(sow_data)
        assert not any(
            w.field == 'deliverables'
            and 'no deliverables' in w.message.lower()
            for w in result.warnings
        )


class TestOosCount:
    def test_valid_count_no_warning(self, sow_data):
        result = ContentValidator().validate(sow_data)
        assert not any(w.field == 'out_of_scope' for w in result.warnings)

    def test_low_count_warns(self, sow_data):
        sow_data['out_of_scope'] = ['item 1', 'item 2']
        result = ContentValidator().validate(sow_data)
        assert any(
            w.field == 'out_of_scope' and w.severity == 'warning'
            for w in result.warnings
        )

    def test_empty_no_warning(self, sow_data):
        """0 < len(oos) < 20 — so empty lists are NOT warned by this rule."""
        sow_data['out_of_scope'] = []
        result = ContentValidator().validate(sow_data)
        assert not any(w.field == 'out_of_scope' for w in result.warnings)

    def test_boundary_at_20(self, sow_data):
        sow_data['out_of_scope'] = [f'item {i}' for i in range(20)]
        result = ContentValidator().validate(sow_data)
        assert not any(w.field == 'out_of_scope' for w in result.warnings)


class TestHappyPath:
    """End-to-end smoke tests of the public API."""

    def test_daf_payload_passes(self, sow_data):
        result = ContentValidator().validate(sow_data)
        assert result.passed, [str(e) for e in result.errors]

    def test_psf_payload_passes(self, sow_data_psf):
        result = ContentValidator().validate(sow_data_psf)
        assert result.passed, [str(e) for e in result.errors]

    def test_full_stage_runs_all_checks(self, sow_data):
        """In full stage, arch + consumption + tech checks fire.

        Content stage skips these — we verify by forcing a failure that is
        only caught in full stage.
        """
        sow_data['architecture_description'] = 'short'
        full = ContentValidator().validate(sow_data, stage='full')
        content = ContentValidator().validate(sow_data, stage='content')
        assert any(
            i.field == 'architecture_description' for i in full.issues
        )
        assert not any(
            i.field == 'architecture_description' for i in content.issues
        )
