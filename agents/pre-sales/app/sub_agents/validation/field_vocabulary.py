"""Canonical writable-field vocabulary for the SOW.

Single source of truth for the question every finding raises implicitly:
*can the agent patch this?* Three closed sets partition the universe of
top-level keys a finding's ``fields`` list might cite:

- :data:`BUNDLE_OWNED_FIELDS_BY_SECTION` / :data:`BUNDLE_OWNED_FIELDS`
  — fields written by section sub-agents into typed bundles. The
  quality loop dispatches finding to the owning section's repair
  agent; the section's tool-based patch is the only writer.
- :data:`MANIFEST_DERIVED_FIELDS` — fields derived deterministically
  from the Extraction Manifest at assembly time. NO writer in the
  agent pipeline can mutate these from a SOW finding: the manifest is
  upstream input and the reviser's ``apply_sow_global_patch`` blocks
  them by contract. Findings whose fields lie entirely in this set are
  structurally unfixable by the agent and must be reclassified
  ``not_fixable_by_agent``.
- *orphan* — anything else. A finding citing an orphan field has no
  writer at all (no section owns it, manifest doesn't supply it, and
  the global patch refuses to create new top-level keys). Structurally
  unfixable; reclassify like manifest-only.

The aggregator's lint pass consumes :func:`classify_finding_fields` to
implement those reclassifications. The router (`_FIELD_TO_SECTION` in
`quality_loop.agent`) re-exports :data:`BUNDLE_OWNED_FIELDS_BY_SECTION`
so both layers see the identical contract — adding a field to a bundle
schema is a one-line change here, not a coordinated edit across modules.

This module is the LEAF of the dependency graph: it has no imports from
the rest of the codebase (only stdlib). Both ``validation.aggregator``
and ``quality_loop.agent`` import from here without creating cycles.
"""

from __future__ import annotations

from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Bundle-owned fields — section agents write these via tool-based patches.
# ---------------------------------------------------------------------------
#
# Sorted by section then alphabetically. ``_FIELD_TO_SECTION`` in
# ``quality_loop.agent`` re-exports this mapping as the routing table.
# Adding a field to a bundle schema (``app.sub_agents.schemas``) MUST
# come with a matching entry here, otherwise the router will not
# dispatch findings citing the new field and the reviser will refuse
# to touch it — the finding becomes structurally unfixable.
#
# Test ``test_test_table_covers_every_bundle_owned_field`` lint-guards
# the routing-table coverage of this map. ``test_field_vocabulary``
# guards the inverse direction (schema → vocabulary).
BUNDLE_OWNED_FIELDS_BY_SECTION: dict[str, str] = {
    # requirements bundle
    'functional_requirements': 'requirements',
    'non_functional_requirements': 'requirements',
    # delivery_plan bundle
    'activity_phases': 'delivery_plan',
    'deliverables': 'delivery_plan',
    'timeline': 'delivery_plan',
    'partner_roles': 'delivery_plan',
    'customer_roles': 'delivery_plan',
    'success_criteria': 'delivery_plan',
    'objectives': 'delivery_plan',
    # scope_boundaries bundle
    'assumptions': 'scope_boundaries',
    'out_of_scope': 'scope_boundaries',
    'risks': 'scope_boundaries',
    'handover_disclaimers': 'scope_boundaries',
    'change_request_policy_text': 'scope_boundaries',
    # architecture bundle
    'architecture_description': 'architecture',
    'architecture_components': 'architecture',
    'architecture_integrations': 'architecture',
    'technology_stack': 'architecture',
    # narrative bundle
    'executive_summary': 'narrative',
    'partner_overview': 'narrative',
    'customer_overview': 'narrative',
    'customer_primary_domain': 'narrative',
}

BUNDLE_OWNED_FIELDS: frozenset[str] = frozenset(BUNDLE_OWNED_FIELDS_BY_SECTION)


# ---------------------------------------------------------------------------
# Manifest-derived fields — assembler-only, never patched.
# ---------------------------------------------------------------------------
#
# Order is preserved as a tuple because ``assemble_payload`` iterates
# over it deterministically for the rendered docx header. The frozenset
# below is what the lint actually checks (membership tests should not
# care about order).
#
# ``apply_sow_global_patch._MANIFEST_BLOCKLIST`` should equal this set;
# the test ``test_test_table_matches_assemble_payload_metadata_keys``
# lint-guards that invariant.
MANIFEST_DERIVED_FIELDS_TUPLE: tuple[str, ...] = (
    'partner_name',
    'customer_name',
    'partner_short_name',
    'customer_short_name',
    'project_title',
    'date',
    'author',
    'funding_type',
    'funding_type_short',
    'project_start_date',
    'project_end_date',
    'engagement_type',
    'organization_term',
)

MANIFEST_DERIVED_FIELDS: frozenset[str] = frozenset(MANIFEST_DERIVED_FIELDS_TUPLE)


# ---------------------------------------------------------------------------
# Classification API consumed by the aggregator's lint pass.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FieldVocabularyClassification:
    """Partition of a finding's ``fields`` list against the canonical sets.

    The aggregator uses this partition to decide whether a finding is
    structurally fixable by the agent. Empty input partitions to all-
    empty (the ``has_any_claim`` accessor distinguishes that from
    "input cited only orphan fields" — both deserve different lint
    actions).
    """

    bundle_owned: tuple[str, ...]
    manifest_derived: tuple[str, ...]
    orphan: tuple[str, ...]

    @property
    def has_any_claim(self) -> bool:
        """True when the original input cited at least one non-empty field.

        Used to distinguish ``fields=[]`` (legal textual finding —
        router falls back to (skill, category) table) from
        ``fields=['nonsense']`` (broken claim — reclassify).
        """
        return bool(self.bundle_owned or self.manifest_derived or self.orphan)

    @property
    def is_writable(self) -> bool:
        """At least one cited field has a writer in the patch pipeline."""
        return bool(self.bundle_owned)

    @property
    def is_manifest_only(self) -> bool:
        """All cited fields are manifest-derived (no writer in the agent)."""
        return (
            self.has_any_claim
            and not self.bundle_owned
            and not self.orphan
            and bool(self.manifest_derived)
        )

    @property
    def is_orphan_only(self) -> bool:
        """All cited fields are orphan (no writer anywhere)."""
        return (
            self.has_any_claim
            and not self.bundle_owned
            and not self.manifest_derived
            and bool(self.orphan)
        )

    @property
    def needs_trim(self) -> bool:
        """Has writable fields AND noise (manifest-derived or orphan) to drop."""
        return self.is_writable and bool(self.manifest_derived or self.orphan)


def classify_finding_fields(
    fields: list[str] | tuple[str, ...] | None,
) -> FieldVocabularyClassification:
    """Partition ``fields`` against the canonical vocabulary.

    Skips empty strings and de-duplicates while preserving first-seen
    order so the telemetry payload is stable when the same finding is
    re-linted across rounds. The three output tuples form a disjoint
    cover of the cited non-empty unique fields.
    """
    bundle: list[str] = []
    manifest: list[str] = []
    orphan: list[str] = []
    seen: set[str] = set()
    for raw in fields or ():
        if not raw or not isinstance(raw, str):
            continue
        if raw in seen:
            continue
        seen.add(raw)
        if raw in BUNDLE_OWNED_FIELDS:
            bundle.append(raw)
        elif raw in MANIFEST_DERIVED_FIELDS:
            manifest.append(raw)
        else:
            orphan.append(raw)
    return FieldVocabularyClassification(
        bundle_owned=tuple(bundle),
        manifest_derived=tuple(manifest),
        orphan=tuple(orphan),
    )
