"""The group compose dialog's inspection screen (spec 012, contracts/routes.md):
the package ``GroupingInspection`` as one table row per peril / region /
model-version partition plus the blocking problems and the treaty term
mismatches, with the Workbench's display names in place of Platform ids.
``grouping_service`` stays at gate + plan scope; nothing here is persisted or
posted.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.grouping_service import GroupingInspectionView
from app.services.irp_gateway import GroupingPartition, GroupingPartitionKey
from app.services.treaty_service import humanize_key


@dataclass(frozen=True)
class SchemeOption:
    event_rate_scheme_id: int
    label: str                # the package label, or ``Scheme <id>`` without one
    member_count: int         # members of the partition run under this scheme
    value: dict               # the posted ``event_rate_selection`` JSON


@dataclass(frozen=True)
class PartitionRow:
    key: GroupingPartitionKey
    label: str                          # ``WS / NA / 11.0``
    engine_versions: tuple[str, ...]    # distinct, sorted, from the region facts
    member_names: tuple[str, ...]
    mode: str                           # choose | resolved | none
    options: tuple[SchemeOption, ...]

    @property
    def resolved(self) -> SchemeOption | None:
        return self.options[0] if self.mode == "resolved" else None


@dataclass(frozen=True)
class ProblemText:
    text: str
    member_names: tuple[str, ...]


@dataclass(frozen=True)
class TreatyMismatch:
    """One ``inconsistent_treaty_terms`` warning: treaties sharing a Treaty
    Number across the members whose loss-affecting terms differ. Members and
    treaty ids are two independent lists — the package does not pair them."""
    treaty_number: str
    differing_terms: tuple[str, ...]    # display labels, ``Attachment Point``
    member_names: tuple[str, ...]
    treaty_ids: tuple[int, ...]


@dataclass(frozen=True)
class InspectionScreen:
    output_loss_table: str
    member_count: int
    rows: tuple[PartitionRow, ...]
    problems: tuple[ProblemText, ...]
    treaty_mismatches: tuple[TreatyMismatch, ...]

    @property
    def blocked(self) -> bool:
        return bool(self.problems)

    @property
    def conflict_count(self) -> int:
        return sum(row.mode == "choose" for row in self.rows)

    @property
    def treaty_mismatch_count(self) -> int:
        return len(self.treaty_mismatches)


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
                differing_terms=tuple(humanize_key(f) for f in p.differing_fields),
                member_names=_names(view, p.analysis_ids),
                treaty_ids=tuple(p.treaty_ids))
            for p in inspection.warnings if p.code == "inconsistent_treaty_terms"),
    )


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
    options = tuple(
        SchemeOption(
            event_rate_scheme_id=opt.event_rate_scheme_id,
            label=opt.label or f"Scheme {opt.event_rate_scheme_id}",
            member_count=len({f.analysis_id for f in facts
                              if f.event_rate_scheme_id == opt.event_rate_scheme_id}),
            value={"peril_code": key.peril_code, "region_code": key.region_code,
                   "model_version": key.model_version,
                   "event_rate_scheme_id": opt.event_rate_scheme_id})
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
        mode=mode, options=options)


__all__ = [
    "InspectionScreen",
    "PartitionRow",
    "ProblemText",
    "SchemeOption",
    "TreatyMismatch",
    "build_inspection_screen",
]
