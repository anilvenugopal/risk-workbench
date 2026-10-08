"""
Rules-based analysis grouping operations.

Grouping uses an inspect-then-submit contract. Inspection reads analyses,
regions, treaties, and reference mappings without creating a Platform job.
Submission repeats the inspection, compares its deterministic fingerprint,
validates the caller's explicit choices, and posts the resulting request
immediately. Treaties with the same Treaty Number and different loss-affecting
terms produce warnings but do not block submission.

Member event-rate schemes are facts used to detect a conflict. For a conflicting
partition whose ELT regions all come from DLM analyses, inspection returns every
active Risk Modeler event-rate scheme with the partition's ``perilCode``,
``modelRegionCode``, and ``modelVersionCode``. A conflicting partition with an
HD ELT region retains the peril and model-region comparison. Whether the group
outputs ELT or PLT does not change either comparison. Inspection selects no
default. Simulation-set choices match the partition and resolve through an
active event-rate scheme reference row; a simulation set's
``eventRateSchemeId`` does not constrain the caller's event-rate selection.

Treaty comparison includes cedant, treaty type, currency, attachment and limit
terms, dates, percentages, priority, reinstatement and aggregate terms, LOBs,
and loss occurrences. Each warning carries the compared analysis treaty rows.
Treaty comparison excludes treaty IDs, display names, producers, premiums,
user-defined fields, tags, and URIs.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import (
    Any, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple,
    TYPE_CHECKING, TypeGuard,
)

from .constants import CREATE_ANALYSIS_GROUP, GET_ANALYSIS_GROUPING_JOB, GET_ANALYSIS_RESULT
from .exceptions import IRPAPIError, IRPGroupingValidationError, IRPValidationError
from .utils import extract_id_from_location_header
from .validators import validate_non_empty_string, validate_positive_int

if TYPE_CHECKING:
    from . import IRPClient


class GroupingProblemCode(str, Enum):
    """
    Stable codes returned for rule-based grouping problems.

    ``EVENT_RATE_SCHEME_MISSING`` reports one ELT region that carries no
    positive ``eventRateSchemeId``. ``EVENT_RATE_SCHEME_MAPPING_MISSING``
    reports a partition whose members disagree on their event-rate scheme
    and for which no active reference row carries the partition's
    ``perilCode``, ``modelRegionCode``, and, when the partition's ELT regions
    all come from DLM analyses, ``modelVersionCode``. The partition then has no
    option to offer, so the problem is returned in ``blocking_problems`` and
    ``submit`` refuses the group.

    A row-level message states what is true of the region row, not what it
    blocks, because ``AnalysisManager.describe_run`` reports the same problems
    for an analysis it is only describing. ``REGION_ROW_METADATA_MISSING``
    reports one region row that is malformed, carries no ELT/PLT
    classification, or is missing its engine, peril, or region.

    The ``MEMBER_`` codes stay confined to ``inspect``: ``MEMBER_NOT_FOUND``,
    ``MEMBER_REGION_DATA_MISSING`` and ``MEMBER_CLASSIFICATION_CONFLICT``
    concern an analysis offered as a group member, and ``describe_run``
    returns none of them. ``APPLY_CONTRACT_FLAG_UNSUPPORTED`` is the one code
    both callers return that also carries a grouping meaning: the row sets
    ``applyContractFlag``, which is a fact about the row, and ``inspect`` will
    not group the analysis.

    ``TREATY_NUMBER_MISSING`` reports one treaty carrying no
    ``treatyNumber``. Only ``AnalysisManager.describe_run`` returns it:
    ``inspect`` keys treaties by ``treatyNumber`` to compare them across
    members, so an unnumbered treaty makes the grouping decision unsafe and
    raises ``IRPAPIError`` there instead.
    """

    INSPECTION_CHANGED = "inspection_changed"
    MEMBER_NOT_FOUND = "member_not_found"
    MEMBER_REGION_DATA_MISSING = "member_region_data_missing"
    MEMBER_CLASSIFICATION_CONFLICT = "member_classification_conflict"
    MODEL_VERSION_MAPPING_MISSING = "model_version_mapping_missing"
    MODEL_VERSION_MAPPING_AMBIGUOUS = "model_version_mapping_ambiguous"
    EVENT_RATE_SCHEME_MISSING = "event_rate_scheme_missing"
    EVENT_RATE_SCHEME_MAPPING_MISSING = "event_rate_scheme_mapping_missing"
    EVENT_RATE_SELECTION_MISSING = "event_rate_selection_missing"
    EVENT_RATE_SELECTION_DUPLICATE = "event_rate_selection_duplicate"
    EVENT_RATE_SELECTION_UNKNOWN_PARTITION = "event_rate_selection_unknown_partition"
    EVENT_RATE_SELECTION_NOT_REQUIRED = "event_rate_selection_not_required"
    EVENT_RATE_SELECTION_NOT_OFFERED = "event_rate_selection_not_offered"
    SIMULATION_SET_SELECTION_MISSING = "simulation_set_selection_missing"
    SIMULATION_SET_SELECTION_DUPLICATE = "simulation_set_selection_duplicate"
    SIMULATION_SET_SELECTION_UNKNOWN_PARTITION = "simulation_set_selection_unknown_partition"
    SIMULATION_SET_SELECTION_NOT_REQUIRED = "simulation_set_selection_not_required"
    SIMULATION_SET_SELECTION_NOT_OFFERED = "simulation_set_selection_not_offered"
    SIMULATION_PERIODS_SELECTION_DUPLICATE = "simulation_periods_selection_duplicate"
    SIMULATION_PERIODS_SELECTION_UNKNOWN_PARTITION = (
        "simulation_periods_selection_unknown_partition"
    )
    SIMULATION_PERIODS_SELECTION_NOT_REQUIRED = "simulation_periods_selection_not_required"
    REGION_ROW_METADATA_MISSING = "region_row_metadata_missing"
    PET_ID_MISSING = "pet_id_missing"
    PET_PERIODS_MISSING = "pet_periods_missing"
    APPLY_CONTRACT_FLAG_UNSUPPORTED = "apply_contract_flag_unsupported"
    SIMULATION_SET_MAPPING_MISSING = "simulation_set_mapping_missing"
    INCONSISTENT_TREATY_TERMS = "inconsistent_treaty_terms"
    TREATY_NUMBER_MISSING = "treaty_number_missing"


@dataclass(frozen=True)
class GroupingPartitionKey:
    """Peril, region, and model-version key used by grouping rules."""

    peril_code: str
    region_code: str
    model_version: str


@dataclass(frozen=True)
class EventRateSchemeOption:
    """
    Event-rate scheme returned for a grouping partition.

    A conflicting partition whose ELT regions all come from DLM analyses
    receives every active Risk Modeler scheme with the partition's
    ``perilCode``, ``modelRegionCode``, and ``modelVersionCode``. A conflicting
    partition with an HD ELT region retains the peril and model-region
    comparison. A non-conflicting partition receives its resolved observed
    scheme. A conflicting partition with no applicable active scheme receives
    no options and reports ``event_rate_scheme_mapping_missing`` in
    ``blocking_problems``.
    """

    event_rate_scheme_id: int
    label: Optional[str] = None


@dataclass(frozen=True)
class SimulationSetOption:
    """
    Simulation set available for converting one ELT partition to PLT.

    ``event_rate_scheme_id`` describes the simulation-set reference row. It
    does not constrain the event-rate scheme selected for the group.
    """

    simulation_set_id: int
    simulation_periods: int
    event_rate_scheme_id: Optional[int] = None
    label: Optional[str] = None


@dataclass(frozen=True)
class GroupingRegionFact:
    """Normalized Platform region fact used by grouping inspection.

    ``pet_name`` is the ``petName`` of the ``PETMetadata`` row for ``pet_id``,
    the name Risk Modeler shows for a PLT region's simulation set. It is
    ``None`` for an ELT region and when the ``PETMetadata`` lookup returned no
    single row. ``PETMetadata`` and the ``SimulationSet`` reference table are
    separate tables with separate ID sequences: a ``pet_id`` does not name a
    ``SimulationSetOption``.
    """

    analysis_id: int
    framework: str
    peril_code: str
    region_code: str
    model_version: str
    engine_version: str
    sub_region: str
    model_region_code: str
    event_rate_scheme_id: Optional[int]
    pet_id: Optional[int]
    pet_name: Optional[str]
    periods: Optional[int]
    apply_contract_flag: bool


@dataclass(frozen=True)
class GroupingMember:
    """Normalized facts for one requested Platform analysis ID."""

    analysis_id: int
    exists: bool
    is_group: bool
    analysis_framework: Optional[str]
    engine_type: Optional[str]
    engine_version: Optional[str]
    peril_code: Optional[str]
    region_code: Optional[str]
    model_version: Optional[str]
    regions: Tuple[GroupingRegionFact, ...]


@dataclass(frozen=True)
class GroupingPartition:
    """Risk Modeler choices and observed member facts for one partition.

    Member event-rate schemes determine whether
    ``event_rate_selection_required`` is true. When member schemes conflict,
    ``event_rate_scheme_options`` for a partition whose ELT regions all come
    from DLM analyses contains every active Risk Modeler scheme with the
    partition's ``perilCode``, ``modelRegionCode``, and ``modelVersionCode``. A
    conflicting partition with an HD ELT region retains the peril and
    model-region comparison. With one observed member scheme, the observed
    scheme remains resolved and no caller selection is required.
    ``simulation_set_options`` contains the simulation sets Risk Modeler
    presents for the partition. The package applies no preference or default to
    either choice.
    """

    key: GroupingPartitionKey
    analysis_ids: Tuple[int, ...]
    event_rate_scheme_options: Tuple[EventRateSchemeOption, ...]
    observed_pet_ids: Tuple[int, ...]
    event_rate_selection_required: bool
    simulation_set_options: Tuple[SimulationSetOption, ...] = ()
    simulation_set_selection_required: bool = False


@dataclass(frozen=True)
class GroupingSimulationMapping:
    """Available reference-data mapping for one simulated ELT partition."""

    partition: GroupingPartitionKey
    analysis_ids: Tuple[int, ...]
    engine_version: str
    model_region_code: str
    event_rate_scheme_id: int
    simulation_set_id: int
    simulation_periods: int


@dataclass(frozen=True)
class GroupingTreaty:
    """One treaty as applied to one analysis, with the loss-affecting terms the grouping comparison read.

    Terms are the analysis-level values from
    ``AnalysisManager.search_analysis_treaties_paginated``, not the EDM
    definition: an analysis run in CAD against a treaty defined in USD
    reports CAD. Keys are the ``LOSS_AFFECTING_TREATY_FIELDS`` names plus
    ``lobs`` and ``lossOccurrences``, normalized the same way the comparison
    normalizes them, so a value shown for a field in ``differing_fields``
    always explains why that field differs.
    """

    analysis_id: int
    treaty_id: Optional[int]
    treaty_number: str
    terms: Dict[str, Any]


@dataclass(frozen=True)
class GroupingProblem:
    """Structured grouping problem suitable for caller rendering.

    ``sub_regions`` names the region row a row-level problem concerns. A
    23-sub-region windstorm analysis whose engine, region and peril resolve no
    ``SoftwareModelVersionMap`` entry reports 23 problems, each naming its own
    sub-region, so a caller can tell which of AL, CT, D1 was dropped.
    ``inspect`` reports one problem per distinct sub-region where it reported
    one for the whole analysis.

    ``terms`` on a ``GroupingTreaty`` in ``treaties`` is a ``Dict``, so a
    ``GroupingProblem`` carrying one is not hashable despite ``frozen=True``.
    """

    code: str
    message: str
    analysis_ids: Tuple[int, ...] = ()
    partition: Optional[GroupingPartitionKey] = None
    pet_ids: Tuple[int, ...] = ()
    sub_regions: Tuple[str, ...] = ()
    treaty_numbers: Tuple[str, ...] = ()
    treaty_ids: Tuple[int, ...] = ()
    differing_fields: Tuple[str, ...] = ()
    treaties: Tuple[GroupingTreaty, ...] = ()


@dataclass(frozen=True)
class GroupingInspection:
    """Fresh facts, choices, warnings, and blocks for selected analyses."""

    analysis_ids: Tuple[int, ...]
    resource_uris: Tuple[str, ...]
    inspected_at: str
    fingerprint: str
    members: Tuple[GroupingMember, ...]
    output_loss_table: str
    simulate_to_plt: bool
    partitions: Tuple[GroupingPartition, ...]
    simulation_mappings: Tuple[GroupingSimulationMapping, ...]
    required_caller_inputs: Tuple[str, ...]
    warnings: Tuple[GroupingProblem, ...]
    blocking_problems: Tuple[GroupingProblem, ...]


@dataclass(frozen=True)
class GroupingCurrency:
    """Explicit currency settings for a grouping request."""

    code: str
    scheme: str
    vintage: str
    as_of_date: str


@dataclass(frozen=True)
class GroupingSettings:
    """Explicit caller settings for a grouping request."""

    analysis_name: str
    currency: GroupingCurrency
    propagate_detailed_losses: bool
    num_of_simulations: int
    description: Optional[str] = None
    reporting_window_start: Optional[str] = None
    simulation_window_start: Optional[str] = None
    simulation_window_end: Optional[str] = None


@dataclass(frozen=True)
class EventRateSelection:
    """Caller-selected event-rate scheme for a conflicting partition."""

    partition: GroupingPartitionKey
    event_rate_scheme_id: int


@dataclass(frozen=True)
class SimulationSetSelection:
    """Caller-selected simulation set for one ELT-to-PLT partition."""

    partition: GroupingPartitionKey
    simulation_set_id: int


@dataclass(frozen=True)
class SimulationPeriodsSelection:
    """Caller-selected ``simulationPeriods`` for one partition of a PLT group.

    Without a selection the request row keeps the member PET's period count
    (PLT partition) or the chosen simulation set's ``defaultPeriods`` (ELT
    partition converted to PLT).
    """

    partition: GroupingPartitionKey
    simulation_periods: int


@dataclass(frozen=True)
class GroupingSubmission:
    """Created grouping job ID and the exact submitted request body."""

    job_id: int
    request_body: Dict[str, Any]


def _positive_int(value: Any) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _row_count(value: Any) -> Optional[int]:
    """Return a ``totalCount`` field as a row count.

    The Platform returns ``totalCount`` as an ``int``, but a shape change to
    ``"151"`` or ``151.0`` must not switch a truncation guard off: a changed
    response shape is exactly the case where a short read would go unnoticed.
    A bool is not a row count, and neither is a float with a fractional part.

    Args:
        value: The ``totalCount`` field as the response carried it

    Returns:
        The row count, or None when the value is not one
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _text(value: Any) -> Optional[str]:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _field(data: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in data and data[name] is not None:
            return data[name]
    return None


def _resolve_code(value: Any, code: Optional[str], name: Optional[str]) -> Optional[str]:
    """Return ``value`` as a code.

    Region rows carry display names such as ``"Windstorm"`` in ``peril`` while
    the analysis detail carries both ``perilCode`` and ``peril``. A value equal
    to the detail's display name resolves to the detail's code; anything else is
    returned unchanged.
    """
    text = _text(value)
    if text is None:
        return None
    if name is not None and text.casefold() == name.casefold():
        return code
    return text


def _is_group(analysis: Mapping[str, Any]) -> bool:
    """Report whether an analysis detail describes a group.

    ``isGroup`` alone does not answer the question: a Risk Modeler broker group
    reports ``isGroup`` false with ``groupType`` ``INGP``. ``GetAnalysisResponse``
    documents ``groupType`` as one of ``ANLS``, ``CDGP``, ``INGP``, ``MCGP`` and
    ``UNRECOGNIZED``, and ``engineType`` as including both ``Group`` and
    ``CEPGroup``.

    Args:
        analysis: Analysis detail from ``AnalysisManager.get_analysis_by_id``

    Returns:
        True when the detail reports a group by any of the three fields
    """
    if _field(analysis, "isGroup"):
        return True
    engine = (_text(_field(analysis, "engineType", "type")) or "").upper()
    group = (_text(_field(analysis, "groupType")) or "").upper()
    return engine in {"GROUP", "CEPGROUP"} or group in {"CDGP", "INGP", "MCGP"}


def _unambiguous_model_regions(
    rows: Sequence[Mapping[str, Any]], id_field: str
) -> Dict[int, str]:
    """Map each reference row ID to its ``modelRegionCode``.

    An ID whose rows carry more than one ``modelRegionCode`` is left out: PET
    IDs repeat across model versions, so the same ``id`` appears more than once.

    Args:
        rows: Reference rows carrying ``modelRegionCode``
        id_field: Field naming the ID to key on, ``eventRateSchemeId`` or ``id``

    Returns:
        The model region code per ID, excluding IDs whose rows disagree
    """
    codes: Dict[int, Set[str]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        row_id = row.get(id_field)
        code = _text(row.get("modelRegionCode"))
        if not _positive_int(row_id) or code is None:
            continue
        codes.setdefault(int(row_id), set()).add(code)
    return {
        row_id: next(iter(values))
        for row_id, values in codes.items()
        if len(values) == 1
    }


def _event_rate_from_analysis(analysis: Mapping[str, Any]) -> Tuple[Optional[int], Optional[str]]:
    direct = _field(analysis, "eventRateSchemeId", "rateSchemeId")
    label = _text(_field(analysis, "eventRateSchemeName", "rateSchemeName"))
    if label is None:
        # The detail lists scheme names under ``eventRateSchemeNames`` with
        # ``id`` 0, so a name is attributed only when exactly one is listed.
        names = analysis.get("eventRateSchemeNames")
        if isinstance(names, list) and len(names) == 1 and isinstance(names[0], Mapping):
            label = _text(names[0].get("name"))
    if _positive_int(direct):
        return int(direct), label

    properties = analysis.get("additionalProperties") or []
    if not isinstance(properties, list):
        return None, label
    for entry in properties:
        if not isinstance(entry, Mapping):
            continue
        key = entry.get("key")
        nested = entry.get("properties") or []
        if not isinstance(nested, list):
            continue
        for prop in nested:
            if not isinstance(prop, Mapping):
                continue
            candidate = prop.get("id")
            value = prop.get("value")
            if isinstance(value, Mapping):
                candidate = _field(value, "eventRateSchemeId", "rateSchemeId")
                label = label or _text(_field(value, "eventRateSchemeName", "name"))
            if key in {"eventRateSchemeId", "eventRateSchemes"} and _positive_int(candidate):
                return int(candidate), label
    return None, label


def _analysis_framework(analysis: Mapping[str, Any]) -> Optional[str]:
    """Return the analysis detail's ELT or PLT classification, upper-cased."""
    framework = _text(_field(analysis, "analysisFramework", "framework"))
    return framework.upper() if framework else None


class _ReferenceLookups:
    """Reference-data reads for one call, cached by their arguments.

    ``GroupingManager._inspect`` and ``AnalysisManager.describe_run`` both turn
    analysis region rows into ``GroupingRegionFact`` rows and read the same
    model-version mappings, PET metadata rows, and active event-rate scheme rows
    while doing it. One instance reads each of them once.
    """

    def __init__(self, irp: "IRPClient") -> None:
        """Initialize the lookups over one IRP client.

        Args:
            irp: Owning IRP client instance
        """
        self._irp = irp
        self._model_versions: Dict[
            Tuple[str, str, str], Tuple[Optional[str], Optional[Exception]]
        ] = {}
        self._pet_metadata: Dict[
            Tuple[int, str, Optional[str]],
            Tuple[Optional[Dict[str, Any]], Optional[Exception]],
        ] = {}
        self._scheme_rows: Optional[Tuple[Mapping[str, Any], ...]] = None
        self._scheme_model_regions: Optional[Dict[int, str]] = None
        self._pet_model_regions: Optional[Dict[int, str]] = None

    def model_version(
        self, engine: str, region: str, peril: str
    ) -> Tuple[Optional[str], Optional[Exception]]:
        """Return the model version for an engine, region, and peril.

        Args:
            engine: Engine version such as ``"RL25"`` or ``"HDv3.0"``
            region: Region code such as ``"NA"``
            peril: Peril code such as ``"WS"``

        Returns:
            The model version, or the ``IRPAPIError`` the lookup raised
        """
        key = (engine, region, peril)
        if key not in self._model_versions:
            try:
                value = self._irp.reference_data.get_model_version_by_engine_region_peril(
                    engine, region, peril
                )
                self._model_versions[key] = (str(value), None)
            except IRPAPIError as exc:
                self._model_versions[key] = (None, exc)
        return self._model_versions[key]

    def pet_metadata(
        self,
        pet_id: int,
        model_version: str,
        model_region_code: Optional[str],
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Exception]]:
        """Return the qualified ``PETMetadata`` row for a PET ID.

        Args:
            pet_id: Positive PET ID from a PLT region row
            model_version: Exact model version code
            model_region_code: Exact model region code, or None

        Returns:
            The single matching row, or the ``IRPAPIError`` the lookup raised
        """
        key = (pet_id, model_version, model_region_code)
        if key not in self._pet_metadata:
            try:
                value = self._irp.reference_data.get_pet_metadata_exact(
                    pet_id=pet_id,
                    model_version=model_version,
                    model_region_code=model_region_code,
                )
                self._pet_metadata[key] = (value, None)
            except IRPAPIError as exc:
                self._pet_metadata[key] = (None, exc)
        return self._pet_metadata[key]

    def event_rate_scheme_rows(self) -> Tuple[Mapping[str, Any], ...]:
        """
        Return active Risk Modeler event-rate scheme reference rows.

        Event-rate applicability and CCM simulation-set exclusion both use
        fields from the same active reference response.

        The endpoint applies no default page size, so one call returns every
        active row. ``totalCount`` is checked against the rows returned rather
        than paginated over, so a server-side cap raises instead of silently
        truncating the peril and model-region comparisons.

        Returns:
            Every active event-rate scheme row

        Raises:
            IRPAPIError: If the reference read returns a non-list response,
                fewer rows than its ``totalCount`` reports, or a
                ``totalCount`` that is not a row count
        """
        if self._scheme_rows is None:
            payload = self._irp.reference_data.get_event_rate_schemes()
            envelope: Mapping[str, Any] = {}
            rows: Any = payload
            if isinstance(payload, Mapping):
                envelope = payload
                rows = payload.get("items")
            if (
                isinstance(rows, (str, bytes))
                or not isinstance(rows, Sequence)
            ):
                raise IRPAPIError(
                    "Event-rate scheme search returned a non-list response"
                )
            total = envelope.get("totalCount")
            if total is not None:
                count = _row_count(total)
                if count is None:
                    raise IRPAPIError(
                        "Event-rate scheme search returned a totalCount that "
                        f"is not a row count: {total!r}"
                    )
                if len(rows) < count:
                    raise IRPAPIError(
                        f"Event-rate scheme search returned {len(rows)} of "
                        f"{count} rows"
                    )
            self._scheme_rows = tuple(
                row for row in rows if isinstance(row, Mapping)
            )
        return self._scheme_rows

    def scheme_name(self, scheme_id: int) -> Optional[str]:
        """Return Risk Modeler's label for an event-rate scheme ID.

        ``eventRateSchemeId`` is read the way ``_unambiguous_model_regions``,
        the active-scheme-ID set in ``_inspect`` and its applicable-scheme
        lookup read the same field over the same rows: through
        ``_positive_int`` and then ``int()``. A bare ``==`` matched a row
        carrying ``True`` against scheme ID 1.

        Args:
            scheme_id: Event-rate scheme ID from a region row

        Returns:
            The ``eventRateSchemeName`` of the row carrying that ID, or None
        """
        for row in self.event_rate_scheme_rows():
            row_id = row.get("eventRateSchemeId")
            if _positive_int(row_id) and int(row_id) == scheme_id:
                return _text(row.get("eventRateSchemeName"))
        return None

    def peril_code_for_row(
        self, raw_region: Mapping[str, Any], region_code: Optional[str]
    ) -> Optional[str]:
        """Return the peril code one region row's IDs resolve to.

        A region row names its peril in ``peril`` as a display name such as
        ``"Windstorm"``, and a multi-peril group's detail carries ``perilCode``
        ``YY`` with ``peril`` ``"Multi-Peril"``, so neither the row nor the
        detail states the row's peril code. The row's ``eventRateSchemeId`` and
        ``petId`` each carry a ``modelRegionCode`` in their reference table, and
        ``modelRegionCode`` is a region code followed by a peril code — the same
        key ``get_model_version_by_engine_region_peril`` builds from its
        ``region_code`` and ``peril_code`` arguments. Stripping ``region_code``
        off the front of it leaves the peril.

        A reference row's own ``perilCode`` is a different value and is not
        read: the ``PETMetadata``, ``modelprofiles`` and
        ``SoftwareModelVersionMap`` rows for ``modelRegionCode`` ``NAWF`` all
        carry ``perilCode`` ``FR``, and ``NA`` followed by ``FR`` matches no
        ``modelRegionCode``.

        The code is the last peril candidate ``_region_facts`` tries, not the
        row's answer: a scheme or PET registered under a ``modelRegionCode``
        whose peril the row does not carry returns a code here that resolves no
        model version. ``_region_facts`` tries the row's own ``perilCode`` and
        the detail's first, because both cost no read and this one costs the
        ``PETMetadata`` pagination when the row's ``eventRateSchemeId``
        resolves no ``modelRegionCode``.

        Args:
            raw_region: One region row from ``AnalysisManager.get_regions``
            region_code: The region code already resolved for that row

        Returns:
            The peril code in upper case, or None when no ID resolves a
            ``modelRegionCode`` starting with ``region_code``
        """
        if not region_code:
            return None
        model_region = self._model_region_for_row(raw_region)
        if model_region is None or len(model_region) <= len(region_code):
            return None
        if not model_region.upper().startswith(region_code.upper()):
            return None
        return model_region[len(region_code):].upper()

    def _model_region_for_row(self, raw_region: Mapping[str, Any]) -> Optional[str]:
        """Return the model region code one region row's scheme or PET ID names.

        A failed ``PETMetadata`` read returns None rather than raising.
        ``_region_facts`` is called outside ``_inspect``'s per-analysis
        ``try/except``, so one 500 on the bulk read would turn a 40-analysis
        ``inspect`` that reports per-analysis problems into a hard raise and
        take ``describe_run`` with it. The row falls through to the next peril
        candidate, its own ``perilCode``, which is what the row states anyway.
        ``model_version`` and ``pet_metadata`` are error-tolerant the same way.

        Args:
            raw_region: One region row from ``AnalysisManager.get_regions``

        Returns:
            The ``modelRegionCode`` the row's ``eventRateSchemeId`` or
            ``petId`` names, or None

        Raises:
            IRPAPIError: If the event-rate scheme read fails. That read is 151
                rows and it is how the ``totalCount`` truncation guard
                surfaces, so it propagates.
        """
        scheme = _field(raw_region, "eventRateSchemeId", "rateSchemeId")
        if _positive_int(scheme):
            if self._scheme_model_regions is None:
                self._scheme_model_regions = _unambiguous_model_regions(
                    self.event_rate_scheme_rows(), "eventRateSchemeId"
                )
            model_region = self._scheme_model_regions.get(int(scheme))
            if model_region is not None:
                return model_region

        pet = _field(raw_region, "petId", "simulationSetId")
        if not _positive_int(pet):
            return None
        if self._pet_model_regions is None:
            try:
                pet_rows = self._irp.reference_data.get_all_pet_metadata()
            except IRPAPIError:
                # Cache the failure the way model_version and pet_metadata
                # cache theirs, so a 15,318-row inspect does not repeat a
                # 2,844-row read that already exhausted the session retries.
                self._pet_model_regions = {}
                return None
            self._pet_model_regions = _unambiguous_model_regions(pet_rows, "id")
        return self._pet_model_regions.get(int(pet))


def _region_facts(
    analysis_id: int,
    analysis: Mapping[str, Any],
    raw_regions: Sequence[Any],
    lookups: _ReferenceLookups,
    report: Callable[[GroupingProblem], None],
) -> Tuple[List[GroupingRegionFact], Set[str]]:
    """Normalize the region rows of one analysis.

    A region row carries the peril as a display name, so the region code is
    resolved first and three peril candidates are tried in order: the code
    ``_resolve_code`` returns for the row's own ``perilCode``, the detail's
    ``perilCode``, and the code ``_ReferenceLookups.peril_code_for_row`` reads
    off the ``modelRegionCode`` the row's ``eventRateSchemeId`` or ``petId``
    names. ``get_model_version_by_engine_region_peril`` picks between them: the
    first candidate that resolves a model version becomes the row's peril. A
    candidate that resolves nothing does not end the row, because a
    ``modelRegionCode`` registered under a peril the row does not carry would
    otherwise drop a row whose own ``perilCode`` maps.

    The first two candidates cost no read. The third costs the 2,844-row
    ``PETMetadata`` pagination whenever the row's ``eventRateSchemeId``
    resolves no ``modelRegionCode``, so it is read only once neither of the
    first two has resolved a model version, or when the row carries neither of
    them. Over the 15,318 region rows of one tenant, 12,892 settle on a free
    candidate, and no row settles on a different peril than it did when the
    derived candidate was tried first.

    A row without an ELT or PLT classification, without engine, peril, or
    region metadata, or for which no candidate resolves a model version is
    reported and dropped.

    Args:
        analysis_id: Platform analysis ID the region rows belong to
        analysis: Analysis detail from ``AnalysisManager.get_analysis_by_id``
        raw_regions: Region rows from ``AnalysisManager.get_regions``
        lookups: Cached model-version, PET metadata, and event-rate reads
        report: Called once per problem found in the rows

    Returns:
        The region facts in row order, and every ELT or PLT classification the
        rows carried, including rows dropped after classification
    """
    analysis_framework = _analysis_framework(analysis)
    detail_engine = _text(_field(analysis, "engineVersion", "softwareVersionCode"))
    detail_peril = _text(_field(analysis, "perilCode", "peril"))
    detail_region = _text(_field(analysis, "regionCode", "region"))
    detail_peril_name = _text(analysis.get("peril"))
    detail_region_name = _text(analysis.get("region"))
    analysis_scheme, _ = _event_rate_from_analysis(analysis)

    region_facts: List[GroupingRegionFact] = []
    observed_frameworks: Set[str] = set()
    for raw_region in raw_regions:
        if not isinstance(raw_region, Mapping):
            report(GroupingProblem(
                code=GroupingProblemCode.REGION_ROW_METADATA_MISSING.value,
                message=f"Analysis {analysis_id} returned a malformed region row.",
                analysis_ids=(analysis_id,),
            ))
            continue
        framework = _text(_field(raw_region, "framework", "analysisFramework"))
        framework = (framework or analysis_framework or "").upper()
        if framework not in {"ELT", "PLT"}:
            report(GroupingProblem(
                code=GroupingProblemCode.REGION_ROW_METADATA_MISSING.value,
                message=(f"Analysis {analysis_id} has a region row with no ELT/PLT "
                         "classification."),
                analysis_ids=(analysis_id,),
            ))
            continue
        observed_frameworks.add(framework)
        sub_region = _text(_field(raw_region, "subRegion", "subRegionCode")) or ""
        row_sub_regions = (sub_region,) if sub_region else ()
        row_engine = _text(_field(raw_region, "engineVersion", "softwareVersionCode"))
        row_region = _resolve_code(
            _field(raw_region, "regionCode", "region"), detail_region, detail_region_name
        )
        region = row_region or detail_region
        engine = row_engine or detail_engine
        # The row's own code and the detail's cost no read. The code derived
        # from the modelRegionCode the row's eventRateSchemeId or petId names
        # costs the 2,844-row PETMetadata pagination whenever the scheme ID
        # resolves nothing, so it is read only once neither free candidate has
        # resolved a model version, or when the row carries neither.
        peril_candidates: List[str] = []
        for candidate in (
            _resolve_code(
                _field(raw_region, "perilCode", "peril"), detail_peril, detail_peril_name
            ),
            detail_peril,
        ):
            if candidate and candidate not in peril_candidates:
                peril_candidates.append(candidate)
        derived_peril: Optional[str] = None
        if not peril_candidates:
            derived_peril = lookups.peril_code_for_row(raw_region, region)
            if derived_peril:
                peril_candidates.append(derived_peril)
        peril = peril_candidates[0] if peril_candidates else None
        apply_contract = bool(_field(raw_region, "applyContractFlag"))
        scheme = _field(raw_region, "eventRateSchemeId", "rateSchemeId")
        scheme_id = int(scheme) if _positive_int(scheme) else analysis_scheme
        pet_value = _field(raw_region, "petId", "simulationSetId")
        pet_id = int(pet_value) if _positive_int(pet_value) else None
        pet_name: Optional[str] = None
        period_value = _field(raw_region, "periods", "simulationPeriods")
        periods = int(period_value) if _positive_int(period_value) else None

        if framework == "PLT":
            if pet_id is None:
                report(GroupingProblem(
                    code=GroupingProblemCode.PET_ID_MISSING.value,
                    message=(f"PLT analysis {analysis_id} has a region row with no "
                             "positive PET ID."),
                    analysis_ids=(analysis_id,),
                    sub_regions=row_sub_regions,
                ))
            if periods is None:
                report(GroupingProblem(
                    code=GroupingProblemCode.PET_PERIODS_MISSING.value,
                    message=(f"PLT analysis {analysis_id} has a region row with no "
                             "positive period count."),
                    analysis_ids=(analysis_id,),
                    pet_ids=(pet_id,) if pet_id else (),
                    sub_regions=row_sub_regions,
                ))
            if apply_contract:
                report(GroupingProblem(
                    code=GroupingProblemCode.APPLY_CONTRACT_FLAG_UNSUPPORTED.value,
                    message=(f"PLT analysis {analysis_id} has a region row with "
                             "applyContractFlag set."),
                    analysis_ids=(analysis_id,),
                    sub_regions=row_sub_regions,
                ))

        if not engine or not peril or not region:
            report(GroupingProblem(
                code=GroupingProblemCode.REGION_ROW_METADATA_MISSING.value,
                message=(f"Analysis {analysis_id} has a region row missing engine, "
                         "peril, or region metadata."),
                analysis_ids=(analysis_id,),
                sub_regions=row_sub_regions,
            ))
            continue

        resolved_version: Optional[str] = None
        version_error: Optional[Exception] = None
        for candidate in peril_candidates:
            version, error = lookups.model_version(engine, region, candidate)
            if error is None and version is not None:
                peril = candidate
                resolved_version = version
                break
            if version_error is None:
                version_error = error

        # derived_peril is None only when the derived candidate has not been
        # read: the branch above reads it whenever the free candidates are
        # empty, and an empty candidate list leaves the row on the metadata
        # guard above rather than here.
        if resolved_version is None and derived_peril is None:
            derived_peril = lookups.peril_code_for_row(raw_region, region)
            if derived_peril and derived_peril not in peril_candidates:
                peril_candidates.append(derived_peril)
                version, error = lookups.model_version(engine, region, derived_peril)
                if error is None and version is not None:
                    peril = derived_peril
                    resolved_version = version
                elif version_error is None:
                    version_error = error

        if resolved_version is None:
            code = (GroupingProblemCode.MODEL_VERSION_MAPPING_AMBIGUOUS.value
                    if "multiple" in str(version_error).lower()
                    else GroupingProblemCode.MODEL_VERSION_MAPPING_MISSING.value)
            report(GroupingProblem(
                code=code,
                message=(f"Model version for analysis {analysis_id}, engine {engine}, "
                         f"region {region}, and peril "
                         f"{', '.join(peril_candidates)} was not resolved exactly."),
                analysis_ids=(analysis_id,),
                sub_regions=row_sub_regions,
            ))
            continue

        if framework == "PLT" and pet_id is not None:
            broad_model_region = f"{region}{peril}"
            pet, _ = lookups.pet_metadata(
                pet_id,
                resolved_version,
                broad_model_region,
            )
            if pet is not None:
                pet_name = _text(pet.get("petName"))

        if framework == "ELT" and scheme_id is None:
            report(GroupingProblem(
                code=GroupingProblemCode.EVENT_RATE_SCHEME_MISSING.value,
                message=(f"ELT analysis {analysis_id} has a region row with no "
                         "positive event-rate scheme ID."),
                analysis_ids=(analysis_id,),
                sub_regions=row_sub_regions,
                partition=GroupingPartitionKey(peril, region, resolved_version),
            ))

        model_region = f"{sub_region}{peril}" if sub_region else f"{region}{peril}"
        region_facts.append(GroupingRegionFact(
            analysis_id=analysis_id,
            framework=framework,
            peril_code=peril,
            region_code=region,
            model_version=resolved_version,
            engine_version=engine,
            sub_region=sub_region,
            model_region_code=model_region,
            event_rate_scheme_id=scheme_id if framework == "ELT" else None,
            pet_id=pet_id if framework == "PLT" else None,
            pet_name=pet_name,
            periods=periods if framework == "PLT" else None,
            apply_contract_flag=apply_contract,
        ))
    return region_facts, observed_frameworks


class GroupingManager:
    """Inspect analysis members and submit resolved grouping requests."""

    FINGERPRINT_VERSION = 9

    LOSS_AFFECTING_TREATY_FIELDS = (
        "cedant",
        "treatyType",
        "currency",
        "attachmentBasis",
        "attachmentLevel",
        "occurrenceLimit",
        "attachmentPoint",
        "riskLimit",
        "retentionAmount",
        "percentagePlaced",
        "effectiveDate",
        "expirationDate",
        "percentageRetention",
        "percentageRiShare",
        "percentageCovered",
        "priority",
        "numberOfReinstatements",
        "reinstatementCharge",
        "maolAmount",
        "aggregateDeductible",
        "aggregateLimit",
    )

    def __init__(self, irp: "IRPClient") -> None:
        """Initialize the grouping manager.

        Args:
            irp: Owning IRP client instance
        """
        self._irp = irp
        self.client = irp.client

    def inspect(self, *, analysis_ids: Sequence[int]) -> GroupingInspection:
        """
        Inspect selected analyses without creating a Platform grouping job.

        Conflicting member event-rate schemes require a caller selection from
        the Risk Modeler-applicable schemes returned for the partition. A
        single observed member scheme remains resolved. Simulation-set options
        are the choices Risk Modeler presents for an ELT partition converted to
        PLT. Inspection selects no preference or default.

        Args:
            analysis_ids: At least two distinct positive Platform analysis IDs

        Returns:
            Normalized member facts, choices, warnings, blocks, and fingerprint

        Raises:
            IRPValidationError: If analysis_ids is malformed
            IRPAPIError: If a Platform or reference-data read fails
        """
        normalized_ids = self._validate_analysis_ids(analysis_ids)
        return self._inspect(normalized_ids)

    def submit(
        self,
        *,
        analysis_ids: Sequence[int],
        settings: GroupingSettings,
        event_rate_selections: Sequence[EventRateSelection],
        expected_inspection_fingerprint: str,
        simulation_set_selections: Sequence[SimulationSetSelection] = (),
        simulation_periods_selections: Sequence[SimulationPeriodsSelection] = (),
    ) -> GroupingSubmission:
        """
        Reinspect, validate explicit choices, and create a grouping job.

        Args:
            analysis_ids: At least two distinct positive Platform analysis IDs
            settings: Explicit grouping request settings
            event_rate_selections: One Risk Modeler-applicable offered scheme
                for each conflicting partition
            expected_inspection_fingerprint: Fingerprint returned by the caller's inspection
            simulation_set_selections: One offered simulation set for each ELT
                partition converted to PLT
            simulation_periods_selections: At most one ``simulationPeriods`` value
                per partition of a PLT group; a partition without one keeps the
                PET's period count or the chosen set's ``defaultPeriods``

        Returns:
            Created grouping job ID and exact submitted request body

        Raises:
            IRPValidationError: If a direct method argument is malformed
            IRPGroupingValidationError: If inspected facts or caller selections block submission
            IRPAPIError: If a Platform read or grouping POST fails
        """
        normalized_ids = self._validate_analysis_ids(analysis_ids)
        self._validate_settings(settings)
        event_selections = self._validate_event_rate_selection_arguments(
            event_rate_selections
        )
        simulation_selections = self._validate_simulation_set_selection_arguments(
            simulation_set_selections
        )
        periods_selections = self._validate_simulation_periods_selection_arguments(
            simulation_periods_selections
        )
        validate_non_empty_string(
            expected_inspection_fingerprint, "expected_inspection_fingerprint"
        )

        inspection = self._inspect(normalized_ids)
        if inspection.fingerprint != expected_inspection_fingerprint:
            raise IRPGroupingValidationError((GroupingProblem(
                code=GroupingProblemCode.INSPECTION_CHANGED.value,
                message="Grouping facts changed after inspection; inspect the analyses again.",
                analysis_ids=normalized_ids,
            ),))
        if inspection.blocking_problems:
            raise IRPGroupingValidationError(inspection.blocking_problems)

        selected_schemes = self._resolve_event_rate_selections(
            inspection, event_selections
        )
        selected_simulation_sets = self._resolve_simulation_set_selections(
            inspection, simulation_selections
        )
        selected_simulation_periods = self._resolve_simulation_periods_selections(
            inspection, periods_selections
        )
        request_body = self._build_request(
            inspection, settings, selected_schemes, selected_simulation_sets,
            selected_simulation_periods,
        )
        try:
            response = self.client.request("POST", CREATE_ANALYSIS_GROUP, json=request_body)
            job_id = int(extract_id_from_location_header(response, "analysis group creation"))
        except IRPAPIError:
            raise
        except Exception as exc:
            raise IRPAPIError(
                f"Failed to submit analysis group '{settings.analysis_name}': {exc}",
                status_code=getattr(exc, 'status_code', None),
            ) from exc
        return GroupingSubmission(job_id=job_id, request_body=request_body)

    def get_job(self, *, job_id: int) -> Dict[str, Any]:
        """Retrieve grouping job status by job ID.

        Args:
            job_id: Positive Platform grouping job ID

        Returns:
            Grouping job status response

        Raises:
            IRPValidationError: If job_id is invalid
            IRPAPIError: If the Platform read fails
        """
        validate_positive_int(job_id, "job_id")
        try:
            response = self.client.request(
                "GET", GET_ANALYSIS_GROUPING_JOB.format(jobId=job_id)
            )
            return response.json()
        except IRPAPIError:
            raise
        except Exception as exc:
            raise IRPAPIError(
                f"Failed to get analysis grouping job status for job ID {job_id}: {exc}",
                status_code=getattr(exc, 'status_code', None),
            ) from exc

    @staticmethod
    def _validate_analysis_ids(analysis_ids: Sequence[int]) -> Tuple[int, ...]:
        if isinstance(analysis_ids, (str, bytes)) or not isinstance(analysis_ids, Sequence):
            raise IRPValidationError("analysis_ids must be a sequence of positive integers")
        normalized = tuple(analysis_ids)
        if len(normalized) < 2:
            raise IRPValidationError("analysis_ids must contain at least two analysis IDs")
        for index, value in enumerate(normalized):
            validate_positive_int(value, f"analysis_ids[{index}]")
        if len(set(normalized)) != len(normalized):
            raise IRPValidationError("analysis_ids must contain distinct analysis IDs")
        return normalized

    @staticmethod
    def _validate_settings(settings: GroupingSettings) -> None:
        if not isinstance(settings, GroupingSettings):
            raise IRPValidationError("settings must be a GroupingSettings instance")
        validate_non_empty_string(settings.analysis_name, "settings.analysis_name")
        if not isinstance(settings.currency, GroupingCurrency):
            raise IRPValidationError("settings.currency must be a GroupingCurrency instance")
        for name in ("code", "scheme", "vintage", "as_of_date"):
            validate_non_empty_string(getattr(settings.currency, name), f"settings.currency.{name}")
        if not isinstance(settings.propagate_detailed_losses, bool):
            raise IRPValidationError("settings.propagate_detailed_losses must be a boolean")
        validate_positive_int(settings.num_of_simulations, "settings.num_of_simulations")
        for name in (
            "description",
            "reporting_window_start",
            "simulation_window_start",
            "simulation_window_end",
        ):
            value = getattr(settings, name)
            if value is not None and not isinstance(value, str):
                raise IRPValidationError(f"settings.{name} must be a string or None")

    @staticmethod
    def _validate_event_rate_selection_arguments(
        selections: Sequence[EventRateSelection],
    ) -> Tuple[EventRateSelection, ...]:
        if isinstance(selections, (str, bytes)) or not isinstance(selections, Sequence):
            raise IRPValidationError("event_rate_selections must be a sequence")
        normalized = tuple(selections)
        for selection in normalized:
            if not isinstance(selection, EventRateSelection):
                raise IRPValidationError(
                    "event_rate_selections must contain EventRateSelection values"
                )
            if not isinstance(selection.partition, GroupingPartitionKey):
                raise IRPValidationError("selection.partition must be a GroupingPartitionKey")
            validate_non_empty_string(
                selection.partition.peril_code, "selection.partition.peril_code"
            )
            validate_non_empty_string(
                selection.partition.region_code, "selection.partition.region_code"
            )
            validate_non_empty_string(
                selection.partition.model_version, "selection.partition.model_version"
            )
            validate_positive_int(
                selection.event_rate_scheme_id, "selection.event_rate_scheme_id"
            )
        return normalized

    @staticmethod
    def _validate_simulation_set_selection_arguments(
        selections: Sequence[SimulationSetSelection],
    ) -> Tuple[SimulationSetSelection, ...]:
        if isinstance(selections, (str, bytes)) or not isinstance(selections, Sequence):
            raise IRPValidationError("simulation_set_selections must be a sequence")
        normalized = tuple(selections)
        for selection in normalized:
            if not isinstance(selection, SimulationSetSelection):
                raise IRPValidationError(
                    "simulation_set_selections must contain SimulationSetSelection values"
                )
            if not isinstance(selection.partition, GroupingPartitionKey):
                raise IRPValidationError("selection.partition must be a GroupingPartitionKey")
            validate_non_empty_string(
                selection.partition.peril_code, "selection.partition.peril_code"
            )
            validate_non_empty_string(
                selection.partition.region_code, "selection.partition.region_code"
            )
            validate_non_empty_string(
                selection.partition.model_version, "selection.partition.model_version"
            )
            validate_positive_int(
                selection.simulation_set_id, "selection.simulation_set_id"
            )
        return normalized

    @staticmethod
    def _validate_simulation_periods_selection_arguments(
        selections: Sequence[SimulationPeriodsSelection],
    ) -> Tuple[SimulationPeriodsSelection, ...]:
        if isinstance(selections, (str, bytes)) or not isinstance(selections, Sequence):
            raise IRPValidationError("simulation_periods_selections must be a sequence")
        normalized = tuple(selections)
        for selection in normalized:
            if not isinstance(selection, SimulationPeriodsSelection):
                raise IRPValidationError(
                    "simulation_periods_selections must contain "
                    "SimulationPeriodsSelection values"
                )
            if not isinstance(selection.partition, GroupingPartitionKey):
                raise IRPValidationError("selection.partition must be a GroupingPartitionKey")
            validate_non_empty_string(
                selection.partition.peril_code, "selection.partition.peril_code"
            )
            validate_non_empty_string(
                selection.partition.region_code, "selection.partition.region_code"
            )
            validate_non_empty_string(
                selection.partition.model_version, "selection.partition.model_version"
            )
            validate_positive_int(
                selection.simulation_periods, "selection.simulation_periods"
            )
        return normalized

    def _inspect(self, analysis_ids: Tuple[int, ...]) -> GroupingInspection:
        problems: List[GroupingProblem] = []
        members: List[GroupingMember] = []
        treaties: List[Dict[str, Any]] = []
        labels: Dict[int, Optional[str]] = {}
        lookups = _ReferenceLookups(self._irp)

        for analysis_id in analysis_ids:
            try:
                analysis = self._irp.analysis.get_analysis_by_id(analysis_id)
            except IRPAPIError as exc:
                message = str(exc).lower()
                if "404" not in message and "not found" not in message:
                    raise
                problems.append(GroupingProblem(
                    code=GroupingProblemCode.MEMBER_NOT_FOUND.value,
                    message=f"Analysis {analysis_id} was not found.",
                    analysis_ids=(analysis_id,),
                ))
                members.append(GroupingMember(
                    analysis_id, False, False, None, None, None, None, None, None, ()
                ))
                continue

            if not isinstance(analysis, Mapping) or not analysis:
                problems.append(GroupingProblem(
                    code=GroupingProblemCode.MEMBER_NOT_FOUND.value,
                    message=f"Analysis {analysis_id} returned no analysis details.",
                    analysis_ids=(analysis_id,),
                ))
                members.append(GroupingMember(
                    analysis_id, False, False, None, None, None, None, None, None, ()
                ))
                continue

            raw_treaties = self._irp.analysis.search_analysis_treaties_paginated(analysis_id)
            if not isinstance(raw_treaties, list):
                raise IRPAPIError(
                    f"Treaty search for analysis ID {analysis_id} returned a non-list response"
                )
            for treaty in raw_treaties:
                if not isinstance(treaty, Mapping):
                    raise IRPAPIError(
                        f"Treaty search for analysis ID {analysis_id} returned a malformed treaty"
                    )
                treaty_number = _text(treaty.get("treatyNumber"))
                if treaty_number is None:
                    raise IRPAPIError(
                        f"Treaty for analysis ID {analysis_id} has no Treaty Number"
                    )
                treaty_id = treaty.get("treatyId")
                treaties.append({
                    "analysis_id": analysis_id,
                    "treaty_id": int(treaty_id) if _positive_int(treaty_id) else None,
                    "treaty_number": treaty_number,
                    "terms": self._loss_affecting_treaty_terms(treaty),
                })

            raw_regions = self._irp.analysis.get_regions(analysis_id)
            if not isinstance(raw_regions, list) or not raw_regions:
                problems.append(GroupingProblem(
                    code=GroupingProblemCode.MEMBER_REGION_DATA_MISSING.value,
                    message=f"Analysis {analysis_id} returned no region data.",
                    analysis_ids=(analysis_id,),
                ))
                raw_regions = []

            analysis_framework = _analysis_framework(analysis)
            engine_type = _text(_field(analysis, "engineType", "type"))
            is_group = _is_group(analysis)
            detail_engine = _text(_field(analysis, "engineVersion", "softwareVersionCode"))
            detail_peril = _text(_field(analysis, "perilCode", "peril"))
            detail_region = _text(_field(analysis, "regionCode", "region"))
            analysis_scheme, analysis_label = _event_rate_from_analysis(analysis)
            if analysis_scheme is not None:
                labels[analysis_scheme] = analysis_label

            region_facts, observed_frameworks = _region_facts(
                analysis_id, analysis, raw_regions, lookups, problems.append
            )

            if analysis_framework and observed_frameworks and observed_frameworks != {analysis_framework}:
                problems.append(GroupingProblem(
                    code=GroupingProblemCode.MEMBER_CLASSIFICATION_CONFLICT.value,
                    message=(f"Analysis {analysis_id} framework {analysis_framework} conflicts "
                             f"with its region frameworks {sorted(observed_frameworks)}."),
                    analysis_ids=(analysis_id,),
                ))
            if analysis_framework is None and len(observed_frameworks) == 1:
                analysis_framework = next(iter(observed_frameworks))
            versions = sorted({fact.model_version for fact in region_facts})
            members.append(GroupingMember(
                analysis_id=analysis_id,
                exists=True,
                is_group=is_group,
                analysis_framework=analysis_framework,
                engine_type=engine_type,
                engine_version=detail_engine,
                peril_code=detail_peril,
                region_code=detail_region,
                model_version=versions[0] if len(versions) == 1 else None,
                regions=tuple(sorted(region_facts, key=self._region_sort_key)),
            ))

        all_facts = [fact for member in members for fact in member.regions]
        simulate_to_plt = any(fact.framework == "PLT" for fact in all_facts)
        output_loss_table = "PLT" if simulate_to_plt else "ELT"
        dlm_analysis_ids = {
            member.analysis_id
            for member in members
            if member.exists and (member.engine_type or "").upper() == "DLM"
        }

        partition_facts: Dict[GroupingPartitionKey, List[GroupingRegionFact]] = {}
        for fact in all_facts:
            key = GroupingPartitionKey(fact.peril_code, fact.region_code, fact.model_version)
            partition_facts.setdefault(key, []).append(fact)

        simulation_rows: List[Dict[str, Any]] = []
        active_event_rate_scheme_ids: set[int] = set()
        if simulate_to_plt and any(fact.framework == "ELT" for fact in all_facts):
            raw_simulation_rows = self._irp.reference_data.get_all_simulation_sets()
            if not isinstance(raw_simulation_rows, list):
                raise IRPAPIError("Simulation-set search returned a non-list response")
            simulation_rows = [
                row for row in raw_simulation_rows if isinstance(row, dict)
            ]
            active_event_rate_scheme_ids = {
                int(row["eventRateSchemeId"])
                for row in lookups.event_rate_scheme_rows()
                if _positive_int(row.get("eventRateSchemeId"))
            }

        partitions: List[GroupingPartition] = []
        mappings: List[GroupingSimulationMapping] = []
        for key in sorted(partition_facts, key=self._partition_sort_key):
            facts = partition_facts[key]
            analysis_id_set = tuple(sorted({fact.analysis_id for fact in facts}))
            elt_facts = [fact for fact in facts if fact.framework == "ELT"]
            scheme_ids = sorted({
                fact.event_rate_scheme_id for fact in facts
                if fact.framework == "ELT" and fact.event_rate_scheme_id is not None
            })
            if len(scheme_ids) > 1:
                dlm_only_partition = all(
                    fact.analysis_id in dlm_analysis_ids for fact in elt_facts
                )
                broad_model_region = f"{key.region_code}{key.peril_code}"
                applicable_schemes = {
                    int(row["eventRateSchemeId"]): _text(
                        row.get("eventRateSchemeName")
                    )
                    for row in lookups.event_rate_scheme_rows()
                    if (
                        _positive_int(row.get("eventRateSchemeId"))
                        and row.get("perilCode") == key.peril_code
                        and row.get("modelRegionCode") == broad_model_region
                        and (
                            not dlm_only_partition
                            or row.get("modelVersionCode") == key.model_version
                        )
                    )
                }
                event_rate_options = tuple(
                    EventRateSchemeOption(scheme_id, applicable_schemes[scheme_id])
                    for scheme_id in sorted(applicable_schemes)
                )
                if not applicable_schemes:
                    problems.append(GroupingProblem(
                        code=GroupingProblemCode.EVENT_RATE_SCHEME_MAPPING_MISSING.value,
                        message=(
                            "No active event-rate scheme is available for the "
                            f"conflicting partition with peril {key.peril_code}, "
                            f"region {key.region_code}, and model version "
                            f"{key.model_version}."
                        ),
                        analysis_ids=tuple(sorted({fact.analysis_id for fact in elt_facts})),
                        partition=key,
                    ))
            else:
                event_rate_options = tuple(
                    EventRateSchemeOption(
                        scheme_id, lookups.scheme_name(scheme_id) or labels.get(scheme_id)
                    )
                    for scheme_id in scheme_ids
                )
            pet_ids = tuple(sorted({
                fact.pet_id for fact in facts
                if fact.framework == "PLT" and fact.pet_id is not None
            }))
            broad_model_region = f"{key.region_code}{key.peril_code}"
            simulation_options: Dict[int, SimulationSetOption] = {}
            if simulate_to_plt and elt_facts:
                for row in simulation_rows:
                    row_version = _field(row, "modelVersionCode", "modelVersion")
                    row_scheme = row.get("eventRateSchemeId")
                    row_scheme_id = (
                        int(row_scheme) if _positive_int(row_scheme) else None
                    )
                    if (
                        row.get("modelRegionCode") != broad_model_region
                        or str(row_version) != key.model_version
                        or row.get("perilCode") != key.peril_code
                        or row.get("peqtSource") not in (None, "SYSTEM")
                        or row_scheme_id not in active_event_rate_scheme_ids
                    ):
                        continue
                    simulation_id = _field(row, "id", "simulationSetId")
                    simulation_periods = _field(
                        row, "defaultPeriods", "simulationPeriods"
                    )
                    if not _positive_int(simulation_id) or not _positive_int(
                        simulation_periods
                    ):
                        continue
                    simulation_options[int(simulation_id)] = SimulationSetOption(
                        simulation_set_id=int(simulation_id),
                        simulation_periods=int(simulation_periods),
                        event_rate_scheme_id=(
                            row_scheme_id
                        ),
                        label=_text(row.get("name")),
                    )

                if not simulation_options:
                    problems.append(GroupingProblem(
                        code=GroupingProblemCode.SIMULATION_SET_MAPPING_MISSING.value,
                        message=(
                            f"No simulation set is available for peril {key.peril_code}, "
                            f"region {key.region_code}, and model version "
                            f"{key.model_version}."
                        ),
                        analysis_ids=tuple(sorted({fact.analysis_id for fact in elt_facts})),
                        partition=key,
                    ))

            ordered_simulation_options = tuple(
                simulation_options[simulation_id]
                for simulation_id in sorted(simulation_options)
            )
            partitions.append(GroupingPartition(
                key=key,
                analysis_ids=analysis_id_set,
                event_rate_scheme_options=event_rate_options,
                observed_pet_ids=pet_ids,
                event_rate_selection_required=len(scheme_ids) > 1,
                simulation_set_options=ordered_simulation_options,
                simulation_set_selection_required=bool(ordered_simulation_options),
            ))
            engines = ",".join(sorted({fact.engine_version for fact in elt_facts}))
            for option in ordered_simulation_options:
                mappings.append(GroupingSimulationMapping(
                    partition=key,
                    analysis_ids=tuple(sorted({fact.analysis_id for fact in elt_facts})),
                    engine_version=engines,
                    model_region_code=broad_model_region,
                    event_rate_scheme_id=option.event_rate_scheme_id or 0,
                    simulation_set_id=option.simulation_set_id,
                    simulation_periods=option.simulation_periods,
                ))

        problems = self._deduplicate_problems(problems)
        warnings = self._treaty_warnings(treaties)
        required = [
            "analysis_name",
            "currency",
            "propagate_detailed_losses",
            "num_of_simulations",
        ]
        if any(partition.event_rate_selection_required for partition in partitions):
            required.append("event_rate_selections")
        if any(partition.simulation_set_selection_required for partition in partitions):
            required.append("simulation_set_selections")

        inspected_at = datetime.now(timezone.utc).isoformat()
        resource_uris = tuple(
            GET_ANALYSIS_RESULT.format(analysisId=analysis_id) for analysis_id in analysis_ids
        )
        fingerprint = self._fingerprint(
            analysis_ids=analysis_ids,
            resource_uris=resource_uris,
            members=tuple(members),
            output_loss_table=output_loss_table,
            simulate_to_plt=simulate_to_plt,
            partitions=tuple(partitions),
            mappings=tuple(mappings),
            treaties=tuple(treaties),
            problems=tuple(problems),
        )
        return GroupingInspection(
            analysis_ids=analysis_ids,
            resource_uris=resource_uris,
            inspected_at=inspected_at,
            fingerprint=fingerprint,
            members=tuple(members),
            output_loss_table=output_loss_table,
            simulate_to_plt=simulate_to_plt,
            partitions=tuple(partitions),
            simulation_mappings=tuple(mappings),
            required_caller_inputs=tuple(required),
            warnings=tuple(warnings),
            blocking_problems=tuple(problems),
        )

    @staticmethod
    def _partition_sort_key(key: GroupingPartitionKey) -> Tuple[str, str, str]:
        return key.peril_code, key.region_code, key.model_version

    @staticmethod
    def _region_sort_key(fact: GroupingRegionFact) -> Tuple[Any, ...]:
        return (
            fact.peril_code,
            fact.region_code,
            fact.model_version,
            fact.framework,
            fact.model_region_code,
            fact.engine_version,
            fact.event_rate_scheme_id or 0,
            fact.pet_id or 0,
            fact.periods or 0,
        )

    @staticmethod
    def _deduplicate_problems(problems: List[GroupingProblem]) -> List[GroupingProblem]:
        unique: Dict[str, GroupingProblem] = {}
        for problem in problems:
            payload = {
                "code": problem.code,
                "analysis_ids": problem.analysis_ids,
                "partition": asdict(problem.partition) if problem.partition else None,
                "pet_ids": problem.pet_ids,
                "sub_regions": problem.sub_regions,
                "treaty_numbers": problem.treaty_numbers,
                "treaty_ids": problem.treaty_ids,
                "differing_fields": problem.differing_fields,
            }
            unique[json.dumps(payload, sort_keys=True)] = problem
        return [unique[key] for key in sorted(unique)]

    @staticmethod
    def _reference_value(value: Any, *keys: str) -> Any:
        if not isinstance(value, Mapping):
            return value
        return _field(value, *keys)

    @classmethod
    def _loss_affecting_treaty_terms(cls, treaty: Mapping[str, Any]) -> Dict[str, Any]:
        terms = {field: treaty.get(field) for field in cls.LOSS_AFFECTING_TREATY_FIELDS}
        terms["cedant"] = cls._reference_value(
            treaty.get("cedant"), "cedantId", "cedantName"
        )
        terms["currency"] = cls._reference_value(treaty.get("currency"), "code", "id")

        lobs = []
        for lob in treaty.get("lobs") or []:
            lobs.append(cls._reference_value(lob, "lobId", "lobName"))
        terms["lobs"] = sorted(
            lobs, key=lambda value: json.dumps(value, sort_keys=True, default=str)
        )

        loss_occurrences = []
        for occurrence in treaty.get("lossOccurrences") or []:
            if not isinstance(occurrence, Mapping):
                loss_occurrences.append(occurrence)
                continue
            loss_occurrences.append({
                "regionPeril": cls._reference_value(
                    occurrence.get("regionPeril"), "code", "id"
                ),
                "lossOccurrenceTime": occurrence.get("lossOccurrenceTime"),
                "lossOccurrenceRadius": occurrence.get("lossOccurrenceRadius"),
                "radiusUnit": cls._reference_value(
                    occurrence.get("radiusUnit"), "code", "id"
                ),
                "multiLossOccurrence": cls._reference_value(
                    occurrence.get("multiLossOccurrence"), "code", "id"
                ),
            })
        terms["lossOccurrences"] = sorted(
            loss_occurrences,
            key=lambda value: json.dumps(value, sort_keys=True, default=str),
        )
        return terms

    @classmethod
    def _treaty_warnings(cls, treaties: List[Dict[str, Any]]) -> List[GroupingProblem]:
        by_number: Dict[str, List[Dict[str, Any]]] = {}
        for treaty in treaties:
            by_number.setdefault(treaty["treaty_number"], []).append(treaty)

        warnings = []
        for treaty_number in sorted(by_number):
            matches = by_number[treaty_number]
            if len(matches) < 2:
                continue
            term_payloads = {
                json.dumps(treaty["terms"], sort_keys=True, separators=(",", ":"))
                for treaty in matches
            }
            if len(term_payloads) == 1:
                continue
            differing_fields = tuple(sorted(
                field
                for field in matches[0]["terms"]
                if len({
                    json.dumps(
                        treaty["terms"].get(field),
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    for treaty in matches
                }) > 1
            ))
            affected_analysis_ids = tuple(sorted({
                int(treaty["analysis_id"]) for treaty in matches
            }))
            affected_treaty_ids = tuple(sorted({
                int(treaty["treaty_id"])
                for treaty in matches
                if treaty["treaty_id"] is not None
            }))
            warnings.append(GroupingProblem(
                code=GroupingProblemCode.INCONSISTENT_TREATY_TERMS.value,
                message=(
                    f"Treaty Number {json.dumps(treaty_number)} has inconsistent "
                    f"loss-affecting terms {list(differing_fields)} across analyses "
                    f"{list(affected_analysis_ids)}."
                ),
                analysis_ids=affected_analysis_ids,
                treaty_numbers=(treaty_number,),
                treaty_ids=affected_treaty_ids,
                differing_fields=differing_fields,
                treaties=tuple(
                    GroupingTreaty(
                        analysis_id=int(treaty["analysis_id"]),
                        treaty_id=treaty["treaty_id"],
                        treaty_number=treaty["treaty_number"],
                        terms=treaty["terms"],
                    )
                    for treaty in sorted(
                        matches,
                        key=lambda treaty: (
                            treaty["analysis_id"],
                            treaty["treaty_id"] or 0,
                        ),
                    )
                ),
            ))
        return warnings

    def _fingerprint(
        self,
        *,
        analysis_ids: Tuple[int, ...],
        resource_uris: Tuple[str, ...],
        members: Tuple[GroupingMember, ...],
        output_loss_table: str,
        simulate_to_plt: bool,
        partitions: Tuple[GroupingPartition, ...],
        mappings: Tuple[GroupingSimulationMapping, ...],
        treaties: Tuple[Dict[str, Any], ...],
        problems: Tuple[GroupingProblem, ...],
    ) -> str:
        partition_payload = []
        for partition in partitions:
            partition_payload.append({
                "key": asdict(partition.key),
                "analysis_ids": partition.analysis_ids,
                "event_rate_scheme_ids": tuple(
                    option.event_rate_scheme_id
                    for option in partition.event_rate_scheme_options
                ),
                "observed_pet_ids": partition.observed_pet_ids,
                "event_rate_selection_required": partition.event_rate_selection_required,
                "simulation_set_options": tuple(
                    (
                        option.simulation_set_id,
                        option.simulation_periods,
                        option.event_rate_scheme_id,
                    )
                    for option in partition.simulation_set_options
                ),
                "simulation_set_selection_required": (
                    partition.simulation_set_selection_required
                ),
            })
        problem_payload = [{
            "code": problem.code,
            "analysis_ids": problem.analysis_ids,
            "partition": asdict(problem.partition) if problem.partition else None,
            "pet_ids": problem.pet_ids,
            "treaty_numbers": problem.treaty_numbers,
            "treaty_ids": problem.treaty_ids,
            "differing_fields": problem.differing_fields,
        } for problem in problems]
        payload = {
            "version": self.FINGERPRINT_VERSION,
            "analysis_ids": tuple(sorted(analysis_ids)),
            "resource_uris": tuple(sorted(resource_uris)),
            "members": [asdict(member) for member in sorted(members, key=lambda value: value.analysis_id)],
            "output_loss_table": output_loss_table,
            "simulate_to_plt": simulate_to_plt,
            "partitions": partition_payload,
            "simulation_mappings": [
                asdict(mapping)
                for mapping in sorted(
                    mappings,
                    key=lambda mapping: (
                        mapping.partition.peril_code,
                        mapping.partition.region_code,
                        mapping.partition.model_version,
                        mapping.simulation_set_id,
                        mapping.event_rate_scheme_id,
                        mapping.simulation_periods,
                        mapping.engine_version,
                        mapping.model_region_code,
                    ),
                )
            ],
            "treaties": sorted(
                treaties,
                key=lambda treaty: (
                    treaty["analysis_id"],
                    treaty["treaty_number"],
                    treaty["treaty_id"] or 0,
                ),
            ),
            "problems": problem_payload,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return f"v{self.FINGERPRINT_VERSION}:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"

    def _resolve_event_rate_selections(
        self,
        inspection: GroupingInspection,
        selections: Tuple[EventRateSelection, ...],
    ) -> Dict[GroupingPartitionKey, int]:
        partitions = {partition.key: partition for partition in inspection.partitions}
        required = {
            key: partition for key, partition in partitions.items()
            if partition.event_rate_selection_required
        }
        supplied: Dict[GroupingPartitionKey, int] = {}
        problems: List[GroupingProblem] = []
        for selection in selections:
            key = selection.partition
            if key in supplied:
                problems.append(GroupingProblem(
                    code=GroupingProblemCode.EVENT_RATE_SELECTION_DUPLICATE.value,
                    message="A conflicting partition has more than one event-rate selection.",
                    partition=key,
                ))
                continue
            supplied[key] = selection.event_rate_scheme_id
            partition = partitions.get(key)
            if partition is None:
                problems.append(GroupingProblem(
                    code=GroupingProblemCode.EVENT_RATE_SELECTION_UNKNOWN_PARTITION.value,
                    message="An event-rate selection names a partition not returned by inspection.",
                    partition=key,
                ))
                continue
            if key not in required:
                problems.append(GroupingProblem(
                    code=GroupingProblemCode.EVENT_RATE_SELECTION_NOT_REQUIRED.value,
                    message="An event-rate selection was supplied for a non-conflicting partition.",
                    analysis_ids=partition.analysis_ids,
                    partition=key,
                ))
                continue
            offered = {
                option.event_rate_scheme_id for option in partition.event_rate_scheme_options
            }
            if selection.event_rate_scheme_id not in offered:
                problems.append(GroupingProblem(
                    code=GroupingProblemCode.EVENT_RATE_SELECTION_NOT_OFFERED.value,
                    message=(f"Event-rate scheme {selection.event_rate_scheme_id} was not "
                             "offered for this partition."),
                    analysis_ids=partition.analysis_ids,
                    partition=key,
                ))
        for key, partition in required.items():
            if key not in supplied:
                problems.append(GroupingProblem(
                    code=GroupingProblemCode.EVENT_RATE_SELECTION_MISSING.value,
                    message="A conflicting partition requires an explicit event-rate selection.",
                    analysis_ids=partition.analysis_ids,
                    partition=key,
                ))
        if problems:
            raise IRPGroupingValidationError(tuple(self._deduplicate_problems(problems)))

        resolved: Dict[GroupingPartitionKey, int] = {}
        for key, partition in partitions.items():
            options = partition.event_rate_scheme_options
            if len(options) == 1:
                resolved[key] = options[0].event_rate_scheme_id
            elif len(options) > 1:
                resolved[key] = supplied[key]
        return resolved

    def _resolve_simulation_set_selections(
        self,
        inspection: GroupingInspection,
        selections: Tuple[SimulationSetSelection, ...],
    ) -> Dict[GroupingPartitionKey, SimulationSetOption]:
        partitions = {partition.key: partition for partition in inspection.partitions}
        required = {
            key: partition for key, partition in partitions.items()
            if partition.simulation_set_selection_required
        }
        supplied: Dict[GroupingPartitionKey, int] = {}
        problems: List[GroupingProblem] = []
        for selection in selections:
            key = selection.partition
            if key in supplied:
                problems.append(GroupingProblem(
                    code=GroupingProblemCode.SIMULATION_SET_SELECTION_DUPLICATE.value,
                    message=(
                        "An ELT-to-PLT partition has more than one simulation-set "
                        "selection."
                    ),
                    partition=key,
                ))
                continue
            supplied[key] = selection.simulation_set_id
            partition = partitions.get(key)
            if partition is None:
                problems.append(GroupingProblem(
                    code=(
                        GroupingProblemCode
                        .SIMULATION_SET_SELECTION_UNKNOWN_PARTITION.value
                    ),
                    message=(
                        "A simulation-set selection names a partition not returned "
                        "by inspection."
                    ),
                    partition=key,
                ))
                continue
            if key not in required:
                problems.append(GroupingProblem(
                    code=GroupingProblemCode.SIMULATION_SET_SELECTION_NOT_REQUIRED.value,
                    message=(
                        "A simulation-set selection was supplied for a partition "
                        "that is not converted from ELT to PLT."
                    ),
                    analysis_ids=partition.analysis_ids,
                    partition=key,
                ))
                continue
            offered = {
                option.simulation_set_id for option in partition.simulation_set_options
            }
            if selection.simulation_set_id not in offered:
                problems.append(GroupingProblem(
                    code=GroupingProblemCode.SIMULATION_SET_SELECTION_NOT_OFFERED.value,
                    message=(
                        f"Simulation set {selection.simulation_set_id} was not offered "
                        "for this partition."
                    ),
                    analysis_ids=partition.analysis_ids,
                    partition=key,
                ))
        for key, partition in required.items():
            if key not in supplied:
                problems.append(GroupingProblem(
                    code=GroupingProblemCode.SIMULATION_SET_SELECTION_MISSING.value,
                    message=(
                        "An ELT-to-PLT partition requires an explicit simulation-set "
                        "selection."
                    ),
                    analysis_ids=partition.analysis_ids,
                    partition=key,
                ))
        if problems:
            raise IRPGroupingValidationError(tuple(self._deduplicate_problems(problems)))

        return {
            key: next(
                option
                for option in partition.simulation_set_options
                if option.simulation_set_id == supplied[key]
            )
            for key, partition in required.items()
        }

    def _resolve_simulation_periods_selections(
        self,
        inspection: GroupingInspection,
        selections: Tuple[SimulationPeriodsSelection, ...],
    ) -> Dict[GroupingPartitionKey, int]:
        partitions = {partition.key: partition for partition in inspection.partitions}
        supplied: Dict[GroupingPartitionKey, int] = {}
        problems: List[GroupingProblem] = []
        for selection in selections:
            key = selection.partition
            if key in supplied:
                problems.append(GroupingProblem(
                    code=GroupingProblemCode.SIMULATION_PERIODS_SELECTION_DUPLICATE.value,
                    message="A partition has more than one simulation-periods selection.",
                    partition=key,
                ))
                continue
            supplied[key] = selection.simulation_periods
            partition = partitions.get(key)
            if partition is None:
                problems.append(GroupingProblem(
                    code=(
                        GroupingProblemCode
                        .SIMULATION_PERIODS_SELECTION_UNKNOWN_PARTITION.value
                    ),
                    message=(
                        "A simulation-periods selection names a partition not "
                        "returned by inspection."
                    ),
                    partition=key,
                ))
                continue
            if not inspection.simulate_to_plt:
                problems.append(GroupingProblem(
                    code=GroupingProblemCode.SIMULATION_PERIODS_SELECTION_NOT_REQUIRED.value,
                    message=(
                        "A simulation-periods selection was supplied for a group "
                        "that is not simulated to PLT."
                    ),
                    analysis_ids=partition.analysis_ids,
                    partition=key,
                ))
        if problems:
            raise IRPGroupingValidationError(tuple(self._deduplicate_problems(problems)))
        return supplied

    def _build_request(
        self,
        inspection: GroupingInspection,
        settings: GroupingSettings,
        selected_schemes: Dict[GroupingPartitionKey, int],
        selected_simulation_sets: Dict[GroupingPartitionKey, SimulationSetOption],
        selected_simulation_periods: Dict[GroupingPartitionKey, int],
    ) -> Dict[str, Any]:
        region_peril = self._build_region_peril_simulation_set(
            inspection, selected_schemes, selected_simulation_sets,
            selected_simulation_periods,
        )
        request_settings: Dict[str, Any] = {
            "analysisName": settings.analysis_name,
            "currency": {
                "code": settings.currency.code,
                "scheme": settings.currency.scheme,
                "vintage": settings.currency.vintage,
                "asOfDate": settings.currency.as_of_date,
            },
            "simulateToPLT": inspection.simulate_to_plt,
            "propagateDetailedLosses": settings.propagate_detailed_losses,
            "numOfSimulations": settings.num_of_simulations,
            "regionPerilSimulationSet": region_peril,
        }
        optional_fields = (
            ("description", settings.description),
            ("reportingWindowStart", settings.reporting_window_start),
            ("simulationWindowStart", settings.simulation_window_start),
            ("simulationWindowEnd", settings.simulation_window_end),
        )
        for field_name, value in optional_fields:
            if value is not None:
                request_settings[field_name] = value
        return {
            "resourceType": "analyses",
            "resourceUris": list(inspection.resource_uris),
            "settings": request_settings,
        }

    def _build_region_peril_simulation_set(
        self,
        inspection: GroupingInspection,
        selected_schemes: Dict[GroupingPartitionKey, int],
        selected_simulation_sets: Dict[GroupingPartitionKey, SimulationSetOption],
        selected_simulation_periods: Dict[GroupingPartitionKey, int],
    ) -> List[Dict[str, Any]]:
        conflicting = any(
            partition.event_rate_selection_required for partition in inspection.partitions
        )
        if not inspection.simulate_to_plt and not conflicting:
            return []

        elt_engines = {
            partition.key: ",".join(sorted({
                fact.engine_version
                for member in inspection.members
                for fact in member.regions
                if fact.framework == "ELT"
                and GroupingPartitionKey(
                    fact.peril_code, fact.region_code, fact.model_version
                ) == partition.key
            }))
            for partition in inspection.partitions
        }
        entries: Dict[str, Dict[str, Any]] = {}
        for member in inspection.members:
            for fact in member.regions:
                key = GroupingPartitionKey(
                    fact.peril_code, fact.region_code, fact.model_version
                )
                if fact.framework == "PLT":
                    if fact.pet_id is None or fact.periods is None:
                        continue
                    entry = {
                        "engineVersion": fact.engine_version,
                        "eventRateSchemeId": 0,
                        "modelRegionCode": fact.model_region_code,
                        "modelVersion": fact.model_version,
                        "perilCode": fact.peril_code,
                        "regionCode": fact.region_code,
                        "simulationPeriods": selected_simulation_periods.get(
                            key, fact.periods
                        ),
                        "simulationSetId": fact.pet_id,
                    }
                else:
                    scheme_id = selected_schemes.get(key)
                    if scheme_id is None:
                        continue
                    if inspection.simulate_to_plt:
                        simulation_set = selected_simulation_sets[key]
                        simulation_set_id = simulation_set.simulation_set_id
                        simulation_periods = selected_simulation_periods.get(
                            key, simulation_set.simulation_periods
                        )
                    else:
                        simulation_set_id = 0
                        simulation_periods = 0
                    entry = {
                        "engineVersion": elt_engines[key],
                        "eventRateSchemeId": scheme_id,
                        "modelRegionCode": fact.model_region_code,
                        "modelVersion": fact.model_version,
                        "perilCode": fact.peril_code,
                        "regionCode": fact.region_code,
                        "simulationPeriods": simulation_periods,
                        "simulationSetId": simulation_set_id,
                    }
                canonical = json.dumps(entry, sort_keys=True, separators=(",", ":"))
                entries[canonical] = entry
        return [entries[key] for key in sorted(entries)]


__all__ = [
    "EventRateSchemeOption",
    "EventRateSelection",
    "GroupingCurrency",
    "GroupingInspection",
    "GroupingManager",
    "GroupingMember",
    "GroupingPartition",
    "GroupingPartitionKey",
    "GroupingProblem",
    "GroupingProblemCode",
    "GroupingRegionFact",
    "GroupingSettings",
    "GroupingSimulationMapping",
    "GroupingSubmission",
    "GroupingTreaty",
    "SimulationPeriodsSelection",
    "SimulationSetOption",
    "SimulationSetSelection",
]
