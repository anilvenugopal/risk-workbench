"""The group compose dialog's inspection screen (spec 012, contracts/routes.md):
the package ``GroupingInspection`` as one table row per peril / region /
model-version partition plus the blocking problems and the treaty term
mismatches, with the Workbench's display names in place of Platform ids.
``grouping_service`` stays at gate + plan scope; nothing here is persisted or
posted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.grouping_service import GroupingInspectionView
from app.services.irp_gateway import (
    GroupingPartition,
    GroupingPartitionKey,
    GroupingRegionFact,
    GroupingTreaty,
    SimulationSetOption,
)
from app.services.treaty_service import display_value, humanize_key

# The loss-affecting terms Risk Modeler's grouping screen shows beside the
# treaty identity, in its column order. ``Per Risk Limit`` is its header for
# ``riskLimit``; ``humanize_key`` would give ``Risk Limit``.
TREATY_COLUMNS = (
    ("treatyType", "Treaty Type"),
    ("effectiveDate", "Effective Date"),
    ("expirationDate", "Expiration Date"),
    ("attachmentPoint", "Attachment Point"),
    ("occurrenceLimit", "Occurrence Limit"),
    ("riskLimit", "Per Risk Limit"),
    ("currency", "Currency"),
)


@dataclass(frozen=True)
class SchemeOption:
    event_rate_scheme_id: int
    label: str                # the package label, or ``Scheme <id>`` without one
    member_count: int         # members of the partition run under this scheme
    value: dict               # the posted ``event_rate_selection`` JSON


@dataclass(frozen=True)
class SimulationSetChoice:
    simulation_set_id: int
    label: str                # the package label, or ``Simulation set <id>`` without one
    simulation_periods: int
    value: dict               # the posted ``simulation_set_selection`` JSON


@dataclass(frozen=True)
class ObservedPet:
    """The PET one PLT member of the partition ran on. Risk Modeler shows it in
    the group dialog's Simulation set column; the analyst cannot change it."""
    pet_id: int
    label: str                          # the PETMetadata name, or ``PET <id>``
    simulation_periods: int | None


@dataclass(frozen=True)
class PartitionRow:
    key: GroupingPartitionKey
    label: str                          # ``WS / NA / 11.0``
    engine_versions: tuple[str, ...]    # distinct, sorted, from the region facts
    member_names: tuple[str, ...]
    mode: str                           # choose | resolved | none
    options: tuple[SchemeOption, ...]
    simulation_set_required: bool       # an ELT partition of a PLT group
    simulation_set_options: tuple[SimulationSetChoice, ...]
    observed_pets: tuple[ObservedPet, ...]  # a PLT partition's fixed PETs

    @property
    def resolved(self) -> SchemeOption | None:
        return self.options[0] if self.mode == "resolved" else None


@dataclass(frozen=True)
class ProblemText:
    text: str
    member_names: tuple[str, ...]


@dataclass(frozen=True)
class TreatyMismatchRow:
    """One treaty as applied to one member. ``terms`` carries the
    ``TREATY_COLUMNS`` values display-shaped (``CATA`` spelled out, dates
    truncated); ``treaty_id`` is ``None`` when Risk Modeler returned no id.
    ``app_analysis_id`` is the id the table shows (RM's web-UI id, FR-020),
    ``None`` when the Workbench holds none; ``analysis_id`` stays the Platform
    id that keys the member."""
    analysis_name: str
    analysis_id: int
    app_analysis_id: str | None
    treaty_id: int | None
    treaty_number: str
    terms: dict[str, Any]


@dataclass(frozen=True)
class TreatyMismatch:
    """One ``inconsistent_treaty_terms`` warning as Risk Modeler's table: the
    treaties sharing a Treaty Number, one row each. ``differing_keys`` can name
    any of the 23 compared terms, including ones ``TREATY_COLUMNS`` does not
    carry, so the heading states them all."""
    treaty_number: str
    differing_keys: tuple[str, ...]
    rows: tuple[TreatyMismatchRow, ...]

    @property
    def differing_terms(self) -> tuple[str, ...]:
        return tuple(humanize_key(key) for key in self.differing_keys)

    @property
    def analysis_count(self) -> int:
        """Distinct analyses, not rows: one analysis can carry two treaties
        sharing a Treaty Number, and the package groups by number alone."""
        return len({row.analysis_id for row in self.rows})


@dataclass(frozen=True)
class InspectionScreen:
    output_loss_table: str
    member_count: int
    rows: tuple[PartitionRow, ...]
    problems: tuple[ProblemText, ...]
    treaty_mismatches: tuple[TreatyMismatch, ...]
    treaty_columns: tuple[tuple[str, str], ...] = TREATY_COLUMNS

    @property
    def blocked(self) -> bool:
        return bool(self.problems)

    @property
    def conflict_count(self) -> int:
        return sum(row.mode == "choose" for row in self.rows)

    @property
    def treaty_mismatch_count(self) -> int:
        return len(self.treaty_mismatches)

    @property
    def simulation_sets_shown(self) -> bool:
        """Every partition of a PLT group has a simulation set: chosen for an
        ELT partition, fixed by the members for a PLT one."""
        return self.output_loss_table == "PLT"


def build_inspection_screen(view: GroupingInspectionView) -> InspectionScreen:
    inspection = view.inspection
    return InspectionScreen(
        output_loss_table=inspection.output_loss_table,
        member_count=len(inspection.analysis_ids),
        rows=tuple(_row(view, part) for part in inspection.partitions),
        problems=tuple(
            ProblemText(text=p.message, member_names=_names(view, p.analysis_ids))
            for p in inspection.blocking_problems),
        treaty_mismatches=tuple(
            TreatyMismatch(
                treaty_number=", ".join(p.treaty_numbers),
                differing_keys=tuple(p.differing_fields),
                rows=tuple(_treaty_row(view, t) for t in p.treaties))
            for p in inspection.warnings if p.code == "inconsistent_treaty_terms"),
    )


def _treaty_row(view: GroupingInspectionView,
                treaty: GroupingTreaty) -> TreatyMismatchRow:
    member = view.members.get(treaty.analysis_id)
    return TreatyMismatchRow(
        analysis_name=member.display_name if member else str(treaty.analysis_id),
        analysis_id=treaty.analysis_id,
        app_analysis_id=member.app_analysis_id if member else None,
        treaty_id=treaty.treaty_id,
        treaty_number=treaty.treaty_number,
        terms={key: display_value(treaty.terms.get(key), key=key)
               for key, _ in TREATY_COLUMNS})


def _names(view: GroupingInspectionView, ids) -> tuple[str, ...]:
    return tuple(view.members[i].display_name if i in view.members else str(i)
                 for i in ids)


def _row(view: GroupingInspectionView, part: GroupingPartition) -> PartitionRow:
    key = part.key
    members = set(part.analysis_ids)
    facts = [
        fact for member in view.inspection.members for fact in member.regions
        if fact.analysis_id in members
        and (fact.peril_code, fact.region_code, fact.model_version)
        == (key.peril_code, key.region_code, key.model_version)]
    partition = {"peril_code": key.peril_code, "region_code": key.region_code,
                 "model_version": key.model_version}
    options = tuple(
        SchemeOption(
            event_rate_scheme_id=opt.event_rate_scheme_id,
            label=opt.label or f"Scheme {opt.event_rate_scheme_id}",
            member_count=len({f.analysis_id for f in facts
                              if f.event_rate_scheme_id == opt.event_rate_scheme_id}),
            value={**partition, "event_rate_scheme_id": opt.event_rate_scheme_id})
        for opt in part.event_rate_scheme_options)
    if part.event_rate_selection_required:
        mode = "choose"
    elif len(options) == 1:
        mode = "resolved"
    else:
        mode = "none"
    return PartitionRow(
        key=key,
        label=f"{key.peril_code} / {key.region_code} / {key.model_version}",
        engine_versions=tuple(sorted({f.engine_version for f in facts
                                      if f.engine_version})),
        member_names=_names(view, part.analysis_ids),
        mode=mode, options=options,
        simulation_set_required=part.simulation_set_selection_required,
        simulation_set_options=tuple(
            _simulation_set(partition, opt) for opt in part.simulation_set_options),
        observed_pets=_observed_pets(facts))


def _observed_pets(facts: list[GroupingRegionFact]) -> tuple[ObservedPet, ...]:
    pets: dict[int, ObservedPet] = {}
    for fact in facts:
        if fact.framework == "PLT" and fact.pet_id is not None:
            pets.setdefault(fact.pet_id, ObservedPet(
                pet_id=fact.pet_id,
                label=fact.pet_name or f"PET {fact.pet_id}",
                simulation_periods=fact.periods))
    return tuple(pets[pet_id] for pet_id in sorted(pets))


def _simulation_set(partition: dict, opt: SimulationSetOption) -> SimulationSetChoice:
    return SimulationSetChoice(
        simulation_set_id=opt.simulation_set_id,
        label=opt.label or f"Simulation set {opt.simulation_set_id}",
        simulation_periods=opt.simulation_periods,
        value={**partition, "simulation_set_id": opt.simulation_set_id})


__all__ = [
    "InspectionScreen",
    "ObservedPet",
    "PartitionRow",
    "ProblemText",
    "SchemeOption",
    "SimulationSetChoice",
    "TreatyMismatch",
    "TreatyMismatchRow",
    "build_inspection_screen",
]
