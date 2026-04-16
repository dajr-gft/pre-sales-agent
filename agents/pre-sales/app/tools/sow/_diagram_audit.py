"""Deterministic structural audit for architecture diagrams.

Runs the AUD-01..AUD-20 checklist from architecture-guide.md Part 7
without invoking any LLM. Returns a structured result the caller can
surface as a tool error to trigger agent-side revision.

The audit is invoked by `generate_architecture_diagram` before rendering.
BLOCKER failures prevent rendering and are surfaced as a ToolError so the
agent can silently revise and retry. WARNING failures are logged but do
not block.

Severity policy (intentional):
- BLOCKERs are reserved for checks that operate on structured data (sets,
  enums, graph topology, exact keyword presence) where false positives
  are effectively impossible. A false positive BLOCKER forces an
  unnecessary retry cycle and can end the conversation in an error state.
- WARNINGs are used for heuristics over free-form text (description
  parsing, justification detection, external-API credential handling).
  These surface useful signal without risking destructive retries.
"""

import re
from dataclasses import dataclass, field
from typing import Literal

from ._diagram_models import (
    ArchitectureEdge,
    ArchitectureNode,
    GcpServiceEnum,
)

Severity = Literal['BLOCKER', 'WARNING']


@dataclass
class AuditFailure:
    """A single check that failed."""

    check_id: str
    severity: Severity
    defect: str


@dataclass
class AuditResult:
    """Outcome of running the full audit against an architecture spec."""

    passed: bool
    failures: list[AuditFailure] = field(default_factory=list)

    @property
    def blockers(self) -> list[AuditFailure]:
        return [f for f in self.failures if f.severity == 'BLOCKER']

    @property
    def warnings(self) -> list[AuditFailure]:
        return [f for f in self.failures if f.severity == 'WARNING']

    def format_defects(self) -> str:
        """Human-readable defect list for surfacing via ToolError."""
        lines: list[str] = []
        if self.blockers:
            lines.append(
                'BLOCKER failures (must fix before diagram generation):'
            )
            for f in self.blockers:
                lines.append(f'  - [{f.check_id}] {f.defect}')
        if self.warnings:
            if lines:
                lines.append('')
            lines.append('WARNING (recommended fixes):')
            for f in self.warnings:
                lines.append(f'  - [{f.check_id}] {f.defect}')
        return '\n'.join(lines)


_GENERIC_LABELS: set[str] = {
    'backend',
    'database',
    'model',
    'logs',
    'gateway',
    'api',
    'server',
    'storage',
    'service',
    'compute',
    'data',
    'function',
    'app',
    'application',
    'frontend',
    'ui',
}

_COMPUTE_SERVICES: set[GcpServiceEnum] = {
    GcpServiceEnum.CLOUD_RUN,
    GcpServiceEnum.CLOUD_FUNCTIONS,
    GcpServiceEnum.GKE,
    GcpServiceEnum.APP_ENGINE,
    GcpServiceEnum.AGENT_ENGINE,
}

_DATA_SERVICES: set[GcpServiceEnum] = {
    GcpServiceEnum.BIGQUERY,
    GcpServiceEnum.FIRESTORE,
    GcpServiceEnum.CLOUD_SQL,
    GcpServiceEnum.SPANNER,
    GcpServiceEnum.CLOUD_STORAGE,
    GcpServiceEnum.MEMORYSTORE,
    GcpServiceEnum.POSTGRESQL,
    GcpServiceEnum.MYSQL,
    GcpServiceEnum.MONGODB,
}

_ENTRY_POINT_SERVICES: set[GcpServiceEnum] = {
    GcpServiceEnum.CLIENT,
    GcpServiceEnum.USERS,
}

_AI_SERVICES: set[GcpServiceEnum] = {
    GcpServiceEnum.VERTEX_AI,
    GcpServiceEnum.VERTEX_AI_SEARCH,
    GcpServiceEnum.GEMINI,
    GcpServiceEnum.AGENT_ENGINE,
    GcpServiceEnum.DIALOGFLOW,
    GcpServiceEnum.AUTOML,
}

_POLICY_LAYER_SERVICES: set[GcpServiceEnum] = {
    GcpServiceEnum.IAM,
}

_NON_GCP_SERVICES: set[GcpServiceEnum] = {
    GcpServiceEnum.CLIENT,
    GcpServiceEnum.USERS,
    GcpServiceEnum.ON_PREM_SERVER,
    GcpServiceEnum.POSTGRESQL,
    GcpServiceEnum.MYSQL,
    GcpServiceEnum.MONGODB,
    GcpServiceEnum.GENERIC,
}

_EXTERNAL_SYSTEM_SERVICES: set[GcpServiceEnum] = {
    GcpServiceEnum.ON_PREM_SERVER,
    GcpServiceEnum.GENERIC,
}


def _service_display_name(service: GcpServiceEnum) -> str:
    """Canonical display name (e.g. 'Cloud Run')."""
    return service.value


def _label_contains_product_name(
    label: str, service: GcpServiceEnum
) -> bool:
    """True if the label redundantly includes the product name.

    'Cloud Run Backend' -> True (defect)
    'Credit Analysis API' -> False (good)
    """
    label_lower = label.lower()
    service_name = _service_display_name(service).lower()
    return service_name in label_lower


def _extract_mentioned_services(text: str) -> set[GcpServiceEnum]:
    """Find GCP service names mentioned in free-text description.

    Uses word-boundary matching to avoid false positives
    (e.g. 'Cloud SQL' should not match plain 'SQL').
    """
    mentioned: set[GcpServiceEnum] = set()
    text_lower = text.lower()
    for svc in GcpServiceEnum:
        if svc in _NON_GCP_SERVICES:
            continue
        name = _service_display_name(svc).lower()
        if re.search(rf'\b{re.escape(name)}\b', text_lower):
            mentioned.add(svc)
    return mentioned


def _stack_services_to_enum(
    technology_stack: list[dict],
) -> set[GcpServiceEnum]:
    """Map Technology Stack table rows to GcpServiceEnum members."""
    result: set[GcpServiceEnum] = set()
    for row in technology_stack:
        name = (row.get('service') or '').strip()
        if not name:
            continue
        for svc in GcpServiceEnum:
            if _service_display_name(svc) == name:
                result.add(svc)
                break
    return result


def audit_architecture(
    nodes: list[ArchitectureNode],
    edges: list[ArchitectureEdge],
    description: str = '',
    technology_stack: list[dict] | None = None,
) -> AuditResult:
    """Run all structural checks against the architecture artifacts.

    Args:
        nodes: Diagram nodes (from spec 1d).
        edges: Diagram edges (from spec 1d).
        description: Architecture description text (from 1b). Optional --
            if empty, AUD-01/AUD-15/AUD-16/AUD-17/AUD-18 are skipped.
        technology_stack: Technology Stack table rows (from 1c), list of
            {'service': str, 'purpose': str}. Optional -- if None,
            AUD-01/AUD-02/AUD-03 are skipped.

    Returns:
        AuditResult with passed=True iff no BLOCKER failures.
    """
    failures: list[AuditFailure] = []

    node_by_id: dict[str, ArchitectureNode] = {n.id: n for n in nodes}
    gcp_services_in_diagram: set[GcpServiceEnum] = {
        n.service for n in nodes if n.service not in _NON_GCP_SERVICES
    }

    for n in nodes:
        if n.label.strip().lower() in _GENERIC_LABELS:
            failures.append(
                AuditFailure(
                    'AUD-05',
                    'BLOCKER',
                    f"Node '{n.id}' uses generic label '{n.label}'. "
                    f'Rename to a project-specific functional role '
                    f"(e.g., 'Credit Analysis API' instead of 'Backend').",
                )
            )

    for n in nodes:
        if n.service in _NON_GCP_SERVICES:
            continue
        if _label_contains_product_name(n.label, n.service):
            failures.append(
                AuditFailure(
                    'AUD-06',
                    'BLOCKER',
                    f"Node '{n.id}' label '{n.label}' repeats the product "
                    f"name '{_service_display_name(n.service)}'. The icon "
                    f'already shows the product -- describe the functional '
                    f'role only.',
                )
            )

    for n in nodes:
        if n.service not in _EXTERNAL_SYSTEM_SERVICES:
            continue
        has_version = bool(
            re.search(r'v\d|\d\.\d|\bAPI\b|\bSDK\b', n.label)
        )
        word_count = len(n.label.split())
        if word_count < 2 and not has_version:
            failures.append(
                AuditFailure(
                    'AUD-07',
                    'WARNING',
                    f"External node '{n.id}' label '{n.label}' may lack "
                    f'system name or version. Include both when known '
                    f"(e.g., 'Salesforce REST API v58').",
                )
            )

    for n in nodes:
        if n.service in _POLICY_LAYER_SERVICES:
            failures.append(
                AuditFailure(
                    'AUD-10',
                    'BLOCKER',
                    f"Node '{n.id}' uses "
                    f"{_service_display_name(n.service)} as a diagram node. "
                    f'This is a policy layer -- represent it in the '
                    f'description text or edge labels, not as a node.',
                )
            )

    third_party_keywords = ['third-party', 'third party', 'saas']
    for n in nodes:
        if n.service not in _ENTRY_POINT_SERVICES:
            continue
        cluster = (n.cluster or '').lower()
        if any(kw in cluster for kw in third_party_keywords):
            failures.append(
                AuditFailure(
                    'AUD-09',
                    'BLOCKER',
                    f"Entry point node '{n.id}' is in cluster "
                    f"'{n.cluster}', which is a third-party/external "
                    f'cluster. Entry points must be in a dedicated '
                    f"'User / Consumer' cluster.",
                )
            )

    on_prem_markers = [
        'on-prem',
        'on prem',
        'customer environment',
        'customer on-premises',
        'internal systems',
    ]
    for n in nodes:
        if n.service in _NON_GCP_SERVICES:
            continue
        cluster = (n.cluster or '').lower()
        if not cluster:
            continue
        looks_non_gcp = any(m in cluster for m in on_prem_markers)
        looks_gcp = 'google cloud' in cluster or 'gcp' in cluster
        if looks_non_gcp and not looks_gcp:
            failures.append(
                AuditFailure(
                    'AUD-08',
                    'BLOCKER',
                    f"GCP service '{_service_display_name(n.service)}' "
                    f"(node '{n.id}') is in non-GCP cluster '{n.cluster}'. "
                    f'GCP products belong in the Google Cloud cluster '
                    f'regardless of who administers them.',
                )
            )

    for i, e in enumerate(edges):
        if not e.label or not e.label.strip():
            src = node_by_id.get(e.source_id)
            tgt = node_by_id.get(e.target_id)
            src_label = src.label if src else e.source_id
            tgt_label = tgt.label if tgt else e.target_id
            failures.append(
                AuditFailure(
                    'AUD-11',
                    'BLOCKER',
                    f"Edge #{i} '{src_label}' -> '{tgt_label}' has no "
                    f'label. Every edge must name a protocol or data '
                    f"mechanism (e.g., 'REST API', 'gRPC', 'Pub/Sub', "
                    f"'HTTPS').",
                )
            )

    for i, e in enumerate(edges):
        if e.source_id not in node_by_id:
            failures.append(
                AuditFailure(
                    'AUD-11',
                    'BLOCKER',
                    f"Edge #{i} references unknown source_id "
                    f"'{e.source_id}'.",
                )
            )
        if e.target_id not in node_by_id:
            failures.append(
                AuditFailure(
                    'AUD-11',
                    'BLOCKER',
                    f"Edge #{i} references unknown target_id "
                    f"'{e.target_id}'.",
                )
            )

    connected_ids: set[str] = set()
    for e in edges:
        connected_ids.add(e.source_id)
        connected_ids.add(e.target_id)
    for n in nodes:
        if n.id not in connected_ids:
            failures.append(
                AuditFailure(
                    'AUD-11',
                    'BLOCKER',
                    f"Node '{n.id}' ({n.label}) has no edges. Either "
                    f'connect it to the data flow or remove it.',
                )
            )

    has_entry = any(n.service in _ENTRY_POINT_SERVICES for n in nodes)
    has_compute = any(n.service in _COMPUTE_SERVICES for n in nodes)
    has_data = any(n.service in _DATA_SERVICES for n in nodes)

    if not has_entry:
        failures.append(
            AuditFailure(
                'AUD-14',
                'BLOCKER',
                'No Entry Point node in diagram (expected at least one '
                'Client/User or Users node).',
            )
        )
    if not has_compute:
        failures.append(
            AuditFailure(
                'AUD-14',
                'BLOCKER',
                'No Compute node in diagram (expected at least one of '
                'Cloud Run, Cloud Functions, GKE, App Engine, or '
                'Agent Engine).',
            )
        )
    if not has_data:
        failures.append(
            AuditFailure(
                'AUD-14',
                'BLOCKER',
                'No Data/Storage node in diagram (expected at least one '
                'of BigQuery, Firestore, Cloud SQL, Spanner, Cloud '
                'Storage, etc.).',
            )
        )

    stack_services: set[GcpServiceEnum] = set()
    if technology_stack is not None:
        stack_services = _stack_services_to_enum(technology_stack)

        missing_in_diagram = stack_services - gcp_services_in_diagram
        for svc in missing_in_diagram:
            failures.append(
                AuditFailure(
                    'AUD-02',
                    'BLOCKER',
                    f"Service '{_service_display_name(svc)}' appears in "
                    f'the Technology Stack table but not as a diagram '
                    f'node.',
                )
            )

        missing_in_stack = gcp_services_in_diagram - stack_services
        for svc in missing_in_stack:
            failures.append(
                AuditFailure(
                    'AUD-03',
                    'BLOCKER',
                    f"Service '{_service_display_name(svc)}' appears as a "
                    f'diagram node but is not in the Technology Stack '
                    f'table.',
                )
            )

    if description:
        desc_lower = description.lower()
        mentioned_in_desc = _extract_mentioned_services(description)

        if technology_stack is not None:
            missing_from_stack = mentioned_in_desc - stack_services
            for svc in missing_from_stack:
                failures.append(
                    AuditFailure(
                        'AUD-01',
                        'BLOCKER',
                        f"Service '{_service_display_name(svc)}' is "
                        f'mentioned in the description but missing from '
                        f'the Technology Stack table.',
                    )
                )

        has_external_nodes = any(
            n.service in _EXTERNAL_SYSTEM_SERVICES for n in nodes
        )
        if has_external_nodes:
            has_secret_manager = (
                GcpServiceEnum.SECRET_MANAGER in gcp_services_in_diagram
                or 'secret manager' in desc_lower
            )
            if not has_secret_manager:
                failures.append(
                    AuditFailure(
                        'AUD-15',
                        'WARNING',
                        'External integrations present but Secret Manager '
                        'is neither in the diagram nor mentioned in the '
                        'description. Verify where API credentials are '
                        'managed.',
                    )
                )

        ai_keywords = [
            'ai',
            'ml',
            'machine learning',
            'genai',
            'gen ai',
            'agent',
            'llm',
            'rag',
        ]
        looks_like_ai = any(kw in desc_lower for kw in ai_keywords)
        has_ai_node = any(n.service in _AI_SERVICES for n in nodes)
        if looks_like_ai and not has_ai_node:
            failures.append(
                AuditFailure(
                    'AUD-16',
                    'WARNING',
                    'Description suggests AI/ML project but no AI/ML '
                    'service node (Vertex AI, Gemini, Agent Engine, etc.) '
                    'is present in the diagram.',
                )
            )

        justification_markers = [
            'fr-',
            'nfr-',
            'because',
            'to satisfy',
            'to meet',
            'to handle',
            'required by',
            'supports',
            'enables',
            'ensures',
            'in order to',
        ]
        if not any(m in desc_lower for m in justification_markers):
            failures.append(
                AuditFailure(
                    'AUD-17',
                    'WARNING',
                    'Description appears to lack explicit justification '
                    'for GCP service choices (no references to FR/NFR IDs '
                    'or justification phrases detected).',
                )
            )

        all_lines = [
            line for line in description.splitlines() if line.strip()
        ]
        bullet_lines = sum(
            1
            for line in all_lines
            if line.strip().startswith(('-', '*', '•'))
        )
        total_lines = max(1, len(all_lines))
        if bullet_lines / total_lines > 0.5:
            failures.append(
                AuditFailure(
                    'AUD-18',
                    'WARNING',
                    'Description reads as a bullet list rather than a '
                    'data-flow narrative. Rewrite as prose describing how '
                    'data moves through the system.',
                )
            )

    passed = not any(f.severity == 'BLOCKER' for f in failures)
    return AuditResult(passed=passed, failures=failures)
