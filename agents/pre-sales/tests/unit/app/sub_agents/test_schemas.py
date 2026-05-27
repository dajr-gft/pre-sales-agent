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
    Deliverable,
    DeliveryPlanBundle,
    FULL_STAGE_KEYS,
    NarrativeBundle,
    RequirementsBundle,
    Risk,
    ScopeBoundariesBundle,
    SOW_BUNDLE_STATE_KEYS,
)
from app.tools.sow._patch_models import _COLLECTION_SPECS


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

    def test_deliverable_number_required_on_item_model(self):
        """``Deliverable`` carries a stable ``number`` id used by the
        patch engine. Validating an item dict that omits it must fail —
        the bundle-level validator is what injects it for legacy
        bundles, never the item model itself."""
        with pytest.raises(ValidationError):
            Deliverable.model_validate({
                'activity': 'Phase 1',
                'name': 'Doc',
                'description': 'Spec.',
                'format': 'Document',
            })

    def test_bundle_injects_number_when_deliverables_omit_it(self):
        """Backwards compat: legacy bundles produced before
        ``Deliverable.number`` became required must still validate
        because the bundle-level ``model_validator(mode='before')``
        runs :func:`ensure_collection_numbers` on the raw dict."""
        bundle = DeliveryPlanBundle.model_validate({
            'activity_phases': [],
            'deliverables': [
                {
                    'activity': 'Phase 1',
                    'name': 'Doc',
                    'description': 'Spec.',
                    'format': 'Document',
                },
                {
                    'activity': 'Phase 2',
                    'name': 'Doc2',
                    'description': 'Spec2.',
                    'format': 'Document',
                },
            ],
            'timeline': [],
            'partner_roles': [],
            'customer_roles': [],
            'success_criteria': [],
        })
        assert bundle.deliverables[0].number == 'WS-01'
        assert bundle.deliverables[1].number == 'WS-02'

    def test_bundle_preserves_explicit_deliverable_numbers(self):
        """When the section worker emits its own ``number``, the
        validator must leave it alone — the helper is only there to
        backfill blanks. Newly-injected ids must also avoid colliding
        with existing ones."""
        bundle = DeliveryPlanBundle.model_validate({
            'activity_phases': [],
            'deliverables': [
                {
                    'number': 'WS-03',
                    'activity': 'Phase 1',
                    'name': 'Doc',
                    'description': 'Spec.',
                    'format': 'Document',
                },
                {
                    'activity': 'Phase 2',
                    'name': 'Doc2',
                    'description': 'Spec2.',
                    'format': 'Document',
                },
            ],
            'timeline': [],
            'partner_roles': [],
            'customer_roles': [],
            'success_criteria': [],
        })
        numbers = [d.number for d in bundle.deliverables]
        assert 'WS-03' in numbers  # preserved
        assert 'WS-01' in numbers  # injected, not WS-04 — sequential from 1
        assert len(set(numbers)) == len(numbers)  # no collisions


class TestScopeBoundariesBundle:
    def test_optional_fields_default(self):
        bundle = ScopeBoundariesBundle.model_validate({
            'assumptions': ['Customer provides access.'],
            'out_of_scope': ['Hardware procurement.'],
        })
        assert bundle.risks == []
        assert bundle.handover_disclaimers == []
        assert bundle.change_request_policy_text == ''

    def test_risk_number_required_on_item_model(self):
        with pytest.raises(ValidationError):
            Risk.model_validate({
                'description': 'SAP rate limits.',
                'mitigation': 'Backoff.',
            })

    def test_bundle_injects_number_when_risks_omit_it(self):
        bundle = ScopeBoundariesBundle.model_validate({
            'assumptions': ['Customer provides access.'],
            'out_of_scope': ['Hardware.'],
            'risks': [
                {'description': 'SAP rate limits.', 'mitigation': 'Backoff.'},
                {'description': 'Data quality.', 'mitigation': 'Sprint.'},
            ],
        })
        assert bundle.risks[0].number == 'R-01'
        assert bundle.risks[1].number == 'R-02'

    def test_bundle_preserves_explicit_risk_numbers(self):
        bundle = ScopeBoundariesBundle.model_validate({
            'assumptions': [],
            'out_of_scope': [],
            'risks': [
                {
                    'number': 'R-05',
                    'description': 'SAP rate limits.',
                    'mitigation': 'Backoff.',
                },
                {'description': 'Data quality.', 'mitigation': 'Sprint.'},
            ],
        })
        numbers = [r.number for r in bundle.risks]
        assert 'R-05' in numbers
        assert len(set(numbers)) == len(numbers)


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
        """Every section bundle lives under the ``app:sow:*`` namespace."""
        for key, value in SOW_BUNDLE_STATE_KEYS.items():
            assert value.startswith('app:sow:'), (key, value)

    def test_content_stage_is_subset_of_full(self):
        assert set(CONTENT_STAGE_KEYS).issubset(set(FULL_STAGE_KEYS))

    def test_full_stage_adds_architecture_and_narrative(self):
        added = set(FULL_STAGE_KEYS) - set(CONTENT_STAGE_KEYS)
        assert added == {
            SOW_BUNDLE_STATE_KEYS['architecture'],
            SOW_BUNDLE_STATE_KEYS['narrative'],
        }


class TestCollectionSpecs:
    """``_COLLECTION_SPECS`` is the contract the patch engine derives
    its allowlist / blocklist from. Pin the audit table directly so
    silent drift (adding a list field to a bundle without an entry,
    or flipping ``supports_item_ops`` accidentally) breaks a test
    rather than the LLM in production."""

    def test_table_covers_every_list_field_in_every_bundle(self):
        """Every ``list[...]`` field in every Bundle must have a
        :class:`CollectionSpec` entry. Without that, the patch engine
        cannot validate ``(collection, item_id)`` for ops touching
        the field — failures would surface as confusing ToolErrors
        at runtime instead of catching at boot."""
        bundles = [
            RequirementsBundle,
            DeliveryPlanBundle,
            ScopeBoundariesBundle,
            ArchitectureBundle,
            NarrativeBundle,
        ]
        for bundle_cls in bundles:
            for name, field_info in bundle_cls.model_fields.items():
                annotation = field_info.annotation
                origin = getattr(annotation, '__origin__', None)
                if origin is list:
                    assert name in _COLLECTION_SPECS, (
                        f'Bundle {bundle_cls.__name__}.{name} is a list '
                        f'field but has no CollectionSpec entry.'
                    )

    def test_identity_field_is_always_in_blocked_set_when_item_ops_supported(
        self,
    ):
        """If a collection supports item ops, its ``identity_field``
        MUST appear in ``blocked_identity_fields`` — that field IS
        the stable id update_item promises not to change."""
        for name, spec in _COLLECTION_SPECS.items():
            if spec.supports_item_ops:
                assert spec.identity_field is not None, name
                assert spec.identity_field in spec.blocked_identity_fields, name

    def test_string_list_collections_disable_item_ops(self):
        """Plain ``list[str]`` collections cannot be patched at item
        granularity — only ``update_field`` with the whole list."""
        for name in (
            'success_criteria',
            'objectives',
            'assumptions',
            'out_of_scope',
            'handover_disclaimers',
        ):
            spec = _COLLECTION_SPECS[name]
            assert spec.supports_item_ops is False, name
            assert spec.item_model is None, name
            assert spec.identity_field is None, name

    def test_deliverable_and_risk_use_number_prefix(self):
        """Numeric ids on these collections must follow the convention
        the critic already uses in evidence (WS-NN, R-NN)."""
        assert _COLLECTION_SPECS['deliverables'].id_prefix == 'WS'
        assert _COLLECTION_SPECS['risks'].id_prefix == 'R'
