"""Integration test: validation → quality gates → template rendering.

Tests the deterministic layers of the SOW pipeline without calling the LLM.
Verifies that a well-formed sow_data dict passes validation, quality gates,
and renders the .docx template without Jinja2 errors.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from docx import Document
from docxtpl import DocxTemplate

from app.shared.validators import ContentValidator
from app.tools.sow._sow_helpers import validate_quality_gates
from app.tools.sow.generate_sow_document import (
    _apply_defaults,
    _auto_derive_fields,
)

_TEMPLATE_PATH = (
    Path(__file__).parent.parent.parent
    / 'app'
    / 'tools'
    / 'sow'
    / 'templates'
    / 'SOW_Template.docx'
)


def _make_sow_data(
    *, funding: str = 'DAF', include_risks: bool = True
) -> dict:
    """Build a representative sow_data dict that should pass all gates."""
    frs = [
        {
            'number': f'FR-{i:02d}',
            'description': f'The system shall perform function {i} with specific technical context and integration details for testing purposes.',
        }
        for i in range(1, 13)
    ]
    nfrs = [
        {
            'number': 'NFR-01',
            'description': 'Security: TLS 1.3 encryption for data in transit, AES-256 at rest.',
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
            'description': 'Operational Excellence: Automated monitoring and alerting via Cloud Monitoring.',
        },
        {
            'number': 'NFR-05',
            'description': 'Cost Optimization: Serverless-first architecture to minimize idle resource costs.',
        },
    ]
    phases = [
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
    ]
    deliverables = [
        {
            'activity': 'Phase 1',
            'name': f'Deliverable {i}',
            'description': f'Detailed deliverable {i} description with measurable outcome.',
            'format': 'Document',
        }
        for i in range(1, 12)
    ]
    timeline = [
        {
            'activity': 'Phase 1: Discovery',
            'timeframe': 'Weeks 1-2',
            'outcomes': 'Approved architecture design',
        },
        {
            'activity': 'Phase 2: Build',
            'timeframe': 'Weeks 3-8',
            'outcomes': 'Solution implemented and tested',
        },
        {
            'activity': 'Phase 3: Deploy',
            'timeframe': 'Weeks 9-10',
            'outcomes': 'Production deployment complete',
        },
    ]
    partner_roles = [
        {
            'role': 'Project Manager',
            'responsibilities': 'Responsible for managing the project timeline, risk mitigation, and stakeholder communication. Conducts weekly status meetings and tracks milestone delivery. Acts as primary point of contact.',
        },
        {
            'role': 'Solution Architect',
            'responsibilities': 'Designs the end-to-end solution architecture and ensures alignment with GCP best practices. Reviews all technical deliverables and provides guidance on service selection. Leads technical workshops with the customer team.',
        },
    ]
    customer_roles = [
        {
            'role': 'Product Owner',
            'responsibilities': 'Defines business priorities and validates functional requirements. Provides timely feedback on deliverables within the agreed SLA. Has authority to approve deliverables and sign off on phase completion.',
        },
        {
            'role': 'Technical Lead',
            'responsibilities': 'Provides access to existing systems and technical documentation. Participates in architecture reviews and integration testing. Escalates internal blockers within the agreed resolution SLA.',
        },
    ]
    oos = [
        f'Out-of-scope item {i}: Specific exclusion with technical detail for category coverage.'
        for i in range(1, 25)
    ]
    assumptions = [
        f'Customer must provide {item} before the start of Phase {(i % 3) + 1}. Failure to do so will result in timeline extension and additional cost.'
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
    ]
    criteria = [
        'Successful deployment of all solution components to the target GCP environment.',
        'Customer acceptance of all deliverables listed in Section 4.',
        'Completion of knowledge transfer sessions with customer technical team.',
        'All functional requirements (FR-01 through FR-12) demonstrated and validated.',
        'Architecture documentation approved by customer Solution Architect.',
    ]
    arch_components = [
        {
            'name': 'Cloud Run',
            'role': 'Hosts the backend API with serverless autoscaling.',
        },
        {
            'name': 'BigQuery',
            'role': 'Centralized data warehouse for analytics workloads.',
        },
        {
            'name': 'Cloud Storage',
            'role': 'Object storage for raw data landing zone.',
        },
        {
            'name': 'Vertex AI',
            'role': 'ML model training and serving environment.',
        },
    ]
    arch_integrations = [
        {
            'name': 'SAP ERP',
            'description': 'Source system for master data via REST API.',
        },
        {
            'name': 'Salesforce',
            'description': 'CRM data integration via batch export.',
        },
    ]

    data = {
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
        'partner_overview': 'GFT Technologies is a Google Cloud Premier Partner with deep expertise in data engineering and AI solutions.',
        'customer_overview': 'Acme Corp is a global manufacturing company seeking to modernize its data infrastructure.',
        'functional_requirements': frs,
        'non_functional_requirements': nfrs,
        'architecture_description': (
            'The solution follows a layered architecture with Cloud Run as the compute layer, '
            'BigQuery as the data warehouse, and Vertex AI for ML workloads. Data ingestion '
            'from SAP ERP and Salesforce flows through Cloud Storage as the landing zone. '
            'Cloud Run was selected for its serverless autoscaling capability, addressing '
            'NFR-02 availability requirements without dedicated infrastructure management. '
            'BigQuery provides the analytical backbone with partitioning by date and clustering '
            'by product category for optimal query performance (NFR-03). Vertex AI enables '
            'model training on refined data features. Cross-cutting concerns are addressed '
            'through Cloud Logging for audit trails (FR-09), Cloud Monitoring for SLA tracking '
            '(NFR-02), and IAM with least-privilege roles for inter-service authentication (NFR-01). '
            'Secret Manager stores all API credentials for external system integrations.'
        ),
        'architecture_components': arch_components,
        'architecture_integrations': arch_integrations,
        'activity_phases': phases,
        'deliverables': deliverables,
        'timeline': timeline,
        'partner_roles': partner_roles,
        'customer_roles': customer_roles,
        'out_of_scope': oos,
        'assumptions': assumptions,
        'success_criteria': criteria,
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

    if funding == 'PSF':
        data['consumption_plan'] = {
            'services': [
                'Cloud Run',
                'BigQuery',
                'Vertex AI',
                'Cloud Storage',
            ],
            'rows': [
                {
                    'month': m,
                    'costs': ['$50', '$200', '$300', '$30'],
                    'total': '$580',
                }
                for m in range(1, 13)
            ],
            'notes': 'Estimates based on development workloads. Production costs may vary.',
        }

    return data


class TestContentValidator:
    """Tests for the deterministic ContentValidator."""

    def test_valid_data_passes(self):
        data = _make_sow_data()
        result = ContentValidator().validate(data)
        assert (
            result.passed
        ), f'Expected pass, got errors: {[str(e) for e in result.errors]}'

    def test_invalid_fr_id_format(self):
        data = _make_sow_data()
        data['functional_requirements'][0]['number'] = 'INVALID'
        result = ContentValidator().validate(data)
        assert not result.passed
        assert any('FR-XX' in e.message for e in result.errors)

    def test_invalid_nfr_id_format(self):
        data = _make_sow_data()
        data['non_functional_requirements'][0]['number'] = 'BAD-01'
        result = ContentValidator().validate(data)
        assert not result.passed
        assert any('NFR-XX' in e.message for e in result.errors)

    def test_psf_requires_consumption_plan(self):
        data = _make_sow_data(funding='PSF')
        del data['consumption_plan']
        result = ContentValidator().validate(data)
        assert not result.passed
        assert any('consumption' in e.message.lower() for e in result.errors)

    def test_psf_with_consumption_plan_passes(self):
        data = _make_sow_data(funding='PSF')
        result = ContentValidator().validate(data)
        assert result.passed

    def test_short_architecture_description_warns(self):
        data = _make_sow_data()
        data['architecture_description'] = 'Short description.'
        result = ContentValidator().validate(data)
        assert any(
            'architecture_description' in w.field for w in result.issues
        )

    def test_low_oos_count_warns(self):
        data = _make_sow_data()
        data['out_of_scope'] = ['Item 1', 'Item 2', 'Item 3']
        result = ContentValidator().validate(data)
        assert any('out_of_scope' in w.field for w in result.warnings)


class TestQualityGates:
    """Tests for the quality gate enforcement."""

    def test_valid_data_passes_gates(self):
        data = _make_sow_data()
        _apply_defaults(data)
        errors = validate_quality_gates(data)
        assert errors == [], f'Expected no errors, got: {errors}'

    def test_insufficient_oos_fails(self):
        data = _make_sow_data()
        data['out_of_scope'] = ['Item 1']
        _apply_defaults(data)
        errors = validate_quality_gates(data)
        assert any('Out-of-Scope' in e for e in errors)

    def test_insufficient_assumptions_fails(self):
        data = _make_sow_data()
        data['assumptions'] = ['One assumption']
        _apply_defaults(data)
        errors = validate_quality_gates(data)
        assert any('Assumptions' in e for e in errors)

    def test_psf_without_consumption_plan_fails(self):
        data = _make_sow_data(funding='PSF')
        del data['consumption_plan']
        _apply_defaults(data)
        errors = validate_quality_gates(data)
        assert any('Consumption Plan' in e for e in errors)


class TestProjectTypeInference:
    """Tests for project_type auto-derivation."""

    def test_genai_project_detected(self):
        data = _make_sow_data()
        _apply_defaults(data)
        _auto_derive_fields(data)
        # architecture_components includes Vertex AI → genai
        assert data['project_type'] == 'genai'

    def test_standard_project_when_no_ai(self):
        data = _make_sow_data()
        data['architecture_components'] = [
            {'name': 'Cloud Run', 'role': 'Backend API.'},
            {'name': 'Cloud SQL', 'role': 'Relational database.'},
        ]
        data['technology_stack'] = [
            {'service': 'Cloud Run', 'purpose': 'Backend API.'},
            {'service': 'Cloud SQL', 'purpose': 'Relational database.'},
        ]
        data[
            'architecture_description'
        ] = 'Simple web application with Cloud Run and Cloud SQL.'
        data[
            'executive_summary'
        ] = 'A standard web application using Cloud Run and Cloud SQL.'
        _apply_defaults(data)
        _auto_derive_fields(data)
        assert data['project_type'] == 'standard'


class TestTemplateRendering:
    """Tests that valid data renders the .docx template without errors."""

    @staticmethod
    def _render_to_text(data: dict) -> str:
        """Apply preprocessing, render template, save, re-read, return text."""
        _apply_defaults(data)
        _auto_derive_fields(data)
        data['partner_logo'] = '[Partner Logo]'
        data['customer_logo'] = '[Customer Logo]'

        doc = DocxTemplate(str(_TEMPLATE_PATH))
        doc.render(data, autoescape=True)

        with tempfile.NamedTemporaryFile(suffix='.docx', delete=False) as f:
            tmp_path = Path(f.name)
        doc.save(str(tmp_path))

        rendered = Document(str(tmp_path))
        full_text = '\n'.join(p.text for p in rendered.paragraphs)
        tmp_path.unlink(missing_ok=True)
        return full_text

    @pytest.mark.skipif(
        not _TEMPLATE_PATH.exists(),
        reason='SOW template not found',
    )
    def test_daf_template_renders(self):
        data = _make_sow_data(funding='DAF')
        full_text = self._render_to_text(data)

        assert 'Acme Corp' in full_text
        assert 'GFT Technologies' in full_text
        assert 'FR-01' in full_text
        assert 'NFR-01' in full_text
        assert 'Phase 1: Discovery' in full_text

    @pytest.mark.skipif(
        not _TEMPLATE_PATH.exists(),
        reason='SOW template not found',
    )
    def test_psf_template_renders_with_consumption_plan(self):
        full_text = self._render_to_text(_make_sow_data(funding='PSF'))
        assert 'PSF' in full_text

    @pytest.mark.skipif(
        not _TEMPLATE_PATH.exists(),
        reason='SOW template not found',
    )
    def test_genai_project_includes_conditional_assumptions(self):
        data = _make_sow_data(funding='DAF')
        _apply_defaults(data)
        _auto_derive_fields(data)
        assert data['project_type'] == 'genai'

        full_text = self._render_to_text(_make_sow_data(funding='DAF'))
        # GenAI conditional assumption should be present
        assert 'generative ai model performance' in full_text.lower()
