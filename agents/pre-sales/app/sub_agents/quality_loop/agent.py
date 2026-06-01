"""``QualityLoopAgent`` — critic → conditional revision → repeat.

Why a custom ``BaseAgent`` instead of ``LoopAgent`` from the ADK:
``LoopAgent`` iterates every sub-agent in order on each round, with no
built-in way to skip the second sub-agent when the first one is happy.
With our pair ``[validation_critic, revision_agent]`` that means
``revision_agent`` would run even when the critic returned ``passed``
or ``needs_human_review`` — a regression vs the current behaviour where
the root prompt branches on ``overall_status`` before invoking
``sow-revision``.

This agent encodes the branching explicitly:

    for round in [1..MAX_ROUNDS]:
        run validation_critic
        match overall_status:
            passed              -> emit result, return
            needs_human_review  -> emit result, return
            blocked AND last    -> emit "exhausted" with last validated
                                   report, return  (do NOT patch without
                                   a follow-up critic run)
            blocked AND
              no-progress (2x)  -> emit "no_progress", return
            blocked             -> run revision_agent, continue
            anything else       -> emit unexpected-status result, return

The "blocked AND last" branch is critical: running ``revision_agent``
on the final round would leave the SOW in ``state['app:sow:current']``
in a modified-but-unvalidated state, while ``final_report`` would still
reflect the pre-patch document. Skipping revision on the last round
preserves the invariant "every patch is followed by a critic run".

The "no_progress" branch is the diagnostic stop. The aggregator already
counts ``new_blocking_finding_count`` and ``resolved_blocking_finding_count``
per round; when those satisfy ``new >= resolved`` for two consecutive
rounds, the reviser is swapping problems for other problems rather than
shrinking the residue. Burning the rest of the round budget would just
accumulate more drift, so the loop stops with a TECHNICAL status — NOT
``needs_human_review`` — so the root prompt explains the non-convergence
honestly instead of asking the user to decide every finding the loop
churned through. The two-round window avoids one-off noise from a
single round that happened to introduce new defects while resolving
none.

The final outcome is written to ``state['app:sow:quality_loop_result']``
via ``EventActions.state_delta`` so the root can read it after the
``AgentTool`` returns and decide the next step (proceed, ask the user,
or surface the loop's failure mode).

The state keys this agent reads / writes:

- Reads ``state['app:validation_result']`` — the ``ValidationReport``
  the critic's assembler writes on every round.
- Writes ``state['app:sow:quality_loop_result']`` — exactly once, when
  the loop terminates.
"""

from __future__ import annotations

import json
import os
from typing import Any, AsyncGenerator, ClassVar, Mapping, Optional

import structlog
from google.adk.agents import BaseAgent, SequentialAgent
from google.adk.agents.base_agent_config import BaseAgentConfig
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from google.genai import types
from pydantic import ConfigDict, Field

from ...shared.logging_config import is_verbose_sow_logging
from ...tools.sow._anchor_utils import (
    ANCHOR_ID_PATTERN as _BUNDLE_ANCHOR_ID_PATTERN,
    extract_anchor_ids as _extract_anchor_ids,
)
from ...tools.sow._sow_helpers import apply_sow_assembly_to_state, sow_data_hash
from ...tools.sow.assemble_payload import AssemblyError
# Section agents are imported directly from their ``agent.py`` modules
# rather than via ``..__init__`` so the cycle that would arise from
# ``app/sub_agents/__init__.py`` importing both ``quality_loop`` and
# the sections is avoided — direct imports bypass the parent package's
# mid-init attribute lookup.
from ..architecture.agent import architecture_repair_agent
from ..delivery_plan.agent import delivery_plan_repair_agent
from ..narrative.agent import narrative_repair_agent
from ..requirements.agent import requirements_repair_agent
from ..revision import revision_agent
from ..scope_boundaries.agent import scope_boundaries_repair_agent
from .._section_agent import STATE_REPAIR_FINDINGS
from ..validation import validation_critic
from ..validation.field_vocabulary import BUNDLE_OWNED_FIELDS_BY_SECTION
from ..validation.schema import STATE_SOW, STATE_STAGE, STATE_VALIDATION_RESULT

logger = structlog.get_logger()

QUALITY_LOOP_RESULT_KEY = 'app:sow:quality_loop_result'

# F-05 anti-thrashing: the loop caches the SOW hash from the last full
# run so a re-invocation on the same staged payload short-circuits to
# the cached terminal result instead of burning a fresh budget of
# critic rounds. Cleared whenever ``stage_sow`` runs with a new payload
# (the next loop entry recomputes the hash and finds a mismatch).
STATE_LAST_LOOP_HASH = 'app:sow:last_loop_hash'

# Default round budget for the quality loop (rounds 1-2 may patch, round
# 3 caps). Overridable per instance via the constructor and per
# deployment via the ``SOW_QUALITY_LOOP_MAX_ROUNDS`` env var.
DEFAULT_MAX_ROUNDS = 3

# Deploy-time override for the round budget. Read via
# :func:`_resolve_max_rounds`, which falls back to ``DEFAULT_MAX_ROUNDS``
# (and warns) on a missing, non-integer, or non-positive value — it never
# raises, so a bad env value degrades to the default instead of crashing
# startup.
_MAX_ROUNDS_ENV = 'SOW_QUALITY_LOOP_MAX_ROUNDS'


def _resolve_max_rounds() -> int:
    """Return the round budget from the env var, or the default."""
    raw = os.environ.get(_MAX_ROUNDS_ENV)
    if raw is None or not raw.strip():
        return DEFAULT_MAX_ROUNDS
    try:
        value = int(raw.strip())
    except ValueError:
        logger.warning(
            'quality_loop_max_rounds_invalid',
            value=raw,
            fallback=DEFAULT_MAX_ROUNDS,
        )
        return DEFAULT_MAX_ROUNDS
    if value <= 0:
        logger.warning(
            'quality_loop_max_rounds_non_positive',
            value=value,
            fallback=DEFAULT_MAX_ROUNDS,
        )
        return DEFAULT_MAX_ROUNDS
    return value

# Number of consecutive rounds whose blocking-finding churn must satisfy
# ``new >= resolved`` before the loop declares ``no_progress``. A single
# round can legitimately show ``new > resolved`` (a refactor exposing a
# previously masked defect, or a patch that cascades into a sibling
# section), so we require the pattern to repeat. Two rounds is the
# smallest window that excludes single-round noise while still detecting
# churn before the whole budget burns.
NO_PROGRESS_WINDOW = 2

LoopStatus = str  # one of: 'passed', 'needs_human_review', 'blocked',
# 'exhausted', 'no_progress', 'unexpected_status'


# ---------------------------------------------------------------------------
# Repair routing — structural fields belong to section bundles.
#
# Two complementary mechanisms decide who patches a finding:
#
# 1. :data:`_FIELD_TO_SECTION` (PRIMARY) — any finding whose ``fields``
#    lists a structural SOW field is routed to the section agent that
#    OWNS that field, regardless of ``(skill, category)``. This holds
#    for coverage, semantic_quality, contradictions, contractual_exposure,
#    everything. The rule is contractual: structural fields are written
#    by section agents into bundles, and the assembler rebuilds the
#    flat SOW from those bundles. If the generic reviser patched a
#    structural field in the flat SOW, the next assembly would
#    overwrite the patch with the unchanged bundle — the two-writers
#    divergence that motivated this commit.
#
# 2. :data:`_CROSS_SECTION_REPAIR_ROUTES` (FALLBACK) — ``(skill,
#    category)`` pairs whose ``fields`` is sometimes omitted by the
#    critic but whose target section is still well-defined. Each entry
#    maps to the canonical section name (the bundle key, NOT the
#    ``_agent`` suffix). Used only when fields-driven routing produced
#    no target.
#
# Why both: production logs showed cross-section contradictions (e.g.
# scope_vs_oos) where the critic occasionally emitted findings with
# ``fields=[]``; without a fallback those would land in the mechanical
# bucket and the reviser would patch the flat SOW. Keeping the table
# as a backstop guarantees those findings still reach a section agent.
#
# The QualityLoopAgent looks the section up in its
# ``repair_section_agents`` mapping at runtime; sections that are not
# wired fall back to the reviser automatically so the loop still
# functions before all sections are mounted.
_CROSS_SECTION_REPAIR_ROUTES: dict[tuple[str, str], str] = {
    # Cross-section contradictions inside delivery_plan's contract.
    ('contradictions', 'timeline_vs_deliverables'): 'delivery_plan',
    ('contradictions', 'activities_vs_deliverables'): 'delivery_plan',
    # Cross-section contradictions inside scope_boundaries's contract.
    ('contradictions', 'scope_vs_oos'): 'scope_boundaries',
    ('contradictions', 'assumptions_vs_risks'): 'scope_boundaries',
    # FR <-> NFR alignment is requirements-internal even though FR and
    # NFR are sibling fields — the agent owns both, so it can reconcile.
    ('contradictions', 'fr_vs_nfr'): 'requirements',
    ('contradictions', 'fr_restated_as_nfr'): 'requirements',
    # Architecture description vs technology_stack table — both are
    # produced by the same agent and must stay consistent.
    ('contradictions', 'architecture_vs_stack'): 'architecture',
}

# Section-agent invocation order when MULTIPLE sections have repair
# findings in the same round. Mirrors the Phase-Step generation order
# (A -> B -> C -> D -> E) so a later section sees the upstream patches
# applied by an earlier one. Without this, scope_boundaries repair
# could be evaluated against a stale delivery_plan bundle, only for the
# next critic round to flag the same contradiction again.
_SECTION_ORDER: tuple[str, ...] = (
    'requirements',
    'delivery_plan',
    'scope_boundaries',
    'architecture',
    'narrative',
)


# Map every top-level ``sow_data`` field to its owning section. Used by
# :func:`_sections_for_finding` to derive cross-bundle targets from
# ``Finding.fields`` — a finding whose fields cross sections needs every
# involved section to patch its own side.
#
# Canonical source: ``validation.field_vocabulary.BUNDLE_OWNED_FIELDS_BY_SECTION``.
# Adding a SOW field to a bundle schema means adding a row THERE, not
# here — both the router (this module) and the aggregator's lint pass
# read from the same dict so the writer / linter / router cannot drift.
_FIELD_TO_SECTION: dict[str, str] = dict(BUNDLE_OWNED_FIELDS_BY_SECTION)


def _sections_for_finding(
    finding: dict, available_sections: set[str],
) -> list[str]:
    """Sections that should repair this finding, in Phase Step order.

    Routing precedence:

    1. **Fields-driven (PRIMARY).** Any finding whose ``fields`` lists
       a structural SOW field (anything in :data:`_FIELD_TO_SECTION`)
       is routed to the section agent that OWNS that field. This rule
       holds for ALL ``(skill, category)`` pairs — coverage,
       semantic_quality, contradictions, contractual_exposure,
       disclosures. The motivation is contractual: structural fields
       are written by section agents into bundles, and the assembler
       rebuilds the flat SOW from bundles. A reviser patch to a
       structural field in the flat SOW is overwritten the next time
       any section agent runs (the two-writers divergence). Sending
       the finding to the bundle owner keeps the SOW internally
       consistent.

       A finding with ``fields=['out_of_scope', 'deliverables']``
       returns ``['delivery_plan', 'scope_boundaries']`` (Phase Step
       order); each section gets a copy of the finding so it can patch
       its own side, and the order guarantees the downstream section
       sees the patched upstream bundle on the next re-assembly.

    2. **Route-table fallback.** When ``fields`` is empty or contains
       no structural entries, fall back to
       :data:`_CROSS_SECTION_REPAIR_ROUTES` for the (skill, category)
       pair. This covers cross-section contradictions whose ``fields``
       the critic occasionally omits but whose target section is still
       well-defined (e.g., ``fr_vs_nfr`` always routes to
       ``requirements``).

    3. **Mechanical.** Returns ``[]`` when neither rule fires — the
       finding has no structural-field hint and no (skill, category)
       in the route table. These purely textual / global findings go
       to the reviser, which only ever touches the flat SOW for
       fields no section owns.

    Args:
        finding: Validation finding as a dict (the loop reads them
            from ``state[STATE_VALIDATION_RESULT].findings``).
        available_sections: Section names the loop actually has agents
            for. Sections derived by either rule but missing from this
            set are dropped — the partition function then falls the
            whole finding back to mechanical if NO sections survive.

    Returns:
        Empty list when the finding is mechanical. Single-element list
        when only one section is involved. Multi-element list in
        :data:`_SECTION_ORDER` for cross-bundle findings.
    """
    fields = finding.get('fields') or []
    sections: set[str] = set()
    for field in fields:
        section = _FIELD_TO_SECTION.get(field)
        if section is not None and section in available_sections:
            sections.add(section)

    if sections:
        return [s for s in _SECTION_ORDER if s in sections]

    # Fields-driven routing produced nothing — try the (skill, category)
    # route table as a fallback for findings whose ``fields`` is
    # empty / non-structural but whose target section is still known.
    skill = finding.get('skill')
    category = finding.get('category')
    default_section = _CROSS_SECTION_REPAIR_ROUTES.get((skill, category))
    if default_section is not None and default_section in available_sections:
        return [default_section]
    return []


# Map section name -> the ``state[<output_key>]`` slot where its bundle
# lives. The loop reads/hashes the bundle before and after a section
# repair to confirm the agent actually changed something — when a
# bundle hash is identical before and after, the section agent ran a
# no-op repair and the next critic round will probably re-flag the
# same findings.
_SECTION_BUNDLE_KEYS: dict[str, str] = {
    'requirements': 'app:sow:requirements',
    'delivery_plan': 'app:sow:delivery_plan',
    'scope_boundaries': 'app:sow:scope_boundaries',
    'architecture': 'app:sow:architecture',
    'narrative': 'app:sow:narrative',
}


# Anchor extraction (``_BUNDLE_ANCHOR_ID_PATTERN`` / ``_extract_anchor_ids``)
# was extracted to :mod:`app.tools.sow._anchor_utils` so the section
# patch engine can reuse the exact same walker without importing from
# this module (which would create an import cycle). They are re-imported
# under the original private names at the top of the module so the
# function bodies below continue to work unchanged.


def _finding_digest(finding: dict) -> dict:
    """Compact digest of a finding for diagnostic logging.

    Keeps only the fields a human reading the log needs to trace a
    finding's lifecycle round-to-round: identity, classification, and
    the first three ``fields`` (the bundle keys the patcher would
    touch). Drops ``evidence`` and ``recommendation`` — those are long
    prose, and the fingerprint is what we use to track identity across
    rounds anyway.
    """
    return {
        'id': finding.get('id'),
        'skill': finding.get('skill'),
        'category': finding.get('category'),
        'severity': finding.get('severity'),
        'resolution_mode': finding.get('resolution_mode'),
        'fields': list(finding.get('fields') or [])[:3],
        'persistent': finding.get('persistent'),
    }


def _partition_findings(
    findings: list[dict],
    available_sections: set[str],
) -> tuple[dict[str, list[dict]], list[dict]]:
    """Split findings into per-section repair groups + mechanical residue.

    A single finding can appear in MULTIPLE ``by_section`` lists when
    its ``fields`` cross sections (see :func:`_sections_for_finding`).
    Each section that owns one of the touched fields gets a copy of the
    same finding payload; the loop invokes them in Phase Step order so
    the downstream section sees the upstream patches via the
    cumulative-visibility mechanism the section agents already have.

    Args:
        findings: ``ValidationReport.findings`` as dicts (the loop sees
            them in dict form via ``state[STATE_VALIDATION_RESULT]``).
        available_sections: section names the loop actually has agents
            for. A route to a section that isn't wired falls back to the
            mechanical bucket so the reviser still gets a shot at it
            (and the loop does not stall on an unimplemented section).

    Returns:
        ``(by_section, mechanical)`` where ``by_section`` keys are
        section names from :data:`_CROSS_SECTION_REPAIR_ROUTES` and
        each value is the list of findings routed to that section in
        original report order. The SAME finding may appear under
        multiple section keys (cross-bundle routing). ``mechanical``
        holds findings with no section route, also in original order.
    """
    by_section: dict[str, list[dict]] = {}
    mechanical: list[dict] = []
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        sections = _sections_for_finding(finding, available_sections)
        if not sections:
            mechanical.append(finding)
            continue
        for section in sections:
            by_section.setdefault(section, []).append(finding)
    return by_section, mechanical


class QualityLoopAgent(BaseAgent):
    """Critic → (conditional revision) loop with explicit stop conditions."""

    # ``repair_section_agents`` is a Mapping holding ADK ``BaseAgent``
    # instances, which Pydantic cannot validate; ``arbitrary_types_allowed``
    # lets the field carry whatever the caller passes (a dict of agents).
    model_config = ConfigDict(arbitrary_types_allowed=True)

    config_type: ClassVar[type[BaseAgentConfig]] = BaseAgentConfig

    # Resolved from the env var per instance (default_factory) so the
    # singleton picks up ``SOW_QUALITY_LOOP_MAX_ROUNDS`` at construction;
    # an explicit ``max_rounds=`` passed to the constructor still wins.
    max_rounds: int = Field(default_factory=_resolve_max_rounds)

    # Section-name -> section agent. The loop uses these to repair
    # cross-section findings in their owning section instead of forcing
    # the generic reviser to coordinate cross-bundle changes. Empty by
    # default — when nothing is wired the loop behaves exactly as it did
    # before commit 5, sending every finding to the reviser. Keys must
    # match values in :data:`_CROSS_SECTION_REPAIR_ROUTES` and
    # :data:`_SECTION_ORDER` ('requirements', 'delivery_plan', etc.).
    repair_section_agents: Mapping[str, BaseAgent] = Field(default_factory=dict)

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        critic, reviser = self.sub_agents[0], self.sub_agents[1]

        # ----- F-05 anti-thrashing cache --------------------------------
        # If the previous loop invocation already terminated on a SOW
        # with the same hash that is in state right now, re-emit the
        # cached terminal result instead of running the critic again.
        # ``stage_sow`` is the only writer of ``STATE_SOW`` between loop
        # runs — when it overwrites the payload with anything materially
        # different the hash changes and the cache misses naturally. The
        # cached hash is written at ``_emit_result`` time on the SOW *as
        # it stood when the loop terminated* (post-revision), so a re-
        # invocation on that same final payload short-circuits cleanly.
        current_sow = ctx.session.state.get(STATE_SOW)
        if isinstance(current_sow, dict) and current_sow:
            entry_hash = sow_data_hash(current_sow)
            last_hash = ctx.session.state.get(STATE_LAST_LOOP_HASH)
            cached_result = ctx.session.state.get(QUALITY_LOOP_RESULT_KEY)
            if (
                last_hash == entry_hash
                and isinstance(cached_result, dict)
                and cached_result
            ):
                logger.info(
                    'quality_loop_cache_hit',
                    sow_hash=entry_hash,
                    cached_status=cached_result.get('status'),
                )
                yield self._emit_cached_result(ctx, cached_result)
                return
        # ----------------------------------------------------------------

        last_status: Optional[str] = None
        # Number of consecutive ``blocked`` rounds whose churn satisfied
        # ``new_blocking_finding_count >= resolved_blocking_finding_count``.
        # Reset to 0 the moment a round shows real progress (``resolved >
        # new``) — we want consecutive non-progress, not lifetime counts.
        # Compared against ``NO_PROGRESS_WINDOW`` after each blocked round.
        consecutive_no_progress_rounds = 0

        for round_idx in range(self.max_rounds):
            round_number = round_idx + 1
            logger.info(
                'quality_loop_round_start',
                round=round_number,
                max_rounds=self.max_rounds,
            )

            # ----- SOW snapshot at round start (verbose only) ----------
            # Full staged SOW entering the critic. With the post-round
            # snapshot below, the pair is the forensic record per round.
            # Verbose-only because a SOW is 5-15 KB and we already log
            # the bundle hash + anchor-drop diff at structural level on
            # every patch, which is enough for everyday debugging.
            if is_verbose_sow_logging():
                pre_round_sow = ctx.session.state.get(STATE_SOW)
                if isinstance(pre_round_sow, dict) and pre_round_sow:
                    logger.info(
                        'quality_loop_sow_snapshot_pre_round',
                        round=round_number,
                        sow_hash=sow_data_hash(pre_round_sow),
                        top_level_keys=sorted(pre_round_sow.keys()),
                        sow=pre_round_sow,
                    )
            # ------------------------------------------------------------

            async for event in critic.run_async(ctx):
                self._apply_state_delta(ctx, event)
                yield event

            report = ctx.session.state.get(STATE_VALIDATION_RESULT) or {}
            status = report.get('overall_status') if isinstance(report, dict) else None
            last_status = status

            if status == 'passed':
                yield self._emit_result(
                    ctx,
                    status='passed',
                    rounds_used=round_number,
                    final_report=report,
                    message=(
                        f'Validation passed after {round_number} round(s).'
                    ),
                )
                return

            if status == 'needs_human_review':
                yield self._emit_result(
                    ctx,
                    status='needs_human_review',
                    rounds_used=round_number,
                    final_report=report,
                    message=(
                        f'Validation needs human review (round {round_number}).'
                    ),
                )
                return

            if status != 'blocked':
                yield self._emit_result(
                    ctx,
                    status='unexpected_status',
                    rounds_used=round_number,
                    final_report=report,
                    observed_status=status,
                    message=(
                        f"Unexpected validation status '{status}' at round "
                        f'{round_number}; aborting loop.'
                    ),
                )
                return

            # status == 'blocked' from here on.
            if round_idx == self.max_rounds - 1:
                # Last round: skipping revision keeps the final_report
                # consistent with the SOW currently in state. Running a
                # patch we cannot revalidate would silently desync them.
                yield self._emit_result(
                    ctx,
                    status='exhausted',
                    rounds_used=round_number,
                    final_report=report,
                    message=(
                        f'Quality loop exhausted {self.max_rounds} rounds '
                        'without converging. Last critic run returned '
                        '`blocked`; no patch applied on the final round '
                        'so the staged SOW matches the report you see.'
                    ),
                )
                return

            # ----- no-progress detection (diagnostic stop) ----------------
            # The aggregator publishes ``new_blocking_finding_count`` and
            # ``resolved_blocking_finding_count`` per round. On round 1
            # both are 0 by construction (no prior round to diff against),
            # so the check is skipped — we only detect churn once there
            # is a real history.
            #
            # ``new >= resolved`` means the reviser is at best running in
            # place (= net zero) and at worst swapping problems for new
            # ones (> resolved). After ``NO_PROGRESS_WINDOW`` consecutive
            # such rounds, burning the rest of the round budget would
            # accumulate drift without converging — stop with a TECHNICAL
            # status so the root prompt can explain the non-convergence
            # honestly instead of surfacing every churned finding to the
            # user as if they needed to decide it.
            if round_number >= 2:
                new_blocking = int(report.get('new_blocking_finding_count') or 0)
                resolved_blocking = int(
                    report.get('resolved_blocking_finding_count') or 0
                )
                if new_blocking >= resolved_blocking:
                    consecutive_no_progress_rounds += 1
                else:
                    consecutive_no_progress_rounds = 0

                if consecutive_no_progress_rounds >= NO_PROGRESS_WINDOW:
                    logger.info(
                        'quality_loop_no_progress_detected',
                        round=round_number,
                        consecutive_rounds=consecutive_no_progress_rounds,
                        new_blocking=new_blocking,
                        resolved_blocking=resolved_blocking,
                    )
                    yield self._emit_result(
                        ctx,
                        status='no_progress',
                        rounds_used=round_number,
                        final_report=report,
                        message=(
                            f'Quality loop halted at round {round_number} '
                            f'after {consecutive_no_progress_rounds} '
                            'consecutive rounds where the reviser introduced '
                            'as many new blocking findings as it resolved. '
                            'This is a technical non-convergence — the '
                            'revision_agent is swapping problems rather '
                            'than reducing the residue. The staged SOW '
                            'matches the report you see (no patch was '
                            'applied this round).'
                        ),
                    )
                    return
            # ---------------------------------------------------------------

            # ----- repair routing -----------------------------------------
            # Partition findings into per-section repair groups
            # (delegated to the owning section agent) + mechanical
            # residue (handed to the generic reviser). Sections without
            # an agent wired fall back to mechanical so the loop never
            # stalls because a route lacks an implementation.
            all_findings = report.get('findings') or []
            available_sections = set(self.repair_section_agents.keys())
            by_section, mechanical = _partition_findings(
                all_findings, available_sections
            )

            # Telemetry: surface the partition decision per round so we
            # can measure whether cross-bundle routing actually helps
            # contradictions converge (the metric the reviewer asked for
            # when approving Opção 4). Track:
            # - per-section counts (how many findings each agent will
            #   patch this round)
            # - distinct findings (de-duplicated — same finding routed
            #   to N sections counts once here, vs N times in the
            #   per-section sum)
            # - cross-bundle count (how many distinct findings touched
            #   >1 section, the population whose convergence was the
            #   motivation for Opção 4)
            distinct_routed_ids: set[str] = set()
            cross_bundle_count = 0
            for finding in all_findings:
                if not isinstance(finding, dict):
                    continue
                sections = _sections_for_finding(finding, available_sections)
                if not sections:
                    continue
                # Use id when present; fall back to identity hash so
                # the count is meaningful even on synthetic findings
                # without an id field.
                fid = finding.get('id') or f'<no-id:{id(finding)}>'
                distinct_routed_ids.add(fid)
                if len(sections) > 1:
                    cross_bundle_count += 1
            logger.info(
                'quality_loop_partition_summary',
                round=round_number,
                total_findings=len(all_findings),
                distinct_routed_findings=len(distinct_routed_ids),
                cross_bundle_findings=cross_bundle_count,
                mechanical_count=len(mechanical),
                by_section_counts={
                    name: len(findings)
                    for name, findings in by_section.items()
                },
            )

            current_stage = ctx.session.state.get(STATE_STAGE) or 'content'
            section_repaired_count = 0
            # Phase 5 telemetry — per-round mechanism counters. After
            # Phase 3 every wired ``repair_section_agents`` entry is a
            # tool-based ``Agent`` (the section's ``*_repair_agent``); a
            # non-zero ``legacy_regenerate`` count means a future change
            # accidentally wired back a first-gen ``SequentialAgent`` and
            # the loop is regenerating bundles again. Pinning this to
            # zero in production logs is the anti-regression signal the
            # plan calls for.
            mechanism_counts: dict[str, int] = {
                'tool_based': 0, 'legacy_regenerate': 0,
            }
            mechanism_by_section: dict[str, str] = {}
            for section_name in _SECTION_ORDER:
                section_findings = by_section.get(section_name)
                if not section_findings:
                    continue
                section_agent = self.repair_section_agents.get(section_name)
                if section_agent is None:
                    # Defensive: ``_partition_findings`` already filters
                    # against ``available_sections``; this branch only
                    # fires if the mapping mutated between check and use.
                    continue
                # Discriminate by ADK agent type: the tool-based repair
                # builder returns a single ``Agent`` (LlmAgent); the
                # first-gen builder returns a ``SequentialAgent``. This
                # is the same classification ``test_quality_loop`` pins,
                # so the metric and the test stay coupled by construction.
                mechanism = (
                    'legacy_regenerate'
                    if isinstance(section_agent, SequentialAgent)
                    else 'tool_based'
                )
                mechanism_counts[mechanism] += 1
                mechanism_by_section[section_name] = mechanism

                # Hand the section its repair packet via the canonical
                # state slot ``STATE_REPAIR_FINDINGS``. The factory wired
                # this slot as an optional input on every section agent
                # in commit 3, and the worker's provider renders
                # <repair_findings> + the repair-mode footer when the
                # slot is non-empty. We clear after invocation so the
                # next section in this same round sees a clean state.
                ctx.session.state[STATE_REPAIR_FINDINGS] = section_findings
                logger.info(
                    'quality_loop_invoking_section_repair',
                    round=round_number,
                    section=section_name,
                    finding_count=len(section_findings),
                )
                # Diagnostic: dump the EXACT findings handed to this
                # section so we can later correlate "section X received
                # finding Y in round N" with "finding Y reappeared (or
                # didn't) in round N+1". Without this we only know the
                # COUNT, not which findings.
                logger.info(
                    'quality_loop_section_dispatch',
                    round=round_number,
                    section=section_name,
                    findings=[
                        _finding_digest(f) for f in section_findings
                    ],
                )
                # Bundle hash BEFORE the section agent runs. Used by the
                # diff log below to confirm whether the agent actually
                # changed its bundle. ``None`` is allowed — first-round
                # sections may not have populated state[<output_key>] yet
                # (the agent reads its previous_bundle from this slot).
                bundle_key = _SECTION_BUNDLE_KEYS.get(section_name)
                pre_bundle = (
                    ctx.session.state.get(bundle_key) if bundle_key else None
                )
                pre_bundle_hash = (
                    sow_data_hash(pre_bundle)
                    if isinstance(pre_bundle, dict) and pre_bundle
                    else None
                )
                # Snapshot anchor ids BEFORE the agent runs (D1
                # instrumentation). Used by the dropped-ids diff log
                # below — see :func:`_extract_anchor_ids` for the
                # rationale.
                pre_anchor_ids = _extract_anchor_ids(pre_bundle)

                async for event in section_agent.run_async(ctx):
                    self._apply_state_delta(ctx, event)
                    yield event
                ctx.session.state[STATE_REPAIR_FINDINGS] = None

                # Bundle hash AFTER the agent ran. Identical to
                # pre_bundle_hash means the agent ran a no-op repair —
                # almost certainly the cause if the next critic round
                # still flags the same findings on this section.
                post_bundle = (
                    ctx.session.state.get(bundle_key) if bundle_key else None
                )
                post_bundle_hash = (
                    sow_data_hash(post_bundle)
                    if isinstance(post_bundle, dict) and post_bundle
                    else None
                )
                logger.info(
                    'quality_loop_section_bundle_diff',
                    round=round_number,
                    section=section_name,
                    bundle_key=bundle_key,
                    pre_hash=pre_bundle_hash,
                    post_hash=post_bundle_hash,
                    changed=pre_bundle_hash != post_bundle_hash,
                    mechanism=mechanism,
                )

                # D1 — anchor-drop detection. Compare the set of stable
                # item ids in the bundle BEFORE the agent ran against
                # the AFTER set. Any id present before AND absent after
                # is a candidate anchor drop (the section agent removed
                # or renamed an item that another section may have been
                # referencing). Logged as WARNING when dropped is
                # non-empty so the event is easy to grep in a long
                # production trace.
                post_anchor_ids = _extract_anchor_ids(post_bundle)
                dropped_anchor_ids = sorted(pre_anchor_ids - post_anchor_ids)
                added_anchor_ids = sorted(post_anchor_ids - pre_anchor_ids)
                if dropped_anchor_ids:
                    logger.warning(
                        'quality_loop_section_anchor_dropped',
                        round=round_number,
                        section=section_name,
                        bundle_key=bundle_key,
                        dropped_ids=dropped_anchor_ids,
                        added_ids=added_anchor_ids,
                        kept_count=len(pre_anchor_ids & post_anchor_ids),
                        pre_count=len(pre_anchor_ids),
                        post_count=len(post_anchor_ids),
                    )

                # The section agent wrote its updated bundle to
                # ``state[<output_key>]``; re-assemble the flat sow_data
                # so the next section (or the reviser, or the next
                # critic) sees the patched payload. Same-stage
                # re-assembly does not perturb round counters /
                # fingerprints / language — see
                # ``apply_sow_assembly_to_state``.
                try:
                    apply_sow_assembly_to_state(
                        ctx.session.state, current_stage,
                    )
                    section_repaired_count += 1
                except AssemblyError as err:
                    # The section agent likely emitted an empty bundle
                    # (MISSING_INPUT sentinel) or a malformed payload.
                    # Logging keeps the diagnostic visible; we DO NOT
                    # abort the loop here — the next critic round will
                    # surface the residual findings as ``blocked`` or
                    # ``no_progress`` and the standard branches handle
                    # it from there.
                    logger.warning(
                        'quality_loop_section_repair_assembly_failed',
                        round=round_number,
                        section=section_name,
                        reason=err.reason,
                        missing_keys=err.missing_keys,
                        sentinel_keys=err.sentinel_keys,
                    )

            # Phase 5 — emit one summary per round naming the mechanism
            # each section used to repair. After Phase 3,
            # ``legacy_regenerate`` should always be zero in production.
            # Grep query for the anti-regression check:
            #   grep quality_loop_repair_mechanism_used logs/agent.log \
            #     | jq 'select(.legacy_regenerate > 0)'
            # An empty result means no section regenerated a bundle this
            # run; any hit is a regression to investigate.
            if mechanism_by_section:
                logger.info(
                    'quality_loop_repair_mechanism_used',
                    round=round_number,
                    tool_based=mechanism_counts['tool_based'],
                    legacy_regenerate=mechanism_counts['legacy_regenerate'],
                    by_section=mechanism_by_section,
                )

            # ----- SOW snapshot post-repair (verbose only) -------------
            # SOW after section repairs + re-assembly. Diff vs. the pre-
            # round snapshot tells us what the worker actually changed
            # end-to-end this round. Gated for the same reason as the
            # pre-round snapshot.
            if is_verbose_sow_logging():
                post_round_sow = ctx.session.state.get(STATE_SOW)
                if isinstance(post_round_sow, dict) and post_round_sow:
                    logger.info(
                        'quality_loop_sow_snapshot_post_round',
                        round=round_number,
                        sow_hash=sow_data_hash(post_round_sow),
                        top_level_keys=sorted(post_round_sow.keys()),
                        sow=post_round_sow,
                    )

            # ----- mechanical residue → reviser ----------------------------
            # If section repairs ran AND there is no mechanical residue,
            # skip the reviser entirely this round — its only job would
            # be to re-emit a noop, wasting a model call.
            if mechanical or section_repaired_count == 0:
                # When section repairs DID run, narrow the report the
                # reviser sees to the mechanical residue so it does not
                # re-patch findings the section agents already addressed.
                # The next critic round overwrites STATE_VALIDATION_RESULT
                # unconditionally, so this temporary mutation cannot leak
                # past the current iteration.
                if section_repaired_count > 0 and mechanical != all_findings:
                    narrowed = dict(report)
                    narrowed['findings'] = mechanical
                    ctx.session.state[STATE_VALIDATION_RESULT] = narrowed

                # Which findings actually went to the reviser this round:
                # when section repairs narrowed the report, that's
                # ``mechanical``; otherwise the reviser sees the full
                # ``all_findings`` list (loop's commit-5 fallback).
                dispatched_to_reviser = (
                    mechanical if section_repaired_count else all_findings
                )

                logger.info(
                    'quality_loop_invoking_revision',
                    round=round_number,
                    finding_count=len(dispatched_to_reviser),
                    sections_repaired=section_repaired_count,
                )
                # Diagnostic: dump exactly which findings the reviser
                # received. Same correlation goal as the section
                # dispatch log above — round N reviser received finding
                # X, round N+1 critic still flags it -> we know the
                # reviser failed THAT specific patch.
                logger.info(
                    'quality_loop_mechanical_dispatch',
                    round=round_number,
                    findings=[
                        _finding_digest(f) for f in dispatched_to_reviser
                    ],
                )

                # STATE_SOW hash BEFORE the reviser runs. The reviser's
                # stage_sow tool writes here; comparing before/after
                # tells us if the patches were actually applied.
                pre_sow = ctx.session.state.get(STATE_SOW)
                pre_sow_hash = (
                    sow_data_hash(pre_sow)
                    if isinstance(pre_sow, dict) and pre_sow
                    else None
                )
                # D1 — anchor snapshot BEFORE the reviser runs. Same
                # rationale as the per-section anchor-drop log above,
                # applied to the flat SOW the reviser writes to.
                pre_sow_anchor_ids = _extract_anchor_ids(pre_sow)

                async for event in reviser.run_async(ctx):
                    self._apply_state_delta(ctx, event)
                    yield event

                post_sow = ctx.session.state.get(STATE_SOW)
                post_sow_hash = (
                    sow_data_hash(post_sow)
                    if isinstance(post_sow, dict) and post_sow
                    else None
                )
                logger.info(
                    'quality_loop_reviser_sow_diff',
                    round=round_number,
                    dispatched_count=len(dispatched_to_reviser),
                    pre_hash=pre_sow_hash,
                    post_hash=post_sow_hash,
                    changed=pre_sow_hash != post_sow_hash,
                )

                # D1 — reviser anchor drop detection. The reviser's
                # contract (sow-revision/SKILL.md) says "touch only
                # fields in finding.fields, preserve byte-for-byte" —
                # any id present pre-call and absent post-call is a
                # contract violation worth surfacing.
                post_sow_anchor_ids = _extract_anchor_ids(post_sow)
                reviser_dropped_ids = sorted(
                    pre_sow_anchor_ids - post_sow_anchor_ids
                )
                reviser_added_ids = sorted(
                    post_sow_anchor_ids - pre_sow_anchor_ids
                )
                if reviser_dropped_ids:
                    logger.warning(
                        'quality_loop_reviser_anchor_dropped',
                        round=round_number,
                        dropped_ids=reviser_dropped_ids,
                        added_ids=reviser_added_ids,
                        kept_count=len(
                            pre_sow_anchor_ids & post_sow_anchor_ids
                        ),
                        pre_count=len(pre_sow_anchor_ids),
                        post_count=len(post_sow_anchor_ids),
                    )
            else:
                logger.info(
                    'quality_loop_skipping_revision_no_mechanical_residue',
                    round=round_number,
                    sections_repaired=section_repaired_count,
                )

        # Defensive: the loop body must return before this point (the
        # blocked branch above handles the last iteration explicitly).
        yield self._emit_result(
            ctx,
            status='unexpected_status',
            rounds_used=self.max_rounds,
            final_report=ctx.session.state.get(STATE_VALIDATION_RESULT) or {},
            observed_status=last_status,
            message='Quality loop fell through without emitting a result.',
        )

    @staticmethod
    def _apply_state_delta(
        ctx: InvocationContext, event: Event
    ) -> None:
        """Mirror an event's ``state_delta`` into ``ctx.session.state``.

        ADK's runner is what normally applies ``EventActions.state_delta``
        to the live session state — outside of that runner (e.g. when the
        QualityLoopAgent is called via ``AgentTool`` and reads state
        between sub-agent invocations) we cannot rely on the runner
        having processed the yielded event before the next read.

        Production sub-agents (the validation aggregator, the assembler,
        and the revision_agent's tools) all write directly to
        ``ctx.session.state`` AND emit ``state_delta`` for persistence,
        so this loop reads the right value either way. But the contract
        ought not depend on that double-write: a sub-agent that only
        emits ``state_delta`` (the canonical ADK pattern) MUST still
        produce a state update the loop's branching logic can see.

        Applying the delta here is idempotent — when the runner later
        processes the yielded event it just rewrites the same keys with
        the same values. Tests assert that a critic which only emits
        ``state_delta`` still drives the loop correctly (see
        ``test_quality_loop::TestStateDeltaOnlyCritic``).

        ``event.actions`` is always populated (``EventActions`` field has
        ``default_factory=dict``), so the only guard we need is on the
        delta dict itself being empty.
        """
        delta = event.actions.state_delta
        if not delta:
            return
        for key, value in delta.items():
            ctx.session.state[key] = value

    def _emit_result(
        self,
        ctx: InvocationContext,
        *,
        status: LoopStatus,
        rounds_used: int,
        final_report: dict[str, Any],
        observed_status: Optional[str] = None,
        message: Optional[str] = None,
    ) -> Event:
        """Build the terminal event that publishes the loop result.

        State is written through both channels:
        - ``ctx.session.state[KEY] = payload`` so any downstream agent
          inside the same invocation sees the update immediately.
        - ``EventActions.state_delta`` so the session service persists
          the change. Mirrors the pattern used by
          ``ValidationAssemblerAgent``.

        The terminal event always carries a ``content`` part — when
        wrapped in an ``AgentTool``, the caller's tool response is built
        from the agent's events, so an empty content can leave the root
        without an explicit signal that the loop finished. Including a
        compact JSON envelope guarantees the root sees the outcome both
        in the tool result and in state, regardless of how the runtime
        composes the AgentTool response.

        F-05 anti-thrashing: we also mirror the hash of the SOW *as it
        stands in state at termination time* (post-revision) into
        ``STATE_LAST_LOOP_HASH``. The next invocation's cache check
        compares that hash against the SOW in state at entry — a match
        means the staged payload hasn't changed since the previous loop
        terminated, so the cached result is the right answer and we can
        skip the critic rounds entirely.
        """
        payload: dict[str, Any] = {
            'status': status,
            'rounds_used': rounds_used,
            'final_report': final_report,
        }
        if observed_status is not None:
            payload['observed_status'] = observed_status

        ctx.session.state[QUALITY_LOOP_RESULT_KEY] = payload
        state_delta: dict[str, Any] = {QUALITY_LOOP_RESULT_KEY: payload}

        terminal_sow = ctx.session.state.get(STATE_SOW)
        terminal_hash: Optional[str] = None
        if isinstance(terminal_sow, dict) and terminal_sow:
            terminal_hash = sow_data_hash(terminal_sow)
            ctx.session.state[STATE_LAST_LOOP_HASH] = terminal_hash
            state_delta[STATE_LAST_LOOP_HASH] = terminal_hash

        logger.info(
            'quality_loop_result',
            status=status,
            rounds_used=rounds_used,
            has_message=bool(message),
            terminal_sow_hash=terminal_hash,
        )

        # Compact summary for the AgentTool response — full report stays
        # in state to avoid burning tokens on the root's context.
        #
        # Severity surface: the gate in :func:`_decide_status` treats
        # BOTH ``BLOCKER`` and ``MAJOR`` as blocking (see
        # ``_is_blocking_finding``). The old envelope only surfaced
        # ``blocker_count`` under a misleading name (``blocking_findings``);
        # an envelope showing ``0`` while ``summary`` mentioned ten MAJOR
        # findings was the bug. Expose all three so the root can read the
        # number that matters without re-deriving anything:
        #
        # - ``blocker_count``  — count of BLOCKER findings (post-calibration).
        # - ``major_count``    — count of MAJOR findings (post-calibration).
        # - ``blocking_total`` — what the gate actually used, the sum of the
        #   two above. Matches ``len([f for f in findings if
        #   _is_blocking_finding(f, det)])`` in the aggregator.
        final_blocker_count = (final_report or {}).get('blocker_count', 0)
        final_major_count = (final_report or {}).get('major_count', 0)
        content_payload = {
            'status': status,
            'rounds_used': rounds_used,
            'summary': (final_report or {}).get('summary', ''),
            'blocker_count': final_blocker_count,
            'major_count': final_major_count,
            'blocking_total': final_blocker_count + final_major_count,
            'state_key': QUALITY_LOOP_RESULT_KEY,
        }
        if observed_status is not None:
            content_payload['observed_status'] = observed_status
        if message:
            content_payload['message'] = message

        text = json.dumps(content_payload, ensure_ascii=False)
        return Event(
            invocation_id=ctx.invocation_id,
            author=self.name,
            branch=ctx.branch,
            content=types.Content(
                role='model',
                parts=[types.Part.from_text(text=text)],
            ),
            actions=EventActions(state_delta=state_delta),
        )

    def _emit_cached_result(
        self,
        ctx: InvocationContext,
        cached_payload: dict[str, Any],
    ) -> Event:
        """Re-emit the cached terminal event (F-05 anti-thrashing).

        Fires when the staged SOW hash matches the hash from the last
        full loop run. Re-running the critic on a payload that already
        produced a terminal status would just spend tokens to arrive at
        the same answer; surfacing the cached envelope keeps the root's
        downstream logic identical to the fresh-run path (same JSON
        shape in ``content``, same ``state_delta`` key).

        ``state[STATE_LAST_LOOP_HASH]`` and
        ``state[QUALITY_LOOP_RESULT_KEY]`` are already in sync with the
        cached payload (the previous run wrote both), so no extra state
        writes happen here — the ``state_delta`` is kept for the runner
        / session service to re-persist the result the caller is about
        to consume.
        """
        # Mirror the severity surface produced by ``_emit_result`` so the
        # cached envelope is structurally indistinguishable from a fresh
        # run (apart from the ``cached: True`` marker). See the rationale
        # comment in ``_emit_result``.
        cached_report = cached_payload.get('final_report') or {}
        cached_blocker_count = cached_report.get('blocker_count', 0)
        cached_major_count = cached_report.get('major_count', 0)
        text_envelope = {
            'status': cached_payload.get('status'),
            'rounds_used': cached_payload.get('rounds_used'),
            'summary': cached_report.get('summary', ''),
            'blocker_count': cached_blocker_count,
            'major_count': cached_major_count,
            'blocking_total': cached_blocker_count + cached_major_count,
            'state_key': QUALITY_LOOP_RESULT_KEY,
            'cached': True,
        }
        if cached_payload.get('observed_status') is not None:
            text_envelope['observed_status'] = cached_payload['observed_status']

        text = json.dumps(text_envelope, ensure_ascii=False)
        return Event(
            invocation_id=ctx.invocation_id,
            author=self.name,
            branch=ctx.branch,
            content=types.Content(
                role='model',
                parts=[types.Part.from_text(text=text)],
            ),
            actions=EventActions(
                state_delta={QUALITY_LOOP_RESULT_KEY: cached_payload},
            ),
        )


sow_quality_loop = QualityLoopAgent(
    name='sow_quality_loop',
    description=(
        'Validates the staged SOW and applies surgical patches until the '
        'critic returns `passed`, escalates `needs_human_review`, or the '
        'loop exhausts its round budget. Reads `state[app:sow:current]` + '
        '`state[app:validation_result]`; writes the terminal outcome to '
        '`state[app:sow:quality_loop_result]`.'
    ),
    sub_agents=[validation_critic, revision_agent],
    # Cross-section repair routes (commit 6). Each entry maps a section
    # name (the bundle key, NOT the ``_agent`` suffix) to the section
    # agent that owns the corresponding bundle. The keys must match the
    # values in :data:`_CROSS_SECTION_REPAIR_ROUTES` — the partition
    # logic and this wiring are coupled by section name. Adding a new
    # section means adding entries to BOTH (the routing table tells the
    # loop which findings belong here; this map tells it which agent to
    # invoke).
    # Phase 3 rollout — every repair route now goes through the
    # tool-based ``apply_<section>_patch`` flow. The first-gen
    # SequentialAgents stay defined for root-side generation only;
    # the loop never regenerates a bundle in repair mode.
    repair_section_agents={
        'requirements': requirements_repair_agent,
        'delivery_plan': delivery_plan_repair_agent,
        'scope_boundaries': scope_boundaries_repair_agent,
        'architecture': architecture_repair_agent,
        'narrative': narrative_repair_agent,
    },
)
