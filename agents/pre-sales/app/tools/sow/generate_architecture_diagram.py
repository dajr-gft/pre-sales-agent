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

_NEEDS_QUOTING = re.compile(r'[.()/#"\'{}:;\\]')

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
    lines.append(f'{indent}{node.id}: "{label}" {{')

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
            lines.extend(
                _render_d2_node(
                    node,
                    indent='  ',
                    is_hub=node.id in hub_node_ids,
                )
            )
            lines.append('')
            node_d2_path[node.id] = f'{zone_key}.{node.id}'

        for sub_name, sub_nodes in sub_groups.items():
            if sub_name is None:
                continue
            sub_key = _d2_key(sub_name)
            lines.extend(_render_sub_cluster_header(zone, sub_name))
            for node in sub_nodes:
                lines.extend(
                    _render_d2_node(
                        node,
                        indent='    ',
                        is_hub=node.id in hub_node_ids,
                    )
                )
                lines.append('')
                node_d2_path[node.id] = f'{zone_key}.{sub_key}.{node.id}'
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
            lines.append(f'{src_path} -> {tgt_path}: "{label}"')
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

    if not audit.passed:
        logger.error(
            'architecture_audit_failed',
            blocker_count=len(audit.blockers),
            warning_count=len(audit.warnings),
            blockers=[
                f'{b.check_id}: {b.defect}' for b in audit.blockers
            ],
        )
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
