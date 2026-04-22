"""Deterministic structural audit for architecture diagrams.

Runs the AUD-01..AUD-19 checklist from architecture-guide.md Part 7
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

Historical note: AUD-08 and AUD-09 performed keyword-based cluster
classification ("is this cluster name GCP-ish?" / "does it look like a
third-party bucket?"). Those checks are retired -- the underlying bug
class is now structurally impossible because `parent_cluster` is a
closed enum (ClusterZone) and AUD-19 validates the service↔zone pair
directly against `expected_zones_for()`.
"""

import re
from dataclasses import dataclass, field
from typing import Literal

from ._diagram_models import (
    ArchitectureEdge,
    ArchitectureNode,
    ClusterZone,
    GcpServiceEnum,
    expected_zones_for,
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


def _format_zone_list(zones: frozenset[ClusterZone]) -> str:
    """Render a deterministic, comma-separated list of zone display names."""
    return ', '.join(
        f"'{z.value}'" for z in sorted(zones, key=lambda z: z.value)
    )


def _aud19_defect_message(node: ArchitectureNode) -> str:
    """Context-specific explanation for a service↔zone mismatch.

    The generic "invalid zone" message is technically correct but not
    useful for the LLM to self-correct. Tailoring the message by service
    category makes the fix obvious on the first retry.
    """
    service_name = _service_display_name(node.service)
    declared = node.parent_cluster.value

    if node.service in _ENTRY_POINT_SERVICES:
        return (
            f"Node '{node.id}' is an entry point ({service_name}) but was "
            f"placed in parent_cluster='{declared}'. Entry points "
            f"(Client/User, Users) MUST be in 'User / Consumer'."
        )

    if node.service == GcpServiceEnum.ON_PREM_SERVER:
        return (
            f"Node '{node.id}' is an on-premises server but was placed in "
            f"parent_cluster='{declared}'. On-prem systems MUST be in "
            f"'Customer Environment'."
        )

    if node.service in _NON_GCP_SERVICES:
        # Self-managed DBs (Postgres/MySQL/Mongo) and GENERIC — valid in
        # either Customer Environment or Third-Party Services depending
        # on who hosts them.
        valid = _format_zone_list(expected_zones_for(node.service))
        return (
            f"Node '{node.id}' (service={service_name}) cannot be in "
            f"'{declared}'. Self-managed databases or generic non-GCP "
            f'systems must be in one of: {valid}. If the service is '
            f'actually managed by Google Cloud, use the GCP-specific '
            f'enum value (e.g. Cloud SQL instead of PostgreSQL).'
        )

    # GCP-native service in a non-GCP zone — the most common mistake
    return (
        f"Node '{node.id}' uses GCP service '{service_name}' but was "
        f"placed in parent_cluster='{declared}'. ALL GCP products MUST "
        f"be in 'Google Cloud Platform', regardless of who administers "
        f'them (Apigee, Cloud Build, BigQuery, etc. remain GCP even '
        f'when customer-managed).'
    )


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

    # ------------------------------------------------------------------
    # AUD-05: generic labels
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # AUD-06: label repeats product name
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # AUD-07: external node label lacks system name or version
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # AUD-10: IAM as node (policy layer, not runtime component)
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # AUD-19: parent_cluster ↔ service coherence
    #
    # Consolidates the old AUD-08 (GCP in non-GCP cluster) and AUD-09
    # (entry point in third-party cluster) into a single structural
    # check. Uses `expected_zones_for()` from the model layer as the
    # single source of truth for what zones a given service may inhabit.
    #
    # Most mismatches become structurally impossible thanks to
    # constrained decoding (Gemini cannot emit an invalid ClusterZone
    # value). This check catches the residual case where the value is
    # syntactically valid but semantically wrong -- e.g. a Vertex AI
    # node declared in 'Customer Environment'.
    # ------------------------------------------------------------------
    for n in nodes:
        valid_zones = expected_zones_for(n.service)
        if n.parent_cluster not in valid_zones:
            failures.append(
                AuditFailure(
                    'AUD-19',
                    'BLOCKER',
                    _aud19_defect_message(n),
                )
            )

    # ------------------------------------------------------------------
    # AUD-11: edge integrity (labels, endpoints, connectivity)
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # AUD-14: minimum component checklist (entry + compute + data)
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # AUD-02 / AUD-03: stack ↔ diagram consistency
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # Description-driven checks (AUD-01, AUD-15, AUD-16, AUD-17, AUD-18)
    # ------------------------------------------------------------------
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
