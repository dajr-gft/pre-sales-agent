from __future__ import annotations

from pathlib import Path

import structlog
from docx.shared import Mm
from docxtpl import DocxTemplate, InlineImage

logger = structlog.get_logger()


QUALITY_GATES = {
    "out_of_scope": ("Out-of-Scope", 20),
    "assumptions": ("Assumptions", 15),
    "deliverables": ("Deliverables", 10),
    "functional_requirements": ("Functional Requirements", 10),
    "success_criteria": ("Success Criteria", 5),
}


def validate_quality_gates(data: dict) -> list[str]:
    """Validate minimum content thresholds before document generation.

    Returns a list of error messages. Empty list means all gates passed.
    """
    errors = []

    for field, (label, minimum) in QUALITY_GATES.items():
        items = data.get(field, [])
        if len(items) < minimum:
            errors.append(f"{label}: {len(items)} itens (mínimo: {minimum})")

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
            "load_logo: %s logo not found | path=%s — using placeholder",
            label,
            logo_path,
        )
        return f"[{label.capitalize()} Logo]"

    valid_extensions = {".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp"}
    if logo_path.suffix.lower() not in valid_extensions:
        logger.warning(
            "load_logo: %s logo has unsupported extension | path=%s",
            label,
            logo_path,
        )
        return f"[{label.capitalize()} Logo]"

    try:
        logo = InlineImage(doc, str(logo_path), width=Mm(width_mm))
        logger.info(
            "load_logo: %s logo loaded | path=%s | width=%dmm",
            label,
            logo_path,
            width_mm,
        )
        return logo
    except Exception as err:
        logger.warning(
            "load_logo: failed to load %s logo | error=%s",
            label,
            str(err),
        )
        return f"[{label.capitalize()} Logo]"
