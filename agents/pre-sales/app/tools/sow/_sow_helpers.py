from __future__ import annotations

import hashlib
import json as _json
from pathlib import Path
from typing import Any

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


def apply_sow_assembly_to_state(
    state: dict[str, Any], stage: str,
) -> dict[str, Any]:
    """Re-assemble and stage the SOW from in-state bundles.

    For sub-agents that run OUTSIDE the ADK tool surface (the
    QualityLoopAgent in particular) and therefore have no ToolContext
    available. The function combines what ``assemble_sow_payload`` +
    ``stage_sow`` would do back-to-back, minus everything the loop must
    NOT touch between repair rounds.

    Side effects (deliberately narrow):

    - Overwrites ``state[STATE_SOW]`` with the freshly built ``sow_data``
      dict produced by :func:`build_sow_data_from_state`.
    - Writes ``state[STATE_STAGE] = stage`` (caller passes the current
      stage). This helper assumes **same-stage repair** — the loop
      reassembles to materialise patches applied by section agents, not
      to transition between content and full. It therefore does NOT
      reset ``STATE_ROUND_COUNT`` or
      ``STATE_PRIOR_BLOCKING_FINGERPRINTS`` even when ``stage`` differs
      from what is already in state (which a misbehaving caller would
      have to do explicitly — and that path is rejected today by
      :func:`stage_sow`'s strict argument).

    Guardrails (deliberately preserved):

    - Never reads or writes ``app:language`` — the orchestrator owns it,
      and the production incident where the reviser overwrote
      ``pt-BR -> en`` cannot be replayed through this surface.
    - Never reads or writes the revision log — it accumulates across
      rounds and the loop drives every entry via the reviser's own tool.
    - Never re-emits the F-05 anti-thrashing hash; the QualityLoopAgent
      already maintains its own terminal-hash cache via ``_emit_result``.

    Returns the assembled ``sow_data`` (also written to state) so the
    caller can hash it, log it, or pass it to downstream logic without
    a second state read. Raises :class:`AssemblyError` on precondition
    failure (missing bundles, MISSING_INPUT sentinel, non-dict manifest,
    blank required metadata) — exactly the same errors
    :func:`assemble_sow_payload` would have wrapped in ToolError.
    """
    # Local import avoids a module-load cycle with the validation
    # schema module: ``assemble_payload`` already imports from
    # ``sub_agents.schemas``; this helper sits one layer below tools and
    # should not pull the schema import chain into every consumer.
    from .assemble_payload import build_sow_data_from_state
    # ``STATE_SOW`` and ``STATE_STAGE`` live in the validation schema
    # module — the canonical source of state-key literals.
    from ...sub_agents.validation.schema import STATE_SOW, STATE_STAGE

    stage_normalized = (stage or '').strip().lower()
    sow_data = build_sow_data_from_state(state, stage_normalized)

    state[STATE_SOW] = sow_data
    state[STATE_STAGE] = stage_normalized

    logger.info(
        'sow_assembly_applied_to_state',
        stage=stage_normalized,
        sow_data_hash=sow_data_hash(sow_data),
        top_level_keys=len(sow_data),
    )
    return sow_data


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
