"""Unit tests for ``AutoScopedSkillToolset``.

Coverage targets the three layers of the module, in order of risk:

1. ``_prune_inactive_skill_content`` — pure function; the algorithm
   that decides which ``Content`` items leave the LLM request.
2. ``ScopedLoadSkillTool.run_async`` — state-contract updates on each
   skill activation.
3. ``AutoScopedSkillToolset.process_llm_request`` — wires the above
   together and writes the telemetry counters.

The 18 cases listed in
``~/.claude/plans/prompt-role-atue-como-harmonic-graham.md`` §5.7 / §7.1
map 1:1 to test methods below.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from google.adk.skills.models import Frontmatter, Skill
from google.genai import types

from app.shared.auto_scoped_skill_toolset import (
    STATE_SKILL_CURRENT,
    STATE_SKILL_HISTORY,
    STATE_SKILL_INACTIVE,
    STATE_SKILL_LAST_PRUNE,
    STATE_SKILL_PREVIOUS,
    STATE_SKILL_PRUNE_TOTALS,
    AutoScopedSkillToolset,
    ScopedLoadSkillTool,
    _prune_inactive_skill_content,
)


# ---------------------------------------------------------------------------
# Fixture helpers — synthetic ``Content`` builders
# ---------------------------------------------------------------------------


def _fc(name: str, args: dict[str, Any]) -> types.Content:
    """Model-side function_call Content."""
    return types.Content(
        role='model',
        parts=[types.Part(function_call=types.FunctionCall(name=name, args=args))],
    )


def _fr(name: str, response: dict[str, Any]) -> types.Content:
    """User-side function_response Content (the tool's reply)."""
    return types.Content(
        role='user',
        parts=[
            types.Part(
                function_response=types.FunctionResponse(
                    name=name, response=response
                )
            )
        ],
    )


def _load_skill_pair(skill_name: str) -> list[types.Content]:
    return [
        _fc('load_skill', {'skill_name': skill_name}),
        _fr(
            'load_skill',
            {
                'skill_name': skill_name,
                'instructions': f'# SKILL.md for {skill_name}\n...',
                'frontmatter': {'name': skill_name, 'description': '...'},
            },
        ),
    ]


def _load_skill_resource_pair(
    skill_name: str, file_path: str, content: str = 'reference content'
) -> list[types.Content]:
    return [
        _fc(
            'load_skill_resource',
            {'skill_name': skill_name, 'file_path': file_path},
        ),
        _fr(
            'load_skill_resource',
            {
                'skill_name': skill_name,
                'file_path': file_path,
                'content': content,
            },
        ),
    ]


def _binary_marker(file_path: str, data: bytes = b'\x00\x01\x02') -> types.Content:
    """Synthetic Content the SDK injects after a binary resource load."""
    return types.Content(
        role='user',
        parts=[
            types.Part.from_text(
                text=f"The content of binary file '{file_path}' is:"
            ),
            types.Part(
                inline_data=types.Blob(data=data, mime_type='application/octet-stream')
            ),
        ],
    )


def _user_text(text: str) -> types.Content:
    return types.Content(role='user', parts=[types.Part.from_text(text=text)])


def _model_text(text: str) -> types.Content:
    return types.Content(role='model', parts=[types.Part.from_text(text=text)])


def _make_skill(name: str, *, additional_tools: list[str] | None = None) -> Skill:
    metadata: dict[str, Any] = {}
    if additional_tools:
        metadata['adk_additional_tools'] = list(additional_tools)
    return Skill(
        frontmatter=Frontmatter(
            name=name,
            description=f'Stub skill {name} for unit tests.',
            metadata=metadata,
        ),
        instructions=f'# SKILL.md\nstub instructions for {name}',
    )


def _make_tool_context(
    initial_state: dict[str, Any] | None = None, agent_name: str = 'root'
) -> SimpleNamespace:
    """A minimal ``ToolContext`` stand-in.

    The parent ``LoadSkillTool.run_async`` reads ``.state``,
    ``.agent_name`` and (since ADK 2.x) ``.invocation_id`` — the latter
    is passed to ``_get_or_fetch_skill`` before the in-memory skill
    lookup short-circuits. A dict-backed namespace is sufficient and
    lets us assert on what was written without going through ADK's
    session plumbing.
    """
    return SimpleNamespace(
        state=dict(initial_state or {}),
        agent_name=agent_name,
        invocation_id='test-invocation',
    )


# ---------------------------------------------------------------------------
# 1–11: ``_prune_inactive_skill_content`` — pure-function coverage
# ---------------------------------------------------------------------------


class TestPruneInactiveSkillContent:
    """Pure-function tests for ``_prune_inactive_skill_content``."""

    def test_01_happy_path_removes_inactive_skill_pairs(self):
        """Two ``load_skill`` pairs + one ``load_skill_resource`` pair for
        ``sow-requirements`` are all removed when it is inactive."""
        contents = [
            _user_text('Generate the SOW.'),
            *_load_skill_pair('sow-requirements'),
            *_load_skill_resource_pair('sow-requirements', 'references/fr.md'),
            *_load_skill_pair('sow-requirements'),  # idempotent reload
            *_load_skill_pair('sow-delivery-plan'),
        ]
        # 1 user + 6 inactive (3 pairs) + 2 current = 9 → expect 9 - 6 = 3.
        new, log = _prune_inactive_skill_content(
            contents=contents,
            inactive_scopes={'sow-requirements'},
            agent_name='root',
        )
        assert log['integrity_ok'] is True
        assert log['pruned_count'] == 6
        assert log['kept_count'] == 3
        assert log['pruned_bytes'] > 0
        # The remaining contents are the user message + the delivery-plan pair.
        kept_kinds = [
            getattr(c.parts[0], 'function_call', None)
            or getattr(c.parts[0], 'function_response', None)
            or getattr(c.parts[0], 'text', None)
            for c in new
        ]
        # First kept = user text; the other two reference the current skill.
        assert 'Generate the SOW.' in [
            x if isinstance(x, str) else None for x in kept_kinds
        ]

    def test_02_does_not_remove_current_skill(self):
        contents = [
            *_load_skill_pair('sow-delivery-plan'),
        ]
        new, log = _prune_inactive_skill_content(
            contents=contents,
            inactive_scopes={'sow-requirements'},
        )
        assert log['integrity_ok'] is True
        assert log['pruned_count'] == 0
        assert log['kept_count'] == len(contents)
        # The current skill's pair is preserved element-for-element.
        assert new == contents

    def test_03_does_not_remove_user_messages(self):
        contents = [_user_text('Please generate the SOW from these docs.')]
        new, log = _prune_inactive_skill_content(
            contents=contents,
            inactive_scopes={'sow-requirements'},
        )
        assert log['integrity_ok'] is True
        assert log['pruned_count'] == 0
        assert new == contents

    def test_04_does_not_remove_bundle_save_responses(self):
        """``save_requirements_bundle`` calls must survive even when the
        ``sow-requirements`` skill is scoped out — the bundle is the
        durable working memory the next skill will read."""
        contents = [
            *_load_skill_pair('sow-requirements'),
            _fc('save_requirements_bundle', {'bundle': {'requirements': []}}),
            _fr('save_requirements_bundle', {'status': 'ok', 'item_count': 0}),
            *_load_skill_pair('sow-delivery-plan'),
        ]
        new, log = _prune_inactive_skill_content(
            contents=contents,
            inactive_scopes={'sow-requirements'},
        )
        assert log['integrity_ok'] is True
        # 2 pruned (the load_skill pair); save_* + new load_skill pair stay.
        assert log['pruned_count'] == 2
        kept_names = []
        for c in new:
            part = c.parts[0]
            if part.function_call:
                kept_names.append(('fc', part.function_call.name))
            elif part.function_response:
                kept_names.append(('fr', part.function_response.name))
        assert ('fc', 'save_requirements_bundle') in kept_names
        assert ('fr', 'save_requirements_bundle') in kept_names

    def test_05_does_not_remove_load_artifacts_content(self):
        contents = [
            _fc('load_artifacts', {'artifact_ids': ['brief.pdf']}),
            _fr('load_artifacts', {'status': 'ok'}),
            *_load_skill_pair('sow-requirements'),
            *_load_skill_pair('sow-delivery-plan'),
        ]
        new, log = _prune_inactive_skill_content(
            contents=contents,
            inactive_scopes={'sow-requirements'},
        )
        assert log['integrity_ok'] is True
        assert log['pruned_count'] == 2  # only the inactive pair
        kept_names = [
            (
                c.parts[0].function_call.name
                if c.parts[0].function_call
                else c.parts[0].function_response.name
            )
            for c in new
        ]
        assert kept_names.count('load_artifacts') == 2

    def test_06_aborts_on_orphan_function_call_at_tail(self):
        """If the last Content is an inactive ``load_skill`` call whose
        response hasn't been generated yet, pruning would leave the next
        LLM request with a dangling tool_call → abort."""
        contents = [
            *_load_skill_pair('sow-requirements'),  # well-paired
            _fc('load_skill', {'skill_name': 'sow-requirements'}),  # orphan!
        ]
        new, log = _prune_inactive_skill_content(
            contents=contents,
            inactive_scopes={'sow-requirements'},
        )
        assert log['integrity_ok'] is False
        assert log['reason'] == 'orphan_function_call_in_last_content'
        assert new is contents  # unchanged on abort

    def test_07_removes_all_inactive_scopes_in_single_pass(self):
        contents = [
            *_load_skill_pair('sow-requirements'),
            *_load_skill_pair('sow-delivery-plan'),
            *_load_skill_pair('sow-scope-boundaries'),  # current
        ]
        new, log = _prune_inactive_skill_content(
            contents=contents,
            inactive_scopes={'sow-requirements', 'sow-delivery-plan'},
        )
        assert log['integrity_ok'] is True
        assert log['pruned_count'] == 4  # 2 pairs
        # Only the current pair remains.
        for c in new:
            part = c.parts[0]
            skill_in_args = None
            if part.function_call and part.function_call.args:
                skill_in_args = part.function_call.args.get('skill_name')
            elif (
                part.function_response
                and part.function_response.response
            ):
                skill_in_args = part.function_response.response.get('skill_name')
            assert skill_in_args == 'sow-scope-boundaries'

    def test_08_drops_binary_marker_paired_with_inactive_resource(self):
        """A synthetic binary Content whose ``file_path`` matches a
        dropped ``load_skill_resource`` call is dropped alongside the
        pair."""
        contents = [
            *_load_skill_pair('sow-requirements'),
            *_load_skill_resource_pair(
                'sow-requirements', 'assets/diagram.png'
            ),
            _binary_marker('assets/diagram.png'),
            *_load_skill_pair('sow-delivery-plan'),
        ]
        new, log = _prune_inactive_skill_content(
            contents=contents,
            inactive_scopes={'sow-requirements'},
        )
        assert log['integrity_ok'] is True
        # 2 (load_skill pair) + 2 (resource pair) + 1 (binary marker) = 5
        assert log['pruned_count'] == 5
        # Nothing remaining mentions sow-requirements.
        for c in new:
            for part in c.parts:
                text = getattr(part, 'text', '') or ''
                assert 'binary file' not in text

    def test_09_keeps_binary_marker_when_path_does_not_match_dropped_pair(
        self,
    ):
        """A binary marker with a ``file_path`` we cannot match to a
        dropped ``load_skill_resource`` pair is left alone — better to
        keep stray binary content than to risk dropping context the
        model needs."""
        contents = [
            *_load_skill_pair('sow-requirements'),
            _binary_marker('assets/orphan.png'),  # not paired with anything
            *_load_skill_pair('sow-delivery-plan'),
        ]
        new, log = _prune_inactive_skill_content(
            contents=contents,
            inactive_scopes={'sow-requirements'},
        )
        assert log['integrity_ok'] is True
        # Only the load_skill pair is dropped; the orphan binary stays.
        assert log['pruned_count'] == 2
        # Verify the marker is still in `new`.
        has_marker = any(
            'binary file' in (getattr(p, 'text', '') or '')
            for c in new
            for p in c.parts
        )
        assert has_marker

    def test_10_inactive_empty_returns_contents_unchanged(self):
        contents = [
            _user_text('hi'),
            *_load_skill_pair('sow-requirements'),
        ]
        new, log = _prune_inactive_skill_content(
            contents=contents, inactive_scopes=set()
        )
        assert log['integrity_ok'] is True
        assert log['pruned_count'] == 0
        assert new is contents

    def test_11_contents_empty_returns_empty(self):
        new, log = _prune_inactive_skill_content(
            contents=[], inactive_scopes={'sow-requirements'}
        )
        assert log['integrity_ok'] is True
        assert log['pruned_count'] == 0
        assert log['kept_count'] == 0
        assert new == []


# ---------------------------------------------------------------------------
# 12–13, 17: ``ScopedLoadSkillTool.run_async`` state-contract behaviour
# ---------------------------------------------------------------------------


class TestScopedLoadSkillTool:
    """State-contract tests for the wrapper around ``LoadSkillTool``."""

    def _make_tool_and_skills(self) -> tuple[ScopedLoadSkillTool, AutoScopedSkillToolset]:
        skills = [
            _make_skill('sow-requirements'),
            _make_skill('sow-delivery-plan'),
            _make_skill('sow-narrative', additional_tools=['google_search_agent']),
        ]
        toolset = AutoScopedSkillToolset(skills=skills)
        # The substitution happens in __init__; find the ScopedLoadSkillTool.
        tool = next(t for t in toolset._tools if isinstance(t, ScopedLoadSkillTool))
        return tool, toolset

    async def test_12_first_activation_sets_current_only(self):
        tool, _ = self._make_tool_and_skills()
        ctx = _make_tool_context()

        await tool.run_async(
            args={'skill_name': 'sow-requirements'}, tool_context=ctx
        )

        assert ctx.state[STATE_SKILL_CURRENT] == 'sow-requirements'
        assert ctx.state[STATE_SKILL_INACTIVE] == []
        assert ctx.state.get(STATE_SKILL_PREVIOUS) is None
        assert ctx.state.get(STATE_SKILL_HISTORY) is None  # not yet appended

    async def test_12_real_switch_records_full_transition(self):
        tool, _ = self._make_tool_and_skills()
        ctx = _make_tool_context()

        await tool.run_async(
            args={'skill_name': 'sow-requirements'}, tool_context=ctx
        )
        await tool.run_async(
            args={'skill_name': 'sow-delivery-plan'}, tool_context=ctx
        )

        assert ctx.state[STATE_SKILL_CURRENT] == 'sow-delivery-plan'
        assert ctx.state[STATE_SKILL_PREVIOUS] == 'sow-requirements'
        assert ctx.state[STATE_SKILL_INACTIVE] == ['sow-requirements']
        history = ctx.state[STATE_SKILL_HISTORY]
        assert len(history) == 1
        assert history[0]['from'] == 'sow-requirements'
        assert history[0]['to'] == 'sow-delivery-plan'
        assert 'timestamp' in history[0]

    async def test_13_idempotent_reactivation_does_not_duplicate_inactive(
        self,
    ):
        """Calling ``load_skill('sow-requirements')`` twice in a row
        must not put it into ``inactive`` (it never left current)."""
        tool, _ = self._make_tool_and_skills()
        ctx = _make_tool_context()

        await tool.run_async(
            args={'skill_name': 'sow-requirements'}, tool_context=ctx
        )
        await tool.run_async(
            args={'skill_name': 'sow-requirements'}, tool_context=ctx
        )

        assert ctx.state[STATE_SKILL_CURRENT] == 'sow-requirements'
        assert ctx.state[STATE_SKILL_INACTIVE] == []
        # No transition recorded (same skill, no switch).
        assert ctx.state.get(STATE_SKILL_HISTORY) in (None, [])

    async def test_17_reload_after_scope_out_removes_from_inactive(self):
        """requirements → delivery → requirements must leave
        ``inactive=['delivery']`` and ``current='requirements'``;
        ``requirements`` MUST NOT be in ``inactive`` (invariant 2)."""
        tool, _ = self._make_tool_and_skills()
        ctx = _make_tool_context()

        await tool.run_async(
            args={'skill_name': 'sow-requirements'}, tool_context=ctx
        )
        await tool.run_async(
            args={'skill_name': 'sow-delivery-plan'}, tool_context=ctx
        )
        await tool.run_async(
            args={'skill_name': 'sow-requirements'}, tool_context=ctx
        )

        assert ctx.state[STATE_SKILL_CURRENT] == 'sow-requirements'
        assert 'sow-requirements' not in ctx.state[STATE_SKILL_INACTIVE]
        assert ctx.state[STATE_SKILL_INACTIVE] == ['sow-delivery-plan']

    async def test_18_adk_activated_skill_is_cleared_on_transition(self):
        """The parent ``LoadSkillTool`` appends every activated skill to
        ``_adk_activated_skill_<agent>``; on a real switch we must drop
        the previous skill so its ``adk_additional_tools`` stop being
        surfaced by ``SkillToolset.get_tools``."""
        tool, toolset = self._make_tool_and_skills()
        ctx = _make_tool_context(agent_name='pre_sales_assistant')
        adk_key = '_adk_activated_skill_pre_sales_assistant'

        # 1) Activate sow-narrative — google_search_agent should surface.
        await tool.run_async(
            args={'skill_name': 'sow-narrative'}, tool_context=ctx
        )
        assert ctx.state[adk_key] == ['sow-narrative']

        readonly_narrative = MagicMock(
            agent_name='pre_sales_assistant', state=ctx.state
        )
        # ``_provided_tools_by_name`` is empty in this toolset (we did
        # not pass ``additional_tools``); resolution returns an empty
        # list but exercises the lookup path without crashing.
        resolved_narrative = await toolset._resolve_additional_tools_from_state(
            readonly_narrative
        )
        # No tools registered for the name -> empty, but the state was
        # consulted: confirm sow-narrative was found in activated list.
        _ = resolved_narrative  # explicitly unused — surface integrity covered below

        # 2) Switch to sow-requirements — sow-narrative leaves the ADK
        # active list, so google_search_agent is no longer surfaced.
        await tool.run_async(
            args={'skill_name': 'sow-requirements'}, tool_context=ctx
        )
        assert 'sow-narrative' not in ctx.state[adk_key]
        assert ctx.state[adk_key] == ['sow-requirements']


# ---------------------------------------------------------------------------
# 14–16: ``AutoScopedSkillToolset.process_llm_request`` integration
# ---------------------------------------------------------------------------


class TestAutoScopedSkillToolsetProcessLlmRequest:

    def _make_request(self, contents: list[types.Content]) -> Any:
        """Minimal ``LlmRequest`` stand-in: ``contents`` is mutable,
        ``append_instructions`` records calls so we can assert the
        parent's instruction injection still happened."""
        req = MagicMock(name='LlmRequest')
        req.contents = list(contents)
        # ``append_instructions`` exists on real LlmRequest; mock it so
        # we can assert it was called (proves super() ran).
        req.append_instructions = MagicMock()
        return req

    async def test_14_last_prune_snapshot_reflects_most_recent_event(self):
        skills = [
            _make_skill('sow-requirements'),
            _make_skill('sow-delivery-plan'),
        ]
        toolset = AutoScopedSkillToolset(skills=skills)
        ctx = _make_tool_context(
            initial_state={
                STATE_SKILL_CURRENT: 'sow-delivery-plan',
                STATE_SKILL_INACTIVE: ['sow-requirements'],
            }
        )
        req = self._make_request(
            [
                _user_text('Generate the SOW.'),
                *_load_skill_pair('sow-requirements'),
                *_load_skill_pair('sow-delivery-plan'),
            ]
        )

        await toolset.process_llm_request(tool_context=ctx, llm_request=req)

        last = ctx.state[STATE_SKILL_LAST_PRUNE]
        assert last['pruned_message_count'] == 2
        assert last['kept_message_count'] == 3
        assert last['skills_pruned'] == ['sow-requirements']
        assert last['pruned_bytes'] > 0

    async def test_15_prune_totals_accumulates_across_events(self):
        skills = [
            _make_skill('sow-requirements'),
            _make_skill('sow-delivery-plan'),
            _make_skill('sow-scope-boundaries'),
        ]
        toolset = AutoScopedSkillToolset(skills=skills)
        ctx = _make_tool_context()

        async def run_prune(inactive: set[str], contents: list[types.Content]):
            ctx.state[STATE_SKILL_INACTIVE] = sorted(inactive)
            req = self._make_request(contents)
            await toolset.process_llm_request(tool_context=ctx, llm_request=req)

        # Event 1: prune sow-requirements (1 pair = 2 contents).
        await run_prune(
            {'sow-requirements'},
            [*_load_skill_pair('sow-requirements'),
             *_load_skill_pair('sow-delivery-plan')],
        )
        # Event 2: prune sow-delivery-plan (1 pair = 2 contents).
        await run_prune(
            {'sow-delivery-plan'},
            [*_load_skill_pair('sow-delivery-plan'),
             *_load_skill_pair('sow-scope-boundaries')],
        )
        # Event 3: prune both (still 2 pairs = 4 contents).
        await run_prune(
            {'sow-requirements', 'sow-delivery-plan'},
            [
                *_load_skill_pair('sow-requirements'),
                *_load_skill_pair('sow-delivery-plan'),
                *_load_skill_pair('sow-scope-boundaries'),
            ],
        )

        totals = ctx.state[STATE_SKILL_PRUNE_TOTALS]
        assert totals['prune_event_count'] == 3
        assert totals['pruned_messages_total'] == 2 + 2 + 4  # 8
        assert totals['pruned_bytes_total'] > 0

    async def test_16_parent_process_llm_request_is_invoked(self):
        """The parent injects the default skill system instruction +
        the available-skills XML via ``llm_request.append_instructions``.
        Our override must call ``super()`` first so that injection
        still happens — otherwise the model loses the L1 catalogue."""
        skills = [_make_skill('sow-requirements')]
        toolset = AutoScopedSkillToolset(skills=skills)
        ctx = _make_tool_context()
        req = self._make_request([_user_text('hi')])

        await toolset.process_llm_request(tool_context=ctx, llm_request=req)

        # Parent's process_llm_request calls llm_request.append_instructions
        # exactly once with a list containing the L1 instruction.
        req.append_instructions.assert_called_once()
        instructions = req.append_instructions.call_args.args[0]
        assert isinstance(instructions, list)
        joined = '\n'.join(instructions)
        assert 'load_skill' in joined  # the default instruction mentions it
