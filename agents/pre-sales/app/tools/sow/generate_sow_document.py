import copy
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
from ...sub_agents.validation.schema import STATE_SOW, STATE_STAGE
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
    tool_context: ToolContext = None,
) -> dict[str, Any]:
    """Render the validated, staged SOW to ``.docx``.

    Reads the SOW payload from ``state['app:sow:current']`` (written by
    ``stage_sow`` and validated by ``sow_quality_loop``) — it does NOT
    take the SOW as an argument. Call only after the architecture review
    is approved AND the final ``sow_quality_loop`` returned ``passed``.

    The expected ``state['app:sow:stage']`` is ``'full'``; calls against
    a ``'content'``-stage payload are rejected. The staged dict is the
    source of truth — never patch the payload in your own turn. On
    failure, the suggestion points back to the regenerate → assemble →
    stage → quality_loop → generate chain.

    Returns:
        A dictionary with status and the file path of the generated
        document on success, or a ``ToolError`` on failure.
    """
    if tool_context is None:
        return ToolError(
            status='error',
            error='tool_context is required.',
            retryable=False,
            tool='generate_sow_document',
            suggestion=(
                'Call this tool from within an ADK runtime; tool_context '
                'is injected automatically.'
            ),
        )

    staged = tool_context.state.get(STATE_SOW)
    if not isinstance(staged, dict) or not staged:
        return ToolError(
            status='error',
            error=(
                "No staged SOW found in state['app:sow:current']. "
                'The validated payload is missing.'
            ),
            retryable=False,
            tool='generate_sow_document',
            suggestion=(
                'Run assemble_sow_payload(stage="full") → stage_sow('
                'stage="full") → sow_quality_loop before generate_sow_document.'
            ),
        )

    current_stage = tool_context.state.get(STATE_STAGE)
    if current_stage != 'full':
        return ToolError(
            status='error',
            error=(
                'Cannot generate the final document from stage='
                f'{current_stage!r}; the document is only rendered from a '
                "stage='full' SOW that the quality loop validated."
            ),
            retryable=False,
            tool='generate_sow_document',
            suggestion=(
                'Re-run assemble_sow_payload(stage="full") → stage_sow('
                'stage="full") → sow_quality_loop, then call '
                'generate_sow_document again.'
            ),
        )

    # CRITICAL: never mutate the staged dict. The preprocessing pipeline
    # below (defaults, derivations, normalization) mutates in place, and
    # the renderer injects non-serializable docxtpl objects (Listing,
    # InlineImage). Mutating state['app:sow:current'] would corrupt the
    # validated payload for any subsequent re-stage / serialization.
    data = copy.deepcopy(staged)

    # Hash BEFORE any mutation (and before docxtpl objects are injected
    # downstream) so the log line records the serializable shape of the
    # staged payload.
    raw_hash = sow_data_hash(
        json.dumps(data, sort_keys=True, ensure_ascii=False)
    )
    logger.info('generate_sow_document_invoked', sow_data_hash=raw_hash)

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
            error=(
                'The staged SOW is missing required fields: '
                f'{", ".join(missing)}.'
            ),
            retryable=False,
            tool='generate_sow_document',
            suggestion=(
                'Regenerate the section bundle(s) that own the missing '
                'fields, then re-run assemble_sow_payload → stage_sow → '
                'sow_quality_loop before calling generate_sow_document '
                'again.'
            ),
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
                'The staged SOW does not meet the quality gates:\n'
                + '\n'.join(f'- {e}' for e in quality_errors)
            ),
            retryable=True,
            tool='generate_sow_document',
            suggestion=(
                'Regenerate the affected section bundle to satisfy the '
                'gate, then re-run assemble_sow_payload → stage_sow → '
                'sow_quality_loop before calling generate_sow_document '
                'again.'
            ),
        )

    # Structural validation — defense-in-depth against drift between
    # what the quality loop blessed and what reaches the renderer. The
    # SOW is sourced from state, so any failure here is an inconsistency
    # to resolve by regenerating the cited section and re-staging — not
    # by patching the payload in the agent's turn (the agent no longer
    # holds one).
    validation = _content_validator.validate(data)
    if not validation.passed:
        logger.error(
            'structural_validation_failed',
            sow_data_hash=raw_hash,
            errors=len(validation.errors),
            warnings=len(validation.warnings),
            error_details=[str(e) for e in validation.errors],
            warning_details=[str(w) for w in validation.warnings],
            sow_data_preview=sow_data_preview(data),
        )
        return ToolError(
            status='error',
            error=(
                'The staged SOW failed the final structural validation:\n'
                + '\n'.join(f'- {e}' for e in validation.errors)
            ),
            retryable=True,
            tool='generate_sow_document',
            suggestion=(
                'Regenerate the section bundle(s) cited in the errors, '
                'then re-run assemble_sow_payload → stage_sow → '
                'sow_quality_loop before calling generate_sow_document '
                'again. Do NOT attempt to patch the payload in your own '
                'turn — the SOW is built from state, not from the model.'
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
