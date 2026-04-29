"""Unit tests for ``app.tools.sow.generate_architecture_diagram``.

Focus on the pure D2-source builder (``_build_d2_code``) and helper functions
(escape, quoting, normalization, node rendering). The public tool's error
paths are also exercised with mocks in place of the D2 subprocess — the
happy path lives in tests/integration because it requires the ``d2`` binary.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.tools.sow import generate_architecture_diagram as gad
from app.tools.sow.generate_architecture_diagram import (
    generate_architecture_diagram,
)
from app.tools.sow._diagram_models import (
    ArchitectureEdge,
    ArchitectureNode,
    ClusterZone,
    GcpServiceEnum,
)


class TestEscapeD2:
    def test_double_quote_escaped(self):
        assert gad._escape_d2('say "hi"') == 'say \\"hi\\"'

    def test_backslash_escaped(self):
        assert gad._escape_d2('a\\b') == 'a\\\\b'

    def test_newline_escaped(self):
        assert gad._escape_d2('line1\nline2') == 'line1\\nline2'

    def test_plain_text_untouched(self):
        assert gad._escape_d2('hello world') == 'hello world'

    def test_backslash_escaped_before_quote(self):
        """Escape order matters — backslashes first."""
        assert gad._escape_d2('a"b\\c') == 'a\\"b\\\\c'


class TestD2Key:
    @pytest.mark.parametrize(
        'name', ['Compute_and_Orchestration', 'simple_name', 'a_b_c']
    )
    def test_plain_names_not_quoted(self, name):
        assert gad._d2_key(name) == name

    @pytest.mark.parametrize(
        'name',
        [
            'AI / ML',  # slash
            'Observability (ext)',  # parens
            'Thing with "quotes"',  # quote
            'Path/to/x',  # slash
            'colon:y',  # colon
            'semi;z',  # semicolon
            'brace{x}',  # curly
            'dot.y',  # dot
            'hash#y',  # hash
        ],
    )
    def test_special_chars_force_quoting(self, name):
        out = gad._d2_key(name)
        assert out.startswith('"') and out.endswith('"')

    def test_ampersand_not_quoted(self):
        """Ampersand is NOT in the NEEDS_QUOTING set — documents current behavior."""
        assert gad._d2_key('Data & Storage') == 'Data & Storage'


class TestNormalizeSub:
    def test_none_returns_none(self):
        assert gad._normalize_sub(None) is None

    def test_empty_string_returns_none(self):
        assert gad._normalize_sub('') is None

    def test_whitespace_returns_none(self):
        assert gad._normalize_sub('   ') is None

    def test_non_empty_stripped(self):
        assert gad._normalize_sub('  AI / ML  ') == 'AI / ML'


class TestRenderD2Node:
    def test_renders_basic_node(self):
        node = ArchitectureNode(
            id='api', label='API',
            service=GcpServiceEnum.CLOUD_RUN,
            parent_cluster=ClusterZone.GOOGLE_CLOUD,
        )
        with patch(
            'app.tools.sow.generate_architecture_diagram.get_d2_icon_path',
            return_value=None,
        ):
            lines = gad._render_d2_node(node)
        assert any('api: "API"' in l for l in lines)
        assert lines[0].startswith('api')
        assert lines[-1].strip() == '}'

    def test_renders_icon_for_user_service(self):
        """USERS / CLIENT now render via the User.svg icon — no shape
        override and no fixed dimensions, so the label can grow naturally
        inside the default D2 rectangle.
        """
        node = ArchitectureNode(
            id='u', label='User',
            service=GcpServiceEnum.USERS,
            parent_cluster=ClusterZone.USER_CONSUMER,
        )
        with patch(
            'app.tools.sow.generate_architecture_diagram.get_d2_icon_path',
            return_value='/icons/User.svg',
        ):
            lines = gad._render_d2_node(node)
        joined = '\n'.join(lines)
        assert 'icon: /icons/User.svg' in joined
        assert 'shape:' not in joined
        assert 'width:' not in joined
        assert 'height:' not in joined

    def test_renders_icon_for_gcp_service(self):
        """GCP services render with ``icon:`` only — the previous ``shape:
        image`` plus ``width/height`` block was removed because it pushed
        labels outside the bounding box and broke ELK's container sizing.
        """
        node = ArchitectureNode(
            id='bq', label='Warehouse',
            service=GcpServiceEnum.BIGQUERY,
            parent_cluster=ClusterZone.GOOGLE_CLOUD,
        )
        with patch(
            'app.tools.sow.generate_architecture_diagram.get_d2_icon_path',
            return_value='/icons/BigQuery.svg',
        ):
            lines = gad._render_d2_node(node)
        joined = '\n'.join(lines)
        assert 'icon: /icons/BigQuery.svg' in joined
        assert 'shape:' not in joined
        # Neutral palette is applied uniformly so zone color carries the signal.
        assert 'style.stroke' in joined
        assert 'style.font-color' in joined

    def test_indentation_applied(self):
        node = ArchitectureNode(
            id='api', label='API',
            service=GcpServiceEnum.CLOUD_RUN,
            parent_cluster=ClusterZone.GOOGLE_CLOUD,
        )
        with patch(
            'app.tools.sow.generate_architecture_diagram.get_d2_icon_path',
            return_value=None,
        ):
            lines = gad._render_d2_node(node, indent='    ')
        assert lines[0].startswith('    api:')
        assert lines[-1].startswith('    }')

    def test_escapes_label_with_special_chars(self):
        node = ArchitectureNode(
            id='x', label='say "hi"',
            service=GcpServiceEnum.CLOUD_RUN,
            parent_cluster=ClusterZone.GOOGLE_CLOUD,
        )
        with patch(
            'app.tools.sow.generate_architecture_diagram.get_d2_icon_path',
            return_value=None,
        ):
            lines = gad._render_d2_node(node)
        assert 'say \\"hi\\"' in lines[0]


class TestBuildD2Code:
    def _valid_spec(self):
        nodes = [
            ArchitectureNode(
                id='user', label='User', service=GcpServiceEnum.USERS,
                parent_cluster=ClusterZone.USER_CONSUMER,
            ),
            ArchitectureNode(
                id='api', label='Credit API', service=GcpServiceEnum.CLOUD_RUN,
                parent_cluster=ClusterZone.GOOGLE_CLOUD,
            ),
            ArchitectureNode(
                id='bq', label='Warehouse', service=GcpServiceEnum.BIGQUERY,
                parent_cluster=ClusterZone.GOOGLE_CLOUD,
            ),
        ]
        edges = [
            ArchitectureEdge(source_id='user', target_id='api', label='HTTPS'),
            ArchitectureEdge(source_id='api', target_id='bq', label='SQL'),
        ]
        return nodes, edges

    def test_basic_structure(self):
        nodes, edges = self._valid_spec()
        with patch(
            'app.tools.sow.generate_architecture_diagram.get_d2_icon_path',
            return_value=None,
        ):
            out = gad._build_d2_code(nodes, edges, 'LR', 'My Arch')

        assert 'vars: { d2-config: { layout-engine: elk } }' in out
        assert 'direction: right' in out
        assert '_title: "My Arch"' in out
        # Zone containers declared
        assert 'user_consumer:' in out
        assert 'gcp:' in out
        # Edges rendered with full zone.node paths
        assert 'user_consumer.user -> gcp.api: "HTTPS"' in out
        assert 'gcp.api -> gcp.bq: "SQL"' in out

    def test_direction_tb_maps_to_down(self):
        nodes, edges = self._valid_spec()
        with patch(
            'app.tools.sow.generate_architecture_diagram.get_d2_icon_path',
            return_value=None,
        ):
            out = gad._build_d2_code(nodes, edges, 'TB', '')
        assert 'direction: down' in out

    def test_unknown_direction_falls_back_to_right(self):
        nodes, edges = self._valid_spec()
        with patch(
            'app.tools.sow.generate_architecture_diagram.get_d2_icon_path',
            return_value=None,
        ):
            out = gad._build_d2_code(nodes, edges, 'weird', '')
        assert 'direction: right' in out

    def test_empty_title_omits_title_block(self):
        nodes, edges = self._valid_spec()
        with patch(
            'app.tools.sow.generate_architecture_diagram.get_d2_icon_path',
            return_value=None,
        ):
            out = gad._build_d2_code(nodes, edges, 'LR', '')
        assert '_title' not in out

    def test_edge_without_label_emits_unlabeled_arrow(self):
        nodes, edges = self._valid_spec()
        edges[0].label = None
        with patch(
            'app.tools.sow.generate_architecture_diagram.get_d2_icon_path',
            return_value=None,
        ):
            out = gad._build_d2_code(nodes, edges, 'LR', '')
        lines = out.splitlines()
        arrow_lines = [l for l in lines if ' -> ' in l]
        # At least one arrow line has no trailing label
        assert any(':' not in l.split('->')[-1] for l in arrow_lines)

    def test_subcluster_nested_correctly(self):
        """sub_cluster nodes are rendered inside a nested box."""
        nodes = [
            ArchitectureNode(
                id='user', label='User', service=GcpServiceEnum.USERS,
                parent_cluster=ClusterZone.USER_CONSUMER,
            ),
            ArchitectureNode(
                id='api', label='API', service=GcpServiceEnum.CLOUD_RUN,
                parent_cluster=ClusterZone.GOOGLE_CLOUD,
                sub_cluster='Compute',
            ),
            ArchitectureNode(
                id='bq', label='Warehouse', service=GcpServiceEnum.BIGQUERY,
                parent_cluster=ClusterZone.GOOGLE_CLOUD,
                sub_cluster='Data',
            ),
        ]
        edges = [
            ArchitectureEdge(source_id='user', target_id='api', label='HTTPS'),
            ArchitectureEdge(source_id='api', target_id='bq', label='SQL'),
        ]
        with patch(
            'app.tools.sow.generate_architecture_diagram.get_d2_icon_path',
            return_value=None,
        ):
            out = gad._build_d2_code(nodes, edges, 'LR', '')

        # Sub-cluster containers present
        assert 'Compute: "Compute"' in out
        assert 'Data: "Data"' in out
        # Edge path reflects sub-cluster nesting
        assert 'gcp.Compute.api -> gcp.Data.bq: "SQL"' in out

    def test_edge_with_unknown_id_skipped(self):
        nodes, edges = self._valid_spec()
        edges.append(
            ArchitectureEdge(
                source_id='ghost', target_id='api', label='bad'
            )
        )
        with patch(
            'app.tools.sow.generate_architecture_diagram.get_d2_icon_path',
            return_value=None,
        ):
            out = gad._build_d2_code(nodes, edges, 'LR', '')
        # 'ghost' must not appear in any edge path
        assert 'ghost' not in out

    def test_zone_rendered_only_when_populated(self):
        """Third-party zone absent → no third_party container emitted."""
        nodes, edges = self._valid_spec()
        with patch(
            'app.tools.sow.generate_architecture_diagram.get_d2_icon_path',
            return_value=None,
        ):
            out = gad._build_d2_code(nodes, edges, 'LR', '')
        assert 'third_party:' not in out

    def test_special_chars_in_subcluster_name_quoted(self):
        nodes = [
            ArchitectureNode(
                id='user', label='User', service=GcpServiceEnum.USERS,
                parent_cluster=ClusterZone.USER_CONSUMER,
            ),
            ArchitectureNode(
                id='api', label='API', service=GcpServiceEnum.CLOUD_RUN,
                parent_cluster=ClusterZone.GOOGLE_CLOUD,
                sub_cluster='AI / ML',
            ),
            ArchitectureNode(
                id='bq', label='Warehouse', service=GcpServiceEnum.BIGQUERY,
                parent_cluster=ClusterZone.GOOGLE_CLOUD,
            ),
        ]
        edges = [
            ArchitectureEdge(source_id='user', target_id='api', label='HTTPS'),
            ArchitectureEdge(source_id='api', target_id='bq', label='SQL'),
        ]
        with patch(
            'app.tools.sow.generate_architecture_diagram.get_d2_icon_path',
            return_value=None,
        ):
            out = gad._build_d2_code(nodes, edges, 'LR', '')
        # Key "AI / ML" needs quoting because of the slash
        assert '"AI / ML":' in out


class TestGenerateArchitectureDiagramErrorPaths:
    """Public-tool error paths that don't require the d2 binary."""

    @staticmethod
    def _call_kwargs(spec):
        """Map the shared fixture's 'description' key to the tool's param name."""
        return {
            'nodes': spec['nodes'],
            'edges': spec['edges'],
            'architecture_description': spec['description'],
            'technology_stack': spec['technology_stack'],
        }

    async def test_d2_missing_returns_tool_error(
        self, architecture_spec, mock_tool_context
    ):
        with patch.object(gad, '_D2_AVAILABLE', False):
            result = await generate_architecture_diagram(
                title='x',
                tool_context=mock_tool_context,
                **self._call_kwargs(architecture_spec),
            )
        assert result['status'] == 'error'
        assert 'D2' in result['error']

    async def test_rsvg_missing_returns_tool_error(
        self, architecture_spec, mock_tool_context
    ):
        with patch.object(gad, '_D2_AVAILABLE', True), patch.object(
            gad, '_RSVG_AVAILABLE', False
        ):
            result = await generate_architecture_diagram(
                title='x',
                tool_context=mock_tool_context,
                **self._call_kwargs(architecture_spec),
            )
        assert result['status'] == 'error'
        assert 'rsvg-convert' in result['error']

    async def test_invalid_node_dict_returns_tool_error(
        self, mock_tool_context
    ):
        with patch.object(gad, '_D2_AVAILABLE', True), patch.object(
            gad, '_RSVG_AVAILABLE', True
        ):
            result = await generate_architecture_diagram(
                title='x',
                nodes=[{'id': 'x'}],  # missing required fields
                edges=[],
                architecture_description='',
                technology_stack=[],
                tool_context=mock_tool_context,
            )
        assert result['status'] == 'error'
        assert 'nós' in result['error'].lower() or 'nodes' in result['error'].lower()

    async def test_audit_blocker_returns_retryable_error(
        self, architecture_spec, mock_tool_context
    ):
        architecture_spec['nodes'][1].label = 'Backend'  # AUD-05 blocker
        with patch.object(gad, '_D2_AVAILABLE', True), patch.object(
            gad, '_RSVG_AVAILABLE', True
        ):
            result = await generate_architecture_diagram(
                title='x',
                tool_context=mock_tool_context,
                **self._call_kwargs(architecture_spec),
            )
        assert result['status'] == 'error'
        assert result['retryable'] is True
        assert 'AUD-05' in result['error']
