"""Deterministic structural validation for SOW content.

Two-layer design:
- The agent calls ``validate_sow_content`` tool BEFORE presenting to the user
  (self-check — catches issues before the human review gate).
- ``generate_sow_document`` runs the same validator BEFORE rendering
  (hard gate — blocks document generation on errors).

Why NOT a separate reviewer agent in a loop:
1. The pipeline already has 2 human review gates. An LLM reviewer would
   second-guess human-approved content and add latency + token cost.
2. Structural issues (ID format, row counts, word counts, cross-references)
   are deterministic. A validator class catches them faster and more reliably.
3. LLM-reviewer loops risk getting stuck on subjective quality disagreements
   ("not specific enough" -> adds detail -> "too verbose" -> loop).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass
class ValidationIssue:
    severity: Literal["error", "warning"]
    field: str
    message: str
    suggestion: str = ""

    def __str__(self) -> str:
        prefix = "ERROR" if self.severity == "error" else "WARN"
        base = f"[{prefix}] {self.field}: {self.message}"
        if self.suggestion:
            base += f" -> {self.suggestion}"
        return base


@dataclass
class ValidationResult:
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def passed(self) -> bool:
        return len(self.errors) == 0

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "issues": [
                {
                    "severity": i.severity,
                    "field": i.field,
                    "message": i.message,
                    "suggestion": i.suggestion,
                }
                for i in self.issues
            ],
        }


_FR_PATTERN = re.compile(r"^FR-\d{2,3}$")
_NFR_PATTERN = re.compile(r"^NFR-\d{2,3}$")

# Keywords that indicate a consequence clause in assumptions
_CONSEQUENCE_KEYWORDS = [
    "result in",
    "additional cost",
    "timeline extension",
    "proportional",
    "impact",
    "change request",
    "may extend",
    "will require",
    "scope reduction",
    "constitutes acceptance",
    "may result",
    "costs associated",
    "reschedule",
    "timeline review",
]


class ContentValidator:
    """Deterministic structural validation for SOW JSON content.

    Call ``validate()`` with the parsed sow_data dict.
    Returns a ``ValidationResult`` with errors and warnings.
    """

    def validate(
        self,
        data: dict,
        funding_type: str | None = None,
    ) -> ValidationResult:
        result = ValidationResult()

        if funding_type is None:
            ft = (
                data.get("funding_type_short") or data.get("funding_type") or ""
            ).upper()
            funding_type = "PSF" if "PSF" in ft else "DAF"

        self._validate_fr_format(data, result)
        self._validate_nfr_format(data, result)
        self._validate_consumption_plan(data, funding_type, result)
        self._validate_role_descriptions(data, result)
        self._validate_architecture_description(data, result)
        self._validate_assumptions_consequences(data, result)
        self._validate_timeline_consistency(data, result)
        self._validate_tech_stack_consistency(data, result)
        self._validate_deliverable_coverage(data, result)
        self._validate_oos_count(data, result)

        return result

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    def _validate_fr_format(self, data: dict, result: ValidationResult) -> None:
        frs = data.get("functional_requirements", [])
        for i, fr in enumerate(frs):
            num = fr.get("number", "")
            if not _FR_PATTERN.match(num):
                result.issues.append(
                    ValidationIssue(
                        severity="error",
                        field="functional_requirements",
                        message=f"Item {i}: ID '{num}' does not match FR-XX format.",
                        suggestion="Use sequential IDs like FR-01, FR-02.",
                    )
                )
            desc = fr.get("description", "")
            if len(desc) < 20:
                result.issues.append(
                    ValidationIssue(
                        severity="warning",
                        field="functional_requirements",
                        message=f"{num}: description too short ({len(desc)} chars).",
                        suggestion="Each FR should be a complete sentence with technical context.",
                    )
                )

    def _validate_nfr_format(self, data: dict, result: ValidationResult) -> None:
        nfrs = data.get("non_functional_requirements", [])
        for i, nfr in enumerate(nfrs):
            num = nfr.get("number", "")
            if not _NFR_PATTERN.match(num):
                result.issues.append(
                    ValidationIssue(
                        severity="error",
                        field="non_functional_requirements",
                        message=f"Item {i}: ID '{num}' does not match NFR-XX format.",
                        suggestion="Use sequential IDs like NFR-01, NFR-02.",
                    )
                )

    def _validate_consumption_plan(
        self, data: dict, funding_type: str, result: ValidationResult
    ) -> None:
        cp = data.get("consumption_plan")

        if funding_type == "PSF" and not cp:
            result.issues.append(
                ValidationIssue(
                    severity="error",
                    field="consumption_plan",
                    message="Consumption plan is required for PSF engagements.",
                    suggestion="Generate a 12-month per-service consumption table.",
                )
            )
            return

        if not isinstance(cp, dict):
            return

        services = cp.get("services", [])
        rows = cp.get("rows", [])

        if len(rows) != 12:
            result.issues.append(
                ValidationIssue(
                    severity="error",
                    field="consumption_plan",
                    message=f"Expected 12 monthly rows, found {len(rows)}.",
                    suggestion="Provide exactly 12 rows (one per month).",
                )
            )

        for i, row in enumerate(rows):
            costs = row.get("costs", [])
            if len(costs) != len(services):
                result.issues.append(
                    ValidationIssue(
                        severity="error",
                        field="consumption_plan",
                        message=f"Row {i + 1}: {len(costs)} costs but {len(services)} services.",
                        suggestion="Each row must have one cost entry per service.",
                    )
                )

        if not cp.get("notes"):
            result.issues.append(
                ValidationIssue(
                    severity="warning",
                    field="consumption_plan",
                    message="Missing 'notes' explaining estimation assumptions.",
                    suggestion="Add notes explaining why values vary across months.",
                )
            )

    def _validate_role_descriptions(self, data: dict, result: ValidationResult) -> None:
        for field_name, _label in [
            ("partner_roles", "Partner roles"),
            ("customer_roles", "Customer roles"),
        ]:
            roles = data.get(field_name, [])
            for role in roles:
                desc = role.get("responsibilities", "")
                role_name = role.get("role", "?")
                if len(desc) < 100:
                    result.issues.append(
                        ValidationIssue(
                            severity="warning",
                            field=field_name,
                            message=f"'{role_name}': description too short ({len(desc)} chars).",
                            suggestion="Each role needs 2-3 sentences of concrete responsibilities (min 100 chars).",
                        )
                    )

    def _validate_architecture_description(
        self, data: dict, result: ValidationResult
    ) -> None:
        desc = data.get("architecture_description", "")
        word_count = len(desc.split()) if desc else 0
        if word_count < 150:
            result.issues.append(
                ValidationIssue(
                    severity="error" if word_count < 50 else "warning",
                    field="architecture_description",
                    message=f"Architecture description has {word_count} words (minimum 150).",
                    suggestion="Write a data-flow narrative with per-service justifications.",
                )
            )

    def _validate_assumptions_consequences(
        self, data: dict, result: ValidationResult
    ) -> None:
        assumptions = data.get("assumptions", [])
        missing_consequence = 0
        for assumption in assumptions:
            text = assumption.lower() if isinstance(assumption, str) else ""
            has_consequence = any(kw in text for kw in _CONSEQUENCE_KEYWORDS)
            if not has_consequence:
                missing_consequence += 1

        if missing_consequence > 0 and len(assumptions) > 0:
            pct = missing_consequence / len(assumptions) * 100
            if pct > 40:
                result.issues.append(
                    ValidationIssue(
                        severity="warning",
                        field="assumptions",
                        message=f"{missing_consequence}/{len(assumptions)} assumptions ({pct:.0f}%) lack consequence clauses.",
                        suggestion="Pattern: '[Customer] must [obligation]. [Consequence if not met].'",
                    )
                )

    def _validate_timeline_consistency(
        self, data: dict, result: ValidationResult
    ) -> None:
        timeline_phases = {
            t.get("activity", "").strip().lower() for t in data.get("timeline", [])
        }
        activity_phases = {
            p.get("name", "").strip().lower() for p in data.get("activity_phases", [])
        }

        if not timeline_phases or not activity_phases:
            return

        missing_in_timeline = activity_phases - timeline_phases
        if missing_in_timeline:
            result.issues.append(
                ValidationIssue(
                    severity="warning",
                    field="timeline",
                    message=f"Activity phases not in timeline: {', '.join(sorted(missing_in_timeline))}.",
                    suggestion="Every activity phase should appear in the timeline table.",
                )
            )

    def _validate_tech_stack_consistency(
        self, data: dict, result: ValidationResult
    ) -> None:
        tech_services = {
            t.get("service", "").strip().lower()
            for t in data.get("technology_stack", [])
        }
        arch_components = {
            c.get("name", "").strip().lower()
            for c in data.get("architecture_components", [])
        }

        if not tech_services or not arch_components:
            return

        in_tech_not_arch = tech_services - arch_components
        in_arch_not_tech = arch_components - tech_services

        if in_tech_not_arch:
            result.issues.append(
                ValidationIssue(
                    severity="warning",
                    field="technology_stack",
                    message=f"In tech stack but not architecture components: {', '.join(sorted(in_tech_not_arch))}.",
                    suggestion="Technology stack and architecture components should be consistent.",
                )
            )
        if in_arch_not_tech:
            result.issues.append(
                ValidationIssue(
                    severity="warning",
                    field="architecture_components",
                    message=f"In architecture but not tech stack: {', '.join(sorted(in_arch_not_tech))}.",
                    suggestion="Every architecture component should appear in the technology stack table.",
                )
            )

    def _validate_deliverable_coverage(
        self, data: dict, result: ValidationResult
    ) -> None:
        phases = data.get("activity_phases", [])
        deliverables = data.get("deliverables", [])

        if not phases or not deliverables:
            return

        phase_names = {p.get("name", "").strip().lower() for p in phases}
        covered_phases = set()
        for d in deliverables:
            activity = (d.get("activity") or "").strip().lower()
            for pn in phase_names:
                if activity in pn or pn in activity:
                    covered_phases.add(pn)
                    break

        uncovered = phase_names - covered_phases
        if uncovered:
            result.issues.append(
                ValidationIssue(
                    severity="warning",
                    field="deliverables",
                    message=f"Phases with no deliverables: {', '.join(sorted(uncovered))}.",
                    suggestion="Every phase should produce at least one deliverable.",
                )
            )

    def _validate_oos_count(self, data: dict, result: ValidationResult) -> None:
        oos = data.get("out_of_scope", [])
        if 0 < len(oos) < 20:
            result.issues.append(
                ValidationIssue(
                    severity="warning",
                    field="out_of_scope",
                    message=f"Only {len(oos)} out-of-scope items (target: 20-30).",
                    suggestion="Cover all 16 OOS categories from the style guide.",
                )
            )
