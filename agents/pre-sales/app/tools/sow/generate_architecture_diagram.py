from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import structlog
from google.genai import types as genai_types

from ...shared.errors import safe_tool
from ...shared.types import ToolError, ToolSuccess
from ._diagram_models import (
    ArchitectureEdge,
    ArchitectureNode,
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

_CLIENT_KEYWORDS = ['user', 'consumer', 'client', 'portal', 'frontend']

_GCP_KEYWORDS = ['google cloud', 'gcp', 'cloud platform']

_GCP_HUB_KEYWORDS = ['compute', 'orchestration', 'edge', 'integration', 'networking']

_GCP_GROUP_KEYWORDS = {
    'ai': ['ai', 'ml', 'machine learning'],
    'data': ['data', 'storage', 'database'],
    'security': ['security', 'identity'],
    'observability': ['observability', 'monitoring', 'logging', 'operations'],
}

_INTERNAL_KEYWORDS = [
    'on-prem', 'on prem', 'internal', 'legacy', 'customer environment',
]
_THIRD_PARTY_KEYWORDS = [
    'third-party', 'third party', 'external', 'saas',
]


def _classify_cluster(cluster_name: str) -> dict:
    """Classify a cluster name into a rendering zone and sub-zone."""
    name_lower = cluster_name.lower()

    if any(kw in name_lower for kw in _CLIENT_KEYWORDS):
        return {'zone': 'clients', 'sub_zone': None, 'original_name': cluster_name}

    if any(kw in name_lower for kw in _GCP_KEYWORDS):
        for group_key, keywords in _GCP_GROUP_KEYWORDS.items():
            if any(kw in name_lower for kw in keywords):
                return {'zone': 'gcp', 'sub_zone': group_key, 'original_name': cluster_name}
        if any(kw in name_lower for kw in _GCP_HUB_KEYWORDS):
            return {'zone': 'gcp', 'sub_zone': 'hub', 'original_name': cluster_name}
        return {'zone': 'gcp', 'sub_zone': 'hub', 'original_name': cluster_name}

    if any(kw in name_lower for kw in _INTERNAL_KEYWORDS):
        return {'zone': 'external', 'sub_zone': 'internal', 'original_name': cluster_name}

    if any(kw in name_lower for kw in _THIRD_PARTY_KEYWORDS):
        return {'zone': 'external', 'sub_zone': 'third_party', 'original_name': cluster_name}

    logger.warning('unrecognized_cluster_name', cluster_name=cluster_name)
    return {'zone': 'external', 'sub_zone': 'third_party', 'original_name': cluster_name}


def _escape_d2(text: str) -> str:
    """Escape a string for use inside a D2 quoted label."""
    return text.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')


def _d2_key(name: str) -> str:
    """Return a D2-safe key reference. Quote only when necessary."""
    if _NEEDS_QUOTING.search(name):
        return f'"{_escape_d2(name)}"'
    return name


def _render_d2_node(node: ArchitectureNode, indent: str = '') -> list[str]:
    """Generate D2 code lines for a single node."""
    lines = []
    label = _escape_d2(node.label)
    lines.append(f'{indent}{node.id}: "{label}" {{')

    icon_path = get_d2_icon_path(node.service)
    shape = get_d2_shape(node.service)

    if shape:
        lines.append(f'{indent}  shape: {shape}')
        lines.append(f'{indent}  width: 80')
        lines.append(f'{indent}  height: 80')
    elif icon_path:
        lines.append(f'{indent}  icon: {icon_path}')
        lines.append(f'{indent}  shape: image')
        lines.append(f'{indent}  width: 80')
        lines.append(f'{indent}  height: 80')

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


def _build_d2_code(
    nodes: list[ArchitectureNode],
    edges: list[ArchitectureEdge],
    direction: str,
    title: str,
) -> str:
    """Build D2 source with 3-zone layout: clients → GCP → external.

    Colors follow the original scheme:
    - Entry point: yellow (#FFF8E1)
    - GCP container: blue (#E3F2FD), groups inside use lighter blue (#F0F7FE)
    - External internal: warm gray (#EFEBE9)
    - External third-party: teal (#E0F2F1)
    """
    lines: list[str] = []

    lines.append('vars: { d2-config: { layout-engine: elk } }')
    lines.append('')

    dir_map = {'LR': 'right', 'TB': 'down'}
    lines.append(f'direction: {dir_map.get(direction, "right")}')
    lines.append('')

    if title:
        lines.extend(_render_title(title))

    node_by_id: dict[str, ArchitectureNode] = {n.id: n for n in nodes}

    client_nodes: list[ArchitectureNode] = []
    gcp_hub_nodes: list[ArchitectureNode] = []
    gcp_groups: dict[str, list[ArchitectureNode]] = {}
    gcp_group_labels: dict[str, str] = {}
    external_internal: dict[str, list[ArchitectureNode]] = {}
    external_third_party: dict[str, list[ArchitectureNode]] = {}

    node_d2_path: dict[str, str] = {}

    for node in nodes:
        if not node.cluster:
            gcp_hub_nodes.append(node)
            node_d2_path[node.id] = f'gcp.{node.id}'
            continue

        classification = _classify_cluster(node.cluster)
        zone = classification['zone']
        sub_zone = classification['sub_zone']

        if zone == 'clients':
            client_nodes.append(node)
            node_d2_path[node.id] = f'clients.{node.id}'

        elif zone == 'gcp':
            if sub_zone == 'hub' or sub_zone is None:
                gcp_hub_nodes.append(node)
                node_d2_path[node.id] = f'gcp.{node.id}'
            else:
                gcp_groups.setdefault(sub_zone, []).append(node)
                raw_name = classification['original_name']
                for sep in ['—', '-', '–']:
                    if sep in raw_name:
                        gcp_group_labels[sub_zone] = raw_name.split(sep)[-1].strip()
                        break
                else:
                    gcp_group_labels[sub_zone] = sub_zone.title()
                node_d2_path[node.id] = f'gcp.{sub_zone}.{node.id}'

        elif zone == 'external':
            label = classification['original_name']
            sg_key = _d2_key(label)
            if sub_zone == 'internal':
                external_internal.setdefault(label, []).append(node)
            else:
                external_third_party.setdefault(label, []).append(node)
            node_d2_path[node.id] = f'external.{sg_key}.{node.id}'

    if client_nodes:
        lines.append('clients: {')
        lines.append('  style.fill: "#FFF8E1"')
        lines.append('  style.stroke: "#FFA000"')
        lines.append('  style.font-color: "#E65100"')
        lines.append('  style.border-radius: 8')
        lines.append('')
        for node in client_nodes:
            lines.extend(_render_d2_node(node, indent='  '))
            lines.append('')
        lines.append('}')
        lines.append('')

    has_gcp = gcp_hub_nodes or gcp_groups
    if has_gcp:
        lines.append('gcp: "Google Cloud Platform" {')
        lines.append('  style.fill: "#E3F2FD"')
        lines.append('  style.stroke: "#4285F4"')
        lines.append('  style.font-color: "#1A73E8"')
        lines.append('  style.border-radius: 12')
        lines.append('')

        for node in gcp_hub_nodes:
            lines.extend(_render_d2_node(node, indent='  '))
            lines.append('')

        for group_key in ['ai', 'data', 'security', 'observability']:
            if group_key not in gcp_groups:
                continue
            group_label = gcp_group_labels.get(group_key, group_key.title())
            group_nodes = gcp_groups[group_key]

            lines.append(f'  {group_key}: "{group_label}" {{')
            lines.append('    style.fill: "#F0F7FE"')
            lines.append('    style.stroke: "#90CAF9"')
            lines.append('    style.font-color: "#1565C0"')
            lines.append('    style.border-radius: 8')
            lines.append('')
            for node in group_nodes:
                lines.extend(_render_d2_node(node, indent='    '))
                lines.append('')
            lines.append('  }')
            lines.append('')

        for group_key, group_nodes in gcp_groups.items():
            if group_key in ['ai', 'data', 'security', 'observability']:
                continue
            group_label = gcp_group_labels.get(group_key, group_key.title())
            lines.append(f'  {group_key}: "{group_label}" {{')
            lines.append('    style.fill: "#F0F7FE"')
            lines.append('    style.stroke: "#90CAF9"')
            lines.append('    style.font-color: "#1565C0"')
            lines.append('    style.border-radius: 8')
            lines.append('')
            for node in group_nodes:
                lines.extend(_render_d2_node(node, indent='    '))
                lines.append('')
            lines.append('  }')
            lines.append('')

        lines.append('}')
        lines.append('')

    has_external = external_internal or external_third_party
    if has_external:
        lines.append('external: "External Systems" {')
        lines.append('  style.fill: "#FAFAFA"')
        lines.append('  style.stroke: "#BDBDBD"')
        lines.append('  style.border-radius: 8')
        lines.append('')

        for sg_name, sg_nodes in external_internal.items():
            sg_key = _d2_key(sg_name)
            lines.append(f'  {sg_key}: {{')
            lines.append('    style.fill: "#EFEBE9"')
            lines.append('    style.stroke: "#795548"')
            lines.append('    style.font-color: "#4E342E"')
            lines.append('    style.border-radius: 8')
            lines.append('')
            for node in sg_nodes:
                lines.extend(_render_d2_node(node, indent='    '))
                lines.append('')
            lines.append('  }')
            lines.append('')

        for sg_name, sg_nodes in external_third_party.items():
            sg_key = _d2_key(sg_name)
            lines.append(f'  {sg_key}: {{')
            lines.append('    style.fill: "#E0F2F1"')
            lines.append('    style.stroke: "#009688"')
            lines.append('    style.font-color: "#00695C"')
            lines.append('    style.border-radius: 8')
            lines.append('')
            for node in sg_nodes:
                lines.extend(_render_d2_node(node, indent='    '))
                lines.append('')
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
    direction: str = 'LR',
    tool_context=None,
) -> dict[str, Any]:
    """Generates a GCP architecture diagram as a PNG image artifact.

    The resulting artifact filename is stored in session state for later
    use by generate_sow_document.

    Args:
        title: Title displayed at the top of the diagram.
        nodes: List of architecture components with service type and optional cluster.
        edges: List of connections between components.
        direction: Diagram layout direction — "LR" (left-to-right) or "TB" (top-to-bottom).

    Returns:
        Dict with status, message, and the artifact filename of the generated image.
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
            suggestion='Verifique se todos os nós possuem id, label e service válidos.',
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
                ['d2', '--bundle', '--pad=80', str(d2_file), str(svg_path)],
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
