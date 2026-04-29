"""Shared fixtures for unit tests.

Fixtures are kept dependency-free and deterministic so unit tests stay fast
and repeatable. Anything that touches the filesystem, network, subprocesses,
or the docx template belongs in tests/integration, not here.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# SOW data builders
# ---------------------------------------------------------------------------


def _build_functional_requirements(count: int = 12) -> list[dict[str, str]]:
    return [
        {
            'number': f'FR-{i:02d}',
            'description': (
                f'The system shall perform function {i} with specific '
                f'technical context and integration details for testing purposes.'
            ),
        }
        for i in range(1, count + 1)
    ]


def _build_non_functional_requirements() -> list[dict[str, str]]:
    return [
        {
            'number': 'NFR-01',
            'description': 'Security: TLS 1.3 in transit, AES-256 at rest.',
        },
        {
            'number': 'NFR-02',
            'description': 'Reliability: 99.5% availability SLA for production workloads.',
        },
        {
            'number': 'NFR-03',
            'description': 'Performance: API response latency under 2 seconds at p95.',
        },
        {
            'number': 'NFR-04',
            'description': 'Operational Excellence: automated monitoring via Cloud Monitoring.',
        },
        {
            'number': 'NFR-05',
            'description': 'Cost Optimization: serverless-first architecture.',
        },
    ]


def _build_roles(prefix: str) -> list[dict[str, str]]:
    return [
        {
            'role': f'{prefix} Project Manager',
            'responsibilities': (
                'Responsible for managing the project timeline, risk mitigation, '
                'and stakeholder communication. Conducts weekly status meetings '
                'and tracks milestone delivery. Acts as primary point of contact.'
            ),
        },
        {
            'role': f'{prefix} Solution Architect',
            'responsibilities': (
                'Designs the end-to-end solution architecture and ensures alignment '
                'with GCP best practices. Reviews all technical deliverables and '
                'provides guidance on service selection across workshops.'
            ),
        },
    ]


def build_sow_data(
    *,
    funding: str = 'DAF',
    include_risks: bool = True,
) -> dict[str, Any]:
    """Build a deterministic, fully-valid SOW payload.

    Override specific fields in the returned dict for negative-path tests so
    each test stays readable.
    """
    data: dict[str, Any] = {
        'partner_name': 'GFT Technologies',
        'customer_name': 'Acme Corp',
        'partner_short_name': 'GFT',
        'customer_short_name': 'Acme',
        'project_title': 'Data Analytics Platform',
        'date': '2026-04-15',
        'author': 'Test Author',
        'funding_type': f'Google {funding}',
        'funding_type_short': funding,
        'executive_summary': (
            'Acme Corp is modernizing its data analytics capabilities by implementing '
            'a cloud-native data platform on Google Cloud. This engagement is strictly '
            'limited to the design, development, and deployment of a centralized data '
            'warehouse with automated ingestion pipelines and ML-powered analytics. '
            'The project will leverage BigQuery, Cloud Run, Vertex AI, and Cloud Storage '
            'to deliver a scalable, secure, and cost-effective solution.'
        ),
        'partner_overview': (
            'GFT Technologies is a Google Cloud Premier Partner with deep expertise '
            'in data engineering and AI solutions.'
        ),
        'customer_overview': (
            'Acme Corp is a global manufacturing company seeking to modernize its data infrastructure.'
        ),
        'functional_requirements': _build_functional_requirements(),
        'non_functional_requirements': _build_non_functional_requirements(),
        'architecture_description': (
            'The solution follows a layered architecture with Cloud Run as the compute layer, '
            'BigQuery as the data warehouse, and Vertex AI for ML workloads. Data ingestion '
            'from SAP ERP and Salesforce flows through Cloud Storage as the landing zone. '
            'Cloud Run was selected for its serverless autoscaling capability, addressing '
            'NFR-02 availability requirements without dedicated infrastructure management. '
            'BigQuery provides the analytical backbone with partitioning by date and clustering '
            'by product category for optimal query performance (NFR-03). Vertex AI enables '
            'model training on refined data features and supports the generative assistants '
            'required by FR-04 in order to provide summarization and retrieval-augmented '
            'generation flows for the end user. Cross-cutting concerns are addressed '
            'through Cloud Logging for audit trails (FR-09), Cloud Monitoring for SLA tracking '
            '(NFR-02), and IAM with least-privilege roles for inter-service authentication (NFR-01). '
            'Secret Manager stores all API credentials for external system integrations, and '
            'Cloud Armor fronts the public endpoints because security hardening is required to '
            'satisfy the enterprise threat model described by the customer security officer. '
            'Pub/Sub decouples the ingestion pipeline from the downstream consumers, enabling '
            'independent scaling and backpressure handling for peak-hour volumes.'
        ),
        'architecture_components': [
            {'name': 'Cloud Run', 'role': 'Hosts the backend API with serverless autoscaling.'},
            {'name': 'BigQuery', 'role': 'Centralized data warehouse for analytics workloads.'},
            {'name': 'Cloud Storage', 'role': 'Object storage for raw data landing zone.'},
            {'name': 'Vertex AI', 'role': 'ML model training and serving environment.'},
        ],
        'architecture_integrations': [
            {'name': 'SAP ERP', 'description': 'Source system for master data via REST API.'},
            {'name': 'Salesforce', 'description': 'CRM data integration via batch export.'},
        ],
        'activity_phases': [
            {
                'name': 'Phase 1: Discovery',
                'description': 'Define architecture and validate requirements.',
                'tasks': ['Conduct kickoff', 'Review systems'],
            },
            {
                'name': 'Phase 2: Build',
                'description': 'Develop core solution components.',
                'tasks': ['Implement pipelines', 'Configure services'],
            },
            {
                'name': 'Phase 3: Deploy',
                'description': 'Deploy and validate in target environment.',
                'tasks': ['Deploy to staging', 'Run UAT'],
            },
        ],
        'deliverables': [
            {
                'activity': f'Phase {((i - 1) % 3) + 1}',
                'name': f'Deliverable {i}',
                'description': f'Detailed deliverable {i} with measurable outcome.',
                'format': 'Document',
            }
            for i in range(1, 12)
        ],
        'timeline': [
            {'activity': 'Phase 1: Discovery', 'timeframe': 'Weeks 1-2', 'outcomes': 'Approved architecture design'},
            {'activity': 'Phase 2: Build', 'timeframe': 'Weeks 3-8', 'outcomes': 'Solution implemented and tested'},
            {'activity': 'Phase 3: Deploy', 'timeframe': 'Weeks 9-10', 'outcomes': 'Production deployment complete'},
        ],
        'partner_roles': _build_roles('Partner'),
        'customer_roles': _build_roles('Customer'),
        'out_of_scope': [
            f'Out-of-scope item {i}: specific exclusion with technical detail for category coverage.'
            for i in range(1, 25)
        ],
        'assumptions': [
            (
                f'Customer must provide {item} before the start of Phase {(i % 3) + 1}. '
                f'Failure to do so will result in timeline extension and additional cost.'
            )
            for i, item in enumerate(
                [
                    'GCP environment access',
                    'API credentials',
                    'technical documentation',
                    'subject matter experts',
                    'labeled test data',
                    'VPN access',
                    'service account permissions',
                    'data dictionaries',
                    'system architecture diagrams',
                    'business rules documentation',
                    'stakeholder availability',
                    'feedback within 3 business days',
                    'project sponsor assignment',
                    'compliance requirements documentation',
                    'data quality validation results',
                    'network access configuration',
                ]
            )
        ],
        'success_criteria': [
            'Successful deployment of all solution components to the target GCP environment.',
            'Customer acceptance of all deliverables listed in Section 4.',
            'Completion of knowledge transfer sessions with customer technical team.',
            'All functional requirements (FR-01 through FR-12) demonstrated and validated.',
            'Architecture documentation approved by customer Solution Architect.',
        ],
        'objectives': [
            'Implement centralized data warehouse',
            'Enable ML-powered analytics',
            'Automate data ingestion pipelines',
        ],
        'project_start_date': '2026-05-01',
        'project_end_date': '2026-07-10',
        'engagement_type': 'project',
        'organization_term': 'phases',
    }

    if include_risks:
        data['risks'] = [
            {
                'description': 'SAP API rate limits may constrain ingestion throughput.',
                'mitigation': 'Implement incremental extraction with backoff.',
            },
            {
                'description': 'Data quality issues in legacy systems may delay pipeline development.',
                'mitigation': 'Allocate validation sprint in Phase 2.',
            },
            {
                'description': 'Customer access provisioning delays may block development.',
                'mitigation': 'Deliver pre-kickoff access checklist with deadlines.',
            },
        ]

    return data


@pytest.fixture
def sow_data() -> dict[str, Any]:
    """A complete, valid DAF sow_data payload."""
    return build_sow_data()


@pytest.fixture
def sow_data_psf() -> dict[str, Any]:
    """A complete, valid PSF sow_data payload."""
    return build_sow_data(funding='PSF')


@pytest.fixture
def sow_data_minimal() -> dict[str, Any]:
    """Minimal payload — only required top-level keys, no quality-gate content.

    Useful when a test wants to control every field explicitly and not inherit
    the heavyweight defaults.
    """
    return {
        'partner_name': 'GFT',
        'customer_name': 'Acme',
        'project_title': 'Test Project',
        'funding_type': 'Google DAF',
        'functional_requirements': [],
        'non_functional_requirements': [],
        'architecture_components': [],
        'architecture_integrations': [],
        'activity_phases': [],
        'deliverables': [],
        'timeline': [],
        'partner_roles': [],
        'customer_roles': [],
        'out_of_scope': [],
        'assumptions': [],
        'success_criteria': [],
    }


# ---------------------------------------------------------------------------
# Architecture diagram builders
# ---------------------------------------------------------------------------


def build_architecture_spec() -> dict[str, Any]:
    """Returns a valid architecture spec (nodes + edges + description + stack).

    Callers can mutate the returned dict to exercise negative paths without
    impacting siblings.
    """
    from app.tools.sow._diagram_models import (
        ArchitectureEdge,
        ArchitectureNode,
        ClusterZone,
        GcpServiceEnum,
    )

    nodes = [
        ArchitectureNode(
            id='user',
            label='End User',
            service=GcpServiceEnum.USERS,
            parent_cluster=ClusterZone.USER_CONSUMER,
        ),
        ArchitectureNode(
            id='api',
            label='Credit Analysis API',
            service=GcpServiceEnum.CLOUD_RUN,
            parent_cluster=ClusterZone.GOOGLE_CLOUD,
        ),
        ArchitectureNode(
            id='warehouse',
            label='Analytics Warehouse',
            service=GcpServiceEnum.BIGQUERY,
            parent_cluster=ClusterZone.GOOGLE_CLOUD,
        ),
    ]
    edges = [
        ArchitectureEdge(source_id='user', target_id='api', label='HTTPS'),
        ArchitectureEdge(source_id='api', target_id='warehouse', label='SQL'),
    ]
    description = (
        'End users send HTTPS requests to the Credit Analysis API running on '
        'Cloud Run, which in turn persists analytics events to BigQuery to '
        'satisfy FR-01 and to handle NFR-02 availability requirements because '
        'serverless autoscaling is required.'
    )
    technology_stack = [
        {'service': 'Cloud Run', 'purpose': 'Hosts the backend API.'},
        {'service': 'BigQuery', 'purpose': 'Analytics warehouse.'},
    ]
    return {
        'nodes': nodes,
        'edges': edges,
        'description': description,
        'technology_stack': technology_stack,
    }


@pytest.fixture
def architecture_spec() -> dict[str, Any]:
    return build_architecture_spec()


# ---------------------------------------------------------------------------
# ADK mocks
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_tool_context() -> MagicMock:
    """A MagicMock that looks like google.adk.tools.ToolContext.

    - ``state`` is a real dict so tools can read/write like the SDK does.
    - ``save_artifact`` / ``load_artifact`` are AsyncMocks so ``await`` works.
    """
    ctx = MagicMock(name='ToolContext')
    ctx.state = {}
    ctx.save_artifact = AsyncMock(return_value=1)
    ctx.load_artifact = AsyncMock(return_value=None)
    return ctx
