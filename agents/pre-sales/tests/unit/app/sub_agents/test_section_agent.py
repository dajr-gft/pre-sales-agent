"""Unit tests for the ``build_section_agent`` factory.

The factory's contract:

- Returns a :class:`SequentialAgent` named after the public ``name``.
- ``sub_agents[0]`` is the worker (``<stem>_worker``): owns tools (the
  ``SectionResourcesToolset`` plus any ``extra_tools``), has NO
  ``output_schema``, writes its JSON-in-text draft to
  ``state[f'{output_key}:draft']``.
- ``sub_agents[1]`` is the formatter (``<stem>_formatter``): no tools,
  has ``output_schema``, writes the validated bundle to
  ``state[output_key]``.
- Both run with ``include_contents='none'`` and ``disallow_transfer_*``.

These tests stub out ``Agent`` / ``Gemini`` / ``SectionResourcesToolset``
so we exercise the wiring deterministically without touching the model
client.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pydantic import BaseModel


class _DummyBundle(BaseModel):
    field: str = ''


def _fake_skill(name: str) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        instructions=f'<{name} instructions>',
        frontmatter=SimpleNamespace(metadata={}),
        resources=SimpleNamespace(
            list_references=lambda: [],
            list_assets=lambda: [],
            list_scripts=lambda: [],
        ),
    )


def _lay_down_skill(root: Path, name: str) -> None:
    (root / name).mkdir(parents=True, exist_ok=True)
    (root / name / 'SKILL.md').write_text(
        f'---\nname: {name}\ndescription: x\n---\nbody',
        encoding='utf-8',
    )


def _common_skill_tree(tmp_path: Path) -> None:
    _lay_down_skill(tmp_path, 'sow-requirements')
    _lay_down_skill(tmp_path, 'sow-shared')


class _AgentCalls:
    """Captures both Agent() instantiations (worker, then formatter)."""

    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        # Build a sentinel that the SequentialAgent can hold.
        return SimpleNamespace(_kwargs=kwargs, name=kwargs['name'])

    @property
    def worker(self) -> dict:
        assert self.calls, 'No Agent() calls captured.'
        return self.calls[0]

    @property
    def formatter(self) -> dict:
        assert len(self.calls) >= 2, 'Formatter never constructed.'
        return self.calls[1]


class _FakeSequentialAgent:
    """Stub for ``SequentialAgent`` — bypasses Pydantic validation so we
    can keep ``Agent`` itself stubbed too."""

    def __init__(self, *, name, description, sub_agents):
        self.name = name
        self.description = description
        self.sub_agents = list(sub_agents)


def _patches(_section_agent, tmp_path: Path, agent_factory: _AgentCalls,
             toolset_factory):
    """The patch tower every test below uses — keeps tests readable."""
    return [
        patch.object(_section_agent, '_SKILLS_DIR', tmp_path),
        patch.object(
            _section_agent, 'load_skill_from_dir',
            side_effect=lambda p: _fake_skill(p.name),
        ),
        patch.object(_section_agent, 'SectionResourcesToolset', toolset_factory),
        patch.object(_section_agent, 'Agent', agent_factory),
        patch.object(_section_agent, 'SequentialAgent', _FakeSequentialAgent),
        patch.object(_section_agent, 'Gemini', lambda **_: object()),
    ]


def _stack(patches):
    """Enter all patches at once via a single nested context manager."""
    from contextlib import ExitStack
    stack = ExitStack()
    for p in patches:
        stack.enter_context(p)
    return stack


# ---------------------------------------------------------------------------
# Skill existence check
# ---------------------------------------------------------------------------


class TestSkillExistenceCheck:
    """A typo in the skill name must fail at import time, not at runtime."""

    def test_missing_skill_directory_raises(self, tmp_path: Path):
        from app.sub_agents import _section_agent

        with patch.object(_section_agent, '_SKILLS_DIR', tmp_path):
            with pytest.raises(FileNotFoundError) as exc:
                _section_agent.build_section_agent(
                    name='x_agent',
                    description='y',
                    skill_name='does-not-exist',
                    output_schema=_DummyBundle,
                    output_key='app:sow:test',
                    output_example='{}',
                )

        assert 'does-not-exist' in str(exc.value)


# ---------------------------------------------------------------------------
# Resources skill loading
# ---------------------------------------------------------------------------


class TestResourcesSkillSelection:
    """Verify which skills end up reachable via load_skill_resource."""

    def test_own_skill_always_included_and_default_adds_sow_shared(
        self, tmp_path: Path
    ):
        from app.sub_agents import _section_agent

        _common_skill_tree(tmp_path)

        loaded: list[str] = []

        def fake_loader(path: Path):
            loaded.append(path.name)
            return _fake_skill(path.name)

        captured_skills: list[list] = []

        def toolset_factory(*, skills):
            captured_skills.append(list(skills))
            return SimpleNamespace(skills=list(skills))

        agent_factory = _AgentCalls()

        with patch.object(_section_agent, '_SKILLS_DIR', tmp_path), \
             patch.object(_section_agent, 'load_skill_from_dir',
                          side_effect=fake_loader), \
             patch.object(_section_agent, 'SectionResourcesToolset',
                          toolset_factory), \
             patch.object(_section_agent, 'Agent', agent_factory), \
             patch.object(_section_agent, 'SequentialAgent',
                          _FakeSequentialAgent), \
             patch.object(_section_agent, 'Gemini', lambda **_: object()):
            _section_agent.build_section_agent(
                name='requirements_agent',
                description='Generates FR/NFR.',
                skill_name='sow-requirements',
                output_schema=_DummyBundle,
                output_key='app:sow:requirements',
                output_example='{}',
            )

        # Both skills loaded once each.
        assert loaded == ['sow-requirements', 'sow-shared']
        # Only the WORKER receives the resources toolset, so only one
        # SectionResourcesToolset is constructed per build call.
        assert len(captured_skills) == 1
        assert [s.name for s in captured_skills[0]] == [
            'sow-requirements',
            'sow-shared',
        ]

    def test_self_is_not_added_twice_when_listed_in_extra(self, tmp_path: Path):
        """``extra_skills_for_resources=('sow-requirements',)`` must not
        re-load the section's own skill — the factory deduplicates."""
        from app.sub_agents import _section_agent

        _lay_down_skill(tmp_path, 'sow-requirements')

        loaded: list[str] = []

        def fake_loader(path: Path):
            loaded.append(path.name)
            return _fake_skill(path.name)

        with patch.object(_section_agent, '_SKILLS_DIR', tmp_path), \
             patch.object(_section_agent, 'load_skill_from_dir',
                          side_effect=fake_loader), \
             patch.object(_section_agent, 'SectionResourcesToolset',
                          lambda *, skills: SimpleNamespace(skills=list(skills))), \
             patch.object(_section_agent, 'Agent', _AgentCalls()), \
             patch.object(_section_agent, 'SequentialAgent',
                          _FakeSequentialAgent), \
             patch.object(_section_agent, 'Gemini', lambda **_: object()):
            _section_agent.build_section_agent(
                name='requirements_agent',
                description='Generates FR/NFR.',
                skill_name='sow-requirements',
                output_schema=_DummyBundle,
                output_key='app:sow:requirements',
                output_example='{}',
                extra_skills_for_resources=('sow-requirements',),
            )

        assert loaded == ['sow-requirements']


# ---------------------------------------------------------------------------
# Worker wiring
# ---------------------------------------------------------------------------


class TestWorkerWiring:
    def _build(self, tmp_path: Path, **kwargs):
        from app.sub_agents import _section_agent

        _common_skill_tree(tmp_path)
        agent_factory = _AgentCalls()
        toolset = SimpleNamespace(name='toolset')

        defaults = dict(
            name='requirements_agent',
            description='Generates FR/NFR.',
            skill_name='sow-requirements',
            output_schema=_DummyBundle,
            output_key='app:sow:requirements',
            output_example='{"functional_requirements": []}',
        )
        defaults.update(kwargs)

        with _stack(_patches(
            _section_agent, tmp_path, agent_factory,
            lambda *, skills: toolset,
        )):
            result = _section_agent.build_section_agent(**defaults)

        return agent_factory, toolset, result

    def test_worker_name_derived_from_public_name(self, tmp_path: Path):
        agent_factory, _, _ = self._build(tmp_path)
        assert agent_factory.worker['name'] == 'requirements_worker'

    def test_worker_has_no_output_schema(self, tmp_path: Path):
        """``output_schema`` would silently disable tools — the whole reason
        for the split. Pinning this prevents future drift."""
        agent_factory, _, _ = self._build(tmp_path)
        assert 'output_schema' not in agent_factory.worker

    def test_worker_writes_to_draft_state_key(self, tmp_path: Path):
        agent_factory, _, _ = self._build(tmp_path)
        assert agent_factory.worker['output_key'] == 'app:sow:requirements:draft'

    def test_worker_isolated_from_history(self, tmp_path: Path):
        agent_factory, _, _ = self._build(tmp_path)
        assert agent_factory.worker['include_contents'] == 'none'

    def test_worker_cannot_transfer_out(self, tmp_path: Path):
        """Workers must stay inside their SequentialAgent — escalation
        would let them hijack the root flow."""
        agent_factory, _, _ = self._build(tmp_path)
        assert agent_factory.worker['disallow_transfer_to_parent'] is True
        assert agent_factory.worker['disallow_transfer_to_peers'] is True

    def test_worker_tools_include_resources_toolset(self, tmp_path: Path):
        agent_factory, toolset, _ = self._build(tmp_path)
        tools = agent_factory.worker['tools']
        assert toolset in tools

    def test_worker_appends_extra_tools(self, tmp_path: Path):
        extra = object()
        agent_factory, toolset, _ = self._build(tmp_path, extra_tools=[extra])
        tools = agent_factory.worker['tools']
        assert tools[0] is toolset
        assert tools[1] is extra

    def test_worker_instruction_includes_skill_body_and_protocol(
        self, tmp_path: Path
    ):
        agent_factory, _, _ = self._build(tmp_path)
        instr = agent_factory.worker['instruction']
        assert '<sow-requirements instructions>' in instr
        assert 'Output protocol' in instr
        # The output_example must be interpolated into the protocol.
        assert '"functional_requirements": []' in instr


# ---------------------------------------------------------------------------
# Formatter wiring
# ---------------------------------------------------------------------------


class TestFormatterWiring:
    def _build(self, tmp_path: Path, **kwargs):
        from app.sub_agents import _section_agent

        _common_skill_tree(tmp_path)
        agent_factory = _AgentCalls()

        defaults = dict(
            name='requirements_agent',
            description='Generates FR/NFR.',
            skill_name='sow-requirements',
            output_schema=_DummyBundle,
            output_key='app:sow:requirements',
            output_example='{}',
        )
        defaults.update(kwargs)

        with _stack(_patches(
            _section_agent, tmp_path, agent_factory,
            lambda *, skills: SimpleNamespace(name='toolset'),
        )):
            _section_agent.build_section_agent(**defaults)

        return agent_factory

    def test_formatter_name_derived_from_public_name(self, tmp_path: Path):
        f = self._build(tmp_path).formatter
        assert f['name'] == 'requirements_formatter'

    def test_formatter_has_output_schema(self, tmp_path: Path):
        f = self._build(tmp_path).formatter
        assert f['output_schema'] is _DummyBundle

    def test_formatter_writes_to_canonical_key(self, tmp_path: Path):
        f = self._build(tmp_path).formatter
        assert f['output_key'] == 'app:sow:requirements'

    def test_formatter_has_no_tools(self, tmp_path: Path):
        """A formatter with tools would re-trigger the silent-drop bug."""
        f = self._build(tmp_path).formatter
        assert 'tools' not in f or not f['tools']

    def test_formatter_isolated_from_history(self, tmp_path: Path):
        f = self._build(tmp_path).formatter
        assert f['include_contents'] == 'none'

    def test_formatter_cannot_transfer_out(self, tmp_path: Path):
        f = self._build(tmp_path).formatter
        assert f['disallow_transfer_to_parent'] is True
        assert f['disallow_transfer_to_peers'] is True

    def test_formatter_instruction_is_callable_provider(self, tmp_path: Path):
        f = self._build(tmp_path).formatter
        assert callable(f['instruction'])

    def test_formatter_instruction_provider_interpolates_draft(
        self, tmp_path: Path
    ):
        f = self._build(tmp_path).formatter
        provider = f['instruction']

        class _Ctx:
            state = {'app:sow:requirements:draft': '{"hello": "world"}'}

        result = provider(_Ctx())
        assert '{"hello": "world"}' in result
        assert '<draft>' in result


# ---------------------------------------------------------------------------
# Public SequentialAgent wiring
# ---------------------------------------------------------------------------


class TestPublicSequentialAgent:
    """The factory must return a SequentialAgent named after the public name
    with worker + formatter in that order.

    We patch ``SequentialAgent`` to avoid Pydantic validation on the stubbed
    sub-agents — the real-thing assertion lives in
    ``requirements/test_requirements_agent.py::test_is_sequential_agent``,
    which exercises the unpatched factory against the real skill.
    """

    def test_wrapped_in_fake_sequential_agent_with_two_sub_agents(
        self, tmp_path: Path
    ):
        from app.sub_agents import _section_agent

        _common_skill_tree(tmp_path)
        agent_factory = _AgentCalls()

        with _stack(_patches(
            _section_agent, tmp_path, agent_factory,
            lambda *, skills: SimpleNamespace(name='toolset'),
        )):
            result = _section_agent.build_section_agent(
                name='requirements_agent',
                description='Generates FR/NFR.',
                skill_name='sow-requirements',
                output_schema=_DummyBundle,
                output_key='app:sow:requirements',
                output_example='{}',
            )

        assert isinstance(result, _FakeSequentialAgent)
        assert result.name == 'requirements_agent'
        assert len(result.sub_agents) == 2
        names = [a.name for a in result.sub_agents]
        assert names == ['requirements_worker', 'requirements_formatter']
