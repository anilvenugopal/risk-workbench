"""Unit tests for custom grouping (spec 005 follow-on — FR-018–021, T-12/T-14).

Covers the group identity (canonical member-set hash), the cart composition
(validation against the stored summary, adopt-not-rename, name exactly as
typed with duplicate names blocked — P-24/P-25, name-derived numbers — P-26,
upper-bound counts, the may-overlap note), the immediate name check
(``check_group_name``), the cart confirm
(ordered refusals writing no rows; one ``breakout_group`` row and one
``run_breakout_custom`` job per group, keyed on the group row's UUID; the
one-episode rule in both directions), the group worker (union within a
dimension, intersect across; empty intersection fails with nothing created;
lineage rows carry dimension ``custom``, the group_key, and the group row id,
reclaim included), ``page_state``'s custom flights and cart-aggregated
banner, and the list read model's group-label resolution.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

import pytest

from app.services import breakout_service, portfolio_service
from app.services.breakout_service import (
    GateRefused,
    StaleSummary,
    SummaryRewritten,
    compose_group_cart,
    compute_group_key,
    evaluate_gate,
    load_approved_group,
    request_group_breakout,
)
from app.services.name_check import CollisionCheck
from db import execute, execute_command, execute_one
from tests.unit.breakout_rows import (
    AS_OF,
    RM_STAMP,
    SUMMARY,
    mk_edm,
    mk_portfolio,
    rerun_breakout_job,
    run_breakout_job,
)

# The gate-test summary plus two peril codes (P-19/W-21: numeric, label null).
GROUP_SUMMARY = dict(SUMMARY, breakout_values=dict(
    SUMMARY["breakout_values"],
    peril=[{"value": "1", "label": None, "accounts": 517},
           {"value": "2", "label": None, "accounts": 1701}]))


def _eligible_pair(fake_irp) -> tuple[str, str]:
    edm_id = mk_edm()
    pid = mk_portfolio(edm_id, summary=GROUP_SUMMARY)
    fake_irp.add_portfolio(edm_exposure_id="90001", irp_id="1",
                           name="usfl_commercial", stamp=RM_STAMP)
    return edm_id, pid


def _group(label: str, filters: dict) -> dict:
    return {"label": label, "filters": filters}


def _group_rows(pid: str) -> list[dict]:
    return execute(
        "SELECT id, group_key, label, filters, name, number, cart_id "
        "FROM breakout_group WHERE source_portfolio_id = :s ORDER BY name",
        {"s": pid}, connection="WORKBENCH")


def _custom_jobs() -> list[dict]:
    return execute(
        "SELECT id, requestor_type, requestor_id, status_code, input_data "
        "FROM rwb_job WHERE rwb_job_type = 'run_breakout_custom'",
        {}, connection="WORKBENCH")


def _generated_rows(source_id: str) -> list[dict]:
    return execute(
        "SELECT id, name, irp_id, breakout_dimension_code, breakout_value, "
        "breakout_group_id, deleted_at FROM irp_portfolio "
        "WHERE source_portfolio_id = :s ORDER BY name",
        {"s": source_id}, connection="WORKBENCH")


# ── group identity ────────────────────────────────────────────────────────────────

def test_group_key_is_canonical_order_insensitive_and_deterministic():
    a = compute_group_key({"state": ["FL", "GA"], "peril": ["2"]})
    b = compute_group_key({"peril": ["2"], "state": ["GA", "FL", "FL"]})
    assert a == b                                  # order/dupes never matter
    assert len(a) == 12 and int(a, 16) >= 0        # 12 hex chars
    assert a != compute_group_key({"state": ["FL"], "peril": ["2"]})
    assert a != compute_group_key({"state": ["FL", "GA"]})


# ── cart composition ──────────────────────────────────────────────────────────────

def test_compose_group_cart_names_bounds_and_overlap_note(iteration2_db):
    edm_id = mk_edm()
    pid = mk_portfolio(edm_id, summary=GROUP_SUMMARY)
    gate = evaluate_gate(edm_id, pid)
    plans = compose_group_cart(gate, edm_id=edm_id, portfolio_id=pid, groups=[
        _group("Coastal", {"state": ["TX", "CA"], "lob": ["EQ Comm"]}),
        _group("Florida Hurricane Commercial Book", {"state": ["TX"]}),
    ])

    first, second = plans
    # name = the label exactly as typed (P-24); number = the name inside 20
    # characters (P-26), hash-tailed once it no longer fits
    assert (first.name, first.number) == ("Coastal", "Coastal")
    assert second.name == "Florida Hurricane Commercial Book"
    assert second.number.startswith("Florida Hurric")
    assert len(second.number) <= breakout_service.PORTFOLIO_NUMBER_MAX
    # upper bound = min over dimensions of Σ selected per-value counts (P-23)
    assert first.accounts_upper_bound == 801       # min(220+1481, 801)
    assert second.accounts_upper_bound == 220
    # the may-overlap note names earlier cart rows sharing a value (P-18)
    assert first.may_overlap_with == []
    assert second.may_overlap_with == ["Coastal"]
    assert not first.exists and not first.adopted


def test_compose_group_cart_refuses_bad_input(iteration2_db):
    edm_id = mk_edm()
    pid = mk_portfolio(edm_id, summary=GROUP_SUMMARY)
    gate = evaluate_gate(edm_id, pid)

    def refuse(groups, match):
        with pytest.raises(GateRefused, match=match):
            compose_group_cart(gate, edm_id=edm_id, portfolio_id=pid,
                               groups=groups)

    refuse([_group("G", {"state": ["ZZ"]})], "unknown state value")
    refuse([_group("G", {"nope": ["X"]})], "unknown breakout dimension")
    refuse([_group("G", {})], "at least one dimension")
    refuse([_group("G", {"state": []})], "no values selected")
    refuse([_group("  ", {"state": ["TX"]})], "needs a name")
    too_long = "X" * (breakout_service.PORTFOLIO_NAME_MAX + 1)
    refuse([_group(too_long, {"state": ["TX"]})], "cap at")
    refuse([_group("A", {"state": ["TX"]}),
            _group("B", {"state": ["TX"]})], "same members")


def test_compose_group_cart_blocks_duplicate_names(iteration2_db):
    """P-25: a name already carried by a live portfolio in the EDM or an
    earlier cart row refuses — case-insensitive, never suffixed."""
    edm_id = mk_edm()
    pid = mk_portfolio(edm_id, summary=GROUP_SUMMARY)   # live "usfl_commercial"
    gate = evaluate_gate(edm_id, pid)

    def refuse(groups, match):
        with pytest.raises(GateRefused, match=match):
            compose_group_cart(gate, edm_id=edm_id, portfolio_id=pid,
                               groups=groups)

    refuse([_group("usfl_commercial", {"state": ["TX"]})],
           "already exists in this EDM")
    refuse([_group("USFL_Commercial", {"state": ["TX"]})],
           "already exists in this EDM")               # case-insensitive
    refuse([_group("Coastal", {"state": ["TX"]}),
            _group("coastal", {"lob": ["EQ Comm"]})],
           "already exists in the cart")


# ── the immediate name check (P-25) ───────────────────────────────────────────────

def test_check_group_name_blocks_local_and_rm_names(iteration2_db, fake_irp):
    edm_id, pid = _eligible_pair(fake_irp)

    # a live workbench row answers without Risk Modeler
    assert breakout_service.check_group_name(edm_id, "usfl_commercial").collides
    # an RM-side portfolio the workbench has no row for — case-insensitive
    fake_irp.add_portfolio(edm_exposure_id="90001", irp_id="77", name="Coastal")
    assert breakout_service.check_group_name(edm_id, "coastal").collides
    # a free name and a blank one
    assert not breakout_service.check_group_name(edm_id, "Fresh").collides
    assert breakout_service.check_group_name(edm_id, "  ") == CollisionCheck()


def test_check_group_name_fails_open_when_rm_unreachable(
        iteration2_db, fake_irp):
    edm_id, pid = _eligible_pair(fake_irp)
    fake_irp.raise_on_search = True
    check = breakout_service.check_group_name(edm_id, "Anything")
    assert not check.collides and check.checked is False


# ── the cart confirm ──────────────────────────────────────────────────────────────

def test_request_group_breakout_writes_rows_and_jobs_per_group(
        iteration2_db, fake_irp):
    edm_id, pid = _eligible_pair(fake_irp)
    job_ids = request_group_breakout(edm_id, pid, [
        _group("Coastal HU", {"state": ["TX"], "peril": ["2"]}),
        _group("EQ book", {"lob": ["EQ Comm"]}),
    ], AS_OF, iteration2_db.user_a)

    assert job_ids is not None and len(job_ids) == 2
    rows = _group_rows(pid)
    assert [r["label"] for r in rows] == ["Coastal HU", "EQ book"]
    assert all(json.loads(r["filters"]) for r in rows)
    jobs = _custom_jobs()
    assert {j["requestor_type"] for j in jobs} == {"breakout_group"}
    # each job keys on its group row's UUID (T-13)
    assert {j["requestor_id"] for j in jobs} == {r["id"] for r in rows}
    # one shared cart id across the jobs and the rows (FR-020)
    cart_ids = {json.loads(j["input_data"])["cart_id"] for j in jobs}
    assert len(cart_ids) == 1
    assert {r["cart_id"] for r in rows} == cart_ids
    # the approved plan travels in input_data (rule 8)
    by_requestor = {j["requestor_id"]: json.loads(j["input_data"])
                    for j in jobs}
    for r in rows:
        group = by_requestor[r["id"]]["group"]
        assert (group["id"], group["key"], group["name"],
                group["number"]) == (r["id"], r["group_key"], r["name"],
                                     r["number"])


def test_request_group_breakout_refusals_write_nothing(
        iteration2_db, fake_irp):
    edm_id, pid = _eligible_pair(fake_irp)
    groups = [_group("G", {"state": ["TX"]})]

    with pytest.raises(SummaryRewritten):
        request_group_breakout(edm_id, pid, groups, "2001-01-01 00:00:00",
                               iteration2_db.user_a)
    fake_irp.set_portfolio_stamp(edm_exposure_id="90001", irp_id="1",
                                 stamp="moved")
    with pytest.raises(StaleSummary):
        request_group_breakout(edm_id, pid, groups, AS_OF,
                               iteration2_db.user_a)
    fake_irp.set_portfolio_stamp(edm_exposure_id="90001", irp_id="1",
                                 stamp=RM_STAMP)
    with pytest.raises(GateRefused, match="unknown state value"):
        request_group_breakout(edm_id, pid, [_group("G", {"state": ["ZZ"]})],
                               AS_OF, iteration2_db.user_a)
    with pytest.raises(GateRefused, match="cart is empty"):
        request_group_breakout(edm_id, pid, [], AS_OF, iteration2_db.user_a)

    assert _group_rows(pid) == []                  # no rows on ANY refusal
    assert _custom_jobs() == []


def test_reconfirm_same_members_adopts_and_dedups(iteration2_db, fake_irp):
    edm_id, pid = _eligible_pair(fake_irp)
    first = request_group_breakout(
        edm_id, pid, [_group("Coastal", {"state": ["TX"], "peril": ["2"]})],
        AS_OF, iteration2_db.user_a)
    execute_command(
        "UPDATE rwb_job SET status_code = 'succeeded' WHERE id = :i",
        {"i": first[0]}, connection="WORKBENCH")
    old_cart = _group_rows(pid)[0]["cart_id"]

    # same members, different label and value order → adopts the row (no
    # duplicate) and the row takes the name as typed (P-22 rev. 2026-08-10)
    second = request_group_breakout(
        edm_id, pid, [_group("Renamed!", {"peril": ["2"], "state": ["TX"]})],
        AS_OF, iteration2_db.user_b)

    rows = _group_rows(pid)
    assert len(rows) == 1                          # one row per member set
    assert rows[0]["label"] == "Renamed!"          # the label as typed (P-24)
    assert rows[0]["name"] == "Renamed!"
    assert rows[0]["number"] == "Renamed!"
    assert rows[0]["cart_id"] != old_cart          # the new cart claimed it
    jobs = _custom_jobs()
    assert len(jobs) == 1                          # the terminal row revived
    assert second == [jobs[0]["id"]]
    assert jobs[0]["status_code"] == "pending"


def test_reconfirm_created_breakout_under_its_own_name(
        iteration2_db, fake_irp):
    """A breakout's own created portfolio never blocks its name — the
    re-confirm heal path (FR-011/FR-019)."""
    fake_irp.selection_by_value = {"TX": [1, 2], "EQ Comm": [1, 2]}
    edm_id, pid, jid = _confirmed_group(fake_irp, iteration2_db)
    assert run_breakout_job(jid, "custom")["status_code"] == "succeeded"   # "Coastal" is now live
    # drain the auto-fired backfill head (FR-013) — while pending it blocks
    # the gate, exactly as a quick breakout's does
    execute_command(
        "UPDATE rwb_job SET status_code = 'succeeded' "
        "WHERE rwb_job_type = 'backfill_edm_detail'",
        {}, connection="WORKBENCH")

    second = request_group_breakout(
        edm_id, pid,
        [_group("Coastal", {"state": ["TX"], "lob": ["EQ Comm"]})],
        AS_OF, iteration2_db.user_b)

    assert second is not None
    assert _group_rows(pid)[0]["name"] == "Coastal"


def test_one_episode_per_portfolio_blocks_both_directions(
        iteration2_db, fake_irp):
    edm_id, pid = _eligible_pair(fake_irp)
    job_ids = request_group_breakout(
        edm_id, pid, [_group("G", {"state": ["TX"]})], AS_OF,
        iteration2_db.user_a)
    assert job_ids

    # a live cart blocks the quick confirm ...
    assert evaluate_gate(edm_id, pid).in_flight == "custom"
    assert breakout_service.request_breakout(
        edm_id, pid, "lob", AS_OF, iteration2_db.user_a) is None
    # ... and a second cart confirm
    assert request_group_breakout(
        edm_id, pid, [_group("H", {"lob": ["EQ Comm"]})], AS_OF,
        iteration2_db.user_a) is None

    # and a live quick job blocks a cart confirm on ANOTHER portfolio's cart
    execute_command(
        "UPDATE rwb_job SET status_code = 'succeeded' "
        "WHERE rwb_job_type = 'run_breakout_custom'",
        {}, connection="WORKBENCH")
    quick = breakout_service.request_breakout(
        edm_id, pid, "lob", AS_OF, iteration2_db.user_a)
    assert quick is not None
    assert request_group_breakout(
        edm_id, pid, [_group("H", {"lob": ["EQ Comm"]})], AS_OF,
        iteration2_db.user_a) is None


# ── the group worker ──────────────────────────────────────────────────────────────

def _confirmed_group(fake_irp, iteration2_db,
                     filters: dict | None = None) -> tuple[str, str, str]:
    """One confirmed single-group cart → (edm_id, portfolio_id, job_id)."""
    edm_id, pid = _eligible_pair(fake_irp)
    job_ids = request_group_breakout(
        edm_id, pid,
        [_group("Coastal", filters or {"state": ["TX"], "lob": ["EQ Comm"]})],
        AS_OF, iteration2_db.user_a)
    return edm_id, pid, job_ids[0]


def test_group_worker_unions_within_and_intersects_across(
        iteration2_db, fake_irp):
    fake_irp.selection_by_value = {"TX": [1, 2, 3], "CA": [7],
                                   "EQ Comm": [2, 3, 4]}
    edm_id, pid, jid = _confirmed_group(
        fake_irp, iteration2_db,
        filters={"state": ["TX", "CA"], "lob": ["EQ Comm"]})

    job = run_breakout_job(jid, "custom")

    assert job["status_code"] == "succeeded"
    out = json.loads(job["output_data"])
    assert (out["planned"], out["created"], out["failed"]) == (1, 1, 0)
    assert out["backfill_enqueued"] is True
    # union within state = {1,2,3,7}; intersect with lob {2,3,4} → {2,3}
    assert fake_irp.created_sub_portfolios[0]["account_ids"] == [2, 3]
    assert fake_irp.created_sub_portfolios[0]["name"] == "Coastal"
    assert fake_irp.created_sub_portfolios[0]["number"] == "Coastal"
    assert fake_irp.created_sub_portfolios[0]["description"] == (
        "Custom breakout Coastal of portfolio usfl_commercial: "
        "lob IN (EQ Comm) AND state IN (CA, TX)")
    rows = _generated_rows(pid)
    assert len(rows) == 1
    row = rows[0]
    assert row["breakout_dimension_code"] == "custom"
    assert row["breakout_value"] == json.loads(
        execute_one("SELECT input_data FROM rwb_job WHERE id = :i",
                    {"i": jid}, connection="WORKBENCH")["input_data"]
        )["group"]["key"]
    assert row["breakout_group_id"] == _group_rows(pid)[0]["id"]
    # one selection read per dimension, each scoped to its own values
    assert [(c["dimension"], c["values"]) for c in fake_irp.selection_calls] \
        == [("lob", ["EQ Comm"]), ("state", ["CA", "TX"])]


def test_group_description_names_perils_by_mnemonic(iteration2_db, fake_irp):
    # D4: the description is what an analyst reads in Risk Modeler, so the
    # peril filter shows WS — while the selection read still runs on the code.
    fake_irp.selection_by_value = {"TX": [1, 2], "2": [2, 3]}
    edm_id, pid, jid = _confirmed_group(
        fake_irp, iteration2_db, filters={"state": ["TX"], "peril": ["2"]})

    assert run_breakout_job(jid, "custom")["status_code"] == "succeeded"

    assert fake_irp.created_sub_portfolios[0]["description"] == (
        "Custom breakout Coastal of portfolio usfl_commercial: "
        "peril IN (WS) AND state IN (TX)")
    assert [(c["dimension"], c["values"]) for c in fake_irp.selection_calls] \
        == [("peril", ["2"]), ("state", ["TX"])]


def test_group_worker_empty_intersection_fails_with_nothing_created(
        iteration2_db, fake_irp):
    fake_irp.selection_by_value = {"TX": [1], "EQ Comm": [2]}
    edm_id, pid, jid = _confirmed_group(fake_irp, iteration2_db)

    job = run_breakout_job(jid, "custom")

    assert job["status_code"] == "failed"
    assert "no account matches every filter" in job["error_detail"]
    out = json.loads(job["output_data"])
    assert (out["failed"], out["backfill_enqueued"]) == (1, False)
    assert fake_irp.created_sub_portfolios == []
    assert _generated_rows(pid) == []


def test_group_worker_selection_read_failure_fails_the_job(
        iteration2_db, fake_irp):
    fake_irp.raise_on_selection_read = True
    edm_id, pid, jid = _confirmed_group(fake_irp, iteration2_db)

    job = run_breakout_job(jid, "custom")

    assert job["status_code"] == "failed"
    assert "account selection failed for lob" in job["error_detail"]
    assert fake_irp.created_sub_portfolios == []
    assert _generated_rows(pid) == []


def test_group_worker_rerun_skips_then_reclaims_after_prune(
        iteration2_db, fake_irp):
    fake_irp.selection_by_value = {"TX": [1, 2], "EQ Comm": [1, 2]}
    edm_id, pid, jid = _confirmed_group(fake_irp, iteration2_db)
    assert run_breakout_job(jid, "custom")["status_code"] == "succeeded"
    first = _generated_rows(pid)[0]

    # idempotent re-run: the live lineage row skips (FR-011)
    job = rerun_breakout_job(jid, "custom")
    assert json.loads(job["output_data"])["skipped_existing"] == 1

    # deleted in RM → prune → re-run reclaims the row in place (T-16)
    fake_irp.taken_portfolio_names.clear()
    portfolio_service.prune_missing(
        edm_id=edm_id, seen=[("1", "usfl_commercial")], now=datetime.utcnow())
    job = rerun_breakout_job(jid, "custom")
    assert json.loads(job["output_data"])["created"] == 1
    rows = _generated_rows(pid)
    assert len(rows) == 1
    assert rows[0]["id"] == first["id"]            # reclaimed, not duplicated
    assert rows[0]["deleted_at"] is None
    assert rows[0]["irp_id"] != first["irp_id"]    # the new RM portfolio
    assert rows[0]["breakout_group_id"] == first["breakout_group_id"]


def test_group_numbers_stay_distinct_past_the_number_cap(
        iteration2_db, fake_irp):
    """Two labels sharing their first 20 characters must not compose one
    portfolio_number — the number is what adoption resolves on (FR-011)."""
    fake_irp.selection_by_value = {"TX": [1, 2], "CA": [3]}
    edm_id, pid = _eligible_pair(fake_irp)
    job_ids = request_group_breakout(
        edm_id, pid,
        [_group("Coastal wind exposure north", {"state": ["TX"]}),
         _group("Coastal wind exposure south", {"state": ["CA"]})],
        AS_OF, iteration2_db.user_a)

    numbers = {r["number"] for r in _group_rows(pid)}
    assert len(numbers) == 2
    assert all(len(n) <= 20 for n in numbers)
    for jid in job_ids:
        assert run_breakout_job(jid, "custom")["status_code"] == "succeeded"

    # the lineage rows go but the Risk Modeler portfolios stay, so each entry
    # takes the create-then-adopt path and must resolve its own number
    execute_command("DELETE FROM irp_portfolio WHERE source_portfolio_id = :s",
                    {"s": pid}, connection="WORKBENCH")
    for jid in job_ids:
        job = rerun_breakout_job(jid, "custom")
        assert job["status_code"] == "succeeded", job["error_detail"]
        assert json.loads(job["output_data"])["adopted"] == 1


def test_group_worker_unusable_group_fails_with_nothing(
        iteration2_db, fake_irp):
    edm_id = mk_edm()
    pid = mk_portfolio(edm_id, summary=GROUP_SUMMARY)
    jid = str(uuid.uuid4())
    execute_command(
        "INSERT INTO rwb_job (id, requestor_type, requestor_id, rwb_job_type, "
        "status_code, input_data, attempt_count, inserted_at, updated_at) "
        "VALUES (:i, 'breakout_group', :r, 'run_breakout_custom', 'pending', "
        ":d, 0, :now, :now)",
        {"i": jid, "r": str(uuid.uuid4()),
         "d": json.dumps({"edm_id": edm_id, "portfolio_id": pid,
                          "dimension": "custom", "group": {"id": "x"}}),
         "now": datetime.utcnow()}, connection="WORKBENCH")
    job = run_breakout_job(jid, "custom")
    assert job["status_code"] == "failed"
    assert "approved breakout unusable" in job["error_detail"]
    assert _generated_rows(pid) == []


def test_load_approved_group_is_strict():
    good = {"group": {"id": "g", "key": "k", "label": "L", "name": "n",
                      "number": "N", "filters": {"state": ["TX"]}}}
    group = load_approved_group(good)
    assert (group.id, group.key, group.filters) == ("g", "k", {"state": ["TX"]})
    for bad in (
        {},
        {"group": "nope"},
        {"group": {"id": "g", "key": "k", "label": "L", "name": "n",
                   "number": "N", "filters": {}}},
        {"group": {"id": "g", "key": "k", "label": "L", "name": "n",
                   "number": "N", "filters": {"state": []}}},
        {"group": {"id": "", "key": "k", "label": "L", "name": "n",
                   "number": "N", "filters": {"state": ["TX"]}}},
    ):
        with pytest.raises(ValueError):
            load_approved_group(bad)


# ── page state: custom flights, cart banner, error lines ─────────────────────────

def test_page_state_custom_flight_and_cart_banner(iteration2_db, fake_irp):
    fake_irp.selection_by_value = {"TX": [1], "EQ Comm": [1], "2": [1]}
    edm_id, pid = _eligible_pair(fake_irp)
    job_ids = request_group_breakout(edm_id, pid, [
        _group("A", {"state": ["TX"]}),
        _group("B", {"lob": ["EQ Comm"]}),
    ], AS_OF, iteration2_db.user_a)

    # both live → one flight for the portfolio: 0 of 2 done
    state = breakout_service.page_state(edm_id)
    assert state.running is True
    flight = state.flights[pid]
    assert (flight.dimension, flight.planned, flight.done) == ("custom", 2, 0)

    # run the first → 1 of 2 done
    assert run_breakout_job(job_ids[0], "custom")["status_code"] == "succeeded"
    flight = breakout_service.page_state(edm_id).flights[pid]
    assert (flight.planned, flight.done) == (2, 1)

    # run the second with an empty selection → failed; both terminal now:
    # the banner aggregates the CART (1 created + 1 failed), errors render
    fake_irp.selection_by_value = {"TX": [1]}
    job = run_breakout_job(job_ids[1], "custom")
    assert job["status_code"] == "failed"
    state = breakout_service.page_state(edm_id)
    assert state.flights == {}
    banner = state.banner
    assert banner is not None
    assert (banner.created, banner.failed, banner.ok) == (1, 1, False)
    assert banner.noun == "custom"
    assert banner.filling_in is True               # the follow-up backfill queued
    lines = state.errors[pid]
    assert len(lines) == 1
    assert lines[0].dimension == "custom"
    assert "no account matches every filter" in lines[0].error


# ── list read model: the group label resolves for custom rows ────────────────────

def test_list_portfolios_resolves_group_label_and_filters(
        iteration2_db, fake_irp):
    fake_irp.selection_by_value = {"TX": [1, 2], "2": [1, 2]}
    edm_id, pid, jid = _confirmed_group(
        fake_irp, iteration2_db, filters={"state": ["TX"], "peril": ["2"]})
    assert run_breakout_job(jid, "custom")["status_code"] == "succeeded"

    rows = portfolio_service.list_portfolios(edm_id=edm_id)
    generated = next(r for r in rows if r.breakout_dimension_code == "custom")
    assert generated.breakout_dimension_label == "Custom group"
    assert generated.breakout_group_label == "Coastal"
    assert generated.breakout_value_label == "Coastal"   # label, never the key
    assert generated.breakout_group_filters == {"peril": ["2"],
                                                "state": ["TX"]}
    assert generated.source_name == "usfl_commercial"
