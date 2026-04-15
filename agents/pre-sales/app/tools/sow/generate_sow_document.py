from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

import structlog
from docx.shared import Mm
from docxtpl import DocxTemplate, InlineImage
from google.adk.tools import ToolContext
from google.genai import types as genai_types

from ...shared.errors import safe_tool
from ...shared.types import ToolError, ToolSuccess
from ...shared.validators import ContentValidator
from ._sow_helpers import load_logo, validate_quality_gates

logger = structlog.get_logger()

_DOCUMENT_PATH_KEY = "sow_document_path"
_TEMPLATE_DIR = Path(__file__).parent / "templates"
_TEMPLATE_FILENAME = "SOW_Template.docx"
_PARTNER_LOGO_FILENAME = "gft_logo.png"

_PARTNER_LOGO_WIDTH_MM = 41
_CUSTOMER_LOGO_WIDTH_MM = 43

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
            - customer_logo_filename: optional. Filename of the customer logo
              the user uploaded earlier in the conversation (e.g.
              "test_customer_logo.png"). When the user uploads a file in
              Gemini Enterprise, you will see it in the message history as
              `<start_of_user_uploaded_file: NAME>` — pass that NAME here.
              Omit if the user skipped the logo step.

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
              Summary table rendered at the beginning of Executive Summary.
              Typical labels: Partner, Customer, Effective Date, GCP Deployment Location,
              Service Delivery, Pricing Model.
            - technology_stack: list of {"service": "BigQuery", "purpose": "Centralized data warehouse organized in Raw, Trusted, and Refined layers."}
              Rendered as a table inside Architecture Overview after components list.
              Map ONLY Google Cloud services to their specific role in the architecture.
              Do NOT include programming languages, IaC tools, or generic entries like "Google Cloud".
            - milestones: list of {"name": "Milestone 1: Kickoff", "deliverables": "Project Plan", "estimated_completion": "Week 2", "payment": "30%"}
              (omit if single payment at project completion)
            - risks: list of {"description": "Data quality issues...", "mitigation": "Implement validation..."}
              (optional — if provided, content will be added to Assumptions section)
            - consumption_plan: dict (optional — required for PSF, omit for DAF
              unless requested). Monthly GCP consumption estimates for 12 months.
              Structure:
              {
                "services": ["Cloud Run", "Vertex AI", "Firestore"],
                "rows": [
                  {"month": 1, "costs": ["$20", "$150", "$10"], "total": "$180"},
                  {"month": 2, "costs": ["$20", "$150", "$10"], "total": "$180"},
                  ...
                ],
                "notes": "Estimates based on 1,200 monthly requests."
              }
              The "services" list defines table column headers (GCP services only).
              Each "rows" entry must have the same number of "costs" as "services".
              The "notes" field provides estimation assumptions rendered below the table.

            Optional simple fields:
            - taxes_included (boolean — default true. Controls which cost table and
              tax paragraph variant is rendered.)
            - non_commit_psf (boolean — default false. If true, includes the Non-Commit
              PSF 30% reduction paragraph.)

    Returns:
        A dictionary with status and the file path of the generated document.
    """
    try:
        data = json.loads(sow_data)
    except json.JSONDecodeError as e:
        return ToolError(
            status="error",
            error=f"Dados inválidos (JSON inválido): {e}",
            retryable=False,
            tool="generate_sow_document",
            suggestion="Verifique a formatação JSON e tente novamente.",
        )

    _apply_defaults(data)
    _auto_derive_fields(data)

    required_fields = [
        "partner_name",
        "customer_name",
        "project_title",
        "executive_summary",
        "functional_requirements",
        "non_functional_requirements",
        "architecture_components",
        "architecture_integrations",
        "activity_phases",
        "deliverables",
        "timeline",
        "partner_roles",
        "customer_roles",
    ]
    missing = [f for f in required_fields if not data.get(f)]
    if missing:
        logger.error("missing_required_fields", fields=missing)
        return ToolError(
            status="error",
            error=f"Campos obrigatórios ausentes no JSON: {', '.join(missing)}",
            retryable=False,
            tool="generate_sow_document",
            suggestion="Preencha todos os campos obrigatórios antes de gerar o documento.",
        )

    quality_errors = validate_quality_gates(data)
    if quality_errors:
        logger.error("quality_gates_failed", errors=quality_errors)
        return ToolError(
            status="error",
            error=(
                "O conteúdo não atinge os mínimos de qualidade. "
                "Corrija e chame a tool novamente:\n"
                + "\n".join(f"- {e}" for e in quality_errors)
            ),
            retryable=True,
            tool="generate_sow_document",
            suggestion="Gere mais conteúdo para atingir os mínimos de qualidade.",
        )

    # Structural validation (hard gate — blocks on errors, warns on warnings)
    validation = _content_validator.validate(data)
    if not validation.passed:
        logger.error(
            "structural_validation_failed",
            errors=len(validation.errors),
            warnings=len(validation.warnings),
        )
        return ToolError(
            status="error",
            error=(
                "Validação estrutural falhou. Corrija os erros abaixo:\n"
                + "\n".join(f"- {e}" for e in validation.errors)
            ),
            retryable=True,
            tool="generate_sow_document",
            suggestion=(
                "Use validate_sow_content para verificar o conteúdo antes "
                "de gerar o documento."
            ),
        )
    if validation.warnings:
        logger.warning(
            "structural_validation_warnings",
            count=len(validation.warnings),
            warnings=[str(w) for w in validation.warnings],
        )

    template_path = _TEMPLATE_DIR / _TEMPLATE_FILENAME
    if not template_path.exists():
        return ToolError(
            status="error",
            error=f"Template SOW não encontrado em: {template_path}",
            retryable=False,
            tool="generate_sow_document",
        )

    customer_logo_tempfile: Path | None = None
    diagram_tempfile: Path | None = None

    try:
        doc = DocxTemplate(str(template_path))

        partner_logo_path = _TEMPLATE_DIR / _PARTNER_LOGO_FILENAME
        data["partner_logo"] = load_logo(
            doc, partner_logo_path, "partner", _PARTNER_LOGO_WIDTH_MM
        )

        customer_logo_filename = data.get("customer_logo_filename")
        customer_logo_tempfile = await _load_artifact_to_tempfile(
            tool_context, customer_logo_filename, "customer logo"
        )
        if customer_logo_tempfile:
            data["customer_logo"] = load_logo(
                doc,
                customer_logo_tempfile,
                "customer",
                _CUSTOMER_LOGO_WIDTH_MM,
            )
        else:
            data["customer_logo"] = "[Customer Logo]"

        diagram_filename = (
            tool_context.state.get("architecture_diagram_artifact")
            if tool_context
            else None
        )
        diagram_tempfile = await _load_artifact_to_tempfile(
            tool_context, diagram_filename, "diagram"
        )
        if diagram_tempfile:
            data["architecture_diagram"] = InlineImage(
                doc, str(diagram_tempfile), width=Mm(150)
            )
        elif not data.get("architecture_diagram"):
            data["architecture_diagram"] = "[Architecture Diagram — to be generated]"

        doc.render(data, autoescape=True)

        output_dir = Path(tempfile.gettempdir()) / "sow_documents"
        output_dir.mkdir(parents=True, exist_ok=True)

        raw_title = data.get("project_title", "SOW")
        safe_title = re.sub(r"[^a-zA-Z0-9]+", "_", raw_title).strip("_")[:20]
        timestamp = str(int(time.time()))[-6:]
        artifact_filename = f"SOW_{safe_title}_{timestamp}.docx"
        output_path = str(output_dir / artifact_filename)

        doc.save(output_path)

        if tool_context:
            tool_context.state[_DOCUMENT_PATH_KEY] = output_path
            try:
                with open(output_path, "rb") as f:
                    docx_bytes = f.read()

                artifact = genai_types.Part.from_bytes(
                    data=docx_bytes,
                    mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
                version = await tool_context.save_artifact(artifact_filename, artifact)
                logger.info(
                    "artifact_saved",
                    filename=artifact_filename,
                    version=version,
                )
            except Exception as artifact_err:
                logger.error(
                    "artifact_save_failed",
                    error=str(artifact_err),
                    error_type=type(artifact_err).__name__,
                )

        logger.info("document_generated", path=output_path)

        return ToolSuccess(
            status="success",
            data={
                "message": (
                    "O documento SOW foi gerado com sucesso e está disponível "
                    "para download como artefato."
                ),
                "document_path": output_path,
                "artifact_filename": artifact_filename,
            },
        )

    finally:
        for tmp, label in [
            (customer_logo_tempfile, "customer logo"),
            (diagram_tempfile, "diagram"),
        ]:
            if tmp and tmp.exists():
                try:
                    tmp.unlink()
                except Exception as cleanup_err:
                    logger.warning(
                        "cleanup_failed",
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
                "artifact_empty",
                label=label,
                filename=artifact_filename,
            )
            return None

        ext = Path(artifact_filename).suffix or ".png"
        fd, tempfile_path = tempfile.mkstemp(suffix=ext)
        os.close(fd)
        tmp = Path(tempfile_path)
        tmp.write_bytes(part.inline_data.data)
        logger.info(
            "artifact_loaded",
            label=label,
            filename=artifact_filename,
            size=len(part.inline_data.data),
        )
        return tmp

    except Exception as err:
        logger.warning(
            "artifact_load_failed",
            label=label,
            filename=artifact_filename,
            error=str(err),
        )
        return None


def _apply_defaults(data: dict) -> None:
    """Apply default values to optional fields."""
    data["organization_term"] = data.get("organization_term", "phases")
    if len(data.get("organization_term", "").split()) > 2:
        logger.warning(
            "invalid_organization_term",
            value=data["organization_term"],
        )
        data["organization_term"] = "phases"

    valid_engagement = {"project", "pilot", "poc", "assessment", "workshop"}
    eng = data.get("engagement_type", "project").lower()
    if eng not in valid_engagement:
        logger.warning("invalid_engagement_type", value=eng)
        data["engagement_type"] = "project"

    data.setdefault("taxes_included", True)
    data.setdefault("non_commit_psf", False)
    data.setdefault("key_engagement_details", [])
    data.setdefault("technology_stack", [])
    data.setdefault("milestones", [])
    data.setdefault("risks", [])
    data.setdefault("architecture_diagram", "")

    cp_raw = data.get("consumption_plan")
    if isinstance(cp_raw, dict) and "rows" in cp_raw and "services" in cp_raw:
        processed_rows = []
        for row in cp_raw.get("rows", []):
            new_row = dict(row)
            if "values" in new_row:
                new_row["costs"] = new_row.pop("values")
            elif "costs" not in new_row:
                new_row["costs"] = []
            processed_rows.append(new_row)
        cp_raw["rows"] = processed_rows
        data["consumption_plan_table"] = cp_raw
        data["consumption_plan"] = ""
    elif isinstance(cp_raw, str) and cp_raw.strip():
        data["consumption_plan_table"] = None
        data["consumption_plan"] = cp_raw
    else:
        data["consumption_plan_table"] = None
        data["consumption_plan"] = ""


def _auto_derive_fields(data: dict) -> None:
    """Auto-derive fields that can be inferred from other fields."""
    if not data.get("activities") and data.get("activity_phases"):
        data["activities"] = [
            phase.get("name", "") for phase in data["activity_phases"]
        ]

    if not data.get("funding_type_short") and data.get("funding_type"):
        ft = data["funding_type"].upper()
        if "PSF" in ft or "PARTNER" in ft:
            data["funding_type_short"] = "PSF"
        elif "DAF" in ft or "ACCELERATION" in ft:
            data["funding_type_short"] = "DAF"
        else:
            data["funding_type_short"] = "DAF"

    if not data.get("technology_stack") and data.get("architecture_components"):
        data["technology_stack"] = [
            {"service": comp.get("name", ""), "purpose": comp.get("role", "")}
            for comp in data["architecture_components"]
        ]

    if not data.get("project_type"):
        data["project_type"] = _infer_project_type(data)

    if not data.get("key_engagement_details"):
        data["key_engagement_details"] = [
            {
                "label": "Partner",
                "value": data.get("partner_name", "[Partner]"),
            },
            {
                "label": "Customer",
                "value": data.get("customer_name", "[Customer]"),
            },
            {
                "label": "Effective Date",
                "value": data.get("project_start_date", "TBD"),
            },
            {"label": "Service Delivery", "value": "Remote"},
            {"label": "Pricing Model", "value": "Fixed Fee"},
        ]


# GenAI/ML service names used to infer project_type for template conditionals.
_GENAI_SERVICES = {
    "vertex ai",
    "gemini",
    "agent engine",
    "dialogflow",
    "vertex ai search",
    "generative ai",
    "genai",
}
_ML_SERVICES = {
    "automl",
    "vertex ai",
    "bigquery ml",
    "tensorflow",
    "pytorch",
}


def _infer_project_type(data: dict) -> str:
    """Infer project_type ('genai', 'ml', or 'standard') from architecture.

    The SOW template uses project_type to conditionally include ML/GenAI
    assumptions (e.g., labeled data, model performance review).
    """
    # Collect all service/component names mentioned in the architecture
    names: set[str] = set()
    for comp in data.get("architecture_components", []):
        names.add(comp.get("name", "").lower())
        names.add(comp.get("role", "").lower())
    for tech in data.get("technology_stack", []):
        names.add(tech.get("service", "").lower())
        names.add(tech.get("purpose", "").lower())

    arch_desc = (data.get("architecture_description") or "").lower()
    exec_summary = (data.get("executive_summary") or "").lower()
    combined_text = " ".join(names) + " " + arch_desc + " " + exec_summary

    # GenAI takes precedence over ML (GenAI projects typically also involve ML)
    if any(svc in combined_text for svc in _GENAI_SERVICES):
        return "genai"
    if any(svc in combined_text for svc in _ML_SERVICES):
        return "ml"
    return "standard"
