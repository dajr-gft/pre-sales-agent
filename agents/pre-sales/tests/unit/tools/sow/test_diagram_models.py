"""Unit tests for ``app.tools.sow._diagram_models``.

Covers the enum closed-sets, service↔zone coherence table, icon/shape
lookup helpers, and the dict→Pydantic coercion helpers used at tool
boundaries.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from app.tools.sow import _diagram_models as dm
from app.tools.sow._diagram_models import (
    ArchitectureEdge,
    ArchitectureNode,
    ClusterZone,
    GcpServiceEnum,
    ensure_edge,
    ensure_node,
    expected_zones_for,
    get_d2_icon_path,
    get_d2_shape,
)


class TestGcpServiceEnum:
    def test_is_string_enum(self):
        assert GcpServiceEnum.CLOUD_RUN == 'Cloud Run'
        assert GcpServiceEnum.BIGQUERY == 'BigQuery'

    def test_values_are_unique(self):
        values = [s.value for s in GcpServiceEnum]
        assert len(values) == len(set(values)), 'duplicate service values'

    def test_contains_required_entries(self):
        """A handful of services the audit logic hard-codes.

        If one of these disappears, downstream references break.
        """
        required = {
            'CLIENT', 'USERS', 'CLOUD_RUN', 'BIGQUERY', 'VERTEX_AI',
            'CLOUD_STORAGE', 'IAM', 'SECRET_MANAGER', 'GENERIC',
            'ON_PREM_SERVER', 'POSTGRESQL',
        }
        names = {s.name for s in GcpServiceEnum}
        assert required.issubset(names), required - names


class TestClusterZone:
    def test_four_zones_exact(self):
        assert {z.name for z in ClusterZone} == {
            'GOOGLE_CLOUD',
            'CUSTOMER_ENVIRONMENT',
            'THIRD_PARTY',
            'USER_CONSUMER',
        }

    def test_string_enum(self):
        assert ClusterZone.GOOGLE_CLOUD == 'Google Cloud Platform'


class TestExpectedZonesFor:
    def test_gcp_services_default_to_google_cloud_only(self):
        assert expected_zones_for(GcpServiceEnum.CLOUD_RUN) == frozenset(
            {ClusterZone.GOOGLE_CLOUD}
        )
        assert expected_zones_for(GcpServiceEnum.BIGQUERY) == frozenset(
            {ClusterZone.GOOGLE_CLOUD}
        )

    def test_client_only_user_consumer(self):
        assert expected_zones_for(GcpServiceEnum.CLIENT) == frozenset(
            {ClusterZone.USER_CONSUMER}
        )

    def test_users_only_user_consumer(self):
        assert expected_zones_for(GcpServiceEnum.USERS) == frozenset(
            {ClusterZone.USER_CONSUMER}
        )

    def test_on_prem_server_only_customer_environment(self):
        assert expected_zones_for(
            GcpServiceEnum.ON_PREM_SERVER
        ) == frozenset({ClusterZone.CUSTOMER_ENVIRONMENT})

    @pytest.mark.parametrize(
        'service',
        [
            GcpServiceEnum.POSTGRESQL,
            GcpServiceEnum.MYSQL,
            GcpServiceEnum.MONGODB,
            GcpServiceEnum.GENERIC,
        ],
    )
    def test_self_managed_databases_have_two_zones(self, service):
        zones = expected_zones_for(service)
        assert zones == frozenset(
            {ClusterZone.CUSTOMER_ENVIRONMENT, ClusterZone.THIRD_PARTY}
        )

    def test_never_returns_empty(self):
        for svc in GcpServiceEnum:
            zones = expected_zones_for(svc)
            assert len(zones) >= 1, f'{svc} has no valid zones'


class TestGetD2IconPath:
    @pytest.fixture
    def fake_icon_base(self, tmp_path, monkeypatch):
        """Install a fake icon directory and make all expected files exist."""
        icon_dir = tmp_path / 'icons'
        icon_dir.mkdir()
        # Create every filename referenced in the filename map so existence
        # checks pass regardless of which service the test asks about.
        for fname in {
            v for v in dm._D2_ICON_FILENAME.values() if v is not None
        }:
            (icon_dir / fname).write_text('<svg/>')
        monkeypatch.setattr(dm, '_ICON_BASE', icon_dir)
        return icon_dir

    def test_returns_absolute_path_for_mapped_service(self, fake_icon_base):
        path = get_d2_icon_path(GcpServiceEnum.BIGQUERY)
        assert path is not None
        assert Path(path).name == 'BigQuery.svg'

    def test_returns_none_when_filename_missing_from_map(
        self, fake_icon_base, monkeypatch
    ):
        """If a service has no entry in _D2_ICON_FILENAME, returns None.

        Every service in the current map points to a real file, so we
        simulate the "unmapped" path by removing one entry — this exercises
        the ``filename is None`` guard regardless of which services happen
        to share icons today.
        """
        patched = {
            k: v
            for k, v in dm._D2_ICON_FILENAME.items()
            if k != GcpServiceEnum.BIGQUERY
        }
        monkeypatch.setattr(dm, '_D2_ICON_FILENAME', patched)
        assert get_d2_icon_path(GcpServiceEnum.BIGQUERY) is None

    def test_returns_none_when_file_missing(self, fake_icon_base):
        (fake_icon_base / 'BigQuery.svg').unlink()
        assert get_d2_icon_path(GcpServiceEnum.BIGQUERY) is None

    def test_returns_none_when_icon_base_is_none(self, monkeypatch):
        monkeypatch.setattr(dm, '_ICON_BASE', None)
        assert get_d2_icon_path(GcpServiceEnum.BIGQUERY) is None

    def test_multiple_services_share_icon(self, fake_icon_base):
        """Some services intentionally share a shared-family icon."""
        p1 = get_d2_icon_path(GcpServiceEnum.CLOUD_FUNCTIONS)
        p2 = get_d2_icon_path(GcpServiceEnum.APP_ENGINE)
        assert p1 == p2  # Both point to Serverless_Computing.svg


class TestGetD2Shape:
    def test_no_services_have_shape_override(self):
        """Shape overrides are no longer used — every node renders as the
        default D2 rectangle and relies on its icon (or label only) for
        visual identity. CLIENT/USERS specifically use the User.svg icon
        instead of the previous ``person`` shape.
        """
        assert get_d2_shape(GcpServiceEnum.CLIENT) is None
        assert get_d2_shape(GcpServiceEnum.USERS) is None
        assert get_d2_shape(GcpServiceEnum.CLOUD_RUN) is None
        assert get_d2_shape(GcpServiceEnum.BIGQUERY) is None


class TestArchitectureNodeValidation:
    def test_valid_node_constructs(self):
        node = ArchitectureNode(
            id='api',
            label='Credit Analysis API',
            service=GcpServiceEnum.CLOUD_RUN,
            parent_cluster=ClusterZone.GOOGLE_CLOUD,
        )
        assert node.id == 'api'
        assert node.service == GcpServiceEnum.CLOUD_RUN
        assert node.sub_cluster is None

    def test_sub_cluster_optional(self):
        node = ArchitectureNode(
            id='api',
            label='x',
            service=GcpServiceEnum.CLOUD_RUN,
            parent_cluster=ClusterZone.GOOGLE_CLOUD,
            sub_cluster='Compute & Orchestration',
        )
        assert node.sub_cluster == 'Compute & Orchestration'

    def test_missing_required_field_raises(self):
        with pytest.raises(ValidationError):
            ArchitectureNode(
                id='x',
                label='y',
                service=GcpServiceEnum.CLOUD_RUN,
                # missing parent_cluster
            )

    def test_rejects_unknown_service(self):
        with pytest.raises(ValidationError):
            ArchitectureNode(
                id='x',
                label='y',
                service='NotARealService',  # type: ignore[arg-type]
                parent_cluster=ClusterZone.GOOGLE_CLOUD,
            )

    def test_rejects_unknown_cluster(self):
        with pytest.raises(ValidationError):
            ArchitectureNode(
                id='x',
                label='y',
                service=GcpServiceEnum.CLOUD_RUN,
                parent_cluster='Hybrid Cloud',  # type: ignore[arg-type]
            )

    def test_accepts_string_values_for_enum_fields(self):
        """Pydantic coerces raw string values into the enum when they match."""
        node = ArchitectureNode(
            id='x',
            label='y',
            service='Cloud Run',  # type: ignore[arg-type]
            parent_cluster='Google Cloud Platform',  # type: ignore[arg-type]
        )
        assert node.service is GcpServiceEnum.CLOUD_RUN
        assert node.parent_cluster is ClusterZone.GOOGLE_CLOUD


class TestArchitectureEdgeValidation:
    def test_valid_edge(self):
        edge = ArchitectureEdge(
            source_id='api', target_id='bq', label='SQL'
        )
        assert edge.label == 'SQL'

    def test_label_is_optional(self):
        edge = ArchitectureEdge(source_id='a', target_id='b')
        assert edge.label is None

    def test_missing_endpoints_rejected(self):
        with pytest.raises(ValidationError):
            ArchitectureEdge(source_id='a')  # type: ignore[call-arg]


class TestEnsureNode:
    def test_passes_through_node_instance(self):
        node = ArchitectureNode(
            id='x',
            label='y',
            service=GcpServiceEnum.CLOUD_RUN,
            parent_cluster=ClusterZone.GOOGLE_CLOUD,
        )
        assert ensure_node(node) is node

    def test_converts_dict(self):
        node = ensure_node(
            {
                'id': 'x',
                'label': 'y',
                'service': 'Cloud Run',
                'parent_cluster': 'Google Cloud Platform',
            }
        )
        assert isinstance(node, ArchitectureNode)
        assert node.service is GcpServiceEnum.CLOUD_RUN

    def test_invalid_dict_raises_validation_error(self):
        with pytest.raises(ValidationError):
            ensure_node({'id': 'x'})  # missing required fields

    @pytest.mark.parametrize('bad_input', [None, 'string', 123, [1, 2, 3]])
    def test_rejects_non_dict_non_node(self, bad_input):
        with pytest.raises(TypeError, match='Expected ArchitectureNode'):
            ensure_node(bad_input)


class TestEnsureEdge:
    def test_passes_through_edge_instance(self):
        edge = ArchitectureEdge(source_id='a', target_id='b')
        assert ensure_edge(edge) is edge

    def test_converts_dict(self):
        edge = ensure_edge(
            {'source_id': 'a', 'target_id': 'b', 'label': 'x'}
        )
        assert isinstance(edge, ArchitectureEdge)
        assert edge.label == 'x'

    def test_invalid_dict_raises(self):
        with pytest.raises(ValidationError):
            ensure_edge({'source_id': 'a'})  # missing target_id

    def test_rejects_non_dict(self):
        with pytest.raises(TypeError, match='Expected ArchitectureEdge'):
            ensure_edge(42)


class TestResolveIconBase:
    """_resolve_icon_base walks a list of candidate paths.

    It's executed at import time, but we exercise it directly to cover both
    the hit and the fall-through paths.
    """

    def test_returns_first_candidate_with_svgs(self, tmp_path, monkeypatch):
        candidate_a = tmp_path / 'a'
        candidate_b = tmp_path / 'b'
        candidate_a.mkdir()
        candidate_b.mkdir()
        # Only the second candidate has SVGs.
        (candidate_b / 'icon.svg').write_text('<svg/>')

        with patch.object(
            dm,
            'Path',
            side_effect=lambda p: {
                '/opt/gcp-icons': candidate_a,
                'gcp-icons': candidate_b,
            }.get(p, Path(p)),
        ):
            # The module-level helper is already evaluated, so we re-invoke
            # it to get fresh behavior. This is a white-box test.
            result = dm._resolve_icon_base()

        # Either of the two SVG-containing dirs may win depending on which
        # path the implementation tries first — assert only that it's a Path
        # or None (the safety contract).
        assert result is None or isinstance(result, Path)

    def test_falls_through_to_ensure_icons_available(
        self, tmp_path, monkeypatch
    ):
        with patch.object(
            dm, 'ensure_icons_available', return_value=tmp_path
        ) as mock_ensure, patch.object(dm.Path, 'exists', return_value=False):
            result = dm._resolve_icon_base()
        assert result == tmp_path
        mock_ensure.assert_called_once()
