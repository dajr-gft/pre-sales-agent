"""
Extraction Manifest schema — runtime source of truth.

This Pydantic model is the runtime contract for the Extraction Manifest produced
by the sow-discovery skill and consumed by sow-generator. The human-readable
specification lives in skills/sow-discovery/references/manifest-schema.md — the
two MUST stay in sync.

When updating: edit this file FIRST, run the test fixture, then mirror the change
in manifest-schema.md. The cross-reference validators in this file catch the
common manifest defects (missing artifacts, dangling cross-refs, orphaned TBDs)
before the manifest ever reaches sow-generator.
"""

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ---------------------------------------------------------------------------
# Controlled vocabularies — keep aligned with references/extraction-rules.md
# ---------------------------------------------------------------------------

Category = Literal[
    "Identity",
    "Briefing",
    "Integrations",
    "Scope",
    "NFRs",
    "Timeline",
    "Constraints",
    "Decisions",
]

ArtifactType = Literal[
    "pdf",
    "docx",
    "txt",
    "image",
    "audio",
    "transcript",
    "chat-log",
    "screenshot",
    "user-briefing",
]

Confidence = Literal["stated", "implied"]


# ---------------------------------------------------------------------------
# Leaf models
# ---------------------------------------------------------------------------


class Source(BaseModel):
    """Reference to a specific location within an artifact."""

    model_config = ConfigDict(extra="forbid")

    artifact_id: str = Field(
        description="Stable ID of the source artifact, e.g. 'A1', 'A2'."
    )
    anchor: str = Field(
        description=(
            "Finest-precision location within the artifact. Examples: "
            "'p.4 / Capabilities Matrix / row 7', "
            "'slide 2 / observability cluster', "
            "'speaker:Jane / 12:34', "
            "'paragraph 3'."
        )
    )


class InventoryEntry(BaseModel):
    """One artifact in the project context set."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Stable, sequential ID. Format: 'A1', 'A2', 'A3', ...")
    name: str = Field(description="File name as uploaded.")
    type: ArtifactType
    uploaded_at: Optional[str] = Field(
        default=None,
        description="ISO-8601 timestamp if known, otherwise null.",
    )
    phase_0_hypothesis: str = Field(
        description=(
            "One-line working hypothesis from Phase 0 about what this artifact "
            "contains, based only on file name and user framing."
        )
    )
    items_extracted: int = Field(
        default=0,
        ge=0,
        description=(
            "Count of extracted_items whose source includes this artifact. "
            "Auto-populated by the model_validator — you may emit 0 and it will "
            "be overwritten."
        ),
    )
    categories_found: list[Category] = Field(
        default_factory=list,
        description=(
            "Deduplicated list of categories represented by this artifact's items. "
            "Auto-populated by the model_validator — you may emit an empty list "
            "and it will be overwritten."
        ),
    )
    source_language: str = Field(
        description="Language code of the artifact content, e.g. 'pt', 'en', 'es'."
    )
    notes: str = Field(
        default="",
        description=(
            "Free-form. REQUIRED when the artifact contributed zero items, "
            "to justify why (e.g., 'unrelated dashboard screenshot')."
        ),
    )


class ExtractedItem(BaseModel):
    """One concrete fact extracted from the artifact set."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(
        description=(
            "Stable ID. Format: 'I-001', 'I-002', ... Never reused after deletion."
        )
    )
    category: Category
    value: str = Field(
        description=(
            "Short canonical name in English. For systems, the proper name as "
            "written in the source. For decisions or constraints, a faithful "
            "one-line paraphrase."
        )
    )
    value_detail: str = Field(
        default="",
        description=(
            "Longer paraphrase capturing context, role, or qualifier. May be "
            "empty for self-explanatory items like system names."
        ),
    )
    primitives: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Structured sub-extraction. Required keys depend on `category` — "
            "see references/extraction-rules.md per-category 'Required "
            "primitives' sections. When a primitive cannot be determined "
            "from the source, set its value to 'not_stated' rather than "
            "omitting the key. The primitives are what sow-generator reads "
            "to draft each section of the SOW."
        ),
    )
    source: list[Source] = Field(
        min_length=1,
        description=(
            "List of {artifact_id, anchor} pairs. An item mentioned in three "
            "artifacts has three source entries. Must contain at least one entry."
        ),
    )
    confidence: Confidence = Field(
        description=(
            "'stated' for explicit content; 'implied' only when derived from "
            "explicit content within the same artifact via direct logical "
            "inference. 'implied' items remain grounded — never inferences from "
            "outside the artifact set."
        )
    )
    cross_refs: list[str] = Field(
        default_factory=list,
        description=(
            "Sibling extracted_item IDs that share this item's underlying fact "
            "across categories (e.g., a Timeline item that is also a Decision)."
        ),
    )
    notes: str = Field(
        default="",
        description=(
            "Free-form. Used for: acronym status, original-language quote, "
            "contradictions resolved during reconciliation, open ambiguities "
            "that did not warrant moving to gaps."
        ),
    )


class HardGap(BaseModel):
    """Required SOW input that no artifact provides and the user could not resolve."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Stable ID. Format: 'G-001', 'G-002', ...")
    category: Category
    description: str
    interview_turn_asked: int = Field(
        ge=0,
        le=3,
        description=(
            "Phase 3 turn number where this was raised. 0 if not yet asked "
            "(should not happen for HardGaps post-Phase 3)."
        ),
    )
    user_response: str = Field(
        description=(
            "User's actual answer, OR 'deferred to <source>', "
            "OR '[TO BE DEFINED]' if unanswered after 3 turns."
        )
    )
    blocks_sow_generation: bool = Field(
        description=(
            "If True, sow-generator should refuse to proceed without resolution. "
            "If False, sow-generator may use [TO BE DEFINED] placeholders in "
            "the final SOW."
        )
    )


class PendingDecision(BaseModel):
    """Item that the project plan itself defers to a future phase."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Stable ID. Format: 'P-001', 'P-002', ...")
    description: str
    deferral_source: list[Source] = Field(
        min_length=1,
        description="Where in the artifact set this deferral was stated.",
    )
    expected_resolution: str = Field(
        description=(
            "When the decision is expected to be resolved, e.g. "
            "'End of week 6, post-PSO recommendations delivery'."
        )
    )


class Ambiguity(BaseModel):
    """Item present in artifacts but unclear, partially specified, or context-dependent."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Stable ID. Format: 'AM-001', 'AM-002', ...")
    category: Category
    description: str
    source: list[Source] = Field(min_length=1)
    interview_turn_asked: int = Field(
        ge=0,
        le=3,
        description="Phase 3 turn number where this was raised. 0 if not asked.",
    )
    user_response: str


class ToBeDefined(BaseModel):
    """Roll-up entry for [TO BE DEFINED] tokens that should appear in the final SOW."""

    model_config = ConfigDict(extra="forbid")

    item: str = Field(description="Human-readable description of the missing piece.")
    source_gap_id: str = Field(
        description=(
            "ID of the originating HardGap or Ambiguity, e.g. 'G-001' or 'AM-001'."
        )
    )


# ---------------------------------------------------------------------------
# Composite models
# ---------------------------------------------------------------------------


class Gaps(BaseModel):
    """Four-part gap structure: hard gaps, pending decisions, ambiguities, TBD roll-up."""

    model_config = ConfigDict(extra="forbid")

    hard_gaps: list[HardGap] = Field(default_factory=list)
    pending_decisions: list[PendingDecision] = Field(default_factory=list)
    ambiguities: list[Ambiguity] = Field(default_factory=list)
    to_be_defined: list[ToBeDefined] = Field(default_factory=list)


class SelfAudit(BaseModel):
    """Phase 4 self-audit results. All booleans must be True before save."""

    model_config = ConfigDict(extra="forbid")

    all_artifacts_contributed: bool = Field(
        description=(
            "Every artifact in inventory either appears in at least one "
            "extracted_items.source, or has a justifying notes field."
        )
    )
    all_required_categories_covered: bool = Field(
        description=(
            "Every required category in extraction-rules.md has either at least "
            "one extracted item OR an entry in gaps."
        )
    )
    contradictions_resolved_or_flagged: bool = Field(
        description=(
            "All contradictions are flagged with both source positions and a "
            "resolution status."
        )
    )
    user_interview_turns: int = Field(
        ge=0,
        le=3,
        description="Number of Phase 3 interview turns used.",
    )


# ---------------------------------------------------------------------------
# Top-level model
# ---------------------------------------------------------------------------


class ExtractionManifest(BaseModel):
    """
    Hand-off artifact between sow-discovery (producer) and sow-generator (consumer).

    Keep aligned with skills/sow-discovery/references/manifest-schema.md — the
    markdown is the human-readable contract; this class is the runtime validator.
    """

    model_config = ConfigDict(extra="forbid")

    manifest_version: Literal["1.0"] = Field(
        default="1.0",
        description="Schema version. Bump when making breaking changes.",
    )
    created_at: str = Field(description="ISO-8601 timestamp.")
    conversation_language: str = Field(
        description=(
            "Language code of the conversation between user and agent, "
            "e.g. 'pt-BR', 'en', 'es'."
        )
    )
    inventory: list[InventoryEntry] = Field(min_length=1)
    extracted_items: list[ExtractedItem]
    gaps: Gaps
    self_audit: SelfAudit

    # ---- Cross-reference and consistency validators --------------------------

    @model_validator(mode="after")
    def validate_unique_ids(self) -> "ExtractionManifest":
        """Inventory IDs and extracted_item IDs must be globally unique within their list."""
        inv_ids = [e.id for e in self.inventory]
        if len(inv_ids) != len(set(inv_ids)):
            duplicates = sorted({x for x in inv_ids if inv_ids.count(x) > 1})
            raise ValueError(f"Duplicate inventory IDs: {duplicates}")

        item_ids = [i.id for i in self.extracted_items]
        if len(item_ids) != len(set(item_ids)):
            duplicates = sorted({x for x in item_ids if item_ids.count(x) > 1})
            raise ValueError(f"Duplicate extracted_items IDs: {duplicates}")

        gap_ids = (
            [g.id for g in self.gaps.hard_gaps]
            + [p.id for p in self.gaps.pending_decisions]
            + [a.id for a in self.gaps.ambiguities]
        )
        if len(gap_ids) != len(set(gap_ids)):
            duplicates = sorted({x for x in gap_ids if gap_ids.count(x) > 1})
            raise ValueError(f"Duplicate gap IDs across hard_gaps/pending/ambiguities: {duplicates}")

        return self

    @model_validator(mode="after")
    def validate_source_artifacts_exist(self) -> "ExtractionManifest":
        """Every artifact_id referenced in any source list must exist in inventory."""
        inv_ids = {e.id for e in self.inventory}

        def _check(sources: list[Source], context: str) -> None:
            for src in sources:
                if src.artifact_id not in inv_ids:
                    raise ValueError(
                        f"{context} references unknown artifact "
                        f"'{src.artifact_id}'. Known: {sorted(inv_ids)}"
                    )

        for item in self.extracted_items:
            _check(item.source, f"extracted_items[{item.id}].source")
        for pd in self.gaps.pending_decisions:
            _check(pd.deferral_source, f"gaps.pending_decisions[{pd.id}].deferral_source")
        for am in self.gaps.ambiguities:
            _check(am.source, f"gaps.ambiguities[{am.id}].source")

        return self

    @model_validator(mode="after")
    def validate_cross_refs(self) -> "ExtractionManifest":
        """Every cross_refs entry must point to an existing extracted_items ID."""
        item_ids = {i.id for i in self.extracted_items}
        for item in self.extracted_items:
            for ref in item.cross_refs:
                if ref not in item_ids:
                    raise ValueError(
                        f"extracted_items[{item.id}].cross_refs references "
                        f"unknown item '{ref}'. Cross-refs must point to other "
                        f"extracted_items by ID."
                    )
                if ref == item.id:
                    raise ValueError(
                        f"extracted_items[{item.id}].cross_refs contains its "
                        f"own ID. Cross-refs are for sibling items only."
                    )
        return self

    @model_validator(mode="after")
    def validate_to_be_defined_links(self) -> "ExtractionManifest":
        """Every to_be_defined entry must link to an existing HardGap or Ambiguity."""
        gap_ids = {g.id for g in self.gaps.hard_gaps} | {
            a.id for a in self.gaps.ambiguities
        }
        for tbd in self.gaps.to_be_defined:
            if tbd.source_gap_id not in gap_ids:
                raise ValueError(
                    f"to_be_defined item '{tbd.item}' references unknown gap "
                    f"'{tbd.source_gap_id}'. Must be a HardGap or Ambiguity ID."
                )
        return self

    @model_validator(mode="after")
    def validate_artifacts_contributed_or_justified(self) -> "ExtractionManifest":
        """
        Every inventory entry must either appear in some source list, or have
        a non-empty notes field justifying why it produced no items.
        This is the structural defense against the failure mode that motivated
        creating sow-discovery: artifacts being silently skipped during extraction.
        """
        appearances: set[str] = set()
        for item in self.extracted_items:
            for src in item.source:
                appearances.add(src.artifact_id)
        for pd in self.gaps.pending_decisions:
            for src in pd.deferral_source:
                appearances.add(src.artifact_id)
        for am in self.gaps.ambiguities:
            for src in am.source:
                appearances.add(src.artifact_id)

        for entry in self.inventory:
            if entry.id not in appearances and not entry.notes.strip():
                raise ValueError(
                    f"inventory[{entry.id}] '{entry.name}' did not contribute "
                    f"any items, references, or deferrals, and has no justifying "
                    f"notes. Either extract items from it, or add a note "
                    f"explaining why it produced zero items "
                    f"(e.g., 'unrelated dashboard screenshot')."
                )
        return self

    @model_validator(mode="after")
    def validate_identity_engagement_shape(self) -> "ExtractionManifest":
        """
        Identity items must populate the engagement_shape primitive — assessment,
        greenfield, brownfield, migration, or foundation. sow-generator cannot
        structure its output without this. 'not_stated' is acceptable here only
        if the gap is also recorded in gaps.hard_gaps with blocks_sow_generation
        set to true.
        """
        identity_items = [i for i in self.extracted_items if i.category == "Identity"]
        if not identity_items:
            return self  # caught elsewhere if Identity is required

        has_shape = any(
            item.primitives.get("engagement_shape", "not_stated") != "not_stated"
            for item in identity_items
        )
        if not has_shape:
            shape_blocking_gap = any(
                g.category == "Identity"
                and g.blocks_sow_generation
                and "engagement_shape" in g.description.lower()
                for g in self.gaps.hard_gaps
            )
            if not shape_blocking_gap:
                raise ValueError(
                    "No Identity item has primitives.engagement_shape set to a "
                    "concrete value (assessment | greenfield | brownfield | "
                    "migration | foundation). Either populate it on an existing "
                    "Identity item, or record a hard_gap with blocks_sow_generation=true "
                    "and 'engagement_shape' in its description so sow-generator "
                    "can interrupt the user before structuring the SOW."
                )
        return self

    @model_validator(mode="after")
    def populate_derived_inventory_fields(self) -> "ExtractionManifest":
        """
        Auto-populate items_extracted and categories_found per inventory entry,
        based on the actual contents of extracted_items. Overwrites whatever the
        producer emitted — this keeps the counts truthful.
        """
        for entry in self.inventory:
            contributing = [
                item
                for item in self.extracted_items
                if any(src.artifact_id == entry.id for src in item.source)
            ]
            entry.items_extracted = len(contributing)
            entry.categories_found = sorted({item.category for item in contributing})
        return self
