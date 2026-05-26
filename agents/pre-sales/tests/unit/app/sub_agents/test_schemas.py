"""Unit tests for section bundle Pydantic schemas.

The bundles are the structural contract between each section sub-agent
and the assembler. ``extra='forbid'`` means an agent producing extra
keys fails fast — these tests pin both the happy path and the failure
modes that catch real-world drift.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.sub_agents.schemas import (
    ArchitectureBundle,
    CONTENT_STAGE_KEYS,
    DeliveryPlanBundle,
    FULL_STAGE_KEYS,
    NarrativeBundle,
    RequirementsBundle,
    ScopeBoundariesBundle,
    SOW_BUNDLE_STATE_KEYS,
)


class TestRequirementsBundle:
    def test_round_trip(self):
        bundle = RequirementsBundle.model_validate({
            'functional_requirements': [
                {'number': 'FR-01', 'description': 'The system shall ingest data.'},
            ],
            'non_functional_requirements': [
                {'number': 'NFR-01', 'description': 'Security: TLS 1.3.'},
            ],
        })
        assert bundle.functional_requirements[0].number == 'FR-01'

    def test_rejects_extra_fields(self):
        with pytest.raises(ValidationError):
            RequirementsBundle.model_validate({
                'functional_requirements': [],
                'non_functional_requirements': [],
                'unexpected_field': 'should be rejected',
            })

    def test_requires_both_lists(self):
        with pytest.raises(ValidationError):
            RequirementsBundle.model_validate({'functional_requirements': []})


class TestDeliveryPlanBundle:
    def test_minimal_valid(self):
        bundle = DeliveryPlanBundle.model_validate({
            'activity_phases': [],
            'deliverables': [],
            'timeline': [],
            'partner_roles': [],
            'customer_roles': [],
            'success_criteria': [],
        })
        assert bundle.objectives == []  # default factory

    def test_rejects_unknown_keys_in_role(self):
        with pytest.raises(ValidationError):
            DeliveryPlanBundle.model_validate({
                'activity_phases': [],
                'deliverables': [],
                'timeline': [],
                'partner_roles': [
                    {'role': 'PM', 'responsibilities': 'Owns timeline.', 'extra': 'no'},
                ],
                'customer_roles': [],
                'success_criteria': [],
            })


class TestScopeBoundariesBundle:
    def test_optional_fields_default(self):
        bundle = ScopeBoundariesBundle.model_validate({
            'assumptions': ['Customer provides access.'],
            'out_of_scope': ['Hardware procurement.'],
        })
        assert bundle.risks == []
        assert bundle.handover_disclaimers == []
        assert bundle.change_request_policy_text == ''


class TestArchitectureBundle:
    def test_round_trip(self):
        bundle = ArchitectureBundle.model_validate({
            'architecture_description': 'Layered cloud-native solution.',
            'architecture_components': [
                {'name': 'Cloud Run', 'role': 'Hosts API.'},
            ],
            'architecture_integrations': [
                {'name': 'SAP', 'description': 'Source system.'},
            ],
            'technology_stack': [
                {'service': 'BigQuery', 'purpose': 'Warehouse.'},
            ],
        })
        assert bundle.technology_stack[0].service == 'BigQuery'


class TestNarrativeBundle:
    def test_domain_optional(self):
        bundle = NarrativeBundle.model_validate({
            'executive_summary': 'Modernizes data platform.',
            'partner_overview': 'GFT is a Premier Partner.',
            'customer_overview': 'Acme manufactures globally.',
        })
        assert bundle.customer_primary_domain is None

    def test_domain_passes_through_when_provided(self):
        bundle = NarrativeBundle.model_validate({
            'executive_summary': 's',
            'partner_overview': 'p',
            'customer_overview': 'c',
            'customer_primary_domain': 'acme.com',
        })
        assert bundle.customer_primary_domain == 'acme.com'


class TestStateKeyContract:
    """The assembler relies on these key constants — pin them explicitly."""

    def test_section_keys_use_app_sow_namespace(self):
        """Section bundles live under the ``app:sow:*`` namespace. The
        ``manifest`` entry is an explicit exception — it must align with
        the pre-existing manifest tools (state['extraction_manifest']).
        Asserted separately in ``test_manifest_key_aligned_with_tools``.
        """
        for key, value in SOW_BUNDLE_STATE_KEYS.items():
            if key == 'manifest':
                continue
            assert value.startswith('app:sow:'), (key, value)

    def test_manifest_key_aligned_with_manifest_tools(self):
        """Discovery's ``finalize_extraction_manifest`` writes to this key;
        ``assemble_sow_payload`` and ``load_extraction_manifest`` read from
        the same key. Drift here would silently break the SOW pipeline.
        """
        from app.tools.sow.manifest_tools import _MANIFEST_STATE_KEY

        assert SOW_BUNDLE_STATE_KEYS['manifest'] == _MANIFEST_STATE_KEY

    def test_content_stage_is_subset_of_full(self):
        assert set(CONTENT_STAGE_KEYS).issubset(set(FULL_STAGE_KEYS))

    def test_full_stage_adds_architecture_and_narrative(self):
        added = set(FULL_STAGE_KEYS) - set(CONTENT_STAGE_KEYS)
        assert added == {
            SOW_BUNDLE_STATE_KEYS['architecture'],
            SOW_BUNDLE_STATE_KEYS['narrative'],
        }
