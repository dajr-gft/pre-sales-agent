"""Step 2 of validation_critic — stratify Manifest items by priority.

Programmatic prefilter that removes items the SowReviewSkill must not waste
tokens on (administrative metadata, intentional [TO BE DEFINED] gaps,
explicit OOS coverage) and tags the rest with ``priority`` so the LLM can
calibrate severity. No LLM call here.

The categories below come from the Extraction Manifest's ``ItemCategory``
enum. Critical categories (`Identity`, `Integrations`, `Constraints`,
`NFRs`, `Decisions`) are never demoted even if they appear literally in the
SOW text — literal mention is not the same as a substantive anchor.
"""

from __future__ import annotations

from typing import AsyncGenerator, ClassVar

import structlog
from google.adk.agents import BaseAgent
from google.adk.agents.base_agent_config import BaseAgentConfig
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions

from .schema import STATE_MANIFEST_RESIDUAL, STATE_SOW

logger = structlog.get_logger()

_MANIFEST_STATE_KEY = 'extraction_manifest'

_CRITICAL_CATEGORIES = frozenset(
    {'Identity', 'Integrations', 'Constraints', 'NFRs', 'Decisions'}
)
_LOW_PRIORITY_CATEGORIES = frozenset({'Briefing'})
_ADMIN_VALUE_KINDS = frozenset(
    {'project_name', 'customer_name', 'partner_name', 'currency', 'date'}
)


def _is_admin_metadata(item: dict) -> bool:
    """Detect items whose substance lives in the SOW header (not in FR/NFR/OOS)."""
    if item.get('category') == 'Identity':
        primitives = item.get('primitives') or {}
        kind = primitives.get('kind') if isinstance(primitives, dict) else None
        if kind in _ADMIN_VALUE_KINDS:
            return True
    return False


def _is_intentionally_deferred(item: dict, manifest: dict) -> bool:
    """Items declared as `[TO BE DEFINED]` upstream — not coverage gaps."""
    item_id = item.get('item_id') or item.get('id')
    if not item_id:
        return False
    gaps = manifest.get('gaps') or {}
    hard = gaps.get('hard_gaps') or []
    for g in hard:
        if g.get('item_id') == item_id and g.get('blocks_sow_generation'):
            return True
    tbd = gaps.get('to_be_defined') or []
    for g in tbd:
        if g.get('item_id') == item_id:
            return True
    return False


def _is_explicitly_excluded(item: dict, sow: dict) -> bool:
    """A Manifest item the SOW explicitly lists in `out_of_scope` is covered."""
    item_id = item.get('item_id') or item.get('id')
    if not item_id:
        return False
    value = (item.get('value') or item.get('value_detail') or '').lower()
    if len(value) < 4:
        return False
    for entry in sow.get('out_of_scope') or []:
        text = entry if isinstance(entry, str) else entry.get('description', '')
        if item_id.lower() in text.lower() or value in text.lower():
            return True
    return False


def _classify(item: dict, manifest: dict, sow: dict) -> str | None:
    """Return priority tag or ``None`` to drop the item from review."""
    if _is_admin_metadata(item):
        return None
    if _is_intentionally_deferred(item, manifest):
        return None
    if _is_explicitly_excluded(item, sow):
        return None
    category = item.get('category', '')
    if category in _CRITICAL_CATEGORIES:
        return 'critical'
    if category in _LOW_PRIORITY_CATEGORIES:
        return 'low_priority'
    return 'normal'


class ManifestPrefilterAgent(BaseAgent):
    """Reads the Manifest from state, stratifies items, persists the residual."""

    config_type: ClassVar[type[BaseAgentConfig]] = BaseAgentConfig

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        manifest = state.get(_MANIFEST_STATE_KEY) or {}
        sow = state.get(STATE_SOW) or {}

        items = (
            (manifest.get('extracted_items') or [])
            if isinstance(manifest, dict)
            else []
        )
        residual: list[dict] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            priority = _classify(item, manifest, sow)
            if priority is None:
                continue
            residual.append(
                {
                    'item_id': item.get('item_id') or item.get('id'),
                    'category': item.get('category'),
                    'value': item.get('value'),
                    'value_detail': item.get('value_detail'),
                    'priority': priority,
                }
            )

        state[STATE_MANIFEST_RESIDUAL] = residual
        logger.info(
            'manifest_prefilter_completed',
            total_items=len(items),
            residual_count=len(residual),
            critical_count=sum(1 for r in residual if r['priority'] == 'critical'),
        )

        # State-only event. Telemetry was already logged above; no Content
        # so this internal step never surfaces as a chat message.
        yield Event(
            invocation_id=ctx.invocation_id,
            author=self.name,
            branch=ctx.branch,
            actions=EventActions(
                state_delta={STATE_MANIFEST_RESIDUAL: residual},
            ),
        )


manifest_prefilter_agent = ManifestPrefilterAgent(
    name='manifest_prefilter_agent',
    description=(
        'Programmatically strips low-risk Manifest items and tags the rest '
        'with priority so SowReviewSkill can calibrate severity.'
    ),
)
