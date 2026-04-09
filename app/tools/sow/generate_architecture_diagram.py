from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path
from typing import List

from google.genai import types as genai_types

from ._diagram_models import (
    ArchitectureEdge,
    ArchitectureNode,
    SERVICE_ICON_MAP,
    _DIAGRAMS_AVAILABLE,
    ensure_edge,
    ensure_node,
)

logger = logging.getLogger(__name__)

try:
    from diagrams import Cluster, Diagram, Edge
    from diagrams.generic.compute import Rack
except ImportError:
    pass

_GRAPHVIZ_AVAILABLE = shutil.which('dot') is not None
if not _GRAPHVIZ_AVAILABLE:
    logger.warning(
        'generate_architecture_diagram: Graphviz not found in PATH — '
        'diagram generation will be skipped. Install Graphviz to enable.'
    )

_DIAGRAM_ARTIFACT_KEY = 'architecture_diagram_artifact'

_DEFAULT_GRAPH_ATTR = {
    'splines': 'ortho',
    'nodesep': '1.0',
    'ranksep': '1.2',
    'pad': '0.5',
    'fontsize': '14',
}


async def generate_architecture_diagram(
    title: str,
    nodes: List[ArchitectureNode],
    edges: List[ArchitectureEdge],
    direction: str = 'LR',
    tool_context=None,
) -> dict:
    """Generates a GCP architecture diagram as a PNG image with official service icons.

    Call this tool after all technical requirements have been collected and the
    architecture has been defined. The diagram is rendered locally to a tempfile
    (required by the ``diagrams`` library), then persisted as an ADK artifact
    via the configured ArtifactService (GCS in production). The artifact
    filename is stored in session state for later use by generate_sow_document,
    so the diagram survives across Agent Engine instances.

    Nodes can be grouped into visual clusters (e.g., "Google Cloud",
    "On-Premises", "Third-Party") by setting the ``cluster`` field.

    Args:
        title: Title displayed at the top of the diagram.
        nodes: List of architecture components with service type and optional cluster.
        edges: List of connections between components.
        direction: Diagram layout direction — "LR" (left-to-right) or "TB" (top-to-bottom).
        tool_context: ADK ToolContext for session state and artifact access
            (injected automatically).

    Returns:
        Dict with status, message, and the artifact filename of the generated PNG.
    """
    if not _DIAGRAMS_AVAILABLE or not _GRAPHVIZ_AVAILABLE:
        reason = (
            'diagrams library not installed'
            if not _DIAGRAMS_AVAILABLE
            else 'Graphviz not found in PATH'
        )
        logger.warning(
            'generate_architecture_diagram: skipped — %s',
            reason,
        )
        return {
            'status': 'skipped',
            'message': (
                f'Geração de diagrama ignorada ({reason}). '
                f'O documento será gerado com placeholder no lugar do diagrama. '
                f'No Agent Engine o Graphviz estará disponível via installation_scripts.'
            ),
        }

    try:
        nodes = [ensure_node(n) for n in nodes]
    except Exception as parse_err:
        print(
            f'[DIAGRAM][ERROR] failed to parse nodes | error={parse_err}',
            flush=True,
        )
        return {
            'error': f'Falha ao interpretar os nós do diagrama: {parse_err}'
        }

    try:
        edges = [ensure_edge(e) for e in edges]
    except Exception as parse_err:
        print(
            f'[DIAGRAM][ERROR] failed to parse edges | error={parse_err}',
            flush=True,
        )
        return {
            'error': f'Falha ao interpretar as conexões do diagrama: {parse_err}'
        }

    output_dir = Path(tempfile.gettempdir()) / 'sow_diagrams'
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_title = title.replace(' ', '_')[:40]
    file_base = str(output_dir / safe_title)
    png_local_path = Path(f'{file_base}.png')

    if direction not in ('LR', 'TB'):
        direction = 'LR'

    try:
        clusters: dict[str, list[ArchitectureNode]] = {}
        unclustered: list[ArchitectureNode] = []
        for node in nodes:
            if node.cluster:
                clusters.setdefault(node.cluster, []).append(node)
            else:
                unclustered.append(node)

        instantiated: dict[str, object] = {}

        with Diagram(
            name=title,
            filename=file_base,
            show=False,
            direction=direction,
            graph_attr=_DEFAULT_GRAPH_ATTR,
        ):
            for node in unclustered:
                icon_class = SERVICE_ICON_MAP.get(node.service, Rack)
                instantiated[node.id] = icon_class(node.label)

            for cluster_name, cluster_nodes in clusters.items():
                with Cluster(cluster_name):
                    for node in cluster_nodes:
                        icon_class = SERVICE_ICON_MAP.get(node.service, Rack)
                        instantiated[node.id] = icon_class(node.label)

            for edge in edges:
                source = instantiated.get(edge.source_id)
                target = instantiated.get(edge.target_id)
                if not source or not target:
                    logger.warning(
                        'generate_architecture_diagram: edge skipped — '
                        'unknown node id | source=%s target=%s',
                        edge.source_id,
                        edge.target_id,
                    )
                    continue
                if edge.label:
                    source >> Edge(label=edge.label) >> target
                else:
                    source >> target

        if not png_local_path.exists():
            logger.error(
                'generate_architecture_diagram: diagrams library ran but PNG not found at expected path | path=%s',
                png_local_path,
            )
            return {
                'error': 'O diagrama foi processado mas o arquivo PNG não foi encontrado.'
            }

        png_bytes = png_local_path.read_bytes()
        artifact_filename = f'architecture_diagram_{safe_title}.png'

        if tool_context:
            try:
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
                    'generate_architecture_diagram: artifact saved | filename=%s | version=%s | nodes=%d | edges=%d',
                    artifact_filename,
                    version,
                    len(nodes),
                    len(edges),
                )
            except Exception as save_err:
                logger.error(
                    'generate_architecture_diagram: failed to save artifact | error=%s | type=%s',
                    str(save_err),
                    type(save_err).__name__,
                )
                return {
                    'error': f'Falha ao salvar o diagrama como artefato: {str(save_err)}'
                }
        else:
            logger.warning(
                'generate_architecture_diagram: tool_context is None — diagram NOT persisted as artifact'
            )

        return {
            'status': 'success',
            'message': f"Diagrama '{title}' gerado com sucesso.",
            'artifact_filename': artifact_filename,
        }

    except Exception as e:
        logger.error(
            'generate_architecture_diagram: failed | error=%s | type=%s',
            str(e),
            type(e).__name__,
        )
        return {'error': f'Falha ao gerar o diagrama: {str(e)}'}

    finally:
        if png_local_path.exists():
            try:
                png_local_path.unlink()
            except Exception as cleanup_err:
                logger.warning(
                    'generate_architecture_diagram: failed to clean up local PNG | path=%s | error=%s',
                    png_local_path,
                    str(cleanup_err),
                )
