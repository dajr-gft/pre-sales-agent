"""Factory for SOW section sub-agents (worker + formatter pattern).

Why the split: the ADK ``LlmAgent`` docstring states that setting
``output_schema`` makes the agent ``"ONLY reply and CANNOT use any
tools"`` (see ``llm_agent.py`` "Controlled input/output configurations"
note). The smoke test on a single-agent variant confirmed Gemini
silently drops the resources toolset when both are set, producing a
bundle without consulting any SKILL.md reference.

The fallback documented in plan v2.1 §6.3 — and applied here as the
canonical pattern for every section — is:

- ``<section>_worker``: SKILL.md + tools enabled (resources toolset +
  any extras like the diagram generator), NO ``output_schema``,
  produces a JSON draft saved to ``state[<draft_key>]`` via ``output_key``.
- ``<section>_formatter``: NO tools, ``output_schema=<Bundle>``, reads
  the draft from state via an instruction provider, emits the schema-
  valid bundle to ``state[<output_key>]``.
- ``<section>_agent``: ``SequentialAgent`` wrapping both. The root only
  sees this SequentialAgent (via ``AgentTool``).

The factory caps the per-section boilerplate at a single function call.

## Runtime input contract

The worker also runs with ``include_contents='none'`` — the root's
conversation history is dropped on entry. To prevent the worker from
fabricating content, the factory builds an **instruction provider**
(callable) that reads pre-declared state keys at every turn and
injects them into the prompt as labelled XML blocks. Each section
declares the packet it needs via ``state_inputs=`` — typically the
extraction manifest plus the bundles produced by the prior Phase
Steps. When any declared input is missing from state, the provider
overrides the closing instruction with a STOP-and-emit-empty-bundle
directive so the worker never invents content from training data.

## Patch mode (F-12)

When ``enable_patch_mode=True`` (the default), the factory also
declares an OPTIONAL input pointing at the section's own ``output_key``
labelled ``previous_bundle``. On the first run that key is empty in
state and nothing is injected — the worker generates from scratch as
usual. On a re-invocation (revision after a review gate, upstream
cascade) the worker sees its prior output alongside the upstream
packet and the provider appends a patch-mode footer instructing the
worker to: preserve every existing id byte-for-byte, carry every
untouched item from ``previous_bundle`` verbatim, and apply only the
minimum delta required by the new upstream inputs or the implicit
edit signal from the orchestrator. This is the structural safeguard
against the "regenerate-from-scratch on every revision" hazard —
without it, a targeted user edit like "change NFR-03 to multi-zone"
would re-roll every other FR/NFR/id in the bundle by coincidence.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from google.adk.agents import Agent, SequentialAgent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.models import Gemini
from google.adk.skills import load_skill_from_dir
from google.genai import types
from pydantic import BaseModel

from ..config import config
from ..shared.safety import build_safety_settings
from ._resources_toolset import SectionResourcesToolset

_SKILLS_DIR = Path(__file__).parents[1] / 'skills'

# Lowest viable thinking budget for the formatter — it is doing
# mechanical re-serialization, not reasoning. Keeping this low matters
# because the formatter runs once per section per round of the SOW flow.
_FORMATTER_THINKING_BUDGET = 512


def _build_worker_output_protocol(output_example: str) -> str:
    """The trailing instruction that turns SKILL.md into a draft producer."""
    return f"""

---

## Output protocol (binding)

After running every step above, end your turn with a single JSON object
that exactly matches the shape below — no prose before or after, no
markdown fences:

{output_example}

The downstream formatter will reject the response if extra top-level
keys appear. If you must convey caveats, put them inside a description
field; never in surrounding prose.
"""


_FORMATTER_BASE_INSTRUCTION = """<role>
You are a strict schema enforcer. You receive a draft produced by an
upstream section worker and you return ONLY a JSON object matching the
target bundle schema for this section.
</role>

<rules>
- Preserve every item from the draft. Do not drop, add, merge, rephrase,
  or reorder items. Stable ids (FR-01, NFR-01, etc.) stay byte-for-byte.
- If the draft is wrapped in markdown fences or surrounding prose, strip
  them and emit the JSON only.
- If the draft is already valid JSON for this schema, emit it verbatim.
- Never invent content. If the draft is missing a required array, leave
  it empty — the validation critic will catch it.
- NEVER produce keys outside the target schema.
</rules>
"""


def _make_formatter_instruction_provider(draft_key: str):
    """Build an instruction provider that interpolates the worker's draft."""

    def _provider(ctx: ReadonlyContext) -> str:
        draft = ctx.state.get(draft_key) or ''
        return (
            _FORMATTER_BASE_INSTRUCTION
            + '\n<draft>\n'
            + draft
            + '\n</draft>\n'
        )

    return _provider


def _serialize_state_value(value: Any) -> str:
    """Compact JSON encoding for state-derived runtime inputs.

    Compact (``separators=(',', ':')``, no indent) keeps prompts lean —
    a fully prettified manifest can run into thousands of tokens. We
    fall back to ``repr`` for anything ``json`` cannot encode so a
    bizarre state value doesn't blow up the whole turn; the worker will
    treat it as raw text. ``ensure_ascii=False`` preserves Portuguese
    accents in customer / vendor names.
    """
    try:
        return json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    except (TypeError, ValueError):
        return repr(value)


def _is_present(value: Any) -> bool:
    """Return True when a state value should count as 'provided'.

    Empty dicts / lists / strings are treated as MISSING — the section
    agents need substantive content, not zero-length placeholders, to
    do their work. ``None`` is obviously missing.
    """
    if value is None:
        return False
    if isinstance(value, (dict, list, str, tuple, set)) and not value:
        return False
    return True


_MISSING_INPUTS_FOOTER = (
    '\n\n---\n\n'
    '# Runtime inputs — MISSING (do not fabricate)\n\n'
    'The following declared inputs are NOT available in session state '
    'on this turn:\n{missing_list}\n\n'
    '**STOP.** Do NOT invent content from prior training, general '
    'knowledge, or earlier turns. Required upstream state has not been '
    'written yet — the orchestrator invoked this section out of order '
    'or with an interrupted manifest.\n\n'
    'End your turn with the JSON object below, exactly as written, '
    'matching the Output protocol shape declared above. Use empty '
    'arrays / empty objects for every list field and the literal '
    'string `"MISSING_INPUT"` for every required scalar string field. '
    'The orchestrator detects this sentinel and surfaces to the user.\n'
)


_INPUTS_PRESENT_FOOTER = (
    '\n\n---\n\n'
    '# Runtime inputs\n\n'
    '{rendered_inputs}\n\n'
    'Use ONLY the data above plus the references loaded via '
    '`load_skill_resource`. Do NOT invent vendors, systems, '
    'integrations, dates, costs, SLAs, scope commitments, customer '
    'responsibilities, or business facts that are not grounded in the '
    'inputs above or the references. When the manifest is silent on a '
    'topic that the style guide or architecture references cover, '
    'safe inference from those references is allowed; inventing new '
    'facts is not.\n'
)


# F-12 — patch-mode footer appended ON TOP OF the present-inputs footer
# whenever a ``previous_bundle`` was injected. The bundle in question is
# the section's OWN last output (the same agent has run before in this
# session). Without this footer the worker would treat every invocation
# as a first-generation and regenerate the whole section from upstream
# alone — losing IDs, re-wording untouched items, and amplifying any
# upstream change into a full cascade. With it, the worker preserves
# every untouched item and applies only the delta the new inputs
# (manifest update, upstream bundle change, or user request received
# via the orchestrator's invocation) demand.
_PATCH_MODE_FOOTER = (
    '\n---\n\n'
    '# Patch mode (binding — `<previous_bundle>` is present)\n\n'
    'You have already produced this bundle in a prior turn (see '
    '`<previous_bundle>` above). The orchestrator is re-invoking you '
    'because either the user requested a targeted change after a review '
    'gate, or an upstream section was updated and you need to reconcile. '
    'This is a PATCH, not a regeneration.\n\n'
    '**Rules (non-negotiable):**\n\n'
    '1. **Preserve every existing id byte-for-byte** (FR-01, NFR-01, '
    'WS-01, OOS-01, A-01, …). Never reorder, renumber, swap, or recycle '
    'ids. Removed items leave a gap in the numeric sequence; new items '
    'append after the last existing id.\n'
    '2. **Carry every untouched item from `<previous_bundle>` into your '
    'new output byte-for-byte** — same id, same wording, same field '
    'order. The only items whose text may change are the ones the '
    'requested edit or the upstream reconciliation actually requires.\n'
    '3. **Identify the minimum delta first**, then apply it. Diff the '
    'new upstream inputs against `<previous_bundle>` and the implicit '
    'instruction from the orchestrator; the diff is the patch surface, '
    'and nothing outside it should move.\n'
    '4. **Never drop a previous item silently.** Removals are deliberate '
    'and require an upstream signal (e.g. the manifest no longer carries '
    'the system the FR named).\n'
    '5. **Cross-section cascades** (e.g. an NFR target changed → a '
    'related success criterion needs updating) follow the same minimum-'
    'change discipline: touch the dependent item, leave its siblings '
    'alone.\n'
    '6. The reference loaded for "patching" in your SKILL.md '
    '(`id-stability-rules.md`) overrides any general "regenerate the '
    'section" instinct in the rest of the skill. Re-read it before '
    'emitting your draft.\n'
)


def _make_worker_instruction_provider(
    *,
    skill_body: str,
    output_protocol: str,
    state_inputs: tuple[tuple[str, str], ...],
    optional_state_inputs: tuple[tuple[str, str], ...] = (),
    patch_mode_label: str | None = None,
):
    """Build the runtime instruction for a section worker.

    The provider is invoked by ADK every time the worker runs, so it
    sees the latest state — including any upstream bundle written by a
    prior section agent within the same SOW build. The closure captures
    only immutable strings + the input tuples; no references to mutable
    state.

    Args:
        skill_body: ``SKILL.md`` instructions block (already stripped
            of frontmatter by ``load_skill_from_dir``).
        output_protocol: The closing block built by
            :func:`_build_worker_output_protocol`. Comes pre-built so
            the factory has full control over the example shape that
            shows up in MISSING mode and PRESENT mode alike.
        state_inputs: Ordered tuple of REQUIRED ``(label, state_key)``
            pairs. ``label`` becomes the XML tag in the rendered prompt
            (``<extraction_manifest>...</extraction_manifest>``) and
            also appears in the MISSING listing so the worker — and the
            user reading logs — sees exactly which dependency is gone.
        optional_state_inputs: Ordered tuple of OPTIONAL
            ``(label, state_key)`` pairs. Injected as XML blocks when
            present in state, silently omitted when absent (does NOT
            trigger the MISSING-and-emit-empty-bundle path). Use for
            inputs whose absence on first run is the normal case (the
            ``previous_bundle`` carrying the section's own prior output
            is the canonical use).
        patch_mode_label: When set AND the matching ``optional_state_inputs``
            label is present in state at provider time, append
            :data:`_PATCH_MODE_FOOTER` so the worker switches from first-
            generation to patch discipline. Typically set to
            ``'previous_bundle'`` for section agents.

    Returns:
        A callable accepted by ADK's ``LlmAgent(instruction=...)``.
    """

    def _provider(ctx: ReadonlyContext) -> str:
        state = ctx.state
        rendered: list[str] = []
        missing: list[str] = []

        for label, key in state_inputs:
            value = state.get(key)
            if not _is_present(value):
                missing.append(f'- `{label}` (state[{key!r}])')
                continue
            rendered.append(
                f'<{label}>\n{_serialize_state_value(value)}\n</{label}>'
            )

        base = skill_body + output_protocol
        if missing:
            # MISSING required inputs always wins — even when an optional
            # previous_bundle is sitting in state. A bundle without its
            # upstream packet is no patching target either, since the
            # upstream change is what would drive the diff.
            footer = _MISSING_INPUTS_FOOTER.format(
                missing_list='\n'.join(missing)
            )
            return base + footer

        # Optional inputs — collected after the required ones so they
        # appear AFTER the upstream packet in the prompt (the model
        # reads top-down; upstream context first, then "here's what
        # you produced last time").
        patch_mode_active = False
        for label, key in optional_state_inputs:
            value = state.get(key)
            if not _is_present(value):
                continue
            rendered.append(
                f'<{label}>\n{_serialize_state_value(value)}\n</{label}>'
            )
            if patch_mode_label is not None and label == patch_mode_label:
                patch_mode_active = True

        if not rendered:
            # No declared inputs at all. Worker runs with SKILL.md +
            # output protocol only. Kept explicit so future readers see
            # this branch is intentional, not a bug.
            return base

        prompt = base + _INPUTS_PRESENT_FOOTER.format(
            rendered_inputs='\n\n'.join(rendered)
        )
        if patch_mode_active:
            prompt += _PATCH_MODE_FOOTER
        return prompt

    return _provider


_PREVIOUS_BUNDLE_LABEL = 'previous_bundle'


def build_section_agent(
    *,
    name: str,
    description: str,
    skill_name: str,
    output_schema: type[BaseModel],
    output_key: str,
    output_example: str,
    extra_tools: list[Any] | None = None,
    extra_skills_for_resources: tuple[str, ...] = ('sow-shared',),
    state_inputs: tuple[tuple[str, str], ...] = (),
    extra_optional_state_inputs: tuple[tuple[str, str], ...] = (),
    enable_patch_mode: bool = True,
    model: str | None = None,
    temperature: float | None = None,
    thinking_budget: int | None = None,
) -> SequentialAgent:
    """Build a section specialist as a worker + formatter ``SequentialAgent``.

    Args:
        name: Public ADK agent name. Should end in ``_agent`` (e.g.
            ``"requirements_agent"``); the worker/formatter sub-agents
            derive their names from this stem.
        description: One-line capability description shown to the root.
        skill_name: Folder under ``app/skills/`` whose ``SKILL.md`` becomes
            the worker's ``instruction=``.
        output_schema: Pydantic model the formatter enforces.
        output_key: Canonical session state key for the final bundle.
            The intermediate draft is stored at ``f"{output_key}:draft"``.
        output_example: JSON shape hint appended to the worker's prompt
            so it knows the expected layout. Keep it short — a one-row
            example per list field is enough.
        extra_tools: Section-specific tools available to the worker
            (e.g. ``generate_architecture_diagram`` for architecture,
            an ``AgentTool(search)`` for narrative).
        extra_skills_for_resources: Additional skills whose ``references/``
            should be reachable via ``load_skill_resource`` (default:
            ``('sow-shared',)``). The section's own skill is always
            included; duplicates are deduplicated.
        state_inputs: Ordered tuple of REQUIRED ``(label, state_key)``
            pairs to inject into the worker's prompt at every turn. The
            label becomes the XML tag (``<label>...</label>``) and the
            value at ``ctx.state[state_key]`` is JSON-serialized into
            the block. Each section declares ONLY the upstream
            artifacts it actually needs (e.g. ``requirements_agent``
            takes the manifest only; ``narrative_agent`` takes every
            prior bundle). When any required input is missing from
            state, the provider switches to a STOP-and-emit-empty-
            bundle footer so the worker cannot fabricate content out
            of training data. See the module docstring "Runtime input
            contract" for the full rationale.
        extra_optional_state_inputs: Ordered tuple of optional
            ``(label, state_key)`` pairs in addition to the auto-
            injected ``previous_bundle`` (see ``enable_patch_mode``).
            Optional inputs are silently omitted when missing — they
            never trigger the MISSING-bundle path. Use for ancillary
            context whose absence is the normal first-run case.
        enable_patch_mode: When ``True`` (the default) the factory
            auto-appends an optional input pointing at the section's
            OWN ``output_key`` labelled ``"previous_bundle"``. On a
            re-invocation (revision after a review gate, upstream
            cascade) the worker sees its prior output and switches to
            patch discipline — preserving ids, leaving untouched items
            byte-for-byte, applying only the minimum delta. The
            instruction provider appends a patch-mode footer
            (:data:`_PATCH_MODE_FOOTER`) whenever the previous_bundle
            input is populated at runtime; on first generation (no
            prior output in state) the footer is omitted and the
            worker generates from scratch as usual. Set to ``False``
            only if a section legitimately should always regenerate
            from upstream (no current section does — the default is
            what you want).
        model: Override the Gemini model id (defaults to
            ``config.GEMINI_MODEL``).
        temperature: Override generation temperature for the worker.
        thinking_budget: Override worker thinking token budget. The
            formatter uses a fixed low budget regardless.

    Returns:
        ``SequentialAgent`` with two sub-agents: worker then formatter.
        Wrap it in ``AgentTool`` to expose it to the root.
    """
    own_skill_dir = _SKILLS_DIR / skill_name
    if not (own_skill_dir / 'SKILL.md').is_file():
        raise FileNotFoundError(
            f"Skill '{skill_name}' has no SKILL.md at {own_skill_dir}.",
        )

    section_stem = name.removesuffix('_agent') if name.endswith('_agent') else name
    draft_key = f'{output_key}:draft'

    own_skill = load_skill_from_dir(own_skill_dir)
    resources_skills = [own_skill]
    for extra_name in extra_skills_for_resources:
        if extra_name == skill_name:
            continue
        resources_skills.append(load_skill_from_dir(_SKILLS_DIR / extra_name))

    resources_toolset = SectionResourcesToolset(skills=resources_skills)
    worker_tools: list[Any] = [resources_toolset]
    if extra_tools:
        worker_tools.extend(extra_tools)

    effective_model = model or config.GEMINI_MODEL

    # Auto-inject the previous-bundle optional input when patch mode is
    # enabled. Placing it FIRST in the optional tuple keeps the order
    # deterministic regardless of what callers pass in
    # ``extra_optional_state_inputs``; reorder caller-supplied inputs
    # explicitly if a different order ever matters.
    optional_inputs: tuple[tuple[str, str], ...] = ()
    if enable_patch_mode:
        optional_inputs = ((_PREVIOUS_BUNDLE_LABEL, output_key),)
    if extra_optional_state_inputs:
        # De-dupe by state_key so a caller cannot shadow the auto-
        # injected previous_bundle with the same key under a different
        # label.
        seen_keys = {key for _, key in optional_inputs}
        optional_inputs = optional_inputs + tuple(
            (label, key)
            for label, key in extra_optional_state_inputs
            if key not in seen_keys
        )

    worker = Agent(
        name=f'{section_stem}_worker',
        description=(
            f'Drafts content for the {section_stem} section, loading '
            'references via load_skill_resource. Persists the JSON draft '
            f'to state[{draft_key!r}]. '
            f'Internal helper of {name} — never invoke directly.'
        ),
        model=Gemini(
            model=effective_model,
            retry_options=types.HttpRetryOptions(attempts=config.MAX_RETRIES),
        ),
        instruction=_make_worker_instruction_provider(
            skill_body=own_skill.instructions,
            output_protocol=_build_worker_output_protocol(output_example),
            state_inputs=state_inputs,
            optional_state_inputs=optional_inputs,
            patch_mode_label=(
                _PREVIOUS_BUNDLE_LABEL if enable_patch_mode else None
            ),
        ),
        include_contents='none',
        tools=worker_tools,
        output_key=draft_key,
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
        generate_content_config=types.GenerateContentConfig(
            temperature=(
                temperature if temperature is not None else config.TEMPERATURE
            ),
            safety_settings=build_safety_settings(),
            thinking_config=types.ThinkingConfig(
                include_thoughts=False,
                thinking_budget=(
                    thinking_budget
                    if thinking_budget is not None
                    else config.THINKING_BUDGET
                ),
            ),
        ),
    )

    formatter = Agent(
        name=f'{section_stem}_formatter',
        description=(
            f'Converts the {section_stem}_worker draft into a '
            f'{output_schema.__name__}. No tools, no reasoning over '
            f'content — pure schema enforcement. Internal helper of {name}.'
        ),
        model=Gemini(
            model=effective_model,
            retry_options=types.HttpRetryOptions(attempts=config.MAX_RETRIES),
        ),
        instruction=_make_formatter_instruction_provider(draft_key),
        include_contents='none',
        output_schema=output_schema,
        output_key=output_key,
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
        generate_content_config=types.GenerateContentConfig(
            temperature=0.0,  # deterministic re-serialization
            safety_settings=build_safety_settings(),
            thinking_config=types.ThinkingConfig(
                include_thoughts=False,
                thinking_budget=_FORMATTER_THINKING_BUDGET,
            ),
        ),
    )

    return SequentialAgent(
        name=name,
        description=description,
        sub_agents=[worker, formatter],
    )
