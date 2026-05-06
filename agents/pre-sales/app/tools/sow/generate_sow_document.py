import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

import structlog
from docx.shared import Mm
from docxtpl import DocxTemplate, InlineImage, Listing, RichText
from google.adk.tools import ToolContext
from google.genai import types as genai_types

from ...shared.errors import safe_tool
from ...shared.types import ToolError, ToolSuccess
from ...shared.validators import ContentValidator
from ._logo_fetcher import fetch_customer_logo
from ._sow_helpers import (
    load_logo,
    sow_data_hash,
    sow_data_preview,
    validate_quality_gates,
)

logger = structlog.get_logger()

_DOCUMENT_PATH_KEY = 'sow_document_path'
_TEMPLATE_DIR = Path(__file__).parent / 'templates'
_TEMPLATE_FILENAME = 'SOW_Template.docx'
_PARTNER_LOGO_FILENAME = 'gft_logo.png'

_PARTNER_LOGO_WIDTH_MM = 41
_CUSTOMER_LOGO_WIDTH_MM = 35

_content_validator = ContentValidator()


@safe_tool
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
            - customer_primary_domain: optional string. The customer's
              main institutional domain, without protocol or www (e.g.
              "itau.com.br", "bv.com.br", "btgpactual.com.br"). Used
              to automatically fetch the customer logo. Omit if the
              customer's domain is not publicly known — the document
              will render a placeholder.

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
            - milestones: list of {"name": "Milestone 1: Kickoff", "deliverables": "Project Plan", "estimated_completion": "Week 2", "payment": "30%"}
              (omit if single payment at project completion)
            - risks: list of {"description": "Data quality issues...", "mitigation": "Implement validation..."}
              (optional — if provided, content will be added to Assumptions section)

            Optional simple fields:
            - taxes_included (boolean — default true. Controls which cost table and
              tax paragraph variant is rendered.)
            - non_commit_psf (boolean — default false. If true, includes the Non-Commit
              PSF 30% reduction paragraph.)

            Multi-line text:
                Any string field — top-level or nested inside a structured
                array — may include `\n` to request a line break in the
                rendered .docx. Use `\n\n` to visually separate paragraphs.
                The tool normalizes common variants (literal `\\n`, `\r\n`,
                runs of 3+ blank lines) and converts each `\n` into a Word
                line break. There is no allowlist: this works on every
                string field, present or future. Keep short labels and
                names single-line.

    Returns:
        A dictionary with status and the file path of the generated document.
    """
    raw_hash = sow_data_hash(sow_data)
    logger.info('generate_sow_document_invoked', sow_data_hash=raw_hash)

    try:
        data = json.loads(sow_data)
    except json.JSONDecodeError as e:
        return ToolError(
            status='error',
            error=f'Dados inválidos (JSON inválido): {e}',
            retryable=False,
            tool='generate_sow_document',
            suggestion='Verifique a formatação JSON e tente novamente.',
        )

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
            'missing_required_fields',
            sow_data_hash=raw_hash,
            fields=missing,
        )
        return ToolError(
            status='error',
            error=f"Campos obrigatórios ausentes no JSON: {', '.join(missing)}",
            retryable=False,
            tool='generate_sow_document',
            suggestion='Preencha todos os campos obrigatórios antes de gerar o documento.',
        )

    quality_errors = validate_quality_gates(data)
    if quality_errors:
        logger.error(
            'quality_gates_failed',
            sow_data_hash=raw_hash,
            errors=quality_errors,
            sow_data_preview=sow_data_preview(data),
        )
        return ToolError(
            status='error',
            error=(
                'O conteúdo não atinge os mínimos de qualidade. '
                'Corrija e chame a tool novamente:\n'
                + '\n'.join(f'- {e}' for e in quality_errors)
            ),
            retryable=True,
            tool='generate_sow_document',
            suggestion='Gere mais conteúdo para atingir os mínimos de qualidade.',
        )

    # Structural validation (hard gate — blocks on errors, warns on warnings)
    validation = _content_validator.validate(data)
    if not validation.passed:
        last_failed_hash = (
            tool_context.state.get('generate_sow_last_failed_hash')
            if tool_context
            else None
        )
        is_repeat = last_failed_hash == raw_hash

        logger.error(
            'structural_validation_failed',
            sow_data_hash=raw_hash,
            errors=len(validation.errors),
            warnings=len(validation.warnings),
            error_details=[str(e) for e in validation.errors],
            warning_details=[str(w) for w in validation.warnings],
            is_repeat=is_repeat,
            sow_data_preview=sow_data_preview(data),
        )

        if tool_context:
            tool_context.state['generate_sow_last_failed_hash'] = raw_hash

        if is_repeat:
            return ToolError(
                status='error',
                error=(
                    'PAYLOAD IDÊNTICO DETECTADO: você enviou exatamente o '
                    'mesmo sow_data duas vezes seguidas sem corrigir o erro '
                    'anterior. Isso significa que você está regerando o '
                    'payload a partir do contexto da conversa em vez de '
                    'editar o payload anterior de forma incremental.\n\n'
                    'AÇÃO REQUERIDA:\n'
                    '1. Pegue o payload EXATO que acabou de enviar.\n'
                    '2. Modifique APENAS o(s) campo(s) citado(s) no erro '
                    'abaixo.\n'
                    '3. NÃO reconstrua outras seções — mantenha o resto '
                    'byte-a-byte idêntico.\n'
                    '4. Chame a tool novamente com o payload editado.\n\n'
                    'Erro que permanece não resolvido:\n'
                    + '\n'.join(f'- {e}' for e in validation.errors)
                ),
                retryable=True,
                tool='generate_sow_document',
                suggestion=(
                    'Edite o payload anterior no lugar. Não regenere do zero.'
                ),
            )

        return ToolError(
            status='error',
            error=(
                'Validação estrutural falhou. Corrija os erros abaixo:\n'
                + '\n'.join(f'- {e}' for e in validation.errors)
            ),
            retryable=True,
            tool='generate_sow_document',
            suggestion=(
                'Use validate_sow_content para verificar o conteúdo antes '
                'de gerar o documento.'
            ),
        )
    if validation.warnings:
        logger.warning(
            'structural_validation_warnings',
            sow_data_hash=raw_hash,
            count=len(validation.warnings),
            warnings=[str(w) for w in validation.warnings],
        )

    template_path = _TEMPLATE_DIR / _TEMPLATE_FILENAME
    if not template_path.exists():
        return ToolError(
            status='error',
            error=f'Template SOW não encontrado em: {template_path}',
            retryable=False,
            tool='generate_sow_document',
        )

    customer_logo_tempfile: Path | None = None
    diagram_tempfile: Path | None = None

    try:
        doc = DocxTemplate(str(template_path))

        partner_logo_path = _TEMPLATE_DIR / _PARTNER_LOGO_FILENAME
        data['partner_logo'] = load_logo(
            doc, partner_logo_path, 'partner', _PARTNER_LOGO_WIDTH_MM
        )

        customer_logo_tempfile = _fetch_customer_logo_to_tempfile(
            customer_name=data.get('customer_name', ''),
            customer_primary_domain=data.get('customer_primary_domain'),
        )
        if customer_logo_tempfile:
            data['customer_logo'] = load_logo(
                doc,
                customer_logo_tempfile,
                'customer',
                _CUSTOMER_LOGO_WIDTH_MM,
            )
        else:
            data['customer_logo'] = '[Customer Logo]'

        diagram_filename = (
            tool_context.state.get('architecture_diagram_artifact')
            if tool_context
            else None
        )
        diagram_tempfile = await _load_artifact_to_tempfile(
            tool_context, diagram_filename, 'diagram'
        )
        if diagram_tempfile:
            data['architecture_diagram'] = InlineImage(
                doc, str(diagram_tempfile), width=Mm(150)
            )
        elif not data.get('architecture_diagram'):
            data[
                'architecture_diagram'
            ] = '[Architecture Diagram — to be generated]'

        _normalize_text_fields(data)

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
                    'artifact_saved',
                    filename=artifact_filename,
                    version=version,
                )
            except Exception as artifact_err:
                logger.error(
                    'artifact_save_failed',
                    error=str(artifact_err),
                    error_type=type(artifact_err).__name__,
                )

        logger.info(
            'document_generated',
            sow_data_hash=raw_hash,
            path=output_path,
        )

        return ToolSuccess(
            status='success',
            data={
                'message': (
                    'O documento SOW foi gerado com sucesso e está disponível '
                    'para download como artefato.'
                ),
                'document_path': output_path,
                'artifact_filename': artifact_filename,
            },
        )

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
                        'cleanup_failed',
                        label=label,
                        path=str(tmp),
                        error=str(cleanup_err),
                    )


async def _load_artifact_to_tempfile(
    tool_context: ToolContext | None,
    artifact_filename: str | None,
    label: str,
) -> Path | None:
    """Load an artifact by filename and write its bytes to a tempfile."""
    if not tool_context or not artifact_filename:
        return None

    try:
        part = await tool_context.load_artifact(filename=artifact_filename)

        if not (part and part.inline_data and part.inline_data.data):
            logger.warning(
                'artifact_empty',
                label=label,
                filename=artifact_filename,
            )
            return None

        ext = Path(artifact_filename).suffix or '.png'
        fd, tempfile_path = tempfile.mkstemp(suffix=ext)
        os.close(fd)
        tmp = Path(tempfile_path)
        tmp.write_bytes(part.inline_data.data)
        logger.info(
            'artifact_loaded',
            label=label,
            filename=artifact_filename,
            size=len(part.inline_data.data),
        )
        return tmp

    except Exception as err:
        logger.warning(
            'artifact_load_failed',
            label=label,
            filename=artifact_filename,
            error=str(err),
        )
        return None


def _fetch_customer_logo_to_tempfile(
    customer_name: str,
    customer_primary_domain: str | None,
) -> Path | None:
    """Fetch customer logo via logo.dev and persist bytes to a tempfile.

    docxtpl/InlineImage needs a file path, so we materialize the bytes on
    disk. The caller is responsible for cleaning up the tempfile after
    rendering (handled by the existing finally block in
    ``generate_sow_document``).

    Returns None when no logo could be retrieved — caller should render
    a placeholder.
    """
    if not customer_name:
        return None

    logo_bytes = fetch_customer_logo(
        customer_name=customer_name,
        inferred_domain=customer_primary_domain,
    )
    if not logo_bytes:
        return None

    fd, tempfile_path = tempfile.mkstemp(suffix='.png')
    os.close(fd)
    tmp = Path(tempfile_path)
    tmp.write_bytes(logo_bytes)
    logger.info(
        'customer_logo_fetched',
        customer_name=customer_name,
        size=len(logo_bytes),
    )
    return tmp


_PRESERVE_TYPES = (Listing, RichText, InlineImage)
_BLANK_LINE_RUN = re.compile(r'\n{3,}')


def _normalize_multiline_string(value: str) -> str | Listing:
    """Coerce a raw string into the right docxtpl payload for line breaks.

    Without this, ``\\n`` characters reach Word as literal text and render
    on a single visual line. ``Listing`` causes docxtpl to emit ``<w:br/>``
    for every newline, which Word renders as a soft line break.

    Defensive normalization handles three model failure modes:
    - ``\\\\n`` (escaped twice in the JSON string) → real ``\\n``
    - ``\\r\\n`` / lone ``\\r`` → ``\\n``
    - runs of 3+ blank lines → 2 (caps visual spacing)

    Strings without any newline are returned unchanged so unaffected
    fields incur zero overhead and zero behavior change.
    """
    if '\\n' in value:
        value = value.replace('\\n', '\n')
    if '\r' in value:
        value = value.replace('\r\n', '\n').replace('\r', '\n')
    if '\n\n\n' in value:
        value = _BLANK_LINE_RUN.sub('\n\n', value)
    if '\n' not in value:
        return value
    return Listing(value)


def _normalize_text_fields(data: Any) -> Any:
    """Walk a render payload and normalize every string leaf in place.

    Field-agnostic on purpose: any current or future string in the
    payload — top-level or nested inside dicts and lists — gets the
    same treatment. The walker is idempotent (preserves ``Listing``,
    ``RichText``, and ``InlineImage`` values untouched), so it is safe
    to re-run on already-normalized data.
    """
    if isinstance(data, _PRESERVE_TYPES):
        return data
    if isinstance(data, str):
        return _normalize_multiline_string(data)
    if isinstance(data, dict):
        for key, value in list(data.items()):
            data[key] = _normalize_text_fields(value)
        return data
    if isinstance(data, list):
        for index, item in enumerate(data):
            data[index] = _normalize_text_fields(item)
        return data
    return data


def _apply_defaults(data: dict) -> None:
    """Apply default values to optional fields."""
    data['organization_term'] = data.get('organization_term', 'phases')
    if len(data.get('organization_term', '').split()) > 2:
        logger.warning(
            'invalid_organization_term',
            value=data['organization_term'],
        )
        data['organization_term'] = 'phases'

    valid_engagement = {'project', 'pilot', 'poc', 'assessment', 'workshop'}
    eng = data.get('engagement_type', 'project').lower()
    if eng not in valid_engagement:
        logger.warning('invalid_engagement_type', value=eng)
        data['engagement_type'] = 'project'

    data.setdefault('taxes_included', True)
    data.setdefault('non_commit_psf', False)
    data.setdefault('milestones', [])
    data.setdefault('risks', [])
    data.setdefault('architecture_diagram', '')


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

    if not data.get('project_type'):
        data['project_type'] = _infer_project_type(data)


# GenAI/ML service names used to infer project_type for template conditionals.
_GENAI_SERVICES = {
    'vertex ai',
    'gemini',
    'agent engine',
    'dialogflow',
    'vertex ai search',
    'generative ai',
    'genai',
}
_ML_SERVICES = {
    'automl',
    'vertex ai',
    'bigquery ml',
    'tensorflow',
    'pytorch',
}


def _infer_project_type(data: dict) -> str:
    """Infer project_type ('genai', 'ml', or 'standard') from architecture.

    The SOW template uses project_type to conditionally include ML/GenAI
    assumptions (e.g., labeled data, model performance review).
    """
    # Collect all service/component names mentioned in the architecture
    names: set[str] = set()
    for comp in data.get('architecture_components', []):
        names.add(comp.get('name', '').lower())
        names.add(comp.get('role', '').lower())

    arch_desc = (data.get('architecture_description') or '').lower()
    exec_summary = (data.get('executive_summary') or '').lower()
    combined_text = ' '.join(names) + ' ' + arch_desc + ' ' + exec_summary

    if any(svc in combined_text for svc in _GENAI_SERVICES):
        return 'genai'
    if any(svc in combined_text for svc in _ML_SERVICES):
        return 'ml'
    return 'standard'
