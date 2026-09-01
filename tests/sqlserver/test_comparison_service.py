"""SQL Server tests for ``analysis_service.list_comparison_pairs`` (spec 013 T-01/T-06).

Covers pair resolution (the four build-time validations from data-model.md)
and the percent-change math — (second − base) / base per displayed row, never
``inf`` on a zero or missing base.
"""

from __future__ import annotations

import json
import uuid

from app.services import analysis_service
from app.services._common import _utcnow
from db import execute_command

_ELEVEN = (5, 10, 25, 50, 100, 250, 500, 1000, 2000, 5000, 10000)


def _extract(*, aal=100.0, std=50.0, scale=1.0, zero_rps=(), drop_rps=(),
             perspectives=("GR",)):
    """A stored loss_results document with per-RP OEP = rp × scale and
    AEP = 2 × rp × scale, so percent changes are exact fractions."""
    oep = {str(rp): (0.0 if rp in zero_rps else float(rp) * scale)
           for rp in _ELEVEN if rp not in drop_rps}
    aep = {str(rp): float(rp) * 2 * scale for rp in _ELEVEN}
    return {
        "engine_type": "DLM", "engine_version": "23.0",
        "perspectives": {
            code: ({"aal": aal, "std_dev": std, "oep": oep, "aep": aep}
                   if code in perspectives else None)
            for code in ("GR", "RL", "WX", "QS", "GU")
        },
    }


def _mk(table: str, **cols) -> str:
    row_id = cols.pop("id", str(uuid.uuid4()))
    now = _utcnow()
    keys = ["id", *cols.keys(), "inserted_at", "updated_at"]
    execute_command(
        f"INSERT INTO {table} ({', '.join(keys)}) "
        f"VALUES ({', '.join(':' + k for k in keys)})",
        {"id": row_id, **cols, "inserted_at": now, "updated_at": now},
        connection="WORKBENCH")
    return row_id


def _own(edm: str, name: str, *, currency: str | None = "USD",
         extract: dict | None = None) -> str:
    return _mk(
        "irp_analysis", edm_id=edm, name=name, full_name=name,
        status_code="ready",
        submitted_settings=(json.dumps({"currency": {"code": currency}})
                            if currency else None),
        loss_results=(json.dumps(extract) if extract is not None else None),
        execution_id=str(uuid.uuid4()), execution_item_no=0)


def _pairs_param(*sides: tuple[str, str]) -> str:
    return ",".join(f"{base}:{second}" for base, second in sides)


# ── pair resolution (T-01) ────────────────────────────────────────────────────


def test_pairs_string_parses_to_ordered_pairs(workbench_db):
    edm = _mk("irp_edm", name="E", status="ready")
    a = _own(edm, "A", extract=_extract())
    b = _own(edm, "B", extract=_extract(scale=1.5))
    c = _own(edm, "C", extract=_extract())
    d = _own(edm, "D", extract=_extract(scale=2.0))

    pairs, drops = analysis_service.list_comparison_pairs(
        pairs=_pairs_param((a, b), (c, d)), perspective="GR")

    assert [(p.base.name, p.second.name) for p in pairs] == [
        ("A", "B"), ("C", "D")]
    assert drops == []


def test_unparseable_or_unresolvable_side_drops_the_pair_whole(workbench_db):
    edm = _mk("irp_edm", name="E", status="ready")
    a = _own(edm, "A", extract=_extract())
    b = _own(edm, "B", extract=_extract())

    pairs, drops = analysis_service.list_comparison_pairs(
        pairs=f"not-a-uuid:{b},{str(uuid.uuid4())}:{b},{a}:{b}",
        perspective="GR")

    assert [(p.base.name, p.second.name) for p in pairs] == [("A", "B")]
    assert [d["kind"] for d in drops] == ["missing", "missing"]


def test_deleted_analysis_drops_the_pair_whole(workbench_db):
    edm = _mk("irp_edm", name="E", status="ready")
    a = _own(edm, "A", extract=_extract())
    b = _own(edm, "B", extract=_extract())
    execute_command(
        "UPDATE irp_analysis SET deleted_at = :now WHERE id = :id",
        {"now": _utcnow(), "id": b}, connection="WORKBENCH")

    pairs, drops = analysis_service.list_comparison_pairs(
        pairs=_pairs_param((a, b)), perspective="GR")

    assert pairs == []
    assert [d["kind"] for d in drops] == ["missing"]


def test_self_pair_is_dropped(workbench_db):
    edm = _mk("irp_edm", name="E", status="ready")
    a = _own(edm, "A", extract=_extract())

    pairs, drops = analysis_service.list_comparison_pairs(
        pairs=_pairs_param((a, a)), perspective="GR")

    assert pairs == []
    assert [d["kind"] for d in drops] == ["other"]


def test_unrecorded_currency_on_either_side_drops_the_pair(workbench_db):
    edm = _mk("irp_edm", name="E", status="ready")
    a = _own(edm, "A", extract=_extract())
    no_currency = _own(edm, "B", currency=None, extract=_extract())

    for param in (_pairs_param((a, no_currency)),
                  _pairs_param((no_currency, a))):
        pairs, drops = analysis_service.list_comparison_pairs(
            pairs=param, perspective="GR")
        assert pairs == []
        assert [d["kind"] for d in drops] == ["other"]


def test_currency_mismatch_drops_the_pair_naming_both(workbench_db):
    edm = _mk("irp_edm", name="E", status="ready")
    usd = _own(edm, "A", extract=_extract())
    eur = _own(edm, "B", currency="EUR", extract=_extract())

    pairs, drops = analysis_service.list_comparison_pairs(
        pairs=_pairs_param((usd, eur)), perspective="GR")

    assert pairs == []
    assert [(d["kind"], d["currencies"]) for d in drops] == [
        ("currency", ("USD", "EUR"))]


def test_only_the_first_five_pairs_survive(workbench_db):
    edm = _mk("irp_edm", name="E", status="ready")
    ids = [_own(edm, f"A{n}", extract=_extract()) for n in range(7)]

    pairs, drops = analysis_service.list_comparison_pairs(
        pairs=_pairs_param(*((ids[n], ids[n + 1]) for n in range(6))),
        perspective="GR")

    assert [p.base.name for p in pairs] == ["A0", "A1", "A2", "A3", "A4"]
    assert [d["kind"] for d in drops] == ["other"]


def test_the_cap_counts_requested_pairs_not_survivors(workbench_db):
    edm = _mk("irp_edm", name="E", status="ready")
    ids = [_own(edm, f"A{n}", extract=_extract()) for n in range(7)]
    # the third requested pair is a self-pair; the seventh must not take its
    # place inside the five the URL asked for
    requested = [(ids[n], ids[n + 1]) for n in range(6)]
    requested[2] = (ids[2], ids[2])

    pairs, drops = analysis_service.list_comparison_pairs(
        pairs=_pairs_param(*requested), perspective="GR")

    assert [p.base.name for p in pairs] == ["A0", "A1", "A3", "A4"]
    assert [d["kind"] for d in drops] == ["other", "other"]


def test_one_analysis_may_sit_in_many_pairs(workbench_db):
    edm = _mk("irp_edm", name="E", status="ready")
    a = _own(edm, "A", extract=_extract())
    b = _own(edm, "B", extract=_extract())
    c = _own(edm, "C", extract=_extract())

    pairs, drops = analysis_service.list_comparison_pairs(
        pairs=_pairs_param((a, b), (a, c), (b, a)), perspective="GR")

    assert [(p.base.name, p.second.name) for p in pairs] == [
        ("A", "B"), ("A", "C"), ("B", "A")]
    assert drops == []


def test_no_pairs_param_yields_nothing(workbench_db):
    assert analysis_service.list_comparison_pairs(
        pairs="", perspective="GR") == ([], [])


# ── percent change (T-06) ─────────────────────────────────────────────────────


def test_percent_change_per_return_period_and_aal_and_std_dev(workbench_db):
    edm = _mk("irp_edm", name="E", status="ready")
    base = _own(edm, "A", extract=_extract(aal=100.0, std=50.0))
    second = _own(edm, "B", extract=_extract(aal=120.0, std=25.0, scale=1.5))

    [pair], _ = analysis_service.list_comparison_pairs(
        pairs=_pairs_param((base, second)), perspective="GR")
    pct = pair.pct

    # (second − base) / base: every stored OEP and AEP point is +50%
    assert all(r["oep"] == 0.5 for r in pct.rows)
    assert all(r["aep"] == 0.5 for r in pct.rows)
    assert len(pct.rows) == 11
    assert pct.aal == 0.2
    assert pct.std_dev == -0.5


def test_zero_or_missing_base_reads_none_never_inf(workbench_db):
    edm = _mk("irp_edm", name="E", status="ready")
    base = _own(edm, "A",
                extract=_extract(aal=0.0, zero_rps=(5,), drop_rps=(10,)))
    second = _own(edm, "B", extract=_extract(scale=1.5, drop_rps=(25,)))

    [pair], _ = analysis_service.list_comparison_pairs(
        pairs=_pairs_param((base, second)), perspective="GR")
    pct = pair.pct

    by_rp = {r["rp"]: r["oep"] for r in pct.rows}
    assert by_rp["5"] is None       # zero base
    assert by_rp["10"] is None      # missing base
    assert by_rp["25"] is None      # missing second value
    assert by_rp["50"] == 0.5
    assert pct.aal is None          # zero AAL base


def test_absent_perspective_on_either_side_yields_no_percent(workbench_db):
    edm = _mk("irp_edm", name="E", status="ready")
    both = _own(edm, "A", extract=_extract(perspectives=("GR", "RL")))
    gr_only = _own(edm, "B", extract=_extract(perspectives=("GR",)))

    def pct(code):
        [pair], _ = analysis_service.list_comparison_pairs(
            pairs=_pairs_param((both, gr_only)), perspective=code)
        return pair.pct

    assert pct("GR") is not None
    assert pct("RL") is None
    assert pct("GU") is None
