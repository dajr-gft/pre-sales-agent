from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

from docx.shared import Mm
from docxtpl import DocxTemplate, InlineImage
from google.adk.tools import ToolContext
from google.genai import types as genai_types

from ._sow_helpers import download_gcs_uri, load_logo, validate_quality_gates

logger = logging.getLogger(__name__)

_DOCUMENT_PATH_KEY = 'sow_document_path'
_TEMPLATE_DIR = Path(__file__).parent / 'templates'
_TEMPLATE_FILENAME = 'SOW_Template.docx'
_PARTNER_LOGO_FILENAME = 'gft_logo.png'

_PARTNER_LOGO_WIDTH_MM = 41
_CUSTOMER_LOGO_WIDTH_MM = 43


async def generate_sow_document(
    sow_data: str,
    tool_context: ToolContext = None,
) -> dict[str, Any]:
    """
    Generates a Statement of Work (SOW) document in .docx format from
    the collected project data.

    Call this tool after all content has been generated and approved by
    the user. The tool assembles the final .docx document with all
    sections, tables, and formatting.

    Args:
        sow_data: A JSON string containing all SOW sections. Expected keys:

            Simple fields:
            - partner_name, customer_name, project_title, date, author (strings)
            - partner_short_name, customer_short_name (strings)
            - funding_type, funding_type_short (strings)
            - executive_summary (string — complete, self-contained paragraph)
            - partner_overview, customer_overview (strings)
            - architecture_description (string)
            - project_start_date, project_end_date (strings)
            - engagement_type: "project", "pilot", "POC", or "assessment"
            - organization_term: "phases", "workstreams", or "activities"

            Simple lists:
            - activities (list of strings — high-level activity descriptions)
            - objectives (list of strings)
            - out_of_scope (list of strings)
            - assumptions (list of strings)
            - success_criteria (list of strings)

            Structured arrays — MUST NOT be empty:
            - functional_requirements: list of {"number": "FR-01", "description": "The system shall..."}
            - non_functional_requirements: list of {"number": "NFR-01", "description": "The platform shall..."}
            - architecture_components: list of {"name": "BigQuery", "role": "Centralized data warehouse"}
            - architecture_integrations: list of {"name": "SAP ERP", "description": "Source system for..."}
            - activity_phases: list of {"name": "Phase 1: Discovery", "description": "Define architecture...", "tasks": ["Conduct kickoff workshop", "Review current systems"]}
            - deliverables: list of {"activity": "Phase 1", "name": "Architecture Design Document", "description": "Detailed technical design...", "format": "Document"}
            - timeline: list of {"activity": "Phase 1: Discovery", "timeframe": "Weeks 1-2", "outcomes": "Approved architecture design"}
            - partner_roles: list of {"role": "Data Architect", "responsibilities": "Design and oversee..."}
            - customer_roles: list of {"role": "Product Owner", "responsibilities": "Define priorities..."}

            Optional structured arrays:
            - key_engagement_details: list of {"label": "Partner", "value": "GFT Brasil"}.
            - technology_stack: list of {"service": "BigQuery", "purpose": "..."}
            - milestones: list of {"name": "Milestone 1", "deliverables": "...", "estimated_completion": "Week 2", "payment": "30%"}
            - risks: list of {"description": "...", "mitigation": "..."}
            - consumption_plan: dict with "services", "rows", "notes" keys.

            Optional simple fields:
            - taxes_included (boolean — default true)
            - non_commit_psf (boolean — default false)

    Returns:
        A dictionary with status and the file path of the generated document.
    """
    try:
        data = json.loads(sow_data)
    except json.JSONDecodeError as e:
        logger.error('generate_sow_document: invalid JSON | error=%s', str(e))
        return {'error': f'Dados inválidos (JSON inválido): {str(e)}'}

    _apply_defaults(data)
    _auto_derive_fields(data)

    required_fields = [
        'partner_name',
        'customer_name',
        'project_title',
        'executive_summary',
        'functional_requirements',
        'non_functional_requirements',
        'architecture_components',
        'architecture_integrations',
        'activity_phases',
        'deliverables',
        'timeline',
        'partner_roles',
        'customer_roles',
    ]
    missing = [f for f in required_fields if not data.get(f)]
    if missing:
        logger.error(
            'generate_sow_document: missing required fields | fields=%s',
            ', '.join(missing),
        )
        return {
            'error': f'Campos obrigatórios ausentes no JSON: {", ".join(missing)}'
        }

    quality_errors = validate_quality_gates(data)
    if quality_errors:
        logger.error(
            'generate_sow_document: quality gates failed | errors=%s',
            '; '.join(quality_errors),
        )
        return {
            'error': (
                'O conteúdo não atinge os mínimos de qualidade. '
                'Corrija e chame a tool novamente:\n'
                + '\n'.join(f'- {e}' for e in quality_errors)
            )
        }

    template_path = _TEMPLATE_DIR / _TEMPLATE_FILENAME
    if not template_path.exists():
        logger.error(
            'generate_sow_document: template not found | path=%s',
            template_path,
        )
        return {'error': f'Template SOW não encontrado em: {template_path}'}

    customer_logo_tempfile: Path | None = None
    diagram_tempfile: Path | None = None

    try:
        doc = DocxTemplate(str(template_path))

        partner_logo_path = _TEMPLATE_DIR / _PARTNER_LOGO_FILENAME
        data['partner_logo'] = load_logo(
            doc, partner_logo_path, 'partner', _PARTNER_LOGO_WIDTH_MM
        )

        customer_logo_tempfile = await _load_image_from_artifact(
            tool_context, 'customer_logo_artifact'
        )
        if customer_logo_tempfile:
            data['customer_logo'] = load_logo(
                doc, customer_logo_tempfile, 'customer', _CUSTOMER_LOGO_WIDTH_MM
            )
        else:
            data['customer_logo'] = '[Customer Logo]'

        diagram_tempfile = await _load_image_from_artifact(
            tool_context, 'architecture_diagram_artifact'
        )
        if diagram_tempfile:
            data['architecture_diagram'] = InlineImage(
                doc, str(diagram_tempfile), width=Mm(150)
            )
            logger.info('generate_sow_document: diagram loaded from artifact')
        elif not data.get('architecture_diagram'):
            data[
                'architecture_diagram'
            ] = '[Architecture Diagram — to be generated]'

        doc.render(data, autoescape=True)

        output_dir = Path(tempfile.gettempdir()) / 'sow_documents'
        output_dir.mkdir(parents=True, exist_ok=True)

        raw_title = data.get('project_title', 'SOW')
        safe_title = re.sub(r'[^a-zA-Z0-9]+', '_', raw_title).strip('_')[:20]
        timestamp = str(int(time.time()))[-6:]
        artifact_filename = f'SOW_{safe_title}_{timestamp}.docx'
        output_path = str(output_dir / artifact_filename)

        doc.save(output_path)

        if tool_context:
            tool_context.state[_DOCUMENT_PATH_KEY] = output_path
            try:
                with open(output_path, 'rb') as f:
                    docx_bytes = f.read()

                artifact = genai_types.Part.from_bytes(
                    data=docx_bytes,
                    mime_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                )
                version = await tool_context.save_artifact(
                    artifact_filename, artifact
                )
                logger.info(
                    'generate_sow_document: artifact saved | filename=%s | version=%s',
                    artifact_filename,
                    version,
                )
            except Exception as artifact_err:
                logger.error(
                    'generate_sow_document: ARTIFACT SAVE FAILED | error=%s | type=%s',
                    str(artifact_err),
                    type(artifact_err).__name__,
                )

        logger.info(
            'generate_sow_document: document generated | path=%s',
            output_path,
        )

        return {
            'status': 'success',
            'message': (
                'O documento SOW foi gerado com sucesso e está disponível '
                'para download como artefato.'
            ),
            'document_path': output_path,
            'artifact_filename': artifact_filename,
        }

    except Exception as e:
        logger.error(
            'generate_sow_document: failed | error=%s | type=%s',
            str(e),
            type(e).__name__,
        )
        return {'error': f'Falha ao gerar o documento SOW: {str(e)}'}

    finally:
        for tmp, label in [
            (customer_logo_tempfile, 'customer logo'),
            (diagram_tempfile, 'diagram'),
        ]:
            if tmp and tmp.exists():
                try:
                    tmp.unlink()
                except Exception as cleanup_err:
                    logger.warning(
                        'generate_sow_document: failed to clean up %s tempfile | path=%s | error=%s',
                        label,
                        tmp,
                        str(cleanup_err),
                    )


async def _load_image_from_artifact(
    tool_context: ToolContext | None,
    state_key: str,
) -> Path | None:
    """Load an image artifact and write it to a temp file.

    Args:
        tool_context: ADK ToolContext (may be None).
        state_key: Session state key holding the artifact filename.

    Returns:
        Path to the temp file with image bytes, or None on failure.
    """
    if not tool_context:
        return None

    artifact_filename = tool_context.state.get(state_key)
    if not artifact_filename:
        logger.info(
            '_load_image_from_artifact: no artifact in state | key=%s',
            state_key,
        )
        return None

    try:
        part = await tool_context.load_artifact(filename=artifact_filename)

        image_bytes: bytes | None = None

        if part and part.inline_data and part.inline_data.data:
            image_bytes = part.inline_data.data
            logger.info(
                '_load_image_from_artifact: bytes from inline_data | key=%s | size=%d',
                state_key,
                len(image_bytes),
            )
        elif part and part.file_data and part.file_data.file_uri:
            image_bytes = download_gcs_uri(part.file_data.file_uri)
            if image_bytes:
                logger.info(
                    '_load_image_from_artifact: bytes from file_data | key=%s | uri=%s | size=%d',
                    state_key,
                    part.file_data.file_uri,
                    len(image_bytes),
                )

        if not image_bytes:
            logger.warning(
                '_load_image_from_artifact: artifact empty | key=%s | filename=%s',
                state_key,
                artifact_filename,
            )
            return None

        ext = Path(artifact_filename).suffix or '.png'
        fd, tempfile_path = tempfile.mkstemp(suffix=ext)
        os.close(fd)
        tmp = Path(tempfile_path)
        tmp.write_bytes(image_bytes)
        return tmp

    except Exception as err:
        logger.warning(
            '_load_image_from_artifact: failed | key=%s | filename=%s | error=%s',
            state_key,
            artifact_filename,
            str(err),
        )
        return None


def _apply_defaults(data: dict) -> None:
    """Apply default values to optional fields."""
    data['organization_term'] = data.get('organization_term', 'phases')
    if len(data.get('organization_term', '').split()) > 2:
        logger.warning(
            'generate_sow_document: organization_term was invalid, defaulting to "phases"'
        )
        data['organization_term'] = 'phases'

    valid_engagement = {'project', 'pilot', 'poc', 'assessment', 'workshop'}
    eng = data.get('engagement_type', 'project').lower()
    if eng not in valid_engagement:
        logger.warning(
            'generate_sow_document: engagement_type "%s" invalid, defaulting to "project"',
            eng,
        )
        data['engagement_type'] = 'project'

    data.setdefault('taxes_included', True)
    data.setdefault('non_commit_psf', False)
    data.setdefault('key_engagement_details', [])
    data.setdefault('technology_stack', [])
    data.setdefault('milestones', [])
    data.setdefault('risks', [])
    data.setdefault('architecture_diagram', '')

    cp_raw = data.get('consumption_plan')
    if isinstance(cp_raw, dict) and 'rows' in cp_raw and 'services' in cp_raw:
        processed_rows = []
        for row in cp_raw.get('rows', []):
            new_row = dict(row)
            if 'values' in new_row:
                new_row['costs'] = new_row.pop('values')
            elif 'costs' not in new_row:
                new_row['costs'] = []
            processed_rows.append(new_row)
        cp_raw['rows'] = processed_rows
        data['consumption_plan_table'] = cp_raw
        data['consumption_plan'] = ''
    elif isinstance(cp_raw, str) and cp_raw.strip():
        data['consumption_plan_table'] = None
        data['consumption_plan'] = cp_raw
    else:
        data['consumption_plan_table'] = None
        data['consumption_plan'] = ''


def _auto_derive_fields(data: dict) -> None:
    """Auto-derive fields that can be inferred from other fields."""
    if not data.get('activities') and data.get('activity_phases'):
        data['activities'] = [
            phase.get('name', '') for phase in data['activity_phases']
        ]

    if not data.get('funding_type_short') and data.get('funding_type'):
        ft = data['funding_type'].upper()
        if 'PSF' in ft or 'PARTNER' in ft:
            data['funding_type_short'] = 'PSF'
        elif 'DAF' in ft or 'ACCELERATION' in ft:
            data['funding_type_short'] = 'DAF'
        else:
            data['funding_type_short'] = 'DAF'

    if not data.get('technology_stack') and data.get('architecture_components'):
        data['technology_stack'] = [
            {'service': comp.get('name', ''), 'purpose': comp.get('role', '')}
            for comp in data['architecture_components']
        ]

    if not data.get('key_engagement_details'):
        data['key_engagement_details'] = [
            {'label': 'Partner', 'value': data.get('partner_name', '[Partner]')},
            {'label': 'Customer', 'value': data.get('customer_name', '[Customer]')},
            {'label': 'Effective Date', 'value': data.get('project_start_date', 'TBD')},
            {'label': 'Service Delivery', 'value': 'Remote'},
            {'label': 'Pricing Model', 'value': 'Fixed Fee'},
        ]
