import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

import structlog
from google.genai import types as genai_types

from ...shared.errors import safe_tool
from ...shared.types import ToolError, ToolSuccess
from ._diagram_audit import audit_architecture
from ._diagram_models import (
    ArchitectureEdge,
    ArchitectureNode,
    ClusterZone,
    ensure_edge,
    ensure_node,
    get_d2_icon_path,
    get_d2_shape,
)

# F-04 — deterministic retry budget for the structural audit.
#
# ``audit-rules.md`` instructs the LLM to retry "up to 3 consecutive
# attempts" after a BLOCKER audit failure. Relying on prompt discipline
# alone lets a confused worker burn the LLM round budget on the same
# defective spec. The tool tracks consecutive failed attempts on the
# same input in session state and refuses from the 4th attempt with a
# fallback ToolError instructing the worker to proceed without a
# diagram (the bundle schema doesn't carry diagram bytes — the docx
# template renders a placeholder).
_AUDIT_FAILURE_BUDGET = 3
_STATE_DIAGRAM_INPUT_HASH = 'app:arch_diagram:last_input_hash'
_STATE_DIAGRAM_AUDIT_FAILURES = 'app:arch_diagram:consecutive_audit_failures'


def _diagram_input_hash(
    title: str,
    nodes: list[Any],
    edges: list[Any],
    architecture_description: str,
    technology_stack: list[dict],
    direction: str,
) -> str:
    """Stable hash of the tool's audit-relevant input.

    The hash keys the consecutive-failure counter so a worker that
    materially changes any of these fields (responding to the audit
    feedback) gets a fresh budget. A worker that retries with the same
    spec — what the budget exists to stop — sees the counter increment.

    Only the audit-relevant arguments are hashed; ``direction`` is
    included because layout-only changes still count as a retry (they
    do not address structural defects). Node / edge objects can be
    Pydantic models or dicts; ``model_dump``/``dict()`` is normalized
    via ``default=str`` to avoid serialization explosions on enums.
    """
    payload = {
        'title': title,
        'direction': direction,
        'nodes': [
            n.model_dump(mode='json') if hasattr(n, 'model_dump') else n
            for n in nodes
        ],
        'edges': [
            e.model_dump(mode='json') if hasattr(e, 'model_dump') else e
            for e in edges
        ],
        'description': architecture_description,
        'technology_stack': technology_stack,
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode('utf-8')).hexdigest()[:16]

logger = structlog.get_logger()

_D2_AVAILABLE = shutil.which('d2') is not None
if not _D2_AVAILABLE:
    logger.warning(
        'd2_not_found',
        message='D2 not found in PATH — diagram generation will be skipped. '
        'Install D2 to enable.',
    )

_RSVG_AVAILABLE = shutil.which('rsvg-convert') is not None
if not _RSVG_AVAILABLE:
    logger.warning(
        'rsvg_convert_not_found',
        message='rsvg-convert not found in PATH — PNG conversion will be skipped. '
        'Install librsvg2-bin to enable.',
    )

_DIAGRAM_ARTIFACT_KEY = 'architecture_diagram_artifact'

_NEEDS_QUOTING = re.compile(r'[\s.()/#"\'{}:;\\]')

_SAFE_ID_RE = re.compile(r'[^a-zA-Z0-9_]')

_ZONE_D2_KEY: dict[ClusterZone, str] = {
    ClusterZone.USER_CONSUMER: 'user_consumer',
    ClusterZone.CUSTOMER_ENVIRONMENT: 'customer_env',
    ClusterZone.GOOGLE_CLOUD: 'gcp',
    ClusterZone.THIRD_PARTY: 'third_party',
}

_ZONE_STYLE: dict[ClusterZone, dict[str, Any]] = {
    ClusterZone.USER_CONSUMER: {
        'label': 'User / Consumer',
        'fill': '#F1F3F4',
        'stroke': '#9AA0A6',
        'font_color': '#3C4043',
        'border_radius': 8,
    },
    ClusterZone.CUSTOMER_ENVIRONMENT: {
        'label': 'Customer Environment',
        'fill': '#E8EAED',
        'stroke': '#5F6368',  
        'font_color': '#3C4043',
        'border_radius': 8,
    },
    ClusterZone.GOOGLE_CLOUD: {
        'label': 'Google Cloud Platform',
        'fill': '#E8F0FE',
        'stroke': '#4285F4',
        'font_color': '#1967D2',
        'border_radius': 12,
    },
    ClusterZone.THIRD_PARTY: {
        'label': 'Third-Party Services',
        'fill': '#E6F4EA',
        'stroke': '#34A853',
        'font_color': '#137333',
        'border_radius': 8,
    },
}

_SUB_CLUSTER_STYLE: dict[ClusterZone, dict[str, Any]] = {
    ClusterZone.GOOGLE_CLOUD: {
        'fill': '#F0F7FE',
        'stroke': '#90CAF9',
        'font_color': '#1565C0',
        'border_radius': 8,
    },
}

_ZONE_RENDER_ORDER: list[ClusterZone] = [
    ClusterZone.USER_CONSUMER,
    ClusterZone.CUSTOMER_ENVIRONMENT,
    ClusterZone.GOOGLE_CLOUD,
    ClusterZone.THIRD_PARTY,
]

# Threshold for rendering a node with style.multiple (stacked-card visual).
# When a node has at least this many connections (in-degree + out-degree),
# it is treated as a "hub" in the architecture — typically an orchestrator,
# API gateway, or high-fan-out service — and rendered as stacked cards to
# visually communicate its centrality. Tuned at 3 because 1-2 connections
# describe a normal participant; 3+ signals a hub role.
_MULTIPLE_CONNECTION_THRESHOLD: int = 3

# Neutral node palette applied uniformly to every leaf node. Without an
# explicit override, D2 defaults to a blue theme that visually clashes
# with the gray zones (Customer Environment, User/Consumer) and the green
# zone (Third-Party Services). A consistent neutral frame lets the zone
# color carry the categorization signal while the node stays discrete.
# Colors come from Google's UI palette (Grey 800) for consistency with
# the font-color used in the User/Consumer and Customer Environment zones.
_NODE_STROKE: str = '#3C4043'
_NODE_FONT_COLOR: str = '#3C4043'


def _escape_d2(text: str) -> str:
    """Escape a string for use inside a D2 quoted label."""
    return text.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')


def _d2_key(name: str) -> str:
    """Return a D2-safe key reference. Quote only when necessary."""
    if _NEEDS_QUOTING.search(name):
        return f'"{_escape_d2(name)}"'
    return name


def _normalize_sub(sub: Optional[str]) -> Optional[str]:
    """Normalize sub_cluster: strip whitespace, treat empty as None."""
    if sub is None:
        return None
    stripped = sub.strip()
    return stripped or None


def _build_safe_id_map(
    nodes: list[ArchitectureNode],
) -> list[str]:
    """Compute D2-safe identifiers aligned with ``nodes`` by position.

    D2 keys used in path syntax (``zone.node``, ``a -> b``) cannot contain
    spaces, dots, or punctuation without breaking the parser. The Pydantic
    contract documents "no spaces" but does not enforce it, so the LLM
    occasionally emits ids like ``api.gateway`` or ``Cloud Run 1`` which
    compile to invalid D2 source and surface as a ``d2_render_failed``
    error cascade.

    Sanitization rule (deterministic, input-order stable):
    - Replace any character outside ``[a-zA-Z0-9_]`` with ``_``.
    - Fall back to ``n_<index>`` when the result has no alphanumeric
      character (e.g. raw id of ``'...'`` or ``''``).
    - Prefix with ``n_`` when starting with a digit.
    - Disambiguate collisions by appending ``_2``, ``_3``, ...

    Returns ``list[str]`` aligned with ``nodes`` by position. Edges still
    resolve through ``node_d2_path`` keyed on the original ``node.id`` —
    this layer only fixes how the id appears in emitted D2 source.
    """
    safe_ids: list[str] = []
    used: set[str] = set()
    for idx, node in enumerate(nodes):
        candidate = _SAFE_ID_RE.sub('_', node.id)
        if not any(c.isalnum() for c in candidate):
            candidate = f'n_{idx}'
        elif candidate[0].isdigit():
            candidate = f'n_{candidate}'
        base = candidate
        suffix = 2
        while candidate in used:
            candidate = f'{base}_{suffix}'
            suffix += 1
        used.add(candidate)
        safe_ids.append(candidate)
    return safe_ids


def _compute_hub_node_ids(
    nodes: list[ArchitectureNode],
    edges: list[ArchitectureEdge],
) -> set[str]:
    """Identify nodes that act as architectural hubs.

    A node qualifies as a hub when the total count of edges referencing it
    (both incoming and outgoing) meets or exceeds
    ``_MULTIPLE_CONNECTION_THRESHOLD``. Hubs are rendered with
    ``style.multiple: true`` so they appear as stacked cards in the diagram,
    signaling that they orchestrate or serve multiple other components.

    Only endpoints that correspond to actual nodes are counted — orphan
    references (already logged as warnings in ``_build_d2_code``) do not
    inflate the degree count.
    """
    node_ids = {n.id for n in nodes}
    degree: dict[str, int] = {nid: 0 for nid in node_ids}
    for edge in edges:
        if edge.source_id in degree:
            degree[edge.source_id] += 1
        if edge.target_id in degree:
            degree[edge.target_id] += 1
    return {
        nid
        for nid, count in degree.items()
        if count >= _MULTIPLE_CONNECTION_THRESHOLD
    }


def _render_d2_node(
    node: ArchitectureNode,
    indent: str = '',
    is_hub: bool = False,
    safe_id: Optional[str] = None,
) -> list[str]:
    """Generate D2 code lines for a single node.

    Rendering strategy:
    - Nodes with an explicit shape override (e.g. ``person`` for CLIENT/USERS)
      keep the shape declaration. Dimensions are left to D2 so the shape grows
      to fit the label inside its bounding box.
    - Nodes with an icon get an ``icon:`` field only, keeping the default
      rectangle shape. This makes the icon render inside the box alongside
      the label, so the label contributes to the node's bounding box and the
      ELK engine can balance the layout symmetrically.
    - Nodes with neither icon nor shape fall back to D2's default rectangle
      with the label centered inside.
    - When ``is_hub`` is True, ``style.multiple: true`` is added so the node
      renders as stacked cards — a visual cue that the component is a hub
      with many connections (orchestrator, gateway, central API, etc).
    - Every leaf node receives a neutral stroke/font color (``#3C4043``).
      Without this override, D2 falls back to its default blue theme palette,
      which clashes with the gray zones (Customer Environment, User/Consumer)
      and the green zone (Third-Party Services). A uniform neutral frame lets
      the zone color carry the categorization signal while the node itself
      stays visually discrete — the icon and label are the primary content.

    Historical note: previous versions emitted ``shape: image`` with fixed
    ``width: 80`` / ``height: 80``. That made labels float outside the
    bounding box, which confused ELK's container sizing and produced
    asymmetric whitespace on the edge-heavy side of the diagram.
    """
    lines = []
    label = _escape_d2(node.label)
    key = safe_id if safe_id is not None else node.id
    lines.append(f'{indent}{key}: "{label}" {{')

    icon_path = get_d2_icon_path(node.service)
    shape = get_d2_shape(node.service)

    if shape:
        lines.append(f'{indent}  shape: {shape}')
    elif icon_path:
        lines.append(f'{indent}  icon: {icon_path}')

    lines.append(f'{indent}  style.stroke: "{_NODE_STROKE}"')
    lines.append(f'{indent}  style.font-color: "{_NODE_FONT_COLOR}"')

    if is_hub:
        lines.append(f'{indent}  style.multiple: true')

    lines.append(f'{indent}}}')
    return lines


def _render_title(title: str) -> list[str]:
    """Render the diagram title as a text shape pinned to the top-center."""
    escaped = _escape_d2(title)
    return [
        f'_title: "{escaped}" {{',
        '  shape: text',
        '  near: top-center',
        '  style.font-size: 24',
        '  style.bold: true',
        '}',
        '',
    ]


def _render_zone_header(zone: ClusterZone) -> list[str]:
    """Emit the opening brace and style block for a zone container."""
    style = _ZONE_STYLE[zone]
    zone_key = _ZONE_D2_KEY[zone]
    lines = [f'{zone_key}: "{_escape_d2(style["label"])}" {{']
    lines.append(f'  style.fill: "{style["fill"]}"')
    lines.append(f'  style.stroke: "{style["stroke"]}"')
    lines.append(f'  style.font-color: "{style["font_color"]}"')
    lines.append(f'  style.border-radius: {style["border_radius"]}')
    lines.append('')
    return lines


def _render_sub_cluster_header(
    zone: ClusterZone, sub_name: str
) -> list[str]:
    """Emit the opening brace and style block for a sub-cluster container.

    Sub-clusters are nested one level inside their parent zone — use 2-space
    indent. If the zone has no custom sub-style, the sub-cluster inherits
    the parent's fill/stroke, signaling the grouping through the label alone.
    """
    lines = [f'  {_d2_key(sub_name)}: "{_escape_d2(sub_name)}" {{']
    sub_style = _SUB_CLUSTER_STYLE.get(zone)
    if sub_style:
        lines.append(f'    style.fill: "{sub_style["fill"]}"')
        lines.append(f'    style.stroke: "{sub_style["stroke"]}"')
        lines.append(f'    style.font-color: "{sub_style["font_color"]}"')
        lines.append(f'    style.border-radius: {sub_style["border_radius"]}')
        lines.append('')
    return lines


def _build_d2_code(
    nodes: list[ArchitectureNode],
    edges: list[ArchitectureEdge],
    direction: str,
    title: str,
) -> str:
    """Build D2 source from a list of nodes/edges using explicit cluster zones.

    Nodes are grouped by ``parent_cluster`` (enum) and ``sub_cluster`` (free
    string). Four top-level zones are always candidates; a zone is rendered
    only when it has at least one node. Sub-clusters are rendered only when
    at least one node in that zone declares one.
    """
    lines: list[str] = []

    lines.append('vars: { d2-config: { layout-engine: elk } }')
    lines.append('')

    dir_map = {'LR': 'right', 'TB': 'down'}
    lines.append(f'direction: {dir_map.get(direction, "right")}')
    lines.append('')

    if title:
        lines.extend(_render_title(title))

    hub_node_ids = _compute_hub_node_ids(nodes, edges)

    safe_ids = _build_safe_id_map(nodes)
    safe_id_by_obj: dict[int, str] = {
        id(n): s for n, s in zip(nodes, safe_ids)
    }

    zones: dict[
        ClusterZone, dict[Optional[str], list[ArchitectureNode]]
    ] = defaultdict(lambda: defaultdict(list))

    for node in nodes:
        sub = _normalize_sub(node.sub_cluster)
        zones[node.parent_cluster][sub].append(node)

    node_d2_path: dict[str, str] = {}

    for zone in _ZONE_RENDER_ORDER:
        if zone not in zones:
            continue

        zone_key = _ZONE_D2_KEY[zone]
        lines.extend(_render_zone_header(zone))

        sub_groups = zones[zone]

        for node in sub_groups.get(None, []):
            safe = safe_id_by_obj[id(node)]
            lines.extend(
                _render_d2_node(
                    node,
                    indent='  ',
                    is_hub=node.id in hub_node_ids,
                    safe_id=safe,
                )
            )
            lines.append('')
            node_d2_path[node.id] = f'{zone_key}.{safe}'

        for sub_name, sub_nodes in sub_groups.items():
            if sub_name is None:
                continue
            sub_key = _d2_key(sub_name)
            lines.extend(_render_sub_cluster_header(zone, sub_name))
            for node in sub_nodes:
                safe = safe_id_by_obj[id(node)]
                lines.extend(
                    _render_d2_node(
                        node,
                        indent='    ',
                        is_hub=node.id in hub_node_ids,
                        safe_id=safe,
                    )
                )
                lines.append('')
                node_d2_path[node.id] = f'{zone_key}.{sub_key}.{safe}'
            lines.append('  }')
            lines.append('')

        lines.append('}')
        lines.append('')

    for edge in edges:
        src_path = node_d2_path.get(edge.source_id)
        tgt_path = node_d2_path.get(edge.target_id)

        if not src_path or not tgt_path:
            logger.warning(
                'edge_skipped',
                source=edge.source_id,
                target=edge.target_id,
                reason='unknown node id',
            )
            continue

        if edge.label:
            label = _escape_d2(edge.label)
            lines.append(f'{src_path} -> {tgt_path}: "{label}" {{')
            lines.append('  style.bold: true')
            lines.append(f'  style.font-color: "{_NODE_FONT_COLOR}"')
            lines.append('}')
        else:
            lines.append(f'{src_path} -> {tgt_path}')

    return '\n'.join(lines)


@safe_tool
async def generate_architecture_diagram(
    title: str,
    nodes: list[ArchitectureNode],
    edges: list[ArchitectureEdge],
    architecture_description: str,
    technology_stack: list[dict],
    direction: str = 'LR',
    tool_context=None,
) -> dict[str, Any]:
    """Generates a GCP architecture diagram as a PNG image artifact.

    Before rendering, the tool runs a deterministic structural audit
    against the spec (see architecture-guide.md Part 7). If BLOCKER
    defects are detected, returns a ToolError — silently revise the
    offending artifact and retry.

    Args:
        title: Title displayed at the top of the diagram.
        nodes: List of architecture components. Each node MUST declare
            ``parent_cluster`` (one of the four ClusterZone values) and
            MAY declare ``sub_cluster`` for intra-zone grouping.
        edges: List of connections between components.
        architecture_description: Text from sub-step (1b). Used by the
            audit to cross-check against the Technology Stack table and
            the diagram spec.
        technology_stack: Technology Stack table from sub-step (1c), as
            list of {"service": str, "purpose": str}. Every row must
            correspond to a GCP node in the diagram.
        direction: Diagram layout direction — "LR" (left-to-right) or
            "TB" (top-to-bottom).

    Returns:
        Dict with status, message, and the artifact filename of the
        generated image.
    """
    if not _D2_AVAILABLE:
        logger.warning('diagram_skipped', reason='D2 not found in PATH')
        return ToolError(
            status='error',
            error='Geração de diagrama ignorada (D2 não encontrado no PATH). '
            'O documento será gerado com placeholder no lugar do diagrama. '
            'No Agent Engine o D2 estará disponível via startup_scripts.',
            retryable=False,
            tool='generate_architecture_diagram',
            suggestion='Deploy no Agent Engine para ter D2 disponível.',
        )

    if not _RSVG_AVAILABLE:
        logger.warning('diagram_skipped', reason='rsvg-convert not available')
        return ToolError(
            status='error',
            error='Geração de diagrama ignorada (rsvg-convert não disponível). '
            'O documento será gerado com placeholder no lugar do diagrama. '
            'No Agent Engine o rsvg-convert estará disponível via startup_scripts '
            '(pacote librsvg2-bin).',
            retryable=False,
            tool='generate_architecture_diagram',
            suggestion='Deploy no Agent Engine para ter rsvg-convert disponível.',
        )

    try:
        nodes = [ensure_node(n) for n in nodes]
    except Exception as parse_err:
        logger.error(
            'node_parse_failed',
            error=str(parse_err),
            error_type=type(parse_err).__name__,
        )
        return ToolError(
            status='error',
            error=f'Falha ao interpretar os nós do diagrama: {parse_err}',
            retryable=False,
            tool='generate_architecture_diagram',
            suggestion='Verifique se todos os nós possuem id, label, service e parent_cluster válidos.',
        )

    try:
        edges = [ensure_edge(e) for e in edges]
    except Exception as parse_err:
        logger.error(
            'edge_parse_failed',
            error=str(parse_err),
            error_type=type(parse_err).__name__,
        )
        return ToolError(
            status='error',
            error=f'Falha ao interpretar as conexões do diagrama: {parse_err}',
            retryable=False,
            tool='generate_architecture_diagram',
            suggestion='Verifique se source_id e target_id correspondem a nós existentes.',
        )

    audit = audit_architecture(
        nodes=nodes,
        edges=edges,
        description=architecture_description,
        technology_stack=technology_stack,
    )

    if audit.warnings:
        logger.warning(
            'architecture_audit_warnings',
            count=len(audit.warnings),
            warnings=[
                f'{w.check_id}: {w.defect}' for w in audit.warnings
            ],
        )

    # ----- F-04 retry budget bookkeeping (audit branch) ----------------
    # The counter is keyed on the input hash so a worker that genuinely
    # changes the spec in response to the audit feedback gets a fresh
    # budget. A worker that calls again with the same arguments — the
    # thrashing case the budget exists to stop — sees the counter
    # increment. We only have a useful counter when ``tool_context`` is
    # present (state is the only durable channel); a missing context
    # falls back to the legacy behaviour (no budget).
    current_input_hash: Optional[str] = None
    consecutive_failures: int = 0
    if tool_context is not None:
        current_input_hash = _diagram_input_hash(
            title=title,
            nodes=nodes,
            edges=edges,
            architecture_description=architecture_description,
            technology_stack=technology_stack,
            direction=direction,
        )
        last_hash = tool_context.state.get(_STATE_DIAGRAM_INPUT_HASH)
        # Input changed → reset budget. Same input → carry the counter so
        # we can detect a repeated thrash.
        if last_hash == current_input_hash:
            consecutive_failures = int(
                tool_context.state.get(_STATE_DIAGRAM_AUDIT_FAILURES) or 0
            )
        else:
            consecutive_failures = 0
    # -------------------------------------------------------------------

    if not audit.passed:
        logger.error(
            'architecture_audit_failed',
            blocker_count=len(audit.blockers),
            warning_count=len(audit.warnings),
            blockers=[
                f'{b.check_id}: {b.defect}' for b in audit.blockers
            ],
        )

        # F-04: refuse from the 4th consecutive failure on the same input
        # so the worker stops burning Gemini turns on a spec it cannot
        # fix. ``audit-rules.md`` documents the fallback path: continue
        # with the textual sections only; the docx renders a placeholder.
        if (
            tool_context is not None
            and consecutive_failures >= _AUDIT_FAILURE_BUDGET
        ):
            logger.error(
                'architecture_audit_budget_exhausted',
                consecutive_failures=consecutive_failures + 1,
                budget=_AUDIT_FAILURE_BUDGET,
                input_hash=current_input_hash,
            )
            tool_context.state[_STATE_DIAGRAM_AUDIT_FAILURES] = (
                consecutive_failures + 1
            )
            tool_context.state[_STATE_DIAGRAM_INPUT_HASH] = current_input_hash
            return ToolError(
                status='error',
                error=(
                    'Audit retry budget exhausted '
                    f'({_AUDIT_FAILURE_BUDGET} consecutive failures on '
                    'the same diagram spec). Do NOT call this tool again '
                    'with the same arguments — proceed without the diagram '
                    'and emit the architecture bundle with the textual '
                    'sections only.\n\n'
                    'Remaining defects (for reference):\n'
                    + audit.format_defects()
                ),
                retryable=False,
                tool='generate_architecture_diagram',
                suggestion=(
                    'Per audit-rules.md the document renders a placeholder '
                    'when the diagram cannot be produced. Emit the bundle '
                    'now; downstream review can ask the user how to proceed.'
                ),
            )

        if tool_context is not None:
            tool_context.state[_STATE_DIAGRAM_AUDIT_FAILURES] = (
                consecutive_failures + 1
            )
            tool_context.state[_STATE_DIAGRAM_INPUT_HASH] = current_input_hash

        return ToolError(
            status='error',
            error=(
                'A arquitetura proposta possui defeitos estruturais que '
                'impedem a geração do diagrama. Corrija os pontos abaixo '
                'e chame a tool novamente:\n\n'
                + audit.format_defects()
            ),
            retryable=True,
            tool='generate_architecture_diagram',
            suggestion=(
                'Revise silenciosamente a descrição, a Technology Stack '
                'e/ou a spec do diagrama conforme os defeitos listados. '
                'Não mencione a auditoria, as falhas ou a nova tentativa '
                'ao usuário.'
            ),
        )

    d2_code = _build_d2_code(nodes, edges, direction, title)

    output_dir = Path(tempfile.gettempdir()) / 'sow_diagrams'
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_title = title.replace(' ', '_')[:40]

    d2_file = output_dir / f'{safe_title}.d2'
    svg_path = output_dir / f'{safe_title}.svg'
    png_path = output_dir / f'{safe_title}.png'

    d2_file.write_text(d2_code, encoding='utf-8')

    try:
        try:
            result = subprocess.run(
                ['d2', '--bundle', str(d2_file), str(svg_path)],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            logger.error('d2_render_timeout', timeout_seconds=60)
            return ToolError(
                status='error',
                error='Renderização D2 excedeu o tempo limite de 60 segundos.',
                retryable=True,
                tool='generate_architecture_diagram',
                suggestion='Reduza a complexidade do diagrama ou tente novamente.',
            )

        if result.returncode != 0:
            logger.error(
                'd2_render_failed',
                stderr=result.stderr,
                returncode=result.returncode,
            )
            return ToolError(
                status='error',
                error=f'Falha na renderização D2: {result.stderr[:500]}',
                retryable=False,
                tool='generate_architecture_diagram',
                suggestion='Verifique o código D2 gerado para erros de sintaxe.',
            )

        if not svg_path.exists():
            logger.error('svg_not_found', path=str(svg_path))
            return ToolError(
                status='error',
                error='D2 executou com sucesso mas o arquivo SVG não foi encontrado.',
                retryable=True,
                tool='generate_architecture_diagram',
            )

        try:
            convert_result = subprocess.run(
                [
                    'rsvg-convert',
                    '-w',
                    '1600',
                    '-f',
                    'png',
                    '-o',
                    str(png_path),
                    str(svg_path),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            logger.error('rsvg_convert_timeout', timeout_seconds=30)
            return ToolError(
                status='error',
                error='Conversão SVG→PNG excedeu o tempo limite de 30 segundos.',
                retryable=True,
                tool='generate_architecture_diagram',
                suggestion='Reduza a complexidade do diagrama ou tente novamente.',
            )

        if convert_result.returncode != 0:
            logger.error(
                'rsvg_convert_failed',
                stderr=convert_result.stderr,
                returncode=convert_result.returncode,
            )
            return ToolError(
                status='error',
                error=f'Falha na conversão SVG→PNG: {convert_result.stderr[:500]}',
                retryable=False,
                tool='generate_architecture_diagram',
                suggestion='Verifique se o SVG gerado pelo D2 é válido.',
            )

        if not png_path.exists():
            logger.error('png_not_found', path=str(png_path))
            return ToolError(
                status='error',
                error='rsvg-convert executou com sucesso mas o arquivo PNG não foi encontrado.',
                retryable=True,
                tool='generate_architecture_diagram',
            )

        png_bytes = png_path.read_bytes()

        if not png_bytes:
            logger.error('png_empty', path=str(png_path))
            return ToolError(
                status='error',
                error='rsvg-convert produziu um PNG vazio.',
                retryable=True,
                tool='generate_architecture_diagram',
            )

        artifact_filename = f'architecture_diagram_{safe_title}.png'

        if tool_context:
            artifact = genai_types.Part.from_bytes(
                data=png_bytes,
                mime_type='image/png',
            )
            version = await tool_context.save_artifact(
                filename=artifact_filename,
                artifact=artifact,
            )
            tool_context.state[_DIAGRAM_ARTIFACT_KEY] = artifact_filename
            # F-04: a successful render clears the consecutive-failure
            # counter so a future audit failure starts fresh. The input
            # hash is also written so the next attempt on a DIFFERENT
            # spec resets the budget via the hash-mismatch branch above.
            tool_context.state[_STATE_DIAGRAM_AUDIT_FAILURES] = 0
            if current_input_hash is not None:
                tool_context.state[_STATE_DIAGRAM_INPUT_HASH] = (
                    current_input_hash
                )
            logger.info(
                'artifact_saved',
                filename=artifact_filename,
                version=version,
                nodes=len(nodes),
                edges=len(edges),
                size_bytes=len(png_bytes),
                audit_warnings=len(audit.warnings),
            )
        else:
            logger.warning(
                'artifact_not_persisted',
                reason='tool_context is None',
            )

        return ToolSuccess(
            status='success',
            data={
                'message': f"Diagrama '{title}' gerado com sucesso.",
                'artifact_filename': artifact_filename,
            },
        )

    finally:
        for f in [d2_file, svg_path, png_path]:
            if f.exists():
                try:
                    f.unlink()
                except Exception as cleanup_err:
                    logger.warning(
                        'cleanup_failed',
                        path=str(f),
                        error=str(cleanup_err),
                    )
