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

    def test_worker_instruction_is_callable_provider(self, tmp_path: Path):
        """Instruction must be a callable so the provider can read state
        at every turn. A static string cannot inject the manifest and
        prior bundles the worker needs to do its job (see plan v2.2 §
        'Runtime input contract')."""
        agent_factory, _, _ = self._build(tmp_path)
        assert callable(agent_factory.worker['instruction'])

    def test_worker_instruction_provider_includes_skill_body_and_protocol(
        self, tmp_path: Path
    ):
        agent_factory, _, _ = self._build(tmp_path)
        provider = agent_factory.worker['instruction']

        class _Ctx:
            state: dict = {}

        instr = provider(_Ctx())
        assert '<sow-requirements instructions>' in instr
        assert 'Output protocol' in instr
        # The output_example must be interpolated into the protocol.
        assert '"functional_requirements": []' in instr

    def test_worker_instruction_provider_injects_state_inputs(
        self, tmp_path: Path
    ):
        agent_factory, _, _ = self._build(
            tmp_path,
            state_inputs=(
                ('extraction_manifest', 'extraction_manifest'),
                ('prior_requirements', 'app:sow:requirements'),
            ),
        )
        provider = agent_factory.worker['instruction']

        class _Ctx:
            state = {
                'extraction_manifest': {'project': 'P1', 'gaps': []},
                'app:sow:requirements': {
                    'functional_requirements': [{'number': 'FR-01'}],
                    'non_functional_requirements': [],
                },
            }

        instr = provider(_Ctx())
        assert '<extraction_manifest>' in instr
        assert '</extraction_manifest>' in instr
        assert '"project":"P1"' in instr
        assert '<prior_requirements>' in instr
        assert '"FR-01"' in instr
        # PRESENT branch must include the anti-invention reminder.
        assert 'Do NOT invent' in instr

    def test_worker_instruction_provider_flags_missing_inputs(
        self, tmp_path: Path
    ):
        agent_factory, _, _ = self._build(
            tmp_path,
            state_inputs=(
                ('extraction_manifest', 'extraction_manifest'),
                ('prior_requirements', 'app:sow:requirements'),
            ),
        )
        provider = agent_factory.worker['instruction']

        class _Ctx:
            state: dict = {}  # nothing in state

        instr = provider(_Ctx())
        assert 'MISSING' in instr
        assert 'extraction_manifest' in instr
        assert 'prior_requirements' in instr
        assert 'STOP' in instr
        assert 'MISSING_INPUT' in instr  # sentinel for empty-bundle mode

    def test_worker_instruction_provider_empty_state_value_counts_as_missing(
        self, tmp_path: Path
    ):
        """An empty dict / list / string in state is treated as missing —
        the section agents need substantive content, not stubs."""
        agent_factory, _, _ = self._build(
            tmp_path,
            state_inputs=(
                ('extraction_manifest', 'extraction_manifest'),
            ),
        )
        provider = agent_factory.worker['instruction']

        class _Ctx:
            state = {'extraction_manifest': {}}

        instr = provider(_Ctx())
        assert 'MISSING' in instr

    def test_worker_instruction_provider_without_state_inputs_skips_block(
        self, tmp_path: Path
    ):
        """A section that declares no inputs (legacy / no upstream needed)
        must still produce a working instruction — no missing-block, no
        runtime-inputs block, just SKILL.md + protocol."""
        agent_factory, _, _ = self._build(tmp_path)  # default state_inputs=()
        provider = agent_factory.worker['instruction']

        class _Ctx:
            state: dict = {}

        instr = provider(_Ctx())
        assert 'MISSING' not in instr
        assert 'Runtime inputs' not in instr
        assert 'Output protocol' in instr


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


# ---------------------------------------------------------------------------
# F-12 — patch mode via previous_bundle optional input
#
# When a section_agent is re-invoked (revision after a review gate,
# upstream cascade), the worker must see its own prior output AND switch
# to patch discipline — preserve ids, leave untouched items byte-for-
# byte, apply only the minimum delta. The factory wires this for every
# section by default (``enable_patch_mode=True``): an optional input
# labelled ``previous_bundle`` pointing at the agent's own output_key,
# plus a patch-mode footer that the provider appends iff that bundle is
# populated at runtime.
# ---------------------------------------------------------------------------


class TestPatchModeWiring:
    """Validates the factory hooks up patch mode by default."""

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
            output_example='{}',
            state_inputs=(
                ('extraction_manifest', 'extraction_manifest'),
            ),
        )
        defaults.update(kwargs)

        with _stack(_patches(
            _section_agent, tmp_path, agent_factory,
            lambda *, skills: toolset,
        )):
            _section_agent.build_section_agent(**defaults)

        return agent_factory

    def test_first_run_without_previous_bundle_omits_patch_footer(
        self, tmp_path: Path
    ):
        """No prior output in state → no patch footer; the worker
        generates from scratch using upstream alone."""
        agent_factory = self._build(tmp_path)
        provider = agent_factory.worker['instruction']

        class _Ctx:
            state = {
                'extraction_manifest': {'project': 'P1'},
                # NO 'app:sow:requirements' key — first run.
            }

        instr = provider(_Ctx())
        assert 'Runtime inputs' in instr  # present-branch fired
        # previous_bundle block must NOT appear when state has no prior.
        assert '<previous_bundle>' not in instr
        # Patch footer must NOT appear either.
        assert 'Patch mode' not in instr

    def test_revision_run_with_previous_bundle_injects_block_and_footer(
        self, tmp_path: Path
    ):
        agent_factory = self._build(tmp_path)
        provider = agent_factory.worker['instruction']

        class _Ctx:
            state = {
                'extraction_manifest': {'project': 'P1'},
                'app:sow:requirements': {
                    'functional_requirements': [
                        {'number': 'FR-01', 'description': 'Ingest data'},
                    ],
                    'non_functional_requirements': [
                        {'number': 'NFR-01', 'description': 'TLS 1.3'},
                    ],
                },
            }

        instr = provider(_Ctx())
        # The bundle is injected as an XML block.
        assert '<previous_bundle>' in instr
        assert '</previous_bundle>' in instr
        # Contents make it into the prompt (compact JSON).
        assert '"FR-01"' in instr
        assert '"NFR-01"' in instr
        # Patch-mode footer is appended ONLY when previous_bundle is
        # populated — this is the F-12 contract.
        assert 'Patch mode' in instr
        # Footer must explicitly forbid regeneration / id changes.
        assert 'Preserve every existing id' in instr
        assert 'byte-for-byte' in instr
        assert 'minimum delta' in instr

    def test_empty_previous_bundle_does_not_trigger_patch_mode(
        self, tmp_path: Path
    ):
        """An empty dict at the output_key (e.g. a stale write that
        never got real content) must not trick the provider into
        patch mode — there's nothing to preserve. ``_is_present``
        treats empty containers as missing for this reason."""
        agent_factory = self._build(tmp_path)
        provider = agent_factory.worker['instruction']

        class _Ctx:
            state = {
                'extraction_manifest': {'project': 'P1'},
                'app:sow:requirements': {},  # empty
            }

        instr = provider(_Ctx())
        assert '<previous_bundle>' not in instr
        assert 'Patch mode' not in instr

    def test_missing_required_input_wins_over_previous_bundle(
        self, tmp_path: Path
    ):
        """If a required upstream input is missing AND a previous_bundle
        exists, MISSING wins — there is no valid patching target without
        the upstream packet that would drive the diff."""
        agent_factory = self._build(tmp_path)
        provider = agent_factory.worker['instruction']

        class _Ctx:
            state = {
                # extraction_manifest deliberately absent
                'app:sow:requirements': {
                    'functional_requirements': [
                        {'number': 'FR-01', 'description': 'x'},
                    ],
                    'non_functional_requirements': [],
                },
            }

        instr = provider(_Ctx())
        assert 'MISSING' in instr
        assert 'STOP' in instr
        # MISSING path returns before any input rendering, so neither
        # the previous_bundle block nor the patch footer leak in.
        assert '<previous_bundle>' not in instr
        assert 'Patch mode' not in instr

    def test_patch_mode_can_be_disabled(self, tmp_path: Path):
        """``enable_patch_mode=False`` removes the auto-injected optional
        input — no previous_bundle block, no patch footer, even when
        state carries the prior output."""
        agent_factory = self._build(tmp_path, enable_patch_mode=False)
        provider = agent_factory.worker['instruction']

        class _Ctx:
            state = {
                'extraction_manifest': {'project': 'P1'},
                'app:sow:requirements': {
                    'functional_requirements': [{'number': 'FR-01'}],
                    'non_functional_requirements': [],
                },
            }

        instr = provider(_Ctx())
        assert '<previous_bundle>' not in instr
        assert 'Patch mode' not in instr

    def test_extra_optional_inputs_render_when_present(
        self, tmp_path: Path
    ):
        """``extra_optional_state_inputs`` works alongside the auto-
        injected previous_bundle — different state keys, both optional,
        both render when populated."""
        agent_factory = self._build(
            tmp_path,
            extra_optional_state_inputs=(
                ('user_directive', 'app:sow:user_directive'),
            ),
        )
        provider = agent_factory.worker['instruction']

        class _Ctx:
            state = {
                'extraction_manifest': {'project': 'P1'},
                'app:sow:user_directive': 'change NFR-03 to multi-zone',
                # No previous_bundle → no patch mode, but the extra
                # optional still renders.
            }

        instr = provider(_Ctx())
        assert '<user_directive>' in instr
        assert 'change NFR-03 to multi-zone' in instr
        # No previous_bundle present, so patch footer must NOT appear.
        assert 'Patch mode' not in instr

    def test_extra_optional_input_cannot_shadow_previous_bundle_key(
        self, tmp_path: Path
    ):
        """De-duplication contract: an extra optional input sharing the
        same state_key as previous_bundle must NOT register a second
        time under a different label — that would render the bundle
        twice in the prompt, wasting tokens and confusing the LLM."""
        agent_factory = self._build(
            tmp_path,
            extra_optional_state_inputs=(
                # Same state_key as the auto-injected previous_bundle.
                ('shadow_label', 'app:sow:requirements'),
            ),
        )
        provider = agent_factory.worker['instruction']

        class _Ctx:
            state = {
                'extraction_manifest': {'project': 'P1'},
                'app:sow:requirements': {
                    'functional_requirements': [{'number': 'FR-01'}],
                    'non_functional_requirements': [],
                },
            }

        instr = provider(_Ctx())
        # previous_bundle is the canonical label and must win.
        assert '<previous_bundle>' in instr
        assert '<shadow_label>' not in instr
        # Patch footer fires once.
        assert instr.count('Patch mode') == 1


class TestPatchModeProvider:
    """Direct coverage of the instruction provider — verifies the
    optional input semantics independently of the factory wiring."""

    def test_optional_input_absent_does_not_trigger_missing(self):
        from app.sub_agents import _section_agent

        provider = _section_agent._make_worker_instruction_provider(
            skill_body='SKILL',
            output_protocol='\nOUTPUT',
            state_inputs=(('manifest', 'mkey'),),
            optional_state_inputs=(('previous_bundle', 'pkey'),),
            patch_mode_label='previous_bundle',
        )

        class _Ctx:
            state = {'mkey': {'x': 1}}  # required present, optional absent

        instr = provider(_Ctx())
        assert 'MISSING' not in instr
        assert '<manifest>' in instr
        assert '<previous_bundle>' not in instr
        assert 'Patch mode' not in instr

    def test_patch_label_label_must_match_to_activate_footer(self):
        """The footer fires only when the optional input that's present
        matches ``patch_mode_label``. Other optional inputs being
        present should NOT trigger patch mode."""
        from app.sub_agents import _section_agent

        provider = _section_agent._make_worker_instruction_provider(
            skill_body='SKILL',
            output_protocol='\nOUTPUT',
            state_inputs=(('manifest', 'mkey'),),
            optional_state_inputs=(
                ('previous_bundle', 'pkey'),
                ('other_optional', 'okey'),
            ),
            patch_mode_label='previous_bundle',
        )

        class _Ctx:
            state = {
                'mkey': {'x': 1},
                'okey': 'some other context',  # not the patch label
                # 'pkey' deliberately absent
            }

        instr = provider(_Ctx())
        assert '<other_optional>' in instr
        assert '<previous_bundle>' not in instr
        assert 'Patch mode' not in instr

    def test_no_patch_label_means_footer_never_fires(self):
        """When ``patch_mode_label`` is None, even a populated optional
        input does not append the patch footer — confirms the opt-out."""
        from app.sub_agents import _section_agent

        provider = _section_agent._make_worker_instruction_provider(
            skill_body='SKILL',
            output_protocol='\nOUTPUT',
            state_inputs=(('manifest', 'mkey'),),
            optional_state_inputs=(('previous_bundle', 'pkey'),),
            patch_mode_label=None,
        )

        class _Ctx:
            state = {'mkey': {'x': 1}, 'pkey': {'y': 2}}

        instr = provider(_Ctx())
        assert '<previous_bundle>' in instr
        assert 'Patch mode' not in instr


class TestSectionAgentsAllOptInToPatchMode:
    """Belt-and-suspenders: confirms every real section agent has patch
    mode wired. If a future section_agent forgets ``enable_patch_mode``
    or sets it to False inadvertently, this test catches it."""

    @pytest.mark.parametrize(
        'module_path,agent_attr,output_key',
        [
            (
                'app.sub_agents.requirements',
                'requirements_agent',
                'app:sow:requirements',
            ),
            (
                'app.sub_agents.delivery_plan',
                'delivery_plan_agent',
                'app:sow:delivery_plan',
            ),
            (
                'app.sub_agents.scope_boundaries',
                'scope_boundaries_agent',
                'app:sow:scope_boundaries',
            ),
            (
                'app.sub_agents.architecture',
                'architecture_agent',
                'app:sow:architecture',
            ),
            (
                'app.sub_agents.narrative',
                'narrative_agent',
                'app:sow:narrative',
            ),
        ],
    )
    def test_worker_provider_emits_patch_footer_when_previous_bundle_present(
        self, module_path: str, agent_attr: str, output_key: str
    ):
        import importlib

        mod = importlib.import_module(module_path)
        agent = getattr(mod, agent_attr)
        worker = agent.sub_agents[0]
        provider = worker.instruction

        # Populate the section's own output_key plus every required
        # upstream state input the worker declares. We don't enumerate
        # them statically — the provider would otherwise complain
        # MISSING on the upstream packet and short-circuit before the
        # patch footer can fire. We construct the minimal state to
        # exercise the patch-mode branch.
        from app.sub_agents.schemas import SOW_BUNDLE_STATE_KEYS

        non_empty_marker = {'__filled__': True}
        state = {
            SOW_BUNDLE_STATE_KEYS['manifest']: non_empty_marker,
            SOW_BUNDLE_STATE_KEYS['requirements']: non_empty_marker,
            SOW_BUNDLE_STATE_KEYS['delivery_plan']: non_empty_marker,
            SOW_BUNDLE_STATE_KEYS['scope_boundaries']: non_empty_marker,
            SOW_BUNDLE_STATE_KEYS['architecture']: non_empty_marker,
            SOW_BUNDLE_STATE_KEYS['narrative']: non_empty_marker,
        }
        # Overwrite the section's OWN bundle with content that flags it
        # as "previously produced" — the patch footer must fire.
        state[output_key] = {'__prev__': True, 'items': [{'id': 'X-01'}]}

        class _Ctx:
            pass

        _Ctx.state = state
        instr = provider(_Ctx())
        assert '<previous_bundle>' in instr, (
            f'{agent_attr}: previous_bundle block must render when the '
            f'output_key {output_key!r} carries prior content.'
        )
        assert 'Patch mode' in instr, (
            f'{agent_attr}: patch-mode footer missing — F-12 contract '
            f'broken. Did someone set enable_patch_mode=False?'
        )


# ---------------------------------------------------------------------------
# Repair mode — auto-injected ``<repair_findings>`` optional input + footer
#
# The QualityLoopAgent writes the section's flagged findings to
# ``state[STATE_REPAIR_FINDINGS]`` before invoking a section agent in
# repair mode. The provider must render that list as an XML block AND
# layer the repair-mode footer ON TOP OF the patch-mode footer so the
# worker addresses every listed defect with minimum delta while keeping
# the patch-mode contracts (preserve ids, carry untouched verbatim).
#
# These tests pin the auto-injection from the factory + the provider
# semantics + the "all real section agents have it" invariant.
# ---------------------------------------------------------------------------


class TestRepairModeWiring:
    """Validates the factory auto-injects the repair_findings optional
    input alongside previous_bundle, and the provider renders both."""

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
            output_example='{}',
            state_inputs=(
                ('extraction_manifest', 'extraction_manifest'),
            ),
        )
        defaults.update(kwargs)

        with _stack(_patches(
            _section_agent, tmp_path, agent_factory,
            lambda *, skills: toolset,
        )):
            _section_agent.build_section_agent(**defaults)

        return agent_factory

    def test_first_run_without_repair_findings_omits_repair_footer(
        self, tmp_path: Path
    ):
        """No repair_findings in state → no repair footer; the worker
        generates from scratch (or patches via previous_bundle alone if
        that is populated)."""
        agent_factory = self._build(tmp_path)
        provider = agent_factory.worker['instruction']

        class _Ctx:
            state = {
                'extraction_manifest': {'project': 'P1'},
                # No repair_findings key.
            }

        instr = provider(_Ctx())
        assert '<repair_findings>' not in instr
        assert 'Repair mode' not in instr

    def test_repair_run_renders_block_and_footer(self, tmp_path: Path):
        """The orchestrator wrote findings to STATE_REPAIR_FINDINGS and
        repopulated the prior bundle — repair mode footer fires on top
        of patch mode footer."""
        from app.sub_agents._section_agent import STATE_REPAIR_FINDINGS

        agent_factory = self._build(tmp_path)
        provider = agent_factory.worker['instruction']

        findings = [
            {
                'id': 'contradictions-001',
                'skill': 'contradictions',
                'category': 'activities_vs_deliverables',
                'severity': 'MAJOR',
                'evidence': 'WS-03 has no activity row.',
                'recommendation': 'Add an activity that produces WS-03.',
                'fields': ['activity_phases', 'deliverables'],
            },
        ]

        class _Ctx:
            state = {
                'extraction_manifest': {'project': 'P1'},
                'app:sow:requirements': {
                    'functional_requirements': [
                        {'number': 'FR-01', 'description': 'x'},
                    ],
                    'non_functional_requirements': [],
                },
                STATE_REPAIR_FINDINGS: findings,
            }

        instr = provider(_Ctx())
        # Both blocks visible.
        assert '<previous_bundle>' in instr
        assert '<repair_findings>' in instr
        # Compact JSON of the finding is in the prompt.
        assert '"activities_vs_deliverables"' in instr
        assert '"WS-03 has no activity row."' in instr
        # Both footers fire — patch mode for the structural rules, repair
        # mode for the targeted action list.
        assert 'Patch mode' in instr
        assert 'Repair mode' in instr
        # Repair footer must appear AFTER the patch footer (structural
        # rules first, then targeted action list).
        assert instr.index('Patch mode') < instr.index('Repair mode')

    def test_empty_repair_findings_list_does_not_trigger_repair_mode(
        self, tmp_path: Path
    ):
        """An empty list of findings means there is nothing to repair —
        the footer must not fire (``_is_present`` already treats empty
        containers as missing)."""
        from app.sub_agents._section_agent import STATE_REPAIR_FINDINGS

        agent_factory = self._build(tmp_path)
        provider = agent_factory.worker['instruction']

        class _Ctx:
            state = {
                'extraction_manifest': {'project': 'P1'},
                'app:sow:requirements': {
                    'functional_requirements': [{'number': 'FR-01'}],
                    'non_functional_requirements': [],
                },
                STATE_REPAIR_FINDINGS: [],  # empty
            }

        instr = provider(_Ctx())
        assert '<repair_findings>' not in instr
        assert 'Repair mode' not in instr
        # Patch mode still fires because the previous_bundle is present.
        assert 'Patch mode' in instr

    def test_repair_findings_without_patch_mode_is_disabled(
        self, tmp_path: Path
    ):
        """``enable_patch_mode=False`` removes BOTH auto-injected inputs.
        Repair mode rides on the same flag because the repair footer
        explicitly asserts patch-mode contracts."""
        from app.sub_agents._section_agent import STATE_REPAIR_FINDINGS

        agent_factory = self._build(tmp_path, enable_patch_mode=False)
        provider = agent_factory.worker['instruction']

        class _Ctx:
            state = {
                'extraction_manifest': {'project': 'P1'},
                STATE_REPAIR_FINDINGS: [{'category': 'x'}],
            }

        instr = provider(_Ctx())
        assert '<repair_findings>' not in instr
        assert 'Repair mode' not in instr

    def test_repair_footer_carries_load_bearing_phrases(
        self, tmp_path: Path
    ):
        """Smoke-test the footer's anchor phrases — these are the
        invariants the QualityLoopAgent relies on the LLM following."""
        from app.sub_agents._section_agent import STATE_REPAIR_FINDINGS

        agent_factory = self._build(tmp_path)
        provider = agent_factory.worker['instruction']

        class _Ctx:
            state = {
                'extraction_manifest': {'project': 'P1'},
                'app:sow:requirements': {
                    'functional_requirements': [{'number': 'FR-01'}],
                    'non_functional_requirements': [],
                },
                STATE_REPAIR_FINDINGS: [{'category': 'x'}],
            }

        instr = provider(_Ctx())
        # Each rule the loop depends on is visible.
        assert 'Address every finding' in instr
        assert 'minimum delta' in instr
        assert 'Patch-mode rules above still apply' in instr
        assert 'Cross-section coordination is implicit' in instr


class TestRepairModeProvider:
    """Direct provider tests — same shape as TestPatchModeProvider."""

    def test_repair_label_must_match_to_activate_footer(self):
        from app.sub_agents import _section_agent

        provider = _section_agent._make_worker_instruction_provider(
            skill_body='SKILL',
            output_protocol='\nOUTPUT',
            state_inputs=(('manifest', 'mkey'),),
            optional_state_inputs=(
                ('previous_bundle', 'pkey'),
                ('repair_findings', 'rkey'),
                ('other_optional', 'okey'),
            ),
            patch_mode_label='previous_bundle',
            repair_mode_label='repair_findings',
        )

        class _Ctx:
            state = {
                'mkey': {'x': 1},
                'pkey': {'y': 2},  # patch mode active
                'okey': 'noise',  # not the repair label
                # 'rkey' deliberately absent
            }

        instr = provider(_Ctx())
        assert 'Patch mode' in instr
        assert '<repair_findings>' not in instr
        assert 'Repair mode' not in instr

    def test_no_repair_label_means_footer_never_fires(self):
        """``repair_mode_label=None`` is the opt-out — even a populated
        optional input does not activate repair mode."""
        from app.sub_agents import _section_agent

        provider = _section_agent._make_worker_instruction_provider(
            skill_body='SKILL',
            output_protocol='\nOUTPUT',
            state_inputs=(('manifest', 'mkey'),),
            optional_state_inputs=(('repair_findings', 'rkey'),),
            patch_mode_label=None,
            repair_mode_label=None,
        )

        class _Ctx:
            state = {'mkey': {'x': 1}, 'rkey': [{'cat': 'c'}]}

        instr = provider(_Ctx())
        assert '<repair_findings>' in instr  # block still rendered
        assert 'Repair mode' not in instr  # but footer suppressed


class TestSectionAgentsAllOptInToRepairMode:
    """Belt-and-suspenders: confirms every real section agent gets the
    repair_findings input auto-injected. Mirrors the patch-mode opt-in
    test above so a future refactor that breaks ONE wiring trips here."""

    @pytest.mark.parametrize(
        'module_path,agent_attr,output_key',
        [
            (
                'app.sub_agents.requirements',
                'requirements_agent',
                'app:sow:requirements',
            ),
            (
                'app.sub_agents.delivery_plan',
                'delivery_plan_agent',
                'app:sow:delivery_plan',
            ),
            (
                'app.sub_agents.scope_boundaries',
                'scope_boundaries_agent',
                'app:sow:scope_boundaries',
            ),
            (
                'app.sub_agents.architecture',
                'architecture_agent',
                'app:sow:architecture',
            ),
            (
                'app.sub_agents.narrative',
                'narrative_agent',
                'app:sow:narrative',
            ),
        ],
    )
    def test_worker_provider_emits_repair_footer_when_findings_present(
        self, module_path: str, agent_attr: str, output_key: str
    ):
        import importlib

        from app.sub_agents._section_agent import STATE_REPAIR_FINDINGS
        from app.sub_agents.schemas import SOW_BUNDLE_STATE_KEYS

        mod = importlib.import_module(module_path)
        agent = getattr(mod, agent_attr)
        worker = agent.sub_agents[0]
        provider = worker.instruction

        non_empty_marker = {'__filled__': True}
        state = {
            SOW_BUNDLE_STATE_KEYS['manifest']: non_empty_marker,
            SOW_BUNDLE_STATE_KEYS['requirements']: non_empty_marker,
            SOW_BUNDLE_STATE_KEYS['delivery_plan']: non_empty_marker,
            SOW_BUNDLE_STATE_KEYS['scope_boundaries']: non_empty_marker,
            SOW_BUNDLE_STATE_KEYS['architecture']: non_empty_marker,
            SOW_BUNDLE_STATE_KEYS['narrative']: non_empty_marker,
        }
        # Section already produced a bundle (precondition for repair).
        state[output_key] = {'__prev__': True}
        # Orchestrator populated the repair findings slot.
        state[STATE_REPAIR_FINDINGS] = [
            {
                'category': 'activities_vs_deliverables',
                'severity': 'MAJOR',
                'evidence': 'WS-03 has no activity row.',
                'recommendation': 'Add the matching activity.',
                'fields': ['activity_phases', 'deliverables'],
            },
        ]

        class _Ctx:
            pass

        _Ctx.state = state
        instr = provider(_Ctx())
        assert '<repair_findings>' in instr, (
            f'{agent_attr}: repair_findings block must render when '
            f'STATE_REPAIR_FINDINGS is populated.'
        )
        assert 'Repair mode' in instr, (
            f'{agent_attr}: repair-mode footer missing — the '
            'QualityLoopAgent cannot route cross-section findings to '
            'this section if the footer never fires.'
        )
