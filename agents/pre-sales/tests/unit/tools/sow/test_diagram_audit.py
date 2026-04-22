"""Unit tests for ``app.tools.sow._diagram_audit``.

Every AUD-XX rule has at least one positive and one negative test. We
intentionally cover each rule in its own class so regressions point at the
rule that broke. Audit is pure: no filesystem, no subprocess, no LLM.
"""
from __future__ import annotations

from copy import deepcopy

import pytest

from app.tools.sow._diagram_audit import (
    AuditFailure,
    AuditResult,
    audit_architecture,
)
from app.tools.sow._diagram_models import (
    ArchitectureEdge,
    ArchitectureNode,
    ClusterZone,
    GcpServiceEnum,
)


def _blockers_of(result: AuditResult, check_id: str) -> list[AuditFailure]:
    return [f for f in result.blockers if f.check_id == check_id]


def _warnings_of(result: AuditResult, check_id: str) -> list[AuditFailure]:
    return [f for f in result.warnings if f.check_id == check_id]


class TestAuditResultDataclasses:
    def test_passed_means_no_blockers(self):
        r = AuditResult(passed=True, failures=[])
        assert r.passed
        assert r.blockers == []
        assert r.warnings == []

    def test_blockers_and_warnings_partitioned(self):
        r = AuditResult(
            passed=False,
            failures=[
                AuditFailure('AUD-05', 'BLOCKER', 'x'),
                AuditFailure('AUD-07', 'WARNING', 'y'),
            ],
        )
        assert len(r.blockers) == 1
        assert len(r.warnings) == 1

    def test_format_defects_with_both_sections(self):
        r = AuditResult(
            passed=False,
            failures=[
                AuditFailure('AUD-05', 'BLOCKER', 'generic label'),
                AuditFailure('AUD-07', 'WARNING', 'missing version'),
            ],
        )
        out = r.format_defects()
        assert 'BLOCKER failures' in out
        assert 'WARNING' in out
        assert '[AUD-05]' in out
        assert '[AUD-07]' in out

    def test_format_defects_with_only_warnings(self):
        r = AuditResult(
            passed=True,
            failures=[AuditFailure('AUD-07', 'WARNING', 'meh')],
        )
        out = r.format_defects()
        assert 'BLOCKER' not in out
        assert 'WARNING' in out

    def test_format_defects_empty(self):
        assert AuditResult(passed=True, failures=[]).format_defects() == ''


class TestHappyPath:
    def test_valid_spec_passes(self, architecture_spec):
        result = audit_architecture(**architecture_spec)
        assert result.passed, result.format_defects()

    def test_valid_spec_without_description_still_passes(
        self, architecture_spec
    ):
        architecture_spec['description'] = ''
        result = audit_architecture(**architecture_spec)
        assert result.passed

    def test_valid_spec_without_stack_still_passes(
        self, architecture_spec
    ):
        architecture_spec['technology_stack'] = None
        result = audit_architecture(**architecture_spec)
        assert result.passed


class TestAud05_GenericLabels:
    @pytest.mark.parametrize(
        'bad_label',
        ['Backend', 'database', 'Model', 'API', 'gateway', 'Storage'],
    )
    def test_generic_label_is_blocker(self, architecture_spec, bad_label):
        architecture_spec['nodes'][1].label = bad_label  # api node
        result = audit_architecture(**architecture_spec)
        assert _blockers_of(result, 'AUD-05'), (
            f'{bad_label!r} should be flagged as generic'
        )

    def test_case_insensitive(self, architecture_spec):
        architecture_spec['nodes'][1].label = 'BACKEND'
        result = audit_architecture(**architecture_spec)
        assert _blockers_of(result, 'AUD-05')

    def test_whitespace_trimmed(self, architecture_spec):
        architecture_spec['nodes'][1].label = '  api  '
        result = audit_architecture(**architecture_spec)
        assert _blockers_of(result, 'AUD-05')


class TestAud06_LabelRepeatsProduct:
    def test_repeating_product_name_is_blocker(self, architecture_spec):
        architecture_spec['nodes'][1].label = 'Cloud Run Backend'
        result = audit_architecture(**architecture_spec)
        assert _blockers_of(result, 'AUD-06')

    def test_descriptive_label_ok(self, architecture_spec):
        # 'Credit Analysis API' — no product name leak
        result = audit_architecture(**architecture_spec)
        assert not _blockers_of(result, 'AUD-06')

    def test_non_gcp_services_exempted(self):
        """Non-GCP services skip the AUD-06 check entirely."""
        nodes = [
            ArchitectureNode(
                id='legacy',
                label='On-Premises Server',  # Matches display name, but exempt
                service=GcpServiceEnum.ON_PREM_SERVER,
                parent_cluster=ClusterZone.CUSTOMER_ENVIRONMENT,
            ),
            ArchitectureNode(
                id='user',
                label='User',
                service=GcpServiceEnum.USERS,
                parent_cluster=ClusterZone.USER_CONSUMER,
            ),
            ArchitectureNode(
                id='run',
                label='API',
                service=GcpServiceEnum.CLOUD_RUN,
                parent_cluster=ClusterZone.GOOGLE_CLOUD,
            ),
            ArchitectureNode(
                id='bq',
                label='Warehouse',
                service=GcpServiceEnum.BIGQUERY,
                parent_cluster=ClusterZone.GOOGLE_CLOUD,
            ),
        ]
        edges = [
            ArchitectureEdge(source_id='user', target_id='run', label='HTTPS'),
            ArchitectureEdge(source_id='run', target_id='bq', label='SQL'),
            ArchitectureEdge(
                source_id='legacy', target_id='run', label='REST'
            ),
        ]
        result = audit_architecture(nodes=nodes, edges=edges)
        assert not _blockers_of(result, 'AUD-06')


class TestAud07_ExternalLabelWarning:
    def test_bare_name_triggers_warning(self):
        node = ArchitectureNode(
            id='ext',
            label='Salesforce',  # 1 word, no version
            service=GcpServiceEnum.GENERIC,
            parent_cluster=ClusterZone.THIRD_PARTY,
        )
        # Pair with a minimal valid diagram to isolate AUD-07
        nodes = [
            ArchitectureNode(
                id='user', label='User', service=GcpServiceEnum.USERS,
                parent_cluster=ClusterZone.USER_CONSUMER,
            ),
            ArchitectureNode(
                id='api', label='Credit API', service=GcpServiceEnum.CLOUD_RUN,
                parent_cluster=ClusterZone.GOOGLE_CLOUD,
            ),
            ArchitectureNode(
                id='bq', label='Warehouse', service=GcpServiceEnum.BIGQUERY,
                parent_cluster=ClusterZone.GOOGLE_CLOUD,
            ),
            node,
        ]
        edges = [
            ArchitectureEdge(source_id='user', target_id='api', label='HTTPS'),
            ArchitectureEdge(source_id='api', target_id='bq', label='SQL'),
            ArchitectureEdge(source_id='api', target_id='ext', label='REST'),
        ]
        # External services need EXTERNAL_SYSTEM_SERVICES — change to ON_PREM
        node.service = GcpServiceEnum.ON_PREM_SERVER  # type: ignore[misc]
        node.parent_cluster = ClusterZone.CUSTOMER_ENVIRONMENT  # type: ignore[misc]
        result = audit_architecture(nodes=nodes, edges=edges)
        assert _warnings_of(result, 'AUD-07')

    def test_multi_word_label_passes(self):
        node = ArchitectureNode(
            id='ext',
            label='Salesforce REST Service',
            service=GcpServiceEnum.ON_PREM_SERVER,
            parent_cluster=ClusterZone.CUSTOMER_ENVIRONMENT,
        )
        nodes = [
            ArchitectureNode(
                id='user', label='User', service=GcpServiceEnum.USERS,
                parent_cluster=ClusterZone.USER_CONSUMER,
            ),
            ArchitectureNode(
                id='api', label='Credit API', service=GcpServiceEnum.CLOUD_RUN,
                parent_cluster=ClusterZone.GOOGLE_CLOUD,
            ),
            ArchitectureNode(
                id='bq', label='Warehouse', service=GcpServiceEnum.BIGQUERY,
                parent_cluster=ClusterZone.GOOGLE_CLOUD,
            ),
            node,
        ]
        edges = [
            ArchitectureEdge(source_id='user', target_id='api', label='HTTPS'),
            ArchitectureEdge(source_id='api', target_id='bq', label='SQL'),
            ArchitectureEdge(source_id='api', target_id='ext', label='REST'),
        ]
        result = audit_architecture(nodes=nodes, edges=edges)
        assert not _warnings_of(result, 'AUD-07')

    def test_version_marker_exempts_single_word(self):
        node = ArchitectureNode(
            id='ext',
            label='SAPv2',  # 1 word but has version pattern
            service=GcpServiceEnum.ON_PREM_SERVER,
            parent_cluster=ClusterZone.CUSTOMER_ENVIRONMENT,
        )
        nodes = [
            ArchitectureNode(
                id='user', label='User', service=GcpServiceEnum.USERS,
                parent_cluster=ClusterZone.USER_CONSUMER,
            ),
            ArchitectureNode(
                id='api', label='Credit API', service=GcpServiceEnum.CLOUD_RUN,
                parent_cluster=ClusterZone.GOOGLE_CLOUD,
            ),
            ArchitectureNode(
                id='bq', label='Warehouse', service=GcpServiceEnum.BIGQUERY,
                parent_cluster=ClusterZone.GOOGLE_CLOUD,
            ),
            node,
        ]
        edges = [
            ArchitectureEdge(source_id='user', target_id='api', label='HTTPS'),
            ArchitectureEdge(source_id='api', target_id='bq', label='SQL'),
            ArchitectureEdge(source_id='api', target_id='ext', label='REST'),
        ]
        result = audit_architecture(nodes=nodes, edges=edges)
        assert not _warnings_of(result, 'AUD-07')


class TestAud10_IamAsNode:
    def test_iam_as_node_is_blocker(self, architecture_spec):
        iam_node = ArchitectureNode(
            id='iam',
            label='Access Control',
            service=GcpServiceEnum.IAM,
            parent_cluster=ClusterZone.GOOGLE_CLOUD,
        )
        architecture_spec['nodes'].append(iam_node)
        architecture_spec['edges'].append(
            ArchitectureEdge(source_id='api', target_id='iam', label='Auth')
        )
        result = audit_architecture(**architecture_spec)
        assert _blockers_of(result, 'AUD-10')


class TestAud19_ServiceZoneCoherence:
    def test_gcp_service_in_customer_env_is_blocker(self, architecture_spec):
        architecture_spec['nodes'][1].parent_cluster = (
            ClusterZone.CUSTOMER_ENVIRONMENT
        )
        result = audit_architecture(**architecture_spec)
        blockers = _blockers_of(result, 'AUD-19')
        assert blockers
        assert 'Cloud Run' in blockers[0].defect

    def test_entry_point_misplaced_gets_specific_message(self):
        nodes = [
            ArchitectureNode(
                id='u', label='User',
                service=GcpServiceEnum.USERS,
                parent_cluster=ClusterZone.GOOGLE_CLOUD,  # wrong
            ),
            ArchitectureNode(
                id='api', label='API', service=GcpServiceEnum.CLOUD_RUN,
                parent_cluster=ClusterZone.GOOGLE_CLOUD,
            ),
            ArchitectureNode(
                id='bq', label='Warehouse', service=GcpServiceEnum.BIGQUERY,
                parent_cluster=ClusterZone.GOOGLE_CLOUD,
            ),
        ]
        edges = [
            ArchitectureEdge(source_id='u', target_id='api', label='HTTPS'),
            ArchitectureEdge(source_id='api', target_id='bq', label='SQL'),
        ]
        result = audit_architecture(nodes=nodes, edges=edges)
        blockers = _blockers_of(result, 'AUD-19')
        assert blockers
        assert 'entry point' in blockers[0].defect.lower()

    def test_onprem_in_gcp_is_blocker(self):
        nodes = [
            ArchitectureNode(
                id='legacy', label='Legacy Mainframe',
                service=GcpServiceEnum.ON_PREM_SERVER,
                parent_cluster=ClusterZone.GOOGLE_CLOUD,  # wrong
            ),
            ArchitectureNode(
                id='u', label='User', service=GcpServiceEnum.USERS,
                parent_cluster=ClusterZone.USER_CONSUMER,
            ),
            ArchitectureNode(
                id='api', label='API', service=GcpServiceEnum.CLOUD_RUN,
                parent_cluster=ClusterZone.GOOGLE_CLOUD,
            ),
            ArchitectureNode(
                id='bq', label='Warehouse', service=GcpServiceEnum.BIGQUERY,
                parent_cluster=ClusterZone.GOOGLE_CLOUD,
            ),
        ]
        edges = [
            ArchitectureEdge(source_id='u', target_id='api', label='HTTPS'),
            ArchitectureEdge(source_id='api', target_id='bq', label='SQL'),
            ArchitectureEdge(source_id='legacy', target_id='api', label='REST'),
        ]
        result = audit_architecture(nodes=nodes, edges=edges)
        blockers = _blockers_of(result, 'AUD-19')
        assert any('on-premises' in b.defect.lower() for b in blockers)

    def test_postgres_in_customer_env_ok(self):
        nodes = [
            ArchitectureNode(
                id='u', label='User', service=GcpServiceEnum.USERS,
                parent_cluster=ClusterZone.USER_CONSUMER,
            ),
            ArchitectureNode(
                id='api', label='API', service=GcpServiceEnum.CLOUD_RUN,
                parent_cluster=ClusterZone.GOOGLE_CLOUD,
            ),
            ArchitectureNode(
                id='bq', label='Warehouse', service=GcpServiceEnum.BIGQUERY,
                parent_cluster=ClusterZone.GOOGLE_CLOUD,
            ),
            ArchitectureNode(
                id='db', label='Internal Postgres v14',
                service=GcpServiceEnum.POSTGRESQL,
                parent_cluster=ClusterZone.CUSTOMER_ENVIRONMENT,
            ),
        ]
        edges = [
            ArchitectureEdge(source_id='u', target_id='api', label='HTTPS'),
            ArchitectureEdge(source_id='api', target_id='bq', label='SQL'),
            ArchitectureEdge(source_id='api', target_id='db', label='SQL'),
        ]
        result = audit_architecture(nodes=nodes, edges=edges)
        assert not _blockers_of(result, 'AUD-19')


class TestAud11_EdgeIntegrity:
    def test_unlabeled_edge_is_blocker(self, architecture_spec):
        architecture_spec['edges'][0].label = None
        result = audit_architecture(**architecture_spec)
        assert _blockers_of(result, 'AUD-11')

    def test_empty_string_label_is_blocker(self, architecture_spec):
        architecture_spec['edges'][0].label = '   '
        result = audit_architecture(**architecture_spec)
        assert _blockers_of(result, 'AUD-11')

    def test_unknown_source_is_blocker(self, architecture_spec):
        architecture_spec['edges'].append(
            ArchitectureEdge(
                source_id='ghost', target_id='api', label='REST'
            )
        )
        result = audit_architecture(**architecture_spec)
        assert any('ghost' in b.defect for b in _blockers_of(result, 'AUD-11'))

    def test_unknown_target_is_blocker(self, architecture_spec):
        architecture_spec['edges'].append(
            ArchitectureEdge(
                source_id='api', target_id='ghost', label='REST'
            )
        )
        result = audit_architecture(**architecture_spec)
        assert any('ghost' in b.defect for b in _blockers_of(result, 'AUD-11'))

    def test_orphan_node_is_blocker(self, architecture_spec):
        architecture_spec['nodes'].append(
            ArchitectureNode(
                id='orphan', label='Orphan',
                service=GcpServiceEnum.CLOUD_STORAGE,
                parent_cluster=ClusterZone.GOOGLE_CLOUD,
            )
        )
        result = audit_architecture(**architecture_spec)
        assert any(
            "orphan" in b.defect.lower() or "no edges" in b.defect.lower()
            for b in _blockers_of(result, 'AUD-11')
        )


class TestAud14_MinimumComponents:
    def test_no_entry_point_is_blocker(self):
        nodes = [
            ArchitectureNode(
                id='api', label='API', service=GcpServiceEnum.CLOUD_RUN,
                parent_cluster=ClusterZone.GOOGLE_CLOUD,
            ),
            ArchitectureNode(
                id='bq', label='Warehouse', service=GcpServiceEnum.BIGQUERY,
                parent_cluster=ClusterZone.GOOGLE_CLOUD,
            ),
        ]
        edges = [
            ArchitectureEdge(source_id='api', target_id='bq', label='SQL')
        ]
        result = audit_architecture(nodes=nodes, edges=edges)
        assert any(
            'Entry Point' in b.defect for b in _blockers_of(result, 'AUD-14')
        )

    def test_no_compute_is_blocker(self):
        nodes = [
            ArchitectureNode(
                id='u', label='User', service=GcpServiceEnum.USERS,
                parent_cluster=ClusterZone.USER_CONSUMER,
            ),
            ArchitectureNode(
                id='bq', label='Warehouse', service=GcpServiceEnum.BIGQUERY,
                parent_cluster=ClusterZone.GOOGLE_CLOUD,
            ),
        ]
        edges = [
            ArchitectureEdge(source_id='u', target_id='bq', label='SQL'),
        ]
        result = audit_architecture(nodes=nodes, edges=edges)
        assert any(
            'Compute' in b.defect for b in _blockers_of(result, 'AUD-14')
        )

    def test_no_data_is_blocker(self):
        nodes = [
            ArchitectureNode(
                id='u', label='User', service=GcpServiceEnum.USERS,
                parent_cluster=ClusterZone.USER_CONSUMER,
            ),
            ArchitectureNode(
                id='api', label='API', service=GcpServiceEnum.CLOUD_RUN,
                parent_cluster=ClusterZone.GOOGLE_CLOUD,
            ),
        ]
        edges = [
            ArchitectureEdge(source_id='u', target_id='api', label='HTTPS'),
        ]
        result = audit_architecture(nodes=nodes, edges=edges)
        assert any(
            'Data/Storage' in b.defect for b in _blockers_of(result, 'AUD-14')
        )


class TestAud02Aud03_StackDiagramConsistency:
    def test_stack_has_extra_service_is_blocker(self, architecture_spec):
        architecture_spec['technology_stack'].append(
            {'service': 'Firestore', 'purpose': 'Session store.'}
        )
        result = audit_architecture(**architecture_spec)
        assert _blockers_of(result, 'AUD-02')

    def test_diagram_has_extra_service_is_blocker(self, architecture_spec):
        architecture_spec['nodes'].append(
            ArchitectureNode(
                id='dataflow', label='ETL Job',
                service=GcpServiceEnum.DATAFLOW,
                parent_cluster=ClusterZone.GOOGLE_CLOUD,
            )
        )
        architecture_spec['edges'].append(
            ArchitectureEdge(
                source_id='dataflow', target_id='warehouse', label='Streaming'
            )
        )
        result = audit_architecture(**architecture_spec)
        assert _blockers_of(result, 'AUD-03')

    def test_no_stack_skips_both_checks(self, architecture_spec):
        architecture_spec['technology_stack'] = None
        architecture_spec['nodes'].append(
            ArchitectureNode(
                id='dataflow', label='ETL Job',
                service=GcpServiceEnum.DATAFLOW,
                parent_cluster=ClusterZone.GOOGLE_CLOUD,
            )
        )
        architecture_spec['edges'].append(
            ArchitectureEdge(
                source_id='dataflow', target_id='warehouse', label='Streaming'
            )
        )
        result = audit_architecture(**architecture_spec)
        assert not _blockers_of(result, 'AUD-02')
        assert not _blockers_of(result, 'AUD-03')


class TestAud01_DescriptionVsStack:
    def test_mentioned_but_not_in_stack_is_blocker(self, architecture_spec):
        architecture_spec['description'] += ' Firestore handles sessions.'
        result = audit_architecture(**architecture_spec)
        assert _blockers_of(result, 'AUD-01')

    def test_no_description_skips_check(self, architecture_spec):
        architecture_spec['description'] = ''
        architecture_spec['technology_stack'] = [
            {'service': 'Cloud Run', 'purpose': 'API'},
        ]
        # Even if a service is missing, no description → no AUD-01
        result = audit_architecture(**architecture_spec)
        assert not _blockers_of(result, 'AUD-01')


class TestAud15_SecretManagerWithExternalNodes:
    def test_external_nodes_without_secret_manager_warn(
        self, architecture_spec
    ):
        architecture_spec['nodes'].append(
            ArchitectureNode(
                id='ext', label='SAP ERP',
                service=GcpServiceEnum.ON_PREM_SERVER,
                parent_cluster=ClusterZone.CUSTOMER_ENVIRONMENT,
            )
        )
        architecture_spec['edges'].append(
            ArchitectureEdge(source_id='api', target_id='ext', label='REST')
        )
        # Description has no Secret Manager mention
        architecture_spec['description'] = (
            'HTTPS to API which talks to BigQuery for warehouse. '
            'API also pulls data from the on-prem SAP system via REST.'
        )
        result = audit_architecture(**architecture_spec)
        assert _warnings_of(result, 'AUD-15')

    def test_secret_manager_mentioned_suppresses_warning(
        self, architecture_spec
    ):
        architecture_spec['nodes'].append(
            ArchitectureNode(
                id='ext', label='SAP ERP',
                service=GcpServiceEnum.ON_PREM_SERVER,
                parent_cluster=ClusterZone.CUSTOMER_ENVIRONMENT,
            )
        )
        architecture_spec['edges'].append(
            ArchitectureEdge(source_id='api', target_id='ext', label='REST')
        )
        architecture_spec['description'] += (
            ' Secret Manager stores credentials for the on-prem integration '
            'to satisfy compliance.'
        )
        result = audit_architecture(**architecture_spec)
        assert not _warnings_of(result, 'AUD-15')


class TestAud16_AiProjectWithoutAiNode:
    def test_ai_keywords_without_ai_nodes_warns(self, architecture_spec):
        architecture_spec['description'] = (
            'This solution uses machine learning and generative AI to drive '
            'insights because the product team requires an LLM-powered '
            'assistant. The API relies on BigQuery in order to serve analytics.'
        )
        result = audit_architecture(**architecture_spec)
        assert _warnings_of(result, 'AUD-16')

    def test_ai_keyword_with_ai_node_no_warning(self, architecture_spec):
        architecture_spec['nodes'].append(
            ArchitectureNode(
                id='ai', label='Model Serving',
                service=GcpServiceEnum.VERTEX_AI,
                parent_cluster=ClusterZone.GOOGLE_CLOUD,
            )
        )
        architecture_spec['edges'].append(
            ArchitectureEdge(source_id='api', target_id='ai', label='gRPC')
        )
        architecture_spec['technology_stack'].append(
            {'service': 'Vertex AI', 'purpose': 'Model serving.'}
        )
        architecture_spec['description'] = (
            'End users send HTTPS requests to the API that uses Vertex AI '
            'for machine learning inference because it satisfies FR-01 and '
            'supports the GenAI copilot. BigQuery serves as the analytics layer.'
        )
        result = audit_architecture(**architecture_spec)
        assert not _warnings_of(result, 'AUD-16')


class TestAud17_Justification:
    def test_description_lacking_justification_warns(self, architecture_spec):
        architecture_spec['description'] = (
            'Cloud Run hosts the API. BigQuery stores data. Done.'
        )
        result = audit_architecture(**architecture_spec)
        assert _warnings_of(result, 'AUD-17')

    def test_fr_reference_suppresses_warning(self, architecture_spec):
        # Default fixture description references FR-01 / NFR-02 → no warning
        result = audit_architecture(**architecture_spec)
        assert not _warnings_of(result, 'AUD-17')


class TestAud18_BulletListWarning:
    def test_bullet_list_description_warns(self, architecture_spec):
        architecture_spec['description'] = (
            '- Cloud Run hosts the API\n'
            '- BigQuery stores data\n'
            '- Vertex AI runs models\n'
            '- Everything connects to satisfy FR-01\n'
        )
        result = audit_architecture(**architecture_spec)
        assert _warnings_of(result, 'AUD-18')

    def test_narrative_prose_no_warning(self, architecture_spec):
        # default fixture is prose
        result = audit_architecture(**architecture_spec)
        assert not _warnings_of(result, 'AUD-18')


class TestAuditPassedInvariant:
    def test_warnings_alone_dont_flip_passed(self, architecture_spec):
        architecture_spec['description'] = (
            '- Cloud Run hosts the API\n'
            '- BigQuery stores data\n'
            '- Everything works because FR-01\n'
        )
        result = audit_architecture(**architecture_spec)
        # AUD-18 fires as WARNING only → passed stays True
        assert result.warnings
        assert result.passed

    def test_any_blocker_flips_passed(self, architecture_spec):
        architecture_spec['nodes'][1].label = 'Backend'
        result = audit_architecture(**architecture_spec)
        assert result.blockers
        assert not result.passed
