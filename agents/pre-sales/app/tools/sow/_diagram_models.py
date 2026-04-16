from __future__ import annotations

import logging
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from ._icon_downloader import ensure_icons_available

logger = logging.getLogger(__name__)


def _resolve_icon_base() -> Path | None:
    local_candidates = [
        Path('/opt/gcp-icons'),
        Path(__file__).parent / 'gcp-icons',
        Path('gcp-icons'),
    ]
    for c in local_candidates:
        if c.exists() and c.is_dir() and any(c.glob('*.svg')):
            return c

    return ensure_icons_available()


_ICON_BASE = _resolve_icon_base()


class GcpServiceEnum(str, Enum):
    """Allowed services for architecture diagram nodes.

    The LLM must pick one of these values. Each maps to an official
    Google Cloud icon (2025 set) or a D2 built-in shape.
    """

    CLIENT = 'Client/User'
    USERS = 'Users'
    ON_PREM_SERVER = 'On-Premises Server'
    CLOUD_RUN = 'Cloud Run'
    CLOUD_FUNCTIONS = 'Cloud Functions'
    GKE = 'Kubernetes Engine'
    APP_ENGINE = 'App Engine'
    VERTEX_AI = 'Vertex AI'
    VERTEX_AI_SEARCH = 'Vertex AI Search'
    GEMINI = 'Gemini'
    AGENT_ENGINE = 'Agent Engine'
    DIALOGFLOW = 'Dialogflow CX'
    AUTOML = 'AutoML'
    BIGQUERY = 'BigQuery'
    DATAFLOW = 'Dataflow'
    PUBSUB = 'Pub/Sub'
    COMPOSER = 'Cloud Composer'
    LOOKER = 'Looker'
    FIRESTORE = 'Firestore'
    CLOUD_SQL = 'Cloud SQL'
    SPANNER = 'Spanner'
    MEMORYSTORE = 'Memorystore'
    CLOUD_STORAGE = 'Cloud Storage'
    API_GATEWAY = 'API Gateway'
    APIGEE = 'Apigee'
    LOAD_BALANCER = 'Load Balancer'
    VPC = 'VPC'
    CDN = 'CDN'
    DNS = 'DNS'
    CLOUD_ARMOR = 'Cloud Armor'
    IAM = 'IAM'
    IAP = 'Identity-Aware Proxy'
    KMS = 'KMS'
    SECRET_MANAGER = 'Secret Manager'
    SCC = 'Security Command Center'
    MONITORING = 'Cloud Monitoring'
    LOGGING = 'Cloud Logging'
    CLOUD_BUILD = 'Cloud Build'
    CLOUD_SCHEDULER = 'Cloud Scheduler'
    CLOUD_TASKS = 'Cloud Tasks'
    POSTGRESQL = 'PostgreSQL'
    MYSQL = 'MySQL'
    MONGODB = 'MongoDB'
    GENERIC = 'Generic'


_D2_ICON_FILENAME: dict[GcpServiceEnum, str | None] = {
    GcpServiceEnum.CLOUD_RUN: 'Cloud_Run.svg',
    GcpServiceEnum.GKE: 'GKE.svg',
    GcpServiceEnum.VERTEX_AI: 'Vertex_AI.svg',
    GcpServiceEnum.VERTEX_AI_SEARCH: 'Vertex_AI.svg',
    GcpServiceEnum.AGENT_ENGINE: 'Agents.svg',
    GcpServiceEnum.BIGQUERY: 'BigQuery.svg',
    GcpServiceEnum.LOOKER: 'Looker.svg',
    GcpServiceEnum.CLOUD_SQL: 'Cloud_SQL.svg',
    GcpServiceEnum.SPANNER: 'Cloud_Spanner.svg',
    GcpServiceEnum.CLOUD_STORAGE: 'Cloud_Storage.svg',
    GcpServiceEnum.APIGEE: 'Apigee.svg',
    GcpServiceEnum.SCC: 'Security_Command_Center.svg',
    GcpServiceEnum.CLOUD_FUNCTIONS: 'Serverless_Computing.svg',
    GcpServiceEnum.APP_ENGINE: 'Serverless_Computing.svg',
    GcpServiceEnum.GEMINI: 'AI_Machine_Learning.svg',
    GcpServiceEnum.DIALOGFLOW: 'Agents.svg',
    GcpServiceEnum.AUTOML: 'AI_Machine_Learning.svg',
    GcpServiceEnum.DATAFLOW: 'Data_Analytics.svg',
    GcpServiceEnum.PUBSUB: 'Integration_Services.svg',
    GcpServiceEnum.COMPOSER: 'Integration_Services.svg',
    GcpServiceEnum.FIRESTORE: 'Databases.svg',
    GcpServiceEnum.MEMORYSTORE: 'Databases.svg',
    GcpServiceEnum.API_GATEWAY: 'Networking.svg',
    GcpServiceEnum.LOAD_BALANCER: 'Networking.svg',
    GcpServiceEnum.VPC: 'Networking.svg',
    GcpServiceEnum.CDN: 'Networking.svg',
    GcpServiceEnum.DNS: 'Networking.svg',
    GcpServiceEnum.CLOUD_ARMOR: 'Security_Identity.svg',
    GcpServiceEnum.IAM: 'Security_Identity.svg',
    GcpServiceEnum.IAP: 'Security_Identity.svg',
    GcpServiceEnum.KMS: 'Security_Identity.svg',
    GcpServiceEnum.SECRET_MANAGER: 'Security_Identity.svg',
    GcpServiceEnum.MONITORING: 'Observability.svg',
    GcpServiceEnum.LOGGING: 'Observability.svg',
    GcpServiceEnum.CLOUD_BUILD: 'DevOps.svg',
    GcpServiceEnum.CLOUD_SCHEDULER: 'Developer_Tools.svg',
    GcpServiceEnum.CLOUD_TASKS: 'Developer_Tools.svg',
    GcpServiceEnum.POSTGRESQL: 'Databases.svg',
    GcpServiceEnum.MYSQL: 'Databases.svg',
    GcpServiceEnum.MONGODB: 'Databases.svg',
    GcpServiceEnum.ON_PREM_SERVER: 'Compute_Engine.svg',
    GcpServiceEnum.GENERIC: 'Compute.svg',
    GcpServiceEnum.CLIENT: None,
    GcpServiceEnum.USERS: None,
}

_D2_SHAPE_OVERRIDE: dict[GcpServiceEnum, str | None] = {
    GcpServiceEnum.CLIENT: 'person',
    GcpServiceEnum.USERS: 'person',
}


def get_d2_icon_path(service: GcpServiceEnum) -> str | None:
    """Return the absolute path to the icon SVG for a service, or None."""
    filename = _D2_ICON_FILENAME.get(service)
    if filename is None or _ICON_BASE is None:
        return None
    path = _ICON_BASE / filename
    if path.exists():
        return str(path)
    logger.warning('Icon file not found: %s', path)
    return None


def get_d2_shape(service: GcpServiceEnum) -> str | None:
    """Return the D2 shape override for a service, or None."""
    return _D2_SHAPE_OVERRIDE.get(service)


class ArchitectureNode(BaseModel):
    id: str = Field(
        ...,
        description="Short unique ID, no spaces (e.g., 'agent_engine', 'firestore_db').",
    )
    label: str = Field(
        ...,
        description='Display name shown below the icon in the diagram.',
    )
    service: GcpServiceEnum = Field(
        ...,
        description='GCP service — must be one of the allowed enum values.',
    )
    cluster: Optional[str] = Field(
        default=None,
        description=(
            'REQUIRED for enterprise architectures. Group services by network zone or responsibility. '
            "Use standardized names such as: 'Customer Environment', 'Google Cloud (Edge/Security)', "
            "'Google Cloud (Core/Compute)', 'Google Cloud (Data/Storage)', or 'Google Cloud (Networking)'."
        ),
    )


class ArchitectureEdge(BaseModel):
    source_id: str = Field(
        ...,
        description='ID of the source node (must match a node id).',
    )
    target_id: str = Field(
        ...,
        description='ID of the target node (must match a node id).',
    )
    label: Optional[str] = Field(
        default=None,
        description="Optional label on the arrow (e.g., 'REST API', 'gRPC', 'Pub/Sub').",
    )


def ensure_node(item: Any) -> ArchitectureNode:
    """Convert a dict to ArchitectureNode if needed (ADK passes raw dicts)."""
    if isinstance(item, ArchitectureNode):
        return item
    if isinstance(item, dict):
        return ArchitectureNode(**item)
    raise TypeError(
        f'Expected ArchitectureNode or dict, got {type(item).__name__}'
    )


def ensure_edge(item: Any) -> ArchitectureEdge:
    """Convert a dict to ArchitectureEdge if needed (ADK passes raw dicts)."""
    if isinstance(item, ArchitectureEdge):
        return item
    if isinstance(item, dict):
        return ArchitectureEdge(**item)
    raise TypeError(
        f'Expected ArchitectureEdge or dict, got {type(item).__name__}'
    )
