"""Contract tests for :mod:`app.sub_agents.validation.field_vocabulary`.

The leaf module is the SINGLE SOURCE OF TRUTH for which top-level
``sow_data`` fields each writer in the patch pipeline can touch.
Three independent consumers must agree on it:

- The quality_loop router (``_FIELD_TO_SECTION``) — bundle-owned set.
- The reviser blocklist (``apply_sow_global_patch``) — both sets.
- The assembler (``assemble_payload._PROJECT_METADATA_KEYS``) —
  manifest-derived set.

Drift between these consumers is the root cause this lint exists to
prevent — once a finding's vocabulary stops matching the writer's
contract, the loop spends rounds patching surfaces nobody owns.

These tests guard the invariants by exercising the classification API
and the canonical sets without naming individual fields beyond the
minimum needed to seed the partition (orphan vs writable vs manifest).
"""
from __future__ import annotations

import pytest

from app.sub_agents.validation.field_vocabulary import (
    BUNDLE_OWNED_FIELDS,
    BUNDLE_OWNED_FIELDS_BY_SECTION,
    MANIFEST_DERIVED_FIELDS,
    MANIFEST_DERIVED_FIELDS_TUPLE,
    classify_finding_fields,
)


# ---------------------------------------------------------------------------
# Cross-module invariants
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_bundle_owned_fields_matches_router_table():
    """The router in quality_loop must read from the canonical leaf."""
    from app.sub_agents.quality_loop.agent import _FIELD_TO_SECTION

    assert dict(_FIELD_TO_SECTION) == BUNDLE_OWNED_FIELDS_BY_SECTION
    assert frozenset(_FIELD_TO_SECTION) == BUNDLE_OWNED_FIELDS


@pytest.mark.unit
def test_manifest_derived_fields_matches_assembler_tuple():
    """The assembler must read from the canonical leaf."""
    from app.tools.sow.assemble_payload import _PROJECT_METADATA_KEYS

    assert tuple(_PROJECT_METADATA_KEYS) == MANIFEST_DERIVED_FIELDS_TUPLE
    assert frozenset(_PROJECT_METADATA_KEYS) == MANIFEST_DERIVED_FIELDS


@pytest.mark.unit
def test_bundle_and_manifest_sets_are_disjoint():
    """A field cannot be both bundle-owned and manifest-derived.

    If they ever overlap, two writers would claim the same field and
    the patch pipeline would diverge — exactly the failure class the
    refactor was supposed to eliminate.
    """
    assert BUNDLE_OWNED_FIELDS.isdisjoint(MANIFEST_DERIVED_FIELDS)


@pytest.mark.unit
def test_bundle_owned_sections_match_router_section_set():
    """Every section value in the canonical map must be a real section."""
    from app.sub_agents.quality_loop.agent import _SECTION_ORDER

    sections_in_map = set(BUNDLE_OWNED_FIELDS_BY_SECTION.values())
    assert sections_in_map <= set(_SECTION_ORDER), (
        f'Unknown sections in vocabulary: '
        f'{sections_in_map - set(_SECTION_ORDER)}'
    )


# ---------------------------------------------------------------------------
# Classifier — partition semantics
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_classifier_partitions_all_three_buckets():
    """A mixed input must split into three disjoint tuples."""
    bundle_field = next(iter(BUNDLE_OWNED_FIELDS))
    manifest_field = next(iter(MANIFEST_DERIVED_FIELDS))
    orphan_field = '__definitely_not_a_real_field__'

    c = classify_finding_fields([bundle_field, manifest_field, orphan_field])

    assert c.bundle_owned == (bundle_field,)
    assert c.manifest_derived == (manifest_field,)
    assert c.orphan == (orphan_field,)
    assert c.has_any_claim is True
    assert c.is_writable is True
    assert c.is_manifest_only is False
    assert c.is_orphan_only is False
    assert c.needs_trim is True


@pytest.mark.unit
def test_classifier_empty_input_partitions_to_no_claim():
    """Empty input is a textual finding — neither writable nor unfixable."""
    c = classify_finding_fields([])

    assert c.bundle_owned == ()
    assert c.manifest_derived == ()
    assert c.orphan == ()
    assert c.has_any_claim is False
    assert c.is_writable is False
    assert c.is_manifest_only is False
    assert c.is_orphan_only is False
    assert c.needs_trim is False


@pytest.mark.unit
def test_classifier_skips_empty_strings_and_dedupes():
    """Defensive normalisation — telemetry payload stays stable."""
    bundle_field = next(iter(BUNDLE_OWNED_FIELDS))
    c = classify_finding_fields([bundle_field, '', bundle_field, '   ', None])  # type: ignore[list-item]

    assert c.bundle_owned == (bundle_field,)
    # Empty/whitespace/None entries do not surface anywhere.
    assert all('' not in bucket for bucket in (c.bundle_owned, c.manifest_derived, c.orphan))


@pytest.mark.unit
def test_classifier_manifest_only_when_all_fields_manifest():
    """Class signal: every cited field is manifest-derived."""
    manifest_fields = list(MANIFEST_DERIVED_FIELDS)[:2]
    c = classify_finding_fields(manifest_fields)

    assert c.is_manifest_only is True
    assert c.is_orphan_only is False
    assert c.is_writable is False
    assert c.needs_trim is False


@pytest.mark.unit
def test_classifier_orphan_only_when_no_recognised_fields():
    """Class signal: every cited field is unknown to both vocabularies."""
    c = classify_finding_fields(['typo_field', 'project_identity', 'qux'])

    assert c.is_orphan_only is True
    assert c.is_manifest_only is False
    assert c.is_writable is False
    assert c.needs_trim is False


@pytest.mark.unit
def test_classifier_writable_with_mixed_noise_needs_trim():
    """At least one bundle-owned field + noise → trim signal fires."""
    bundle_field = next(iter(BUNDLE_OWNED_FIELDS))
    c = classify_finding_fields([bundle_field, 'orphan_x', next(iter(MANIFEST_DERIVED_FIELDS))])

    assert c.is_writable is True
    assert c.needs_trim is True
    assert c.is_manifest_only is False
    assert c.is_orphan_only is False
