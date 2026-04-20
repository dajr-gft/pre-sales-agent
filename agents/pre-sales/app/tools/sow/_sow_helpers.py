from __future__ import annotations

import hashlib
import json as _json
from pathlib import Path

import structlog
from docx.shared import Mm
from docxtpl import DocxTemplate, InlineImage

logger = structlog.get_logger()


QUALITY_GATES = {
    'out_of_scope': ('Out-of-Scope', 20),
    'assumptions': ('Assumptions', 15),
    'deliverables': ('Deliverables', 10),
    'functional_requirements': ('Functional Requirements', 10),
    'non_functional_requirements': ('Non-Functional Requirements', 5),
    'success_criteria': ('Success Criteria', 5),
}


def validate_quality_gates(data: dict) -> list[str]:
    """Validate minimum content thresholds before document generation.

    Returns a list of error messages. Empty list means all gates passed.
    """
    errors = []

    for field, (label, minimum) in QUALITY_GATES.items():
        items = data.get(field, [])
        if len(items) < minimum:
            errors.append(f'{label}: {len(items)} itens (mínimo: {minimum})')

    # Risks: at least 3 if the section is provided at all
    risks = data.get('risks', [])
    if risks and len(risks) < 3:
        errors.append(f'Risks: {len(risks)} itens (mínimo: 3 quando presente)')

    # Consumption plan: required for PSF engagements
    ft = (
        data.get('funding_type_short') or data.get('funding_type') or ''
    ).upper()
    if 'PSF' in ft:
        cp = data.get('consumption_plan_table') or data.get('consumption_plan')
        if not cp:
            errors.append('Consumption Plan: ausente (obrigatório para PSF)')

    return errors


def load_logo(
    doc: DocxTemplate,
    logo_path: Path,
    label: str,
    width_mm: int,
) -> InlineImage | str:
    """Load a logo image as InlineImage, or return placeholder text on failure.

    Args:
        doc: The DocxTemplate instance (required by InlineImage).
        logo_path: Path to the image file.
        label: Human-readable label for logging ('partner' or 'customer').
        width_mm: Desired width in millimeters.

    Returns:
        InlineImage if successful, placeholder string if not.
    """
    if not logo_path or not logo_path.exists():
        logger.info(
            'load_logo: %s logo not found | path=%s — using placeholder',
            label,
            logo_path,
        )
        return f'[{label.capitalize()} Logo]'

    valid_extensions = {'.png', '.jpg', '.jpeg', '.svg', '.gif', '.webp'}
    if logo_path.suffix.lower() not in valid_extensions:
        logger.warning(
            'load_logo: %s logo has unsupported extension | path=%s',
            label,
            logo_path,
        )
        return f'[{label.capitalize()} Logo]'

    try:
        logo = InlineImage(doc, str(logo_path), width=Mm(width_mm))
        logger.info(
            'load_logo: %s logo loaded | path=%s | width=%dmm',
            label,
            logo_path,
            width_mm,
        )
        return logo
    except Exception as err:
        logger.warning(
            'load_logo: failed to load %s logo | error=%s',
            label,
            str(err),
        )
        return f'[{label.capitalize()} Logo]'

def sow_data_hash(data: dict | str) -> str:
    """Stable 12-char hash of a sow_data payload for log correlation.

    Normalizes JSON serialization (sort_keys=True) so that logically identical
    payloads produce identical hashes even if serialized differently by the
    calling agent.

    Args:
        data: Either the parsed dict or the raw JSON string.

    Returns:
        12-char hex prefix of SHA256. Returns 'unhashable' on any error
        (never raises — diagnostic code must not break the caller).
    """
    try:
        parsed = _json.loads(data) if isinstance(data, str) else data
        normalized = _json.dumps(parsed, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:12]
    except Exception:
        return 'unhashable'


def sow_data_preview(data: dict | str, max_chars: int = 2000) -> str:
    """Structural preview of a sow_data payload for failure logs.

    Shows the SHAPE of the payload (top-level keys, list lengths, sampled
    first item per list, truncated long strings) without dumping full
    content. Better than a flat truncation for diagnosing structural issues
    ("which field is malformed?") and reduces incidental content exposure
    in logs.

    Only call on the failure path — on successful runs the hash alone is
    enough.

    Args:
        data: Parsed dict or raw JSON string.
        max_chars: Hard cap on output length.

    Returns:
        JSON-formatted preview string. Returns '<preview_failed: ...>' on
        any error (never raises).
    """
    try:
        parsed = _json.loads(data) if isinstance(data, str) else data
        if not isinstance(parsed, dict):
            return f'<not_a_dict: type={type(parsed).__name__}>'

        preview: dict = {}
        for k, v in parsed.items():
            if isinstance(v, list):
                if v:
                    first = v[0]
                    if isinstance(first, str):
                        sample = first[:100] + ('…' if len(first) > 100 else '')
                    elif isinstance(first, dict):
                        sample = {
                            sk: (
                                (str(sv)[:80] + '…')
                                if len(str(sv)) > 80
                                else sv
                            )
                            for sk, sv in list(first.items())[:5]
                        }
                    else:
                        sample = first
                    preview[k] = {'_count': len(v), '_first': sample}
                else:
                    preview[k] = {'_count': 0}
            elif isinstance(v, dict):
                preview[k] = {'_keys': list(v.keys())[:10]}
            elif isinstance(v, str):
                preview[k] = v[:150] + ('…' if len(v) > 150 else '')
            else:
                preview[k] = v

        serialized = _json.dumps(
            preview, ensure_ascii=False, indent=2, default=str
        )
        if len(serialized) > max_chars:
            serialized = serialized[:max_chars] + '…<truncated>'
        return serialized
    except Exception as e:
        return f'<preview_failed: {type(e).__name__}: {e}>'
