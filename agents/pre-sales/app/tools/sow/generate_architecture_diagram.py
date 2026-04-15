import shutil
import tempfile
from pathlib import Path
from typing import Any

import structlog
from google.genai import types as genai_types

from ...shared.errors import safe_tool
from ...shared.types import ToolError, ToolSuccess
from ._diagram_models import (
    _DIAGRAMS_AVAILABLE,
    SERVICE_ICON_MAP,
    ArchitectureEdge,
    ArchitectureNode,
    ensure_edge,
    ensure_node,
)

logger = structlog.get_logger()

try:
    from diagrams import Cluster, Diagram, Edge
    from diagrams.generic.compute import Rack
except ImportError:
    pass

_GRAPHVIZ_AVAILABLE = shutil.which("dot") is not None
if not _GRAPHVIZ_AVAILABLE:
    logger.warning(
        "graphviz_not_found",
        message="Graphviz not found in PATH — diagram generation will be skipped. "
        "Install Graphviz to enable.",
    )

_DIAGRAM_ARTIFACT_KEY = "architecture_diagram_artifact"

_DEFAULT_GRAPH_ATTR = {
    "splines": "line",
    "nodesep": "0.8",
    "ranksep": "1.0",
    "ratio": "auto",
    "fontsize": "14",
}


@safe_tool
async def generate_architecture_diagram(
    title: str,
    nodes: list[ArchitectureNode],
    edges: list[ArchitectureEdge],
    direction: str = "LR",
    tool_context=None,
) -> dict[str, Any]:
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
            "diagrams library not installed"
            if not _DIAGRAMS_AVAILABLE
            else "Graphviz not found in PATH"
        )
        logger.warning("diagram_skipped", reason=reason)
        return ToolError(
            status="error",
            error=f"Geração de diagrama ignorada ({reason}). "
            f"O documento será gerado com placeholder no lugar do diagrama. "
            f"No Agent Engine o Graphviz estará disponível via installation_scripts.",
            retryable=False,
            tool="generate_architecture_diagram",
            suggestion="Deploy no Agent Engine para ter Graphviz disponível.",
        )

    try:
        nodes = [ensure_node(n) for n in nodes]
    except Exception as parse_err:
        logger.error("node_parse_failed", error=str(parse_err))
        return ToolError(
            status="error",
            error=f"Falha ao interpretar os nós do diagrama: {parse_err}",
            retryable=False,
            tool="generate_architecture_diagram",
            suggestion="Verifique se todos os nós possuem id, label e service válidos.",
        )

    try:
        edges = [ensure_edge(e) for e in edges]
    except Exception as parse_err:
        logger.error("edge_parse_failed", error=str(parse_err))
        return ToolError(
            status="error",
            error=f"Falha ao interpretar as conexões do diagrama: {parse_err}",
            retryable=False,
            tool="generate_architecture_diagram",
            suggestion="Verifique se source_id e target_id correspondem a nós existentes.",
        )

    output_dir = Path(tempfile.gettempdir()) / "sow_diagrams"
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_title = title.replace(" ", "_")[:40]
    file_base = str(output_dir / safe_title)
    png_local_path = Path(f"{file_base}.png")

    try:
        clusters: dict[str, list[ArchitectureNode]] = {}
        unclustered: list[ArchitectureNode] = []
        for node in nodes:
            if node.cluster:
                clusters.setdefault(node.cluster, []).append(node)
            else:
                unclustered.append(node)

        max_nodes_in_cluster = max(
            [len(c_nodes) for c_nodes in clusters.values()] + [0]
        )

        if max_nodes_in_cluster > 3:
            final_direction = "TB"
        elif direction in ("LR", "TB"):
            final_direction = direction
        else:
            final_direction = "LR"

        instantiated: dict[str, object] = {}

        with Diagram(
            name=title,
            filename=file_base,
            show=False,
            direction=final_direction,
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
                        "edge_skipped",
                        source=edge.source_id,
                        target=edge.target_id,
                        reason="unknown node id",
                    )
                    continue
                if edge.label:
                    source >> Edge(label=edge.label) >> target
                else:
                    source >> target

        if not png_local_path.exists():
            logger.error("png_not_found", path=str(png_local_path))
            return ToolError(
                status="error",
                error="O diagrama foi processado mas o arquivo PNG não foi encontrado.",
                retryable=True,
                tool="generate_architecture_diagram",
            )

        png_bytes = png_local_path.read_bytes()
        artifact_filename = f"architecture_diagram_{safe_title}.png"

        if tool_context:
            artifact = genai_types.Part.from_bytes(
                data=png_bytes,
                mime_type="image/png",
            )
            version = await tool_context.save_artifact(
                filename=artifact_filename,
                artifact=artifact,
            )
            tool_context.state[_DIAGRAM_ARTIFACT_KEY] = artifact_filename
            logger.info(
                "artifact_saved",
                filename=artifact_filename,
                version=version,
                nodes=len(nodes),
                edges=len(edges),
            )
        else:
            logger.warning(
                "artifact_not_persisted",
                reason="tool_context is None",
            )

        return ToolSuccess(
            status="success",
            data={
                "message": f"Diagrama '{title}' gerado com sucesso.",
                "artifact_filename": artifact_filename,
            },
        )

    finally:
        if png_local_path.exists():
            try:
                png_local_path.unlink()
            except Exception as cleanup_err:
                logger.warning(
                    "cleanup_failed",
                    path=str(png_local_path),
                    error=str(cleanup_err),
                )
