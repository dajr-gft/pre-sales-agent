"""Step 4 of validation_critic — Python-only gate decision.

Reads ``state[STATE_DET_RESULT]`` and the five per-skill keys
(``state[app:skill_findings:{name}]``), deduplicates findings, calibrates
severity, decides ``overall_status``, and writes a partial
``ValidationReport`` (no summary, no next_action) to
``state[STATE_REPORT_PARTIAL]``. The summary skill never alters anything
produced here — it only fills the two text fields.

Severity calibration rules (executed in order, all deterministic):
- ``(skill, category)`` in policy table           → force
                                                    ``resolution_mode = auto_fixable``
                                                    (mechanical-fix categories
                                                    cannot escalate even if the
                                                    LLM emitted a stricter mode).
- BLOCKER with `confidence < 0.7`                 → downgrade to MAJOR.
- BLOCKER without ≥ 2 quoted anchors in evidence  → downgrade to MAJOR.
- ``resolution_mode == auto_fixable``             → force
                                                    ``requires_human_review = False``
                                                    (auto-fixable always wins
                                                    over a stale legacy flag).
- ``resolution_mode != auto_fixable``             → force
                                                    ``requires_human_review = True``
                                                    so the report stays
                                                    internally consistent.

Gate rules (per-finding routing, order matters):
- Critical skill failed                           → `needs_human_review`.
- Any auto_fixable finding present (or any
  deterministic error)                            → `blocked`.
- No auto_fixable findings remain AND at least one
  finding has ``resolution_mode`` in
  {decision_required, source_conflict,
  not_fixable_by_agent}                           → `needs_human_review`.
- Any BLOCKER or MAJOR not classified above       → `blocked`
                                                    (defensive — calibration
                                                    should have settled mode).
- Otherwise                                       → `passed`.

Per-finding routing is the central invariant: a mixed batch of
``auto_fixable`` and ``decision_required`` findings ALWAYS resolves to
``blocked`` first so the revision_agent gets to patch the auto-fixable
ones. Only when the next round's critic returns a report whose only
remaining findings are escalations does the gate flip to
``needs_human_review``. This prevents a single escalated finding from
contaminating an entire batch and bypassing automatic corrections the
revision_agent could have applied — the failure mode observed when
``coverage/manifest_item_uncovered`` (auto_fixable) rode alongside a
single ``contradictions/assumptions_vs_risks`` (decision_required) into
the user-facing question.

Severity is intentionally NOT a trigger for `needs_human_review`: a
BLOCKER with ``resolution_mode == auto_fixable`` resolves to `blocked`
so the QualityLoopAgent can invoke the revision_agent. Human review is
reserved for findings whose resolution requires a decision the agent
cannot make from the SOW, manifest, and references alone — AND only
after every auto-fixable finding in the same report has been processed.

`blocked` means the root/reviser may attempt an automatic correction.
`needs_human_review` means every auto-fixable finding has already been
addressed (or the report had none to begin with) and the remaining
findings require external guidance.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import AsyncGenerator, ClassVar, Iterable

import structlog
from google.adk.agents import BaseAgent
from google.adk.agents.base_agent_config import BaseAgentConfig
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions

from .field_vocabulary import classify_finding_fields
from .schema import (
    PERSISTENCE_WINDOW_ROUNDS,
    PRIOR_FINGERPRINTS_CAP,
    SKILL_NAMES,
    STATE_DET_RESULT,
    STATE_PRIOR_BLOCKING_FINGERPRINTS,
    STATE_REPORT_PARTIAL,
    STATE_ROUND_COUNT,
    STATE_STAGE,
    DeterministicResult,
    Finding,
    SkillRunMetadata,
    Status,
    ValidationReport,
    skill_findings_state_key,
)

logger = structlog.get_logger()

_SEVERITY_ORDER = {'BLOCKER': 0, 'MAJOR': 1, 'MINOR': 2}
_BLOCKER_CONFIDENCE_FLOOR = 0.7
_CRITICAL_SKILLS = frozenset({'coverage', 'contradictions'})
# Resolution modes that justify ``needs_human_review``. ``auto_fixable``
# is the default and is excluded on purpose — the whole calibration
# change exists to stop severity alone from leaking into the human-review
# gate.
_HUMAN_REVIEW_MODES: frozenset[str] = frozenset(
    {'decision_required', 'source_conflict', 'not_fixable_by_agent'},
)

# Policy table: (skill, category) pairs whose ``resolution_mode`` is
# forced to ``auto_fixable`` regardless of what the LLM emitted. These
# are defects whose remedy is mechanical — restore a manifest anchor,
# insert a canonical contractual clause, disambiguate a scope phrase,
# remove out-of-source drift, link a deliverable to its phase — and
# every category here has a corresponding mapping in
# ``sow-revision/references/finding-map.md`` so the revision_agent has
# a reference to load before patching.
#
# Why force, not trust: the per-skill SKILL.md prompts already instruct
# the LLM to default to ``auto_fixable`` for these categories, but
# probabilistic outputs occasionally emit ``decision_required`` or
# ``not_fixable_by_agent`` and the aggregator used to trust them
# verbatim — single-finding over-escalation could surface a user
# question that the revision_agent had every input to resolve
# automatically. Forcing the mode here turns "expected behavior" into a
# determinism the rest of the pipeline can rely on.
#
# Severity remains independent. A BLOCKER in this table still routes
# to ``blocked`` so the quality loop can patch; severity is calibrated
# separately by the existing confidence + anchor checks.
#
# Categories deliberately NOT in this table keep LLM discretion because
# their fix can be a real commercial / scope / trade-off decision (e.g.
# ``contractual_exposure/subjective_nfr_target`` — quantifying an NFR
# from the manifest is auto-fixable, but choosing whether to relax the
# target to fit a cost envelope is ``decision_required``).
_POLICY_FORCED_AUTO_FIXABLE: frozenset[tuple[str, str]] = frozenset(
    {
        # Coverage gaps — restore the manifest anchor.
        ('coverage', 'manifest_item_uncovered'),
        # Mechanical contradictions — disambiguate / link / reconcile.
        ('contradictions', 'scope_vs_oos'),
        ('contradictions', 'timeline_vs_deliverables'),
        ('contradictions', 'activities_vs_deliverables'),
        # Internal contradictions between assumptions and risks — both
        # sides live in the same SOW, so the fix is always refinement
        # of one item to align with the other (no external dependency).
        # Unlike ``self_sufficiency_break`` (external doc reference)
        # which is intentionally kept off the policy table because the
        # gap can be genuinely external, this pair has no external
        # dependency and is safe to force.
        ('contradictions', 'assumptions_vs_risks'),
        # Standard contractual hardening — insert the canonical clause.
        ('contractual_exposure', 'missing_consequence_clause'),
        ('contractual_exposure', 'missing_change_request_gate'),
        ('contractual_exposure', 'missing_handover_boundary'),
        ('contractual_exposure', 'incomplete_parent_contract_reference'),
        # All required disclosures — insert the canonical sentence.
        ('disclosures', 'missing_ai_nondeterminism_disclosure'),
        ('disclosures', 'missing_external_api_dependency_disclosure'),
        ('disclosures', 'missing_pii_responsibility_disclosure'),
        ('disclosures', 'missing_production_handover_disclosure'),
        ('disclosures', 'missing_customer_infra_dependency_disclosure'),
        ('disclosures', 'missing_multi_region_authority_disclosure'),
        # Out-of-source drift / hallucination — remove the ungrounded item.
        ('semantic_quality', 'naming_drift'),
    }
)


# Anchor pattern: SOW-specific stable identifiers the critic quotes in
# ``evidence``. Stable across critic invocations because the IDs come from
# the staged SOW itself (the section agents own ID generation), not from
# the critic's prose. Used to derive a textual discriminator for
# :func:`_fingerprint` that survives the critic re-quoting the same
# finding with slightly different wording across rounds.
#
# Word boundaries on both sides prevent false hits like ``AES-256`` (the
# leading ``\b`` matches between non-word ``S`` neighbours but the next
# captured group is ``A`` so the position must be a word boundary BEFORE
# an A/I/etc — ``AES-256`` does not satisfy that).
# Kept in sync with ``app.tools.sow._anchor_utils.ANCHOR_ID_PATTERN``.
# Two formats: the legacy dashed form (``FR-NN``, ``NFR-NN``, ``WS-NN``,
# ``R-NN``, …) AND the dotted deliverable form (``WS<phase>.<seq>``,
# e.g. ``WS01.1``, ``WS03.6``) which is what the model actually emits
# for deliverables.
_ANCHOR_PATTERN = re.compile(
    r'\b(?:'
    r'(?:FR|NFR|WS|OOS|A|I|R|T|G|P)-\d{1,4}'
    r'|WS\d{1,3}\.\d{1,3}'
    r')\b',
    flags=re.IGNORECASE,
)

# Fallback normalisation cap. The unstable evidence prose is at most this
# long after lower+alphanumeric+collapse-whitespace, so two near-identical
# findings (same skill+category+fields, no anchors) still share a
# fingerprint even if punctuation / case / extra adjective drift between
# rounds.
_EVIDENCE_FALLBACK_CHARS = 120


# Categories whose canonical identity is the ``manifest_item_id`` alone.
# These findings flag a specific manifest entry as uncovered/misaligned;
# the manifest id is generated by the manifest tools (not by the critic)
# and stays stable across critic re-invocations on the same defect. The
# critic's evidence prose, by contrast, can shift round-to-round even
# when the underlying gap is identical — using it in the fingerprint key
# caused the same defect to be re-fingerprinted every round (production
# log: a single manifest item appeared in 4 of 5 rounds with 4 distinct
# fingerprints, breaking persistence detection entirely).
#
# Rule: for any (skill, category) in this set, the fingerprint key is
# (skill, category, manifest_item_id) — evidence and fields are
# intentionally dropped. The manifest id is the identity; everything
# else is unstable description.
_MANIFEST_ID_FINGERPRINT_CATEGORIES: frozenset[tuple[str, str]] = frozenset({
    ('coverage', 'manifest_item_uncovered'),
})

# Categories whose canonical identity is the missing field(s) the
# finding flags as absent. Each entry here represents "a specific
# schema field is missing from the SOW" — the field name IS the
# defect's identity, and the critic's prose describing the absence is
# inherently unstable. Keying on the sorted ``fields`` tuple makes the
# same missing-field defect resolve to the same fingerprint regardless
# of how the critic phrased the gap on a given round.
#
# Coverage with the prior set + this set means every "structurally
# identifiable" finding has a deterministic identity that survives
# critic rewording — the evidence-derived discriminator is reserved
# for findings whose only stable signal IS in the prose (the textual
# / semantic categories below).
_FIELD_FINGERPRINT_CATEGORIES: frozenset[tuple[str, str]] = frozenset({
    ('contractual_exposure', 'missing_consequence_clause'),
    ('contractual_exposure', 'missing_change_request_gate'),
    ('contractual_exposure', 'missing_handover_boundary'),
    ('contractual_exposure', 'incomplete_parent_contract_reference'),
    ('disclosures', 'missing_ai_nondeterminism_disclosure'),
    ('disclosures', 'missing_external_api_dependency_disclosure'),
    ('disclosures', 'missing_pii_responsibility_disclosure'),
    ('disclosures', 'missing_production_handover_disclosure'),
    ('disclosures', 'missing_customer_infra_dependency_disclosure'),
    ('disclosures', 'missing_multi_region_authority_disclosure'),
})


def _evidence_discriminator(evidence: str) -> str | tuple[str, ...]:
    """Stable textual discriminator extracted from ``Finding.evidence``.

    Returns a sorted tuple of anchor literals when any are present in the
    evidence — the canonical case, since contradictions / coverage /
    contractual_exposure findings always quote SOW ids in their evidence.
    Falls back to a heavily normalised prefix of the prose for skills
    that legitimately have no anchors (some ``semantic_quality`` style
    findings, ``naming_drift``, generic disclosure language).

    Why both: the production loop showed ``persistent_blocking_finding_count: 0``
    across every round even when the reviser only patched 5 of ~10
    significant findings — meaning the OLD discriminator
    (``evidence[:240].strip().lower()``) was so wording-sensitive that
    even unpatched findings re-fingerprinted differently round to round.
    Anchors-when-present + normalised prefix-when-absent is the smallest
    change that recovers persistence detection (and by extension makes
    :data:`QualityLoopAgent._consecutive_no_progress_rounds` operate on
    meaningful counts).
    """
    if not evidence:
        return ''
    anchors = sorted({m.upper() for m in _ANCHOR_PATTERN.findall(evidence)})
    if anchors:
        return tuple(anchors)
    normalised = re.sub(r'[^a-z0-9 ]+', ' ', evidence.lower())
    normalised = re.sub(r'\s+', ' ', normalised).strip()
    return normalised[:_EVIDENCE_FALLBACK_CHARS]


def _fingerprint(f: Finding) -> str:
    """Stable identity used to deduplicate findings across rounds/skills.

    Category-aware to defend against critic prose drift:

    - ``(skill, category)`` in :data:`_MANIFEST_ID_FINGERPRINT_CATEGORIES`
      → key is ``(skill, category, manifest_item_id)``. The manifest id
      is the canonical identity; evidence and fields are dropped because
      they are unstable across critic re-invocations on the same gap.
    - ``(skill, category)`` in :data:`_FIELD_FINGERPRINT_CATEGORIES`
      → key is ``(skill, category, sorted_fields_tuple)``. The missing
      field set is the canonical identity; evidence is dropped because
      these categories describe "field absent" defects whose identity
      lives in the schema, not the prose.
    - Otherwise → fall back to the legacy textual discriminator
      (anchors when present, normalised prose prefix otherwise) plus
      sorted fields and ``manifest_item_id``. Reserved for categories
      whose only stable signal IS in the evidence (some
      ``semantic_quality`` cases, generic contradictions without
      structural identity).
    """
    key_pair = (f.skill, f.category)
    if key_pair in _MANIFEST_ID_FINGERPRINT_CATEGORIES:
        key: tuple = (f.skill, f.category, f.manifest_item_id or '')
    elif key_pair in _FIELD_FINGERPRINT_CATEGORIES:
        key = (f.skill, f.category, tuple(sorted(f.fields or [])))
    else:
        key = (
            f.skill,
            f.category,
            _evidence_discriminator(f.evidence or ''),
            tuple(sorted(f.fields or [])),
            f.manifest_item_id or '',
        )
    return hashlib.sha256(repr(key).encode('utf-8')).hexdigest()[:16]


def _has_two_anchors(evidence: str) -> bool:
    """Heuristic for the BLOCKER evidence bar — count quoted SOW items."""
    if not evidence:
        return False
    anchors = sum(1 for token in ('FR-', 'NFR-', 'OOS-', 'A-', 'I-') if token in evidence)
    return anchors >= 2 or evidence.count("'") >= 4 or evidence.count('"') >= 4


def _normalize_findings(raw: Iterable[dict] | None) -> list[Finding]:
    """Drop malformed entries (wrong schema) rather than crashing the gate."""
    out: list[Finding] = []
    if not raw:
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            out.append(Finding.model_validate(item))
        except Exception as exc:
            logger.warning(
                'invalid_finding_dropped',
                error=str(exc),
                evidence_excerpt=(item.get('evidence') or '')[:120],
            )
    return out


@dataclass(slots=True)
class _LintReport:
    """Findings + structural diagnosis produced by :func:`_lint_field_vocabulary`.

    The aggregator emits the counters and drift sets as a single
    ``validation_field_vocabulary_summary`` event per round. Visibility
    of these classes is the whole point of running the lint: a
    high orphan count round-after-round is a signal that the critic
    is cunhing new vocabulary the schema does not carry; a high
    manifest-only count signals the critic is mislabeling re-discovery
    work as auto-fixable.

    Vocabulary drift sets are stable-sorted so identical rounds produce
    identical payloads in the log — easy to diff across runs.
    """

    findings: list[Finding] = field(default_factory=list)
    reclassified_manifest_only: int = 0
    reclassified_orphan_only: int = 0
    fields_trimmed: int = 0
    untouched: int = 0
    # Set of distinct field names observed in each non-writable class.
    # When the same orphan name (e.g. ``"project_identity"``) recurs
    # across rounds, the drift set is the deterministic signal to
    # promote it into the canonical vocabulary or fix the critic's
    # emission template.
    unique_manifest_fields_seen: list[str] = field(default_factory=list)
    unique_orphan_fields_seen: list[str] = field(default_factory=list)
    # Per-skill breakdown of which crítico emits the most lint-worthy
    # vocabulary. Helps narrow which SKILL.md prompt to harden first.
    actions_by_skill: dict[str, dict[str, int]] = field(default_factory=dict)


def _lint_field_vocabulary(findings: list[Finding]) -> _LintReport:
    """Post-calibration pass: align ``fields`` with the writable vocabulary.

    Every finding entering the quality loop must carry a ``fields`` list
    the patch pipeline can act on. The patch surface is closed:

    - Section repair agents write bundle-owned fields (one per section).
    - The reviser's ``apply_sow_global_patch`` refuses bundle-owned
      fields (cross-writer divergence) AND manifest-derived fields (the
      manifest is upstream input, not patchable) AND brand-new top-
      level keys.

    Findings whose ``fields`` cite no writable target are therefore
    structurally unfixable by the agent — leaving them as
    ``auto_fixable`` burns rounds on impossible work. The lint
    reclassifies them to ``not_fixable_by_agent`` so the gate can
    surface them for human review instead of looping.

    Three classes of action, partitioned by
    :func:`classify_finding_fields`:

    - **Manifest-only** (every cited field is manifest-derived) →
      ``resolution_mode = not_fixable_by_agent``,
      ``requires_human_review = True``. The fix lives in the manifest /
      discovery loop, not in the SOW.
    - **Orphan-only** (every cited field is unknown to both vocabularies)
      → same reclassification. The finding cites a field no writer owns;
      the agent cannot patch it. Most often a critic typo or a stale
      field name the schema no longer carries.
    - **Mixed** (at least one bundle-owned field plus some manifest or
      orphan noise) → trim ``fields`` to the bundle-owned subset and
      leave the resolution mode intact. The router now has a clean
      writable list to dispatch; the section agent will patch what it
      owns.

    Findings with empty ``fields`` are LEFT UNTOUCHED — the router falls
    back to ``_CROSS_SECTION_REPAIR_ROUTES`` for those, and trimming an
    already-empty list would only add noise to telemetry.

    Order in the aggregator: the lint runs AFTER :func:`_calibrate`'s
    policy-table override on purpose. Structural impossibility (no
    writer for the cited fields) is the final word; a policy claim
    that the category is mechanically fixable cannot conjure a writer
    that does not exist. The lint freely overrides any ``auto_fixable``
    set by the policy table when the fields make the fix structurally
    impossible.
    """
    report = _LintReport()
    manifest_seen: set[str] = set()
    orphan_seen: set[str] = set()
    actions: dict[str, dict[str, int]] = {}

    def _bump(skill: str, action: str) -> None:
        actions.setdefault(skill, {}).setdefault(action, 0)
        actions[skill][action] += 1

    for f in findings:
        classification = classify_finding_fields(f.fields)
        update: dict = {}
        action: str | None = None
        if classification.is_manifest_only or classification.is_orphan_only:
            # Structurally unfixable — no writer can act on these fields.
            if f.resolution_mode != 'not_fixable_by_agent':
                update['resolution_mode'] = 'not_fixable_by_agent'
            if not f.requires_human_review:
                update['requires_human_review'] = True
            if classification.is_manifest_only:
                action = 'reclassified_manifest_only'
                report.reclassified_manifest_only += 1
            else:
                action = 'reclassified_orphan_only'
                report.reclassified_orphan_only += 1
            logger.warning(
                'finding_field_lint_applied',
                finding_id=f.id,
                skill=f.skill,
                category=f.category,
                original_fields=list(f.fields or []),
                manifest_derived=list(classification.manifest_derived),
                orphan=list(classification.orphan),
                action='reclassified_not_fixable',
                reason=action,
            )
        elif classification.needs_trim:
            # At least one writable field remains; drop the noise so the
            # router and the section agent see a clean target list.
            trimmed = list(classification.bundle_owned)
            if trimmed != list(f.fields or []):
                update['fields'] = trimmed
            action = 'fields_trimmed'
            report.fields_trimmed += 1
            logger.info(
                'finding_field_lint_applied',
                finding_id=f.id,
                skill=f.skill,
                category=f.category,
                original_fields=list(f.fields or []),
                writable_fields=list(classification.bundle_owned),
                dropped_manifest=list(classification.manifest_derived),
                dropped_orphan=list(classification.orphan),
                action=action,
                reason='mixed_writable_and_unwritable',
            )
        else:
            action = 'untouched'
            report.untouched += 1
        # Drift sets accumulate from ALL findings — even ``untouched``
        # ones may have cited manifest-derived names alongside writable
        # ones (those go via the trim path, but the manifest names show
        # up too). For untouched findings ``classification.manifest_derived``
        # and ``.orphan`` are empty by construction, so the union below
        # only grows when the field actually IS non-writable.
        manifest_seen.update(classification.manifest_derived)
        orphan_seen.update(classification.orphan)
        if action is not None:
            _bump(f.skill, action)
        if update:
            f = f.model_copy(update=update)
        report.findings.append(f)

    report.unique_manifest_fields_seen = sorted(manifest_seen)
    report.unique_orphan_fields_seen = sorted(orphan_seen)
    report.actions_by_skill = actions
    return report


def _calibrate(findings: list[Finding]) -> list[Finding]:
    """Apply policy override + severity downgrade + flag reconciliation.

    Three independent calibrations applied in a single pass so the rest
    of the pipeline (gate, score, persistence tracking) can trust the
    severity label, the resolution mode, AND the legacy human-review
    flag simultaneously:

    1. **Policy override.** When ``(skill, category)`` is in
       :data:`_POLICY_FORCED_AUTO_FIXABLE`, ``resolution_mode`` is
       forced to ``auto_fixable`` even if the LLM emitted something
       stricter. These are categories whose fix is mechanical (canonical
       clause insertion, manifest-anchor restore, drift removal, phase
       linking) and that the revision_agent has a finding-map entry
       for. Forcing here turns "the prompt usually defaults to
       auto_fixable" into a deterministic guarantee. See the policy
       table's module-level docstring for the rationale.

    2. **Severity downgrade.** A BLOCKER without enough confidence
       (≥ 0.7) or without two quoted anchors in the evidence drops to
       MAJOR. This protects against costly false positives — a
       confidently-quoted BLOCKER is a real blocker; anything weaker is
       at most a MAJOR.

    3. **resolution_mode / requires_human_review reconciliation.**
       After steps 1 and 2, ``resolution_mode`` is authoritative:

       - ``auto_fixable`` → force ``requires_human_review=False``. The
         revision_agent can apply the fix from the recommendation alone;
         a stray ``True`` from the LLM does not override that.
       - any other mode → force ``requires_human_review=True`` so the
         report's two human-review channels never disagree.

    Severity does NOT influence the human-review flag. A BLOCKER can
    remain ``auto_fixable``; conversely a MINOR can be
    ``decision_required``. This decoupling is the whole point of the
    calibration change — see the module docstring for the rationale.
    """
    calibrated: list[Finding] = []
    for f in findings:
        update: dict = {}
        # Step 1 — policy override. Compute the effective mode locally
        # so the reconciliation in step 3 sees the post-override value
        # without depending on the order Pydantic applies model_copy
        # updates.
        if (f.skill, f.category) in _POLICY_FORCED_AUTO_FIXABLE:
            if f.resolution_mode != 'auto_fixable':
                update['resolution_mode'] = 'auto_fixable'
        effective_mode = update.get('resolution_mode', f.resolution_mode)
        # Step 2 — severity downgrade.
        if f.severity == 'BLOCKER':
            if f.confidence < _BLOCKER_CONFIDENCE_FLOOR or not _has_two_anchors(
                f.evidence
            ):
                update['severity'] = 'MAJOR'
        # Step 3 — reconcile the legacy human-review flag against the
        # (possibly-forced) mode.
        if effective_mode == 'auto_fixable':
            if f.requires_human_review:
                update['requires_human_review'] = False
        elif effective_mode in _HUMAN_REVIEW_MODES:
            if not f.requires_human_review:
                update['requires_human_review'] = True
        if update:
            f = f.model_copy(update=update)
        calibrated.append(f)
    return calibrated


def _dedupe(findings: list[Finding]) -> list[Finding]:
    seen: dict[str, Finding] = {}
    for f in findings:
        fp = _fingerprint(f)
        existing = seen.get(fp)
        if existing is None or f.confidence > existing.confidence:
            seen[fp] = f
    return sorted(
        seen.values(),
        key=lambda x: (_SEVERITY_ORDER.get(x.severity, 9), -x.confidence),
    )


def _is_blocking_finding(f: Finding, det: DeterministicResult) -> bool:
    """Return True if this finding (post-calibration) contributes to ``blocked``.

    Single source of truth for "blocking" semantics consumed by round tracking.
    Today this mirrors the gate policy in :func:`_decide_status`: any
    ``BLOCKER`` or ``MAJOR`` finding makes the gate ``blocked``. If the gate
    policy changes (e.g. additional criteria), update this helper in one
    place and round tracking stays consistent.

    Note: ``det`` is accepted for forward-compatibility (e.g. if a future
    policy makes some severities blocking only when there are no
    deterministic errors). It is unused today.
    """
    del det  # reserved for future policy revisions
    return f.severity in {'BLOCKER', 'MAJOR'}


def _decide_status(
    det: DeterministicResult,
    findings: list[Finding],
    skills_failed_critical: bool,
) -> tuple[Status, bool]:
    """Return (overall_status, requires_human_review). LLM never touches this.

    Per-finding routing: ``auto_fixable`` findings ALWAYS get the first
    word. The status only flips to ``needs_human_review`` when the
    report contains zero auto-fixable findings AND zero deterministic
    errors AND at least one finding whose ``resolution_mode`` requires
    external guidance. Mixed batches (some auto, some escalating)
    resolve to ``blocked`` so the revision_agent processes the auto-
    fixable ones first; the next critic round either drops them (fixed)
    or marks them ``persistent``, and only when the round contains
    nothing patchable does the gate ask the user.

    Why: the previous policy escalated the whole batch the moment any
    finding carried ``decision_required``. A common production pattern —
    a manifest-anchor gap (auto_fixable) alongside a doc-reference
    contradiction (decision_required) — surfaced both to the user and
    the auto-fixable one was never patched, even though the policy
    table guarantees its resolution_mode. Per-finding routing eliminates
    that contamination.

    Severity does NOT trigger ``needs_human_review`` independently of
    ``resolution_mode``. A BLOCKER + auto_fixable still routes to
    ``blocked``; the defensive severity branch at the bottom catches
    findings that survived calibration without a clear mode (should not
    happen, kept for safety).
    """
    if skills_failed_critical:
        return 'needs_human_review', True

    has_det_error = det.error_count > 0

    # Routing is restricted to *significant* findings — BLOCKER and
    # MAJOR. MINOR findings are cosmetic (style, wording, label
    # consistency); running a full revision round to fix one MINOR is
    # bad ROI, and routinely escalating cosmetic issues to the user is
    # the over-escalation behaviour we are killing. MINOR findings stay
    # visible in the report (telemetry, optional voluntary fix during
    # the user review) but never gate ``overall_status``.
    significant = [
        f for f in findings if f.severity in ('BLOCKER', 'MAJOR')
    ]
    has_auto_significant = any(
        f.resolution_mode == 'auto_fixable' for f in significant
    )
    has_escalate_significant = any(
        f.resolution_mode in _HUMAN_REVIEW_MODES for f in significant
    )

    # Anything patchable that matters? Defer escalation, let the
    # revision_agent take its turn. Once the auto-fixable findings clear
    # (or hit max rounds and become persistent), the next iteration's
    # report flows through the escalation branch below.
    if has_auto_significant or has_det_error:
        return 'blocked', False

    # No auto-fixable significant findings remain. If escalations are
    # still here, they are now the sole reason the report is not
    # ``passed`` — surface them to the user.
    if has_escalate_significant:
        return 'needs_human_review', True

    # Defensive: a significant finding that survived calibration without
    # a clear resolution mode still blocks (rather than silently pass)
    # so the loop keeps iterating instead of declaring victory on a
    # broken report.
    if significant:
        return 'blocked', False
    return 'passed', False


def _overall_score(det: DeterministicResult, findings: list[Finding]) -> float:
    """Heuristic 0..1 score for telemetry — never the gate."""
    score = 1.0
    score -= 0.4 * min(det.error_count, 2)
    score -= 0.05 * min(det.warning_count, 4)
    for f in findings:
        weight = {'BLOCKER': 0.4, 'MAJOR': 0.15, 'MINOR': 0.05}.get(
            f.severity, 0.0
        )
        score -= weight
    return max(0.0, min(1.0, score))


class ValidationAggregatorAgent(BaseAgent):
    """Python-only gate. Consumes deterministic + 5 skill outputs from state."""

    config_type: ClassVar[type[BaseAgentConfig]] = BaseAgentConfig

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        det_raw = state.get(STATE_DET_RESULT) or {}
        det = (
            DeterministicResult.model_validate(det_raw)
            if det_raw
            else DeterministicResult(passed=False, error_count=1)
        )

        all_findings: list[Finding] = []
        skills_run: list[SkillRunMetadata] = []
        skills_not_run: list[str] = []
        skills_failed_critical = False
        # ``round_count`` is incremented later in this method; compute the
        # value the current round WILL have so the raw-emission log is
        # tagged consistently with the post-lint summary below.
        provisional_round_count = int(state.get(STATE_ROUND_COUNT) or 0) + 1

        for name in SKILL_NAMES:
            payload = state.get(skill_findings_state_key(name)) or {}
            ran = bool(payload)
            raw_findings = (
                payload.get('findings')
                if isinstance(payload, dict)
                else None
            )
            findings = _normalize_findings(raw_findings)
            all_findings.extend(findings)

            if ran:
                skills_run.append(
                    SkillRunMetadata(
                        skill=name,
                        ran=True,
                        finding_count=len(findings),
                    )
                )
                # Raw emission snapshot — captures what the LLM-backed
                # skill actually returned, BEFORE policy override,
                # severity downgrade, lint, or dedupe. This is the post-
                # mortem trail when ``validation_field_vocabulary_summary``
                # shows orphan/manifest noise: we can correlate which
                # skill emitted which field at which round and harden
                # the SKILL.md prompt at the source.
                logger.info(
                    'validation_raw_skill_emission',
                    round_count=provisional_round_count,
                    skill=name,
                    raw_finding_count=len(findings),
                    raw_findings=[
                        {
                            'id': f.id,
                            'category': f.category,
                            'severity': f.severity,
                            'confidence': round(f.confidence, 2),
                            'resolution_mode': f.resolution_mode,
                            'fields': list(f.fields or [])[:6],
                            'manifest_item_id': f.manifest_item_id,
                        }
                        for f in findings
                    ],
                )
            else:
                skills_not_run.append(name)
                if name in _CRITICAL_SKILLS:
                    skills_failed_critical = True

        # Order matters: ``_calibrate`` applies the policy-table override,
        # then ``_lint_field_vocabulary`` has the FINAL word — structural
        # impossibility (no writer for the cited fields) always beats a
        # policy claim that the category is "mechanically fixable". A
        # finding whose fields are entirely manifest-derived or orphan
        # ends up ``not_fixable_by_agent`` even when the policy table
        # would normally have forced ``auto_fixable``.
        lint_report = _lint_field_vocabulary(_calibrate(all_findings))
        findings = _dedupe(lint_report.findings)

        # Vocabulary drift telemetry — one event per round so dashboards
        # can chart class counts over time and surface recurring orphan
        # names (e.g. ``project_identity`` repeating across runs is the
        # signal to either promote the name into the canonical
        # vocabulary OR fix the SKILL.md prompt that emits it). Distinct
        # field names are sorted for diffability across runs.
        logger.info(
            'validation_field_vocabulary_summary',
            round_count=provisional_round_count,
            total_findings_examined=len(lint_report.findings),
            reclassified_manifest_only=lint_report.reclassified_manifest_only,
            reclassified_orphan_only=lint_report.reclassified_orphan_only,
            fields_trimmed=lint_report.fields_trimmed,
            untouched=lint_report.untouched,
            unique_manifest_fields_seen=lint_report.unique_manifest_fields_seen,
            unique_orphan_fields_seen=lint_report.unique_orphan_fields_seen,
            actions_by_skill=lint_report.actions_by_skill,
        )
        overall_status, requires_human = _decide_status(
            det, findings, skills_failed_critical
        )

        # --- Round tracking --------------------------------------------------
        # The aggregator is the only writer of round_count and the prior
        # blocking fingerprint window. Reset on stage transition is the root's
        # responsibility (see root_prompt). We only ever increment here.
        #
        # Persistence uses a SLIDING WINDOW (see PERSISTENCE_WINDOW_ROUNDS):
        # a finding is "persistent" when its fingerprint appears in ANY of
        # the last N rounds, not just the immediately prior one. This
        # catches the "disappears for 1 round, returns the next" pattern
        # that single-round comparison classified as "new", under-reporting
        # persistence and starving the loop's no-progress detector.
        window_raw = state.get(STATE_PRIOR_BLOCKING_FINGERPRINTS) or []
        # Adapter for the legacy list[str] shape — in-flight sessions whose
        # state was written before this commit will surface a flat list of
        # fingerprints; treat it as a one-entry window so persistence
        # detection survives the migration without resetting.
        if window_raw and isinstance(window_raw[0], str):
            window: list[list[str]] = [list(window_raw)]
        else:
            # Preserve empty round entries — they shift older non-empty
            # rounds out of the window over time, which is the whole
            # point of the time-bounded lookback.
            window = [
                list(round_fps) if isinstance(round_fps, list) else []
                for round_fps in window_raw
            ]

        # Union across the window — the fingerprint set a current finding
        # is compared against to decide persistence.
        prior_blocking_fps: set[str] = set()
        for round_fps in window:
            prior_blocking_fps.update(round_fps)

        current_blocking_pairs = [
            (f, _fingerprint(f)) for f in findings if _is_blocking_finding(f, det)
        ]
        current_blocking_fps: set[str] = {fp for _, fp in current_blocking_pairs}

        # Mark each finding that is blocking AND was already blocking in
        # ANY recent round as persistent. We mutate the calibrated copies
        # in place so the report carries the flag through to the root.
        for f, fp in current_blocking_pairs:
            if fp in prior_blocking_fps:
                f.persistent = True

        persistent_count = len(current_blocking_fps & prior_blocking_fps)
        new_count = len(current_blocking_fps - prior_blocking_fps)
        resolved_count = len(prior_blocking_fps - current_blocking_fps)

        round_count = int(state.get(STATE_ROUND_COUNT) or 0) + 1

        # Append the current round's blocking set to the window, then trim
        # to the configured window size (oldest first → drop from the head).
        # Per-round cap is independent of window length: PRIOR_FINGERPRINTS_CAP
        # bounds a single round's storage; PERSISTENCE_WINDOW_ROUNDS bounds
        # how many rounds we keep. Worst-case state size is the product.
        capped_round_fps = list(current_blocking_fps)[:PRIOR_FINGERPRINTS_CAP]
        new_window = (window + [capped_round_fps])[-PERSISTENCE_WINDOW_ROUNDS:]
        # ---------------------------------------------------------------------

        sev_counts = Counter(f.severity for f in findings)
        skill_counts = Counter(f.skill for f in findings)

        report = ValidationReport(
            overall_status=overall_status,
            overall_score=_overall_score(det, findings),
            requires_human_review=requires_human,
            deterministic=det,
            findings=findings,
            skills_run=skills_run,
            skills_not_run=skills_not_run,
            stage=state.get(STATE_STAGE) or 'full',
            blocker_count=sev_counts.get('BLOCKER', 0),
            major_count=sev_counts.get('MAJOR', 0),
            minor_count=sev_counts.get('MINOR', 0),
            findings_by_skill=dict(skill_counts),
            round_count=round_count,
            persistent_blocking_finding_count=persistent_count,
            new_blocking_finding_count=new_count,
            resolved_blocking_finding_count=resolved_count,
        )

        partial = report.model_dump()
        state[STATE_REPORT_PARTIAL] = partial
        state[STATE_ROUND_COUNT] = round_count
        state[STATE_PRIOR_BLOCKING_FINGERPRINTS] = new_window

        logger.info(
            'validation_aggregated',
            overall_status=overall_status,
            requires_human_review=requires_human,
            blocker=report.blocker_count,
            major=report.major_count,
            minor=report.minor_count,
            findings_by_skill=report.findings_by_skill,
            skills_not_run=skills_not_run,
            round_count=round_count,
            persistent_blocking_finding_count=persistent_count,
            new_blocking_finding_count=new_count,
            resolved_blocking_finding_count=resolved_count,
        )

        # ----- diagnostic snapshot ------------------------------------------
        # Dump every finding's identity so we can trace what the loop sees
        # round-to-round WITHOUT having to inspect session state. Captures:
        # - id, skill, category, severity, confidence, resolution_mode,
        #   persistent — all the calibration outputs.
        # - manifest_item_id — key for tracking coverage churn (if the
        #   same manifest item keeps re-appearing as uncovered, a section
        #   agent is dropping its anchor).
        # - fields (up to 3) — key for tracking which top-level keys keep
        #   getting flagged after patches.
        # - fingerprint — short hash from :func:`_fingerprint`; tells us
        #   when "the same defect" recurs even if the textual evidence
        #   shifts slightly (the fix we landed in
        #   :func:`_evidence_discriminator` makes this number meaningful).
        #
        # Recomputes fingerprints for non-blocking findings (we only kept
        # the blocking pair list above for state-tracking) — cheap, ~20
        # findings per round at most.
        findings_snapshot = []
        for f in findings:
            findings_snapshot.append({
                'id': f.id,
                'skill': f.skill,
                'category': f.category,
                'severity': f.severity,
                'confidence': round(f.confidence, 2),
                'resolution_mode': f.resolution_mode,
                'persistent': f.persistent,
                'manifest_item_id': f.manifest_item_id,
                'fields': list(f.fields or [])[:3],
                'fingerprint': _fingerprint(f),
            })
        logger.info(
            'validation_findings_snapshot',
            round_count=round_count,
            stage=state.get(STATE_STAGE) or 'full',
            total_findings=len(findings_snapshot),
            findings=findings_snapshot,
        )
        # --------------------------------------------------------------------

        # State-only event. Telemetry already in Cloud Logging via logger.info.
        # No Content so the gate decision does not surface to the chat — the
        # root agent reads `state[STATE_VALIDATION_RESULT]` after the
        # assembler runs and produces the user-facing reply.
        yield Event(
            invocation_id=ctx.invocation_id,
            author=self.name,
            branch=ctx.branch,
            actions=EventActions(
                state_delta={
                    STATE_REPORT_PARTIAL: partial,
                    STATE_ROUND_COUNT: round_count,
                    STATE_PRIOR_BLOCKING_FINGERPRINTS: new_window,
                },
            ),
        )


validation_aggregator_agent = ValidationAggregatorAgent(
    name='validation_aggregator_agent',
    description=(
        'Python-only gate that dedupes findings from the 5 skills, '
        'calibrates severity, decides overall_status, and writes the '
        'partial ValidationReport.'
    ),
)
