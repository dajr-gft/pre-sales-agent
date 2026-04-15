from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

try:
    from diagrams.gcp.analytics import (
        BigQuery,
        Composer,
        Dataflow,
        Looker,
        PubSub,
    )
    from diagrams.gcp.api import APIGateway, Apigee
    from diagrams.gcp.compute import (
        AppEngine,
        Functions,
        KubernetesEngine,
        Run,
    )
    from diagrams.gcp.database import SQL, Firestore, Memorystore, Spanner
    from diagrams.gcp.devtools import Build, Scheduler, Tasks
    from diagrams.gcp.ml import AutoML, DialogFlowEnterpriseEdition, VertexAI
    from diagrams.gcp.network import CDN, DNS, VPC, Armor, LoadBalancing
    from diagrams.gcp.operations import Logging, Monitoring
    from diagrams.gcp.security import IAP, KMS, SCC, Iam, SecretManager
    from diagrams.gcp.storage import Storage
    from diagrams.generic.compute import Rack
    from diagrams.onprem.client import Client, Users
    from diagrams.onprem.compute import Server
    from diagrams.onprem.database import MongoDB, MySQL, PostgreSQL

    _DIAGRAMS_AVAILABLE = True
except ImportError:
    _DIAGRAMS_AVAILABLE = False
    logger.warning('_diagram_models: diagrams library not installed')


class GcpServiceEnum(str, Enum):
    """Allowed services for architecture diagram nodes.

    The LLM must pick one of these values. Each maps to a specific icon
    from the ``diagrams`` library. Services without a dedicated icon are
    mapped to the closest visual equivalent.
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


SERVICE_ICON_MAP = {}

if _DIAGRAMS_AVAILABLE:
    SERVICE_ICON_MAP = {
        GcpServiceEnum.CLIENT: Client,
        GcpServiceEnum.USERS: Users,
        GcpServiceEnum.ON_PREM_SERVER: Server,
        GcpServiceEnum.CLOUD_RUN: Run,
        GcpServiceEnum.CLOUD_FUNCTIONS: Functions,
        GcpServiceEnum.GKE: KubernetesEngine,
        GcpServiceEnum.APP_ENGINE: AppEngine,
        GcpServiceEnum.VERTEX_AI: VertexAI,
        GcpServiceEnum.VERTEX_AI_SEARCH: VertexAI,
        GcpServiceEnum.GEMINI: VertexAI,
        GcpServiceEnum.AGENT_ENGINE: Run,
        GcpServiceEnum.DIALOGFLOW: DialogFlowEnterpriseEdition,
        GcpServiceEnum.AUTOML: AutoML,
        GcpServiceEnum.BIGQUERY: BigQuery,
        GcpServiceEnum.DATAFLOW: Dataflow,
        GcpServiceEnum.PUBSUB: PubSub,
        GcpServiceEnum.COMPOSER: Composer,
        GcpServiceEnum.LOOKER: Looker,
        GcpServiceEnum.FIRESTORE: Firestore,
        GcpServiceEnum.CLOUD_SQL: SQL,
        GcpServiceEnum.SPANNER: Spanner,
        GcpServiceEnum.MEMORYSTORE: Memorystore,
        GcpServiceEnum.CLOUD_STORAGE: Storage,
        GcpServiceEnum.API_GATEWAY: APIGateway,
        GcpServiceEnum.APIGEE: Apigee,
        GcpServiceEnum.LOAD_BALANCER: LoadBalancing,
        GcpServiceEnum.VPC: VPC,
        GcpServiceEnum.CDN: CDN,
        GcpServiceEnum.DNS: DNS,
        GcpServiceEnum.CLOUD_ARMOR: Armor,
        GcpServiceEnum.IAM: Iam,
        GcpServiceEnum.IAP: IAP,
        GcpServiceEnum.KMS: KMS,
        GcpServiceEnum.SECRET_MANAGER: SecretManager,
        GcpServiceEnum.SCC: SCC,
        GcpServiceEnum.MONITORING: Monitoring,
        GcpServiceEnum.LOGGING: Logging,
        GcpServiceEnum.CLOUD_BUILD: Build,
        GcpServiceEnum.CLOUD_SCHEDULER: Scheduler,
        GcpServiceEnum.CLOUD_TASKS: Tasks,
        GcpServiceEnum.POSTGRESQL: PostgreSQL,
        GcpServiceEnum.MYSQL: MySQL,
        GcpServiceEnum.MONGODB: MongoDB,
        GcpServiceEnum.GENERIC: Rack,
    }


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
