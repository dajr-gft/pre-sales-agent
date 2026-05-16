"""Unit tests for the ``revision_agent`` instruction provider + wiring.

The revision agent runs with ``include_contents='none'``, so it cannot
see the staged SOW or the latest ValidationReport via conversation
history. The instruction provider is the ONLY thing standing between
the LLM and an empty prompt — these tests pin every branch of that
provider plus the transfer-disallow flags that keep the QualityLoop in
control of the round budget.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.sub_agents.revision.agent import (
    _is_present,
    _make_revision_instruction_provider,
    _serialize_state_value,
    revision_agent,
)
from app.sub_agents.revision.log_tools import REVISION_LOG_STATE_KEY
from app.sub_agents.validation.schema import (
    STATE_SOW,
    STATE_VALIDATION_RESULT,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx(state: dict) -> SimpleNamespace:
    """ReadonlyContext stand-in — the provider only touches ``state``."""
    return SimpleNamespace(state=state)


_SKILL_BODY = '<sow-revision SKILL.md body>'


# ---------------------------------------------------------------------------
# _is_present — same semantics as _section_agent's helper
# ---------------------------------------------------------------------------


class TestIsPresent:
    @pytest.mark.parametrize(
        'value',
        [
            None,
            {},
            [],
            '',
            (),
            set(),
        ],
    )
    def test_empty_values_are_missing(self, value):
        assert _is_present(value) is False

    @pytest.mark.parametrize(
        'value',
        [
            {'foo': 'bar'},
            ['x'],
            'staged',
            0,
            False,
            ('a',),
        ],
    )
    def test_substantive_values_are_present(self, value):
        # Note: integers / booleans are present even when "falsy" — the
        # provider only cares about whether the structural payload was
        # written, not whether it evaluates truthy.
        assert _is_present(value) is True


# ---------------------------------------------------------------------------
# _serialize_state_value
# ---------------------------------------------------------------------------


class TestSerializeStateValue:
    def test_round_trips_simple_dict_as_compact_json(self):
        s = _serialize_state_value({'a': 1, 'b': 'x'})
        # Compact separators — no spaces.
        assert s == '{"a":1,"b":"x"}'

    def test_preserves_non_ascii_characters(self):
        s = _serialize_state_value({'customer': 'Inversão'})
        assert 'Inversão' in s

    def test_falls_back_to_repr_for_unserializable(self):
        class Weird:
            def __repr__(self) -> str:
                return '<Weird>'

        s = _serialize_state_value(Weird())
        assert s == '<Weird>'


# ---------------------------------------------------------------------------
# Provider — MISSING branch
# ---------------------------------------------------------------------------


class TestProviderMissingBranch:
    def test_empty_state_lists_both_required_keys(self):
        provider = _make_revision_instruction_provider(_SKILL_BODY)
        result = provider(_ctx({}))

        assert _SKILL_BODY in result
        assert 'MISSING' in result
        # Both required inputs named so the LLM can produce a precise
        # diagnostic. The state-key literals are quoted to make telemetry
        # parseable.
        assert STATE_SOW in result
        assert STATE_VALIDATION_RESULT in result
        # The footer must explicitly forbid the side-effect tools so the
        # LLM cannot silently push an invented patch.
        assert 'stage_sow' in result
        assert 'record_revision_log_entries' in result
        # And it must forbid invention.
        assert 'fabricate' in result.lower() or 'invent' in result.lower()

    def test_only_sow_present_still_flags_report_missing(self):
        provider = _make_revision_instruction_provider(_SKILL_BODY)
        result = provider(_ctx({STATE_SOW: {'project_title': 'P'}}))

        assert 'MISSING' in result
        assert STATE_VALIDATION_RESULT in result
        # The present-branch payload must NOT have leaked in.
        assert '<staged_sow>' not in result

    def test_only_report_present_still_flags_sow_missing(self):
        provider = _make_revision_instruction_provider(_SKILL_BODY)
        result = provider(
            _ctx({STATE_VALIDATION_RESULT: {'overall_status': 'blocked'}})
        )

        assert 'MISSING' in result
        assert STATE_SOW in result
        assert '<validation_report>' not in result

    def test_empty_sow_dict_treated_as_missing(self):
        """An empty dict in state is structurally indistinguishable from
        a stub upstream write; the agent has no payload to patch either
        way, so it MUST fall through to the STOP path."""
        provider = _make_revision_instruction_provider(_SKILL_BODY)
        result = provider(
            _ctx(
                {
                    STATE_SOW: {},
                    STATE_VALIDATION_RESULT: {'overall_status': 'blocked'},
                }
            )
        )

        assert 'MISSING' in result
        assert STATE_SOW in result


# ---------------------------------------------------------------------------
# Provider — PRESENT branch
# ---------------------------------------------------------------------------


class TestProviderPresentBranch:
    def _state_with_inputs(
        self,
        *,
        sow: dict | None = None,
        report: dict | None = None,
        revision_log: list | None = None,
    ) -> dict:
        out: dict = {}
        out[STATE_SOW] = sow or {
            'project_title': 'Data Platform',
            'functional_requirements': [{'number': 'FR-01', 'description': 'x'}],
        }
        out[STATE_VALIDATION_RESULT] = report or {
            'overall_status': 'blocked',
            'findings': [
                {
                    'id': 'coverage-001',
                    'skill': 'coverage',
                    'category': 'manifest_item_uncovered',
                    'severity': 'BLOCKER',
                    'fields': ['functional_requirements'],
                    'evidence': '...',
                    'recommendation': 'Add the missing FR.',
                }
            ],
        }
        if revision_log is not None:
            out[REVISION_LOG_STATE_KEY] = revision_log
        return out

    def test_emits_all_three_xml_blocks_in_order(self):
        provider = _make_revision_instruction_provider(_SKILL_BODY)
        state = self._state_with_inputs(revision_log=[])
        result = provider(_ctx(state))

        # Order matters less than presence — but the three blocks must
        # exist and the LLM must see them as a single contiguous packet.
        assert '<staged_sow>' in result
        assert '</staged_sow>' in result
        assert '<validation_report>' in result
        assert '</validation_report>' in result
        assert '<revision_log>' in result
        assert '</revision_log>' in result
        # No MISSING footer when the inputs are present.
        assert 'MISSING' not in result.split('<staged_sow>', 1)[1]

    def test_serializes_sow_into_staged_sow_block(self):
        provider = _make_revision_instruction_provider(_SKILL_BODY)
        state = self._state_with_inputs(
            sow={
                'project_title': 'Data Platform',
                'functional_requirements': [
                    {'number': 'FR-01', 'description': 'Ingest data.'},
                ],
            }
        )
        result = provider(_ctx(state))

        # Compact JSON — no whitespace between tokens.
        assert '"project_title":"Data Platform"' in result
        assert '"FR-01"' in result
        assert '"Ingest data."' in result

    def test_serializes_report_into_validation_report_block(self):
        provider = _make_revision_instruction_provider(_SKILL_BODY)
        state = self._state_with_inputs(
            report={
                'overall_status': 'blocked',
                'blocker_count': 2,
                'findings': [{'id': 'x-1', 'severity': 'MAJOR'}],
            }
        )
        result = provider(_ctx(state))

        assert '"overall_status":"blocked"' in result
        assert '"blocker_count":2' in result
        assert '"x-1"' in result

    def test_revision_log_defaults_to_empty_list_when_absent(self):
        """Round 1 of the QualityLoop starts with no revision log key
        in state — that must NOT trigger the MISSING branch (the log is
        OPTIONAL, only sow + report are required)."""
        provider = _make_revision_instruction_provider(_SKILL_BODY)
        state = self._state_with_inputs()
        # Explicitly leave REVISION_LOG_STATE_KEY out.
        state.pop(REVISION_LOG_STATE_KEY, None)
        result = provider(_ctx(state))

        assert 'MISSING' not in result
        assert '<revision_log>' in result
        assert '[]' in result

    def test_present_footer_references_anti_regeneration_contracts(self):
        provider = _make_revision_instruction_provider(_SKILL_BODY)
        result = provider(_ctx(self._state_with_inputs()))

        # The footer must remind the LLM of the three contracts so the
        # field-scoped patch discipline survives the prompt assembly.
        assert 'finding.fields' in result
        assert 'stage_sow' in result
        assert 'record_revision_log_entries' in result

    def test_skill_body_kept_byte_for_byte_above_footer(self):
        """The SKILL.md body must come first; the footer appends only."""
        body = 'SKILL-MARKER-XYZ\nLine two of the skill.'
        provider = _make_revision_instruction_provider(body)
        result = provider(_ctx(self._state_with_inputs()))

        assert result.startswith(body)


# ---------------------------------------------------------------------------
# Agent wiring — F-01 + F-03
# ---------------------------------------------------------------------------


class TestRevisionAgentWiring:
    """Sanity checks against the real ``revision_agent`` instance."""

    def test_instruction_is_a_provider_not_a_static_string(self):
        """Static instruction was the F-01 BLOCKER — pinning callable."""
        assert callable(revision_agent.instruction), (
            'revision_agent must use an instruction provider so the staged '
            'SOW + validation report are injected from state on every turn.'
        )

    def test_include_contents_is_none(self):
        # Confirms context isolation is preserved post-F-01: provider
        # carries everything the LLM needs; parent history would only
        # bias the patcher toward fields outside finding.fields.
        assert revision_agent.include_contents == 'none'

    def test_disallow_transfer_to_parent(self):
        # F-03: the loop owns the round budget; revision must not
        # escalate to the QualityLoopAgent parent.
        assert revision_agent.disallow_transfer_to_parent is True

    def test_disallow_transfer_to_peers(self):
        # F-03: the revision agent has no legitimate peer to talk to;
        # forbidding peer transfer keeps the loop's branching authoritative.
        assert revision_agent.disallow_transfer_to_peers is True

    def test_tools_are_only_load_reference_stage_and_log(self):
        tool_names = {
            getattr(t, '__name__', getattr(t, 'name', repr(t)))
            for t in revision_agent.tools
        }
        assert tool_names == {
            'load_sow_reference',
            'stage_sow',
            'record_revision_log_entries',
        }

    def test_has_no_output_schema(self):
        """Avoids the known ``output_schema + tools`` infinite-loop bug
        documented in the ADK reference."""
        assert getattr(revision_agent, 'output_schema', None) is None
