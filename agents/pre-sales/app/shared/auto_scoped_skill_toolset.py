"""``SkillToolset`` that scopes skill content to the currently active skill.

The default ``google.adk.tools.skill_toolset.SkillToolset`` records every
``load_skill`` and ``load_skill_resource`` call as a
``function_call`` / ``function_response`` pair in
``llm_request.contents``. After several sequential ``load_skill`` calls
the LLM is reasoning over every SKILL.md it has ever opened in the run
plus all their resources — orders-of-magnitude more context than is
relevant at any given step.

``AutoScopedSkillToolset`` tracks which skill is "current" in session
state and treats previously activated skills as "inactive". Before the
next LLM call it walks ``llm_request.contents`` and drops the
function-call / function-response parts of inactive skills.

Documents loaded via ``load_artifacts`` and bundles persisted via
``save_<section>_bundle`` (or any other non-skill tool) are NEVER
pruned: they are the persistent working memory the SOW generation
pipeline relies on between phases.

State contract (under the ``app:skill_scope:`` prefix):

- ``current``      (``str | None``): the active skill.
- ``previous``     (``str | None``): the previously active skill.
- ``inactive``     (``list[str]``): scoped-out skills.
- ``history``      (``list[dict]``): append-only audit of transitions.
- ``last_prune``   (``dict``): snapshot of the most recent prune event
  (debug-oriented; overwritten each prune).
- ``prune_totals`` (``dict``): monotonic aggregator across prunes
  (telemetry-oriented; ``pruned_messages_total``,
  ``pruned_bytes_total``, ``prune_event_count``).

Invariant: the ``current`` skill is NEVER also in ``inactive`` — a
reload of the same skill removes it from ``inactive`` first.

Full design: ``~/.claude/plans/prompt-role-atue-como-harmonic-graham.md`` §5.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog
from google.adk.tools.skill_toolset import LoadSkillTool, SkillToolset
from google.adk.tools.tool_context import ToolContext

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# State keys — keep in lockstep with telemetry's ``app:skill_scope:*`` reads.
# ---------------------------------------------------------------------------

STATE_SKILL_CURRENT = 'app:skill_scope:current'
STATE_SKILL_PREVIOUS = 'app:skill_scope:previous'
STATE_SKILL_INACTIVE = 'app:skill_scope:inactive'
STATE_SKILL_HISTORY = 'app:skill_scope:history'
STATE_SKILL_LAST_PRUNE = 'app:skill_scope:last_prune'
STATE_SKILL_PRUNE_TOTALS = 'app:skill_scope:prune_totals'

_SKILL_TOOL_NAMES = frozenset({'load_skill', 'load_skill_resource'})

# Synthetic Content text injected by
# ``LoadSkillResourceTool.process_llm_request`` after a binary resource
# load. We need to recognise it so we can drop binary content paired
# with a dropped ``load_skill_resource`` call.
_BINARY_MARKER_PREFIX = "The content of binary file '"
_BINARY_MARKER_SUFFIX = "' is:"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Pure-function content classification & pruning
# ---------------------------------------------------------------------------


def _is_binary_marker_text(text: Any) -> bool:
    return (
        isinstance(text, str)
        and text.startswith(_BINARY_MARKER_PREFIX)
    )


def _extract_binary_marker_path(text: str) -> str | None:
    """Extract ``<file_path>`` from the synthetic binary marker text."""
    after_prefix = text[len(_BINARY_MARKER_PREFIX):]
    end_idx = after_prefix.find(_BINARY_MARKER_SUFFIX)
    if end_idx < 0:
        return None
    return after_prefix[:end_idx]


def _classify_content(content: Any) -> dict[str, Any]:
    """Classify a ``Content`` for pruning purposes.

    Returns a dict with:

    - ``type``: one of ``'skill_call'``, ``'skill_response'``,
      ``'binary_marker'``, ``'other'``.
    - ``skill_name``: the skill referenced (or ``None``).
    - ``tool_name``: ``'load_skill'`` / ``'load_skill_resource'``
      / other / ``None``.
    - ``file_path``: present for ``load_skill_resource`` and binary
      markers; otherwise ``None``.

    The first matching part wins. Mixed Contents (one part fc + one
    part text) are rare in practice; classifying by the first
    function_call/function_response part is correct because the SDK
    emits skill calls as a Content with a single fc/fr part.
    """
    parts = getattr(content, 'parts', None) or []

    for part in parts:
        fc = getattr(part, 'function_call', None)
        if fc is not None:
            name = getattr(fc, 'name', None)
            if name in _SKILL_TOOL_NAMES:
                args = getattr(fc, 'args', None) or {}
                return {
                    'type': 'skill_call',
                    'skill_name': args.get('skill_name'),
                    'tool_name': name,
                    'file_path': args.get('file_path'),
                }
            return {
                'type': 'other',
                'skill_name': None,
                'tool_name': name,
                'file_path': None,
            }

        fr = getattr(part, 'function_response', None)
        if fr is not None:
            name = getattr(fr, 'name', None)
            if name in _SKILL_TOOL_NAMES:
                resp = getattr(fr, 'response', None) or {}
                return {
                    'type': 'skill_response',
                    'skill_name': resp.get('skill_name'),
                    'tool_name': name,
                    'file_path': resp.get('file_path'),
                }
            return {
                'type': 'other',
                'skill_name': None,
                'tool_name': name,
                'file_path': None,
            }

    # No function_call/response → check for the synthetic binary marker.
    for part in parts:
        text = getattr(part, 'text', None)
        if _is_binary_marker_text(text):
            return {
                'type': 'binary_marker',
                'skill_name': None,
                'tool_name': None,
                'file_path': _extract_binary_marker_path(text),
            }

    return {
        'type': 'other',
        'skill_name': None,
        'tool_name': None,
        'file_path': None,
    }


def _estimate_content_bytes(content: Any) -> int:
    """Rough byte estimate for telemetry. UTF-8 length of text + repr of
    args/response + raw bytes of inline_data."""
    parts = getattr(content, 'parts', None) or []
    total = 0
    for part in parts:
        text = getattr(part, 'text', None)
        if isinstance(text, str):
            total += len(text.encode('utf-8'))
        fc = getattr(part, 'function_call', None)
        if fc is not None:
            args = getattr(fc, 'args', None) or {}
            total += len(repr(args).encode('utf-8'))
        fr = getattr(part, 'function_response', None)
        if fr is not None:
            resp = getattr(fr, 'response', None) or {}
            total += len(repr(resp).encode('utf-8'))
        inline = getattr(part, 'inline_data', None)
        if inline is not None:
            data = getattr(inline, 'data', None)
            if isinstance(data, (bytes, bytearray)):
                total += len(data)
    return total


def _prune_inactive_skill_content(
    contents: list,
    inactive_scopes: set[str],
    *,
    agent_name: str = 'root',
) -> tuple[list, dict]:
    """Drop function_call/function_response Contents of inactive skills.

    Pure function — no I/O, no state mutation. Always returns a tuple
    ``(new_contents, log)``. When ``log['integrity_ok']`` is ``False``,
    ``new_contents`` is the original ``contents`` reference unchanged
    and the caller MUST skip the prune for this turn.

    Integrity guarantees:

    - Every dropped ``skill_call`` has a matching dropped
      ``skill_response`` (matched by aggregate count).
    - The last Content is not a dangling inactive ``skill_call``
      (would imply the response hasn't been generated yet — pruning
      would break the call/response pairing the model API enforces).

    Binary synthetic Contents (the "The content of binary file 'X' is:"
    marker + inline_data injected by
    ``LoadSkillResourceTool.process_llm_request``) are dropped only
    when the marker's ``file_path`` matches a
    ``load_skill_resource`` whose pair is also being dropped — never
    on best-effort heuristics. Orphan markers are kept.
    """
    if not inactive_scopes or not contents:
        return contents, {
            'integrity_ok': True,
            'pruned_count': 0,
            'kept_count': len(contents) if contents else 0,
            'pruned_bytes': 0,
            'agent_name': agent_name,
            'skills_pruned': sorted(inactive_scopes) if inactive_scopes else [],
        }

    classifications = [_classify_content(c) for c in contents]

    inactive_calls: list[tuple[int, str, str, str | None]] = []
    inactive_responses: list[tuple[int, str, str, str | None]] = []
    drop_indices: set[int] = set()

    for i, cls in enumerate(classifications):
        skill = cls['skill_name']
        if skill not in inactive_scopes:
            continue
        if cls['type'] == 'skill_call':
            inactive_calls.append(
                (i, skill, cls['tool_name'], cls['file_path'])
            )
            drop_indices.add(i)
        elif cls['type'] == 'skill_response':
            inactive_responses.append(
                (i, skill, cls['tool_name'], cls['file_path'])
            )
            drop_indices.add(i)

    # Refuse to prune mid-flight: if the very last Content is an
    # inactive skill_call (response not in this batch yet), we'd break
    # the in-flight pairing the API expects. Check this BEFORE the
    # call/response balance check — the orphan is the cause of the
    # imbalance and the more informative diagnosis.
    last_cls = classifications[-1] if classifications else None
    if (
        last_cls
        and last_cls['type'] == 'skill_call'
        and last_cls['skill_name'] in inactive_scopes
    ):
        return contents, {
            'integrity_ok': False,
            'reason': 'orphan_function_call_in_last_content',
            'pruned_count': 0,
            'kept_count': len(contents),
            'pruned_bytes': 0,
            'agent_name': agent_name,
            'skills_pruned': sorted(inactive_scopes),
        }

    if len(inactive_calls) != len(inactive_responses):
        return contents, {
            'integrity_ok': False,
            'reason': 'unbalanced_skill_call_response_pairs',
            'inactive_calls': len(inactive_calls),
            'inactive_responses': len(inactive_responses),
            'pruned_count': 0,
            'kept_count': len(contents),
            'pruned_bytes': 0,
            'agent_name': agent_name,
            'skills_pruned': sorted(inactive_scopes),
        }

    # Drop binary synthetic Contents whose file_path matches a dropped
    # load_skill_resource call. We match by ``file_path`` only because
    # the binary marker text does not carry a ``skill_name``; the SDK
    # injects markers only for resources it just loaded, so the
    # file_path is unique to a single recent call in practice.
    dropped_resource_paths = {
        path
        for (_i, _skill, tool, path) in inactive_calls
        if tool == 'load_skill_resource' and path
    }
    if dropped_resource_paths:
        for i, cls in enumerate(classifications):
            if cls['type'] != 'binary_marker':
                continue
            marker_path = cls['file_path']
            if marker_path and marker_path in dropped_resource_paths:
                drop_indices.add(i)

    new_contents = [c for i, c in enumerate(contents) if i not in drop_indices]
    pruned_bytes = sum(_estimate_content_bytes(contents[i]) for i in drop_indices)

    return new_contents, {
        'integrity_ok': True,
        'pruned_count': len(drop_indices),
        'kept_count': len(new_contents),
        'pruned_bytes': pruned_bytes,
        'agent_name': agent_name,
        'skills_pruned': sorted(inactive_scopes),
    }


# ---------------------------------------------------------------------------
# Scoped LoadSkillTool — updates the state contract on each activation
# ---------------------------------------------------------------------------


class ScopedLoadSkillTool(LoadSkillTool):
    """``LoadSkillTool`` wrapper that maintains the skill-scope state.

    Delegates the actual SKILL.md fetch to the parent (which also
    appends the new skill to ``_adk_activated_skill_<agent>``). On top
    of that, on every successful activation we:

    1. Move the previously-current skill into ``inactive`` (unless the
       activation is a reload of the same skill).
    2. If the new skill is being reloaded after a prior scope-out,
       remove it from ``inactive`` so the invariant "current ∉ inactive"
       holds.
    3. Append a transition record to ``history``.
    4. Drop the previous skill from ``_adk_activated_skill_<agent>``
       so any ``adk_additional_tools`` it surfaced (e.g. a search tool
       declared by ``sow-narrative``) stop being exposed by
       ``SkillToolset.get_tools()``.
    """

    async def run_async(
        self, *, args: dict[str, Any], tool_context: ToolContext
    ) -> Any:
        result = await super().run_async(args=args, tool_context=tool_context)
        new_skill = args.get('skill_name')
        if not new_skill:
            return result
        if isinstance(result, dict) and result.get('error'):
            # Parent rejected the load (unknown skill, bad args, ...).
            # Don't mutate the scope contract on failure.
            return result

        state = tool_context.state
        previous = state.get(STATE_SKILL_CURRENT)
        inactive = list(state.get(STATE_SKILL_INACTIVE) or [])

        # Reload: the new skill was previously scoped out. Bring it back
        # to "current" — invariant 2 forbids being in both lists.
        if new_skill in inactive:
            inactive = [s for s in inactive if s != new_skill]

        if previous and previous != new_skill:
            state[STATE_SKILL_PREVIOUS] = previous
            if previous not in inactive:
                inactive.append(previous)
            history = list(state.get(STATE_SKILL_HISTORY) or [])
            history.append({
                'from': previous,
                'to': new_skill,
                'timestamp': _now_iso(),
            })
            state[STATE_SKILL_HISTORY] = history

            agent_name = getattr(tool_context, 'agent_name', None)
            if agent_name:
                adk_key = f'_adk_activated_skill_{agent_name}'
                adk_active = list(state.get(adk_key) or [])
                if previous in adk_active:
                    adk_active = [s for s in adk_active if s != previous]
                    state[adk_key] = adk_active

        state[STATE_SKILL_INACTIVE] = inactive
        state[STATE_SKILL_CURRENT] = new_skill
        return result


# ---------------------------------------------------------------------------
# AutoScopedSkillToolset — wires it all together
# ---------------------------------------------------------------------------


class AutoScopedSkillToolset(SkillToolset):
    """``SkillToolset`` that prunes inactive-skill content from
    ``llm_request.contents`` on every outgoing turn.

    Substitutes ``LoadSkillTool`` with :class:`ScopedLoadSkillTool` so
    every activation updates the skill-scope state, then walks
    ``llm_request.contents`` in :meth:`process_llm_request` to drop the
    function-call / function-response Contents of skills that are no
    longer current.

    The parent's behaviour (injecting the L1 skill instruction + skill
    catalogue into ``llm_request.instructions``) is preserved by calling
    ``super().process_llm_request`` first; only the contents pass is new.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Substitute LoadSkillTool in self._tools with our scoped wrapper.
        # Parent constructed the original list in __init__; mutate in
        # place so any other tool order or identity is preserved.
        for i, tool in enumerate(self._tools):
            if isinstance(tool, LoadSkillTool) and not isinstance(
                tool, ScopedLoadSkillTool
            ):
                self._tools[i] = ScopedLoadSkillTool(self)
                break

    async def process_llm_request(
        self, *, tool_context: ToolContext, llm_request: Any
    ) -> None:
        # 1) Parent injects the default skill system instruction +
        # the available-skills XML. Always run it so the L1 list is
        # present regardless of current scope.
        await super().process_llm_request(
            tool_context=tool_context, llm_request=llm_request
        )

        # 2) Prune inactive-skill content from the outgoing contents.
        state = tool_context.state
        inactive = set(state.get(STATE_SKILL_INACTIVE) or [])
        if not inactive:
            return

        contents = getattr(llm_request, 'contents', None)
        if not contents:
            return

        agent_name = getattr(tool_context, 'agent_name', 'root')
        new_contents, log = _prune_inactive_skill_content(
            contents=list(contents),
            inactive_scopes=inactive,
            agent_name=agent_name,
        )

        if not log['integrity_ok']:
            logger.warning('autoscope_prune_skipped_integrity', **log)
            return

        if log['pruned_count'] == 0:
            # Nothing to drop this turn — leave contents untouched and
            # skip the telemetry update so prune_totals reflects only
            # real prune events.
            return

        llm_request.contents = new_contents

        state[STATE_SKILL_LAST_PRUNE] = {
            'pruned_message_count': log['pruned_count'],
            'pruned_bytes': log['pruned_bytes'],
            'kept_message_count': log['kept_count'],
            'skills_pruned': log['skills_pruned'],
        }

        totals = dict(state.get(STATE_SKILL_PRUNE_TOTALS) or {})
        totals['pruned_messages_total'] = (
            totals.get('pruned_messages_total', 0) + log['pruned_count']
        )
        totals['pruned_bytes_total'] = (
            totals.get('pruned_bytes_total', 0) + log['pruned_bytes']
        )
        totals['prune_event_count'] = totals.get('prune_event_count', 0) + 1
        state[STATE_SKILL_PRUNE_TOTALS] = totals

        logger.info(
            'autoscope_prune',
            current_skill=state.get(STATE_SKILL_CURRENT),
            previous_skill=state.get(STATE_SKILL_PREVIOUS),
            inactive_skills=sorted(inactive),
            pruned_message_count=log['pruned_count'],
            pruned_bytes=log['pruned_bytes'],
            kept_message_count=log['kept_count'],
            agent_name=agent_name,
            totals=totals,
        )
