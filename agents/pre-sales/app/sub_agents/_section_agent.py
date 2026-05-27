"""Factory for SOW section *repair* sub-agents (tool-based patching).

In the root-skills variant the first-gen section agents are gone — the
root generates each section inline by loading the section skill and
calling ``save_<section>_bundle``. What remains here is the repair
path: :func:`build_section_repair_agent` builds a single tool-based
:class:`Agent` that the QualityLoopAgent invokes when a finding must be
fixed by section-internal knowledge rather than by the generic
revision_agent.

## Repair mode

The QualityLoopAgent writes the section's findings to
``state[STATE_REPAIR_FINDINGS]`` and invokes the section repair agent.
The factory auto-injects that key as the optional ``repair_findings``
input alongside the section's own ``previous_bundle``; the worker calls
``apply_<section>_patch`` to mutate the bundle transactionally. ADK's
constraint that ``output_schema`` disables tools is why a regenerate
flow would need a separate formatter — the tool-based repair flow does
not, so this is a single ``Agent``.

Repair mode never runs alone — it requires ``<previous_bundle>`` to be
present, which the orchestrator guarantees by only repair-routing AFTER
a first generation has populated the bundle key. The provider emits a
STOP directive when the findings packet is absent so a mis-routed
invocation cannot drift.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from google.adk.agents import Agent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.models import Gemini
from google.adk.skills import load_skill_from_dir
from google.genai import types

from ..config import config
from ..shared.safety import build_safety_settings
from ._resources_toolset import SectionResourcesToolset

_SKILLS_DIR = Path(__file__).parents[1] / 'skills'

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
    'or with a missing upstream bundle.\n\n'
    'End your turn with the JSON object below, exactly as written, '
    'matching the Output protocol shape declared above. Use empty '
    'arrays / empty objects for every list field and the literal '
    'string `"MISSING_INPUT"` for every required scalar string field. '
    'The orchestrator detects this sentinel and surfaces to the user.\n'
)


_PREVIOUS_BUNDLE_LABEL = 'previous_bundle'

# Repair mode (commit 3 of the cross-section repair router). The
# QualityLoopAgent writes the section's findings here before invoking
# the section agent in repair mode; the agent reads them from the
# auto-injected ``<repair_findings>`` block. Shared across all section
# agents because only one repair runs at a time per loop iteration.
_REPAIR_FINDINGS_LABEL = 'repair_findings'
STATE_REPAIR_FINDINGS = 'app:sow:repair_findings'


def _build_repair_tool_footer(
    *, section_name: str, max_ops_per_call: int,
) -> str:
    """Compose the repair-mode-with-tool footer for ``section_name``.

    The footer is interpolated rather than templated so the section
    name appears in every reference to the tool function — Gemini is
    sensitive to whether the prompt agrees with the function-call
    schema it is being given.
    """
    tool_name = f'apply_{section_name}_patch'
    return (
        '\n---\n\n'
        '# Repair mode (tool-based — binding)\n\n'
        'The QualityLoopAgent flagged specific defects in your section. '
        'Each entry in `<repair_findings>` carries `category`, `severity`, '
        '`evidence`, `recommendation`, and `fields`.\n\n'
        f'You MUST address every finding by calling `{tool_name}(ops=[...])`. '
        'Do NOT emit a JSON bundle in your reply — the tool persists '
        'changes directly to the bundle state. After the tool returns, '
        'end your turn with a short summary message naming which findings '
        'you addressed and which op kinds you used.\n\n'
        '## Tool op vocabulary (every list-touching op REQUIRES `collection`)\n\n'
        '- `update_item(collection, item_id, fields={...})` — modify one '
        "or more fields of an existing item INSIDE the named collection. "
        "The item's identity field (number / name / role / service) is "
        'preserved AUTOMATICALLY — the tool REJECTS attempts to change '
        'it via `fields`. Example: `collection="deliverables"`, '
        '`item_id="WS-03"`, `fields={"description": "..."}`.\n'
        '- `add_item(collection, item={...}, after_item_id=None)` — append '
        'a new item to the named collection. If the identity field is '
        'omitted, the next sequential id is auto-assigned. '
        '`after_item_id` places the new item right after the named id; '
        'default is end of list.\n'
        '- `remove_item(collection, item_id, coverage_transferred_to=<other_id> | None)` '
        '— remove an item from the named collection. If any manifest '
        'anchor (FR-NN, WS-NN, A-NN, …) was riding on the removed item, '
        'you MUST set `coverage_transferred_to` to another item id that '
        'now covers it. If no coverage was at stake, set it to `None` '
        'explicitly to acknowledge you checked.\n'
        '- `update_field(field, value)` — overwrite a top-level non-list '
        'field (`executive_summary`, `change_request_policy_text`, '
        '`architecture_description`). For string-list collections '
        '(`assumptions`, `out_of_scope`, `success_criteria`, '
        '`objectives`, `handover_disclaimers`), pass the WHOLE '
        'replacement list as `value` — item-level ops are not available '
        'for these collections.\n\n'
        '## Constraints\n\n'
        '1. **Every op MUST cite `finding_id`** from `<repair_findings>` '
        'that motivates it. No anonymous patches.\n'
        f'2. **Maximum {max_ops_per_call} ops per tool call.** If you '
        'have more, prioritise by severity (BLOCKER > MAJOR > MINOR) '
        'then by `recommendation` specificity; tail findings can run '
        'in a follow-up call within the same turn.\n'
        '3. **NEVER emit a full bundle JSON in your reply.** The tool '
        'is the only writer.\n'
        '4. **If the tool returns a ToolError**, read the '
        '`validation_errors` carefully and retry with corrected ops. Do '
        'not silently move on.\n'
        '5. **If the tool returns `status: "ok_with_warnings"` with '
        '`anchor_drops`**, decide deliberately: either restore the '
        'anchor by patching another item that now covers it, or '
        'document in your summary message that the removal is '
        'intentional. Do not ignore the warning.\n'
        '6. **The bundle is the source of truth.** Do not call '
        '`stage_sow` or any other tool that mutates the flat SOW — the '
        'loop reassembles it from your bundle after you finish.\n'
    )


_REPAIR_TOOL_INPUTS_FOOTER = (
    '\n\n---\n\n'
    '# Runtime inputs\n\n'
    '{rendered_inputs}\n\n'
    'Use ONLY the data above plus the references loaded via '
    '`load_skill_resource` to decide which patch ops to apply. Do NOT '
    'invent vendors, integrations, scope commitments, or business facts '
    'that are not grounded in the inputs above or the references.\n'
)


def _make_repair_worker_instruction_provider(
    *,
    skill_body: str,
    state_inputs: tuple[tuple[str, str], ...],
    optional_state_inputs: tuple[tuple[str, str], ...],
    repair_tool_footer: str,
    required_repair_label: str,
):
    """Build the runtime instruction provider for a section repair agent.

    Renders the required/optional state inputs as labelled XML blocks and
    appends the tool-based repair footer. Key points:

    - No "Output protocol" block — the patch tool is the writer; the
      agent's final message is informational.
    - When ``required_repair_label`` is absent from state at runtime,
      the provider returns a STOP directive — repair mode without a
      findings packet is a contract violation the orchestrator must
      not produce.
    - The repair-tool footer is always appended when the repair input
      IS present (and required inputs are all present); the patch-mode
      legacy footer is NOT layered on, because the tool encodes those
      invariants by construction.
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

        if missing:
            # No output protocol to fall back to here — emit the same
            # STOP directive as the regular section worker so the
            # orchestrator's diagnostics still recognise the sentinel.
            return skill_body + _MISSING_INPUTS_FOOTER.format(
                missing_list='\n'.join(missing)
            )

        repair_present = False
        for label, key in optional_state_inputs:
            value = state.get(key)
            if not _is_present(value):
                continue
            rendered.append(
                f'<{label}>\n{_serialize_state_value(value)}\n</{label}>'
            )
            if label == required_repair_label:
                repair_present = True

        if not repair_present:
            # The QualityLoopAgent populates STATE_REPAIR_FINDINGS
            # right before invoking this agent. An invocation without
            # findings means the loop is mis-routing — emit a STOP
            # directive so the agent does not patch arbitrarily.
            return (
                skill_body
                + '\n\n---\n\n'
                + '# Stop — no repair findings\n\n'
                + 'This repair agent was invoked without '
                + f'`<{required_repair_label}>` in state. Do not call '
                + 'any patch tool. End your turn with a brief message '
                + 'noting the missing input.\n'
            )

        prompt = skill_body + _REPAIR_TOOL_INPUTS_FOOTER.format(
            rendered_inputs='\n\n'.join(rendered)
        )
        prompt += repair_tool_footer
        return prompt

    return _provider


def build_section_repair_agent(
    *,
    name: str,
    description: str,
    section_name: str,
    skill_name: str,
    bundle_key: str,
    patch_tool: Callable[..., Any],
    extra_tools: list[Any] | None = None,
    extra_skills_for_resources: tuple[str, ...] = ('sow-shared',),
    state_inputs: tuple[tuple[str, str], ...] = (),
    extra_optional_state_inputs: tuple[tuple[str, str], ...] = (),
    max_ops_per_call: int = 5,
    model: str | None = None,
    temperature: float | None = None,
    thinking_budget: int | None = None,
) -> Agent:
    """Build a section repair specialist that patches via tool calls.

    Returns a single :class:`Agent` — there is no formatter because the
    bundle is mutated by ``patch_tool`` in state, not by parsing an
    LLM-emitted JSON draft. ADK's constraint that ``output_schema``
    disables tools is why a regenerate flow would need two agents; the
    tool-based flow does not.

    Args:
        name: Public ADK agent name (e.g. ``"delivery_plan_repair_agent"``).
        description: One-liner shown in the agent registry / logs.
        section_name: Stem identifying the section (``"delivery_plan"``,
            ``"requirements"``, …). Used to interpolate the tool name
            into the repair footer.
        skill_name: Folder under ``app/skills/`` whose ``SKILL.md``
            becomes the agent's base instruction. Same skill the root
            uses to generate the section — domain knowledge is shared.
        bundle_key: Session-state key for the section's own bundle
            (``app:sow:delivery_plan``). The agent reads this as
            ``<previous_bundle>`` so it knows what's already there
            before deciding which ops to emit.
        patch_tool: The section-specific patch tool produced by
            :func:`app.tools.sow.apply_section_patch._build_apply_section_patch`.
            ADK inspects ``__name__`` of the callable, so passing the
            same callable to a sibling repair agent would let it patch
            another section — which is the bug Per-section isolation
            prevents. Always pair ``section_name`` with the matching
            tool.
        extra_tools: Section-specific tools beyond the patch tool and
            the resources toolset. Most sections need none.
        extra_skills_for_resources: Additional skills available via
            ``load_skill_resource`` (default ``('sow-shared',)``).
        state_inputs: REQUIRED ``(label, state_key)`` pairs — the
            upstream section bundles this repair needs as read context.
            Typical packet: ``(('prior_requirements', ...),)``. When ANY
            is missing, the provider switches to the STOP-and-emit-empty
            footer and the agent does not call the patch tool.
        extra_optional_state_inputs: Additional optional inputs beyond
            the auto-injected ``previous_bundle`` and ``repair_findings``.
        max_ops_per_call: Pilot cap (default 5). Configurable per
            section once the vertical slice (delivery_plan) confirms
            the LLM converges within the cap on real SOWs.
        model / temperature / thinking_budget: Standard overrides.

    Returns:
        A single :class:`Agent`. Wire it into the QualityLoopAgent's
        ``repair_section_agents`` mapping; do NOT wrap it in
        ``AgentTool`` for the root (the root never invokes a section
        agent directly — it generates sections inline via skills).
    """
    own_skill_dir = _SKILLS_DIR / skill_name
    if not (own_skill_dir / 'SKILL.md').is_file():
        raise FileNotFoundError(
            f"Skill '{skill_name}' has no SKILL.md at {own_skill_dir}.",
        )

    own_skill = load_skill_from_dir(own_skill_dir)
    resources_skills = [own_skill]
    for extra_name in extra_skills_for_resources:
        if extra_name == skill_name:
            continue
        resources_skills.append(load_skill_from_dir(_SKILLS_DIR / extra_name))

    resources_toolset = SectionResourcesToolset(skills=resources_skills)
    tools: list[Any] = [resources_toolset, patch_tool]
    if extra_tools:
        tools.extend(extra_tools)

    # Auto-inject the two slots the QualityLoopAgent populates on
    # invocation: the section's own bundle (read-only context for the
    # LLM, mutated only by the patch tool) and the findings packet
    # (the repair signal — provider emits a STOP directive when it is
    # absent so a mis-routed invocation cannot drift).
    optional_inputs: tuple[tuple[str, str], ...] = (
        (_PREVIOUS_BUNDLE_LABEL, bundle_key),
        (_REPAIR_FINDINGS_LABEL, STATE_REPAIR_FINDINGS),
    )
    if extra_optional_state_inputs:
        seen_keys = {key for _, key in optional_inputs}
        optional_inputs = optional_inputs + tuple(
            (label, key)
            for label, key in extra_optional_state_inputs
            if key not in seen_keys
        )

    effective_model = model or config.SECTION_AGENT_MODEL

    repair_tool_footer = _build_repair_tool_footer(
        section_name=section_name,
        max_ops_per_call=max_ops_per_call,
    )

    return Agent(
        name=name,
        description=description,
        model=Gemini(
            model=effective_model,
            retry_options=types.HttpRetryOptions(attempts=config.MAX_RETRIES),
        ),
        instruction=_make_repair_worker_instruction_provider(
            skill_body=own_skill.instructions,
            state_inputs=state_inputs,
            optional_state_inputs=optional_inputs,
            repair_tool_footer=repair_tool_footer,
            required_repair_label=_REPAIR_FINDINGS_LABEL,
        ),
        include_contents='none',
        tools=tools,
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
                    else config.SECTION_AGENT_THINKING_BUDGET
                ),
            ),
        ),
    )
