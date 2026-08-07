"""Unit tests for the ``backfill_edm_detail`` worker (spec 004 US1, T012).

On ``import_edm`` FINISHED the poller enqueues a ``backfill_edm_detail`` head;
the worker fetches the EDM's portfolios + per-portfolio exposure through the
gateway (fake here — Article 12) and idempotently upserts ``irp_portfolio`` rows
with the JSON snapshot + ``as_of`` (R2). Covers: the happy path, the in-place
overwrite on re-run (no duplicate rows under UNIQUE(edm_id, irp_id)), a gateway
failure failing the ``rwb_job`` while the EDM stays ``ready`` and recoverable
(FR-005), per-portfolio failure isolation, and the graceful skip paths.
"""

from __future__ import annotations

import json

from app.poller import run as poller
from app.services import edm_service, portfolio_service, rwb_job_service, treaty_service
from app.workers import package_jobs
from db import execute, execute_command, execute_one

# Real RM /metrics payloads (sandbox-confirmed shape, data-model §2) — stored
# verbatim under the snapshot's "metrics" namespace.
EXPOSURE_A = {
    "totalAccounts": 1120, "totalLocations": 8240, "totalPolicies": 1180,
    "perilsExposed": "WS, EQ",
    "name": "Primary 2026", "number": "Primary 2026",
    "geocodeVersion": "23.0", "hazardVersion": "23.0",
}
EXPOSURE_B = {
    "totalAccounts": 720, "totalLocations": 3900, "totalPolicies": 760,
    "perilsExposed": "WS, FL",
    "name": "Excess 2026", "number": "Excess 2026",
    "geocodeVersion": "23.0", "hazardVersion": "23.0",
}


def _edm_ready(drive, fake, actor, name="EDM") -> str:
    """Import a standalone EDM and drive it to ``ready`` (submit → FINISHED →
    poll). The poller pass also enqueues the ``backfill_edm_detail`` head; the
    caller seeds fake portfolios (before or after — the worker fetches at run
    time) then drains the queue with ``run_pending``. Returns the edm id."""
    res = edm_service.import_edm(name=name, source_file_path=str(drive / "edm1.bak"),
                                 actor_id=actor)
    package_jobs.run_pending(worker_id="w1")  # submit → irp_job(import_edm, QUEUED)
    row = execute_one(
        "SELECT irp_id FROM irp_job WHERE irp_edm_id=:e AND irp_job_type='import_edm'",
        {"e": res.entity_id}, connection="WORKBENCH")
    fake.finish(str(row["irp_id"]))
    poller.poll_once()  # EDM → ready + exposureId; enqueues backfill_edm_detail
    return res.entity_id


def _portfolio_rows(edm_id: str) -> list[dict]:
    return execute(
        "SELECT name, irp_id, exposure_detail, as_of FROM irp_portfolio "
        "WHERE edm_id=:e ORDER BY name",
        {"e": edm_id}, connection="WORKBENCH")


def _backfill_job() -> dict:
    return execute_one(
        "SELECT id, status_code, output_data, error_detail FROM rwb_job "
        "WHERE rwb_job_type='backfill_edm_detail'", {}, connection="WORKBENCH")


def test_backfill_upserts_portfolios_with_snapshot_and_as_of(
        iteration2_db, fake_irp, drive):
    edm_id = _edm_ready(drive, fake_irp, iteration2_db.user_a)
    exposure_id = fake_irp.edm_exposure_id("EDM")
    fake_irp.add_portfolio(edm_exposure_id=exposure_id, irp_id="501",
                           name="Primary 2026", exposure=EXPOSURE_A)
    fake_irp.add_portfolio(edm_exposure_id=exposure_id, irp_id="502",
                           name="Excess 2026", exposure=EXPOSURE_B)

    package_jobs.run_pending(worker_id="w1")  # runs backfill_edm_detail

    rows = _portfolio_rows(edm_id)
    assert [r["name"] for r in rows] == ["Excess 2026", "Primary 2026"]
    assert {r["irp_id"] for r in rows} == {"501", "502"}
    by_irp = {r["irp_id"]: r for r in rows}
    # namespaced snapshot: /metrics verbatim under "metrics"; no DataBridge summary
    # seeded → "summary" is null (cells render "—"), never a stale/absent key
    assert json.loads(by_irp["501"]["exposure_detail"]) == {
        "metrics": EXPOSURE_A, "summary": None}
    assert json.loads(by_irp["502"]["exposure_detail"]) == {
        "metrics": EXPOSURE_B, "summary": None}
    assert all(r["as_of"] is not None for r in rows)
    # the EDM-level last-synced trust signal is stamped on success (FR-052)
    edm = execute_one("SELECT as_of, status FROM irp_edm WHERE id=:i",
                      {"i": edm_id}, connection="WORKBENCH")
    assert edm["as_of"] is not None
    assert edm["status"] == edm_service.READY
    job = _backfill_job()
    assert job["status_code"] == "succeeded"
    assert json.loads(job["output_data"])["portfolios"] == 2


def test_rerun_overwrites_snapshot_in_place_no_duplicates(
        iteration2_db, fake_irp, drive):
    edm_id = _edm_ready(drive, fake_irp, iteration2_db.user_a)
    exposure_id = fake_irp.edm_exposure_id("EDM")
    fake_irp.add_portfolio(edm_exposure_id=exposure_id, irp_id="501",
                           name="Primary 2026", exposure=EXPOSURE_A)
    package_jobs.run_pending(worker_id="w1")
    first = _portfolio_rows(edm_id)
    assert len(first) == 1

    # RM's figures change; a redelivery / reconciler re-run of the SAME job body
    # must overwrite exposure_detail/as_of in place — never insert a duplicate.
    updated = dict(EXPOSURE_A, totalLocations=9999)
    fake_irp._portfolios[str(exposure_id)][0]["exposure"] = updated
    job = _backfill_job()
    package_jobs._backfill_edm_detail_body(job["id"])

    rows = _portfolio_rows(edm_id)
    assert len(rows) == 1  # UNIQUE(edm_id, irp_id) — no duplicate row
    assert json.loads(rows[0]["exposure_detail"])["metrics"]["totalLocations"] == 9999


def test_gateway_failure_fails_job_but_edm_stays_ready_and_recoverable(
        iteration2_db, fake_irp, drive):
    edm_id = _edm_ready(drive, fake_irp, iteration2_db.user_a)
    exposure_id = fake_irp.edm_exposure_id("EDM")
    fake_irp.add_portfolio(edm_exposure_id=exposure_id, irp_id="501",
                           name="Primary 2026", exposure=EXPOSURE_A)
    fake_irp.raise_on_list_portfolios = True

    package_jobs.run_pending(worker_id="w1")

    job = _backfill_job()
    assert job["status_code"] == "failed"
    assert job["error_detail"]
    assert edm_service.get_edm(edm_id).status == edm_service.READY  # FR-005
    assert _portfolio_rows(edm_id) == []

    # Recoverable: a later re-run of the same body (retry machinery) succeeds
    # and populates the snapshot idempotently.
    fake_irp.raise_on_list_portfolios = False
    package_jobs._backfill_edm_detail_body(job["id"])
    assert len(_portfolio_rows(edm_id)) == 1


def test_one_portfolio_exposure_failure_does_not_abort_the_rest(
        iteration2_db, fake_irp, drive):
    edm_id = _edm_ready(drive, fake_irp, iteration2_db.user_a)
    exposure_id = fake_irp.edm_exposure_id("EDM")
    fake_irp.add_portfolio(edm_exposure_id=exposure_id, irp_id="501",
                           name="Primary 2026", exposure=EXPOSURE_A)
    fake_irp.add_portfolio(edm_exposure_id=exposure_id, irp_id="502",
                           name="Excess 2026", exposure=EXPOSURE_B)
    fake_irp.add_portfolio(edm_exposure_id=exposure_id, irp_id="503",
                           name="Facultative 2026")
    fake_irp.fail_exposure_for = {"502"}

    package_jobs.run_pending(worker_id="w1")

    rows = _portfolio_rows(edm_id)
    assert {r["irp_id"] for r in rows} == {"501", "503"}  # 502 skipped, rest stored
    assert edm_service.get_edm(edm_id).status == edm_service.READY
    job = _backfill_job()
    assert job["status_code"] == "succeeded"  # partial snapshot beats none
    out = json.loads(job["output_data"])
    assert out["portfolios"] == 2
    assert out["exposure_failures"] == 1


# ── the DataBridge exposure summary (Addendum A T057) ─────────────────────────────
# One aggregate read per EDM supplies geography/LOB/currency (the RM
# /metrics ceiling carries none of them). Enrichment only: ANY summary failure
# degrades to "summary": null — the job still succeeds and metrics still land.

SUMMARY_A = {
    "portfolio_name": "Primary 2026",
    "currencies": ["USD"],
    "states": ["FL", "LA", "TX"],
    "lines_of_business": ["Commercial"],
}


def test_backfill_merges_databridge_summary_per_portfolio(
        iteration2_db, fake_irp, drive):
    edm_id = _edm_ready(drive, fake_irp, iteration2_db.user_a)
    exposure_id = fake_irp.edm_exposure_id("EDM")
    fake_irp.add_portfolio(edm_exposure_id=exposure_id, irp_id="501",
                           name="Primary 2026", exposure=EXPOSURE_A)
    fake_irp.add_portfolio(edm_exposure_id=exposure_id, irp_id="502",
                           name="Excess 2026", exposure=EXPOSURE_B)
    fake_irp.set_exposure_summary("EDM", {"501": SUMMARY_A})  # 502: no coverage

    package_jobs.run_pending(worker_id="w1")

    by_irp = {r["irp_id"]: r for r in _portfolio_rows(edm_id)}
    assert json.loads(by_irp["501"]["exposure_detail"]) == {
        "metrics": EXPOSURE_A, "summary": SUMMARY_A}
    assert json.loads(by_irp["502"]["exposure_detail"]) == {
        "metrics": EXPOSURE_B, "summary": None}  # uncovered → null, never absent
    assert json.loads(_backfill_job()["output_data"])["summary"] == "ok"


def test_summary_matches_by_name_when_ids_diverge(iteration2_db, fake_irp, drive):
    # The DataBridge aggregate keys on portinfo.PORTINFOID, which is only assumed
    # to equal RM's portfolioId — portfolio_name is the contract's fallback key.
    edm_id = _edm_ready(drive, fake_irp, iteration2_db.user_a)
    exposure_id = fake_irp.edm_exposure_id("EDM")
    fake_irp.add_portfolio(edm_exposure_id=exposure_id, irp_id="501",
                           name="Primary 2026", exposure=EXPOSURE_A)
    fake_irp.set_exposure_summary("EDM", {"9999": SUMMARY_A})  # id mismatch

    package_jobs.run_pending(worker_id="w1")

    rows = _portfolio_rows(edm_id)
    assert json.loads(rows[0]["exposure_detail"])["summary"] == SUMMARY_A


def test_summary_failure_degrades_to_null_and_job_still_succeeds(
        iteration2_db, fake_irp, drive):
    edm_id = _edm_ready(drive, fake_irp, iteration2_db.user_a)
    exposure_id = fake_irp.edm_exposure_id("EDM")
    fake_irp.add_portfolio(edm_exposure_id=exposure_id, irp_id="501",
                           name="Primary 2026", exposure=EXPOSURE_A)
    fake_irp.set_exposure_summary("EDM", {"501": SUMMARY_A})
    fake_irp.raise_on_exposure_summary = True  # missing wheel method / env / SQL

    package_jobs.run_pending(worker_id="w1")

    job = _backfill_job()
    assert job["status_code"] == "succeeded"  # enrichment only — never fails the job
    out = json.loads(job["output_data"])
    assert out["portfolios"] == 1
    assert out["summary"] == "unavailable"
    snap = json.loads(_portfolio_rows(edm_id)[0]["exposure_detail"])
    assert snap["metrics"] == EXPOSURE_A  # metrics landed regardless
    assert snap["summary"] is None


def test_summary_not_fetched_for_zero_portfolio_edm(iteration2_db, fake_irp, drive):
    _edm_ready(drive, fake_irp, iteration2_db.user_a)  # no portfolios seeded
    fake_irp.raise_on_exposure_summary = True  # would raise if called

    package_jobs.run_pending(worker_id="w1")

    job = _backfill_job()
    assert job["status_code"] == "succeeded"
    assert fake_irp.summary_reads == []  # no pointless DataBridge round-trip
    assert "summary" not in json.loads(job["output_data"])


# ── the treaty path (spec 004 US2, T025) ──────────────────────────────────────────
# The same worker, after the portfolio loop, fetches the EDM's treaties
# (search_treaties) and idempotently upserts irp_treaty rows with the full
# attribute map verbatim + as_of (R2). Attribute keys mirror the documented RM
# treaty schema (GET /exposures/{id}/treaties — IRP knowledge base, 2026-07-23).

TREATY_CAT = {
    "treatyId": 1042, "treatyName": "Meridian Property Cat XoL",
    "treatyNumber": "TR-1042", "treatyType": "CATA",
    "attachmentBasis": "L", "attachmentLevel": "PORT",
    "attachmentPoint": 25000000.0, "occurrenceLimit": 100000000.0,
    "percentageRiShare": 20.0, "percentagePlaced": 85.0,
    "premium": 4200000.0, "currency": {"code": "USD"},
    "effectiveDate": "2026-01-01T00:00:00Z", "expirationDate": "2026-12-31T00:00:00Z",
}
TREATY_QS = {
    "treatyId": 1043, "treatyName": "Meridian Quota Share",
    "treatyNumber": "TR-1043", "treatyType": "QUOT",
    "attachmentBasis": "R", "attachmentLevel": "POL",
    "attachmentPoint": 0.0, "riskLimit": 10000000.0,
    "percentageRiShare": 40.0, "currency": {"code": "USD"},
}


def _treaty_rows(edm_id: str) -> list[dict]:
    return execute(
        "SELECT name, irp_id, attributes, as_of FROM irp_treaty "
        "WHERE edm_id=:e ORDER BY name",
        {"e": edm_id}, connection="WORKBENCH")


def test_backfill_upserts_treaties_with_snapshot_and_as_of(
        iteration2_db, fake_irp, drive):
    edm_id = _edm_ready(drive, fake_irp, iteration2_db.user_a)
    exposure_id = fake_irp.edm_exposure_id("EDM")
    fake_irp.add_treaty(edm_exposure_id=exposure_id, irp_id="1042",
                        name="Meridian Property Cat XoL", attributes=TREATY_CAT)
    fake_irp.add_treaty(edm_exposure_id=exposure_id, irp_id="1043",
                        name="Meridian Quota Share", attributes=TREATY_QS)

    package_jobs.run_pending(worker_id="w1")  # runs backfill_edm_detail

    rows = _treaty_rows(edm_id)
    assert [r["name"] for r in rows] == [
        "Meridian Property Cat XoL", "Meridian Quota Share"]
    by_irp = {r["irp_id"]: r for r in rows}
    assert json.loads(by_irp["1042"]["attributes"]) == TREATY_CAT  # verbatim (R2)
    assert json.loads(by_irp["1043"]["attributes"]) == TREATY_QS
    assert all(r["as_of"] is not None for r in rows)
    job = _backfill_job()
    assert job["status_code"] == "succeeded"
    assert json.loads(job["output_data"])["treaties"] == 2


def test_treaty_rerun_overwrites_in_place_no_duplicates(
        iteration2_db, fake_irp, drive):
    edm_id = _edm_ready(drive, fake_irp, iteration2_db.user_a)
    exposure_id = fake_irp.edm_exposure_id("EDM")
    fake_irp.add_treaty(edm_exposure_id=exposure_id, irp_id="1042",
                        name="Meridian Property Cat XoL", attributes=TREATY_CAT)
    package_jobs.run_pending(worker_id="w1")
    assert len(_treaty_rows(edm_id)) == 1

    # RM's attributes change; a redelivery / reconciler re-run of the SAME job
    # body must overwrite attributes/as_of in place — never insert a duplicate.
    updated = dict(TREATY_CAT, occurrenceLimit=150000000.0)
    fake_irp._treaties[str(exposure_id)][0]["attributes"] = updated
    package_jobs._backfill_edm_detail_body(_backfill_job()["id"])

    rows = _treaty_rows(edm_id)
    assert len(rows) == 1  # UNIQUE(edm_id, irp_id) — no duplicate row
    assert json.loads(rows[0]["attributes"])["occurrenceLimit"] == 150000000.0


def test_treaty_enumeration_failure_fails_job_but_keeps_portfolios(
        iteration2_db, fake_irp, drive):
    # Treaties are fetched AFTER the portfolio loop: an enumeration failure fails
    # the rwb_job (recoverable) but the portfolio snapshots already written stay,
    # and the EDM's ready status is never touched (FR-005).
    edm_id = _edm_ready(drive, fake_irp, iteration2_db.user_a)
    exposure_id = fake_irp.edm_exposure_id("EDM")
    fake_irp.add_portfolio(edm_exposure_id=exposure_id, irp_id="501",
                           name="Primary 2026", exposure=EXPOSURE_A)
    fake_irp.add_treaty(edm_exposure_id=exposure_id, irp_id="1042",
                        name="Meridian Property Cat XoL", attributes=TREATY_CAT)
    fake_irp.raise_on_search_treaties = True

    package_jobs.run_pending(worker_id="w1")

    job = _backfill_job()
    assert job["status_code"] == "failed"
    assert edm_service.get_edm(edm_id).status == edm_service.READY
    assert len(_portfolio_rows(edm_id)) == 1  # portfolios landed before the failure
    assert _treaty_rows(edm_id) == []

    # Recoverable: a re-run of the same body completes the treaty half.
    fake_irp.raise_on_search_treaties = False
    package_jobs._backfill_edm_detail_body(job["id"])
    assert len(_treaty_rows(edm_id)) == 1


def test_malformed_stored_snapshot_renders_empty_not_error(
        iteration2_db, fake_irp, drive):
    # The read models' whole defensive-parse contract: a corrupted stored
    # snapshot degrades to the graceful empty state (detail None), never an
    # exception on the page render.
    edm_id = _edm_ready(drive, fake_irp, iteration2_db.user_a)
    exposure_id = fake_irp.edm_exposure_id("EDM")
    fake_irp.add_portfolio(edm_exposure_id=exposure_id, irp_id="501",
                           name="Primary 2026", exposure=EXPOSURE_A)
    fake_irp.add_treaty(edm_exposure_id=exposure_id, irp_id="1042",
                        name="Cat XoL", attributes=TREATY_CAT)
    package_jobs.run_pending(worker_id="w1")
    execute_command("UPDATE irp_portfolio SET exposure_detail = 'not json'",
                    {}, connection="WORKBENCH")
    execute_command("UPDATE irp_treaty SET attributes = '{'",
                    {}, connection="WORKBENCH")

    portfolios = portfolio_service.list_portfolios(edm_id=edm_id)
    treaties = treaty_service.list_treaties(edm_id=edm_id)
    assert [p.exposure_detail for p in portfolios] == [None]
    assert [t.attributes for t in treaties] == [None]
    # ... and the derived layers stay graceful too
    assert portfolio_service.aggregate_exposure(portfolios) is None
    assert treaties[0].attribute_items() == []


# ── stale-row pruning (sync reconciles the row set against RM) ─────────────────────
# A successful enumeration is the full truth: rows RM no longer returns are
# soft-deleted (a deleted portfolio/treaty must not keep rendering under a
# fresh-looking as_of), a re-created entity resurrects its row, and a FAILED
# enumeration never prunes anything.


def test_sync_prunes_rows_rm_no_longer_returns(iteration2_db, fake_irp, drive):
    edm_id = _edm_ready(drive, fake_irp, iteration2_db.user_a)
    exposure_id = fake_irp.edm_exposure_id("EDM")
    fake_irp.add_portfolio(edm_exposure_id=exposure_id, irp_id="501",
                           name="Primary 2026", exposure=EXPOSURE_A)
    fake_irp.add_portfolio(edm_exposure_id=exposure_id, irp_id="502",
                           name="Excess 2026", exposure=EXPOSURE_B)
    fake_irp.add_treaty(edm_exposure_id=exposure_id, irp_id="1042",
                        name="Cat XoL", attributes=TREATY_CAT)
    fake_irp.add_treaty(edm_exposure_id=exposure_id, irp_id="1043",
                        name="Quota Share", attributes=TREATY_QS)
    package_jobs.run_pending(worker_id="w1")
    assert len(_portfolio_rows(edm_id)) == 2 and len(_treaty_rows(edm_id)) == 2

    # The analyst deletes one of each in RM; the next sync reconciles.
    fake_irp._portfolios[str(exposure_id)] = [
        p for p in fake_irp._portfolios[str(exposure_id)] if p["irp_id"] != "502"]
    fake_irp._treaties[str(exposure_id)] = [
        t for t in fake_irp._treaties[str(exposure_id)] if t["irp_id"] != "1043"]
    result = package_jobs._backfill_edm_detail_body(_backfill_job()["id"])

    assert [p.irp_id
            for p in portfolio_service.list_portfolios(edm_id=edm_id)] == ["501"]
    assert [t.irp_id
            for t in treaty_service.list_treaties(edm_id=edm_id)] == ["1042"]
    assert result.output["pruned_portfolios"] == 1
    assert result.output["pruned_treaties"] == 1
    # Soft delete — the rows survive with deleted_at stamped, never hard-deleted.
    dead = execute(
        "SELECT irp_id FROM irp_portfolio WHERE edm_id=:e AND deleted_at IS NOT NULL",
        {"e": edm_id}, connection="WORKBENCH")
    assert [r["irp_id"] for r in dead] == ["502"]

    # RM empties out entirely → every remaining row is pruned.
    fake_irp._portfolios[str(exposure_id)] = []
    fake_irp._treaties[str(exposure_id)] = []
    package_jobs._backfill_edm_detail_body(_backfill_job()["id"])
    assert portfolio_service.list_portfolios(edm_id=edm_id) == []
    assert treaty_service.list_treaties(edm_id=edm_id) == []


def test_pruned_portfolio_resurrects_when_recreated_in_rm(
        iteration2_db, fake_irp, drive):
    edm_id = _edm_ready(drive, fake_irp, iteration2_db.user_a)
    exposure_id = fake_irp.edm_exposure_id("EDM")
    fake_irp.add_portfolio(edm_exposure_id=exposure_id, irp_id="501",
                           name="Primary 2026", exposure=EXPOSURE_A)
    package_jobs.run_pending(worker_id="w1")
    fake_irp._portfolios[str(exposure_id)] = []
    package_jobs._backfill_edm_detail_body(_backfill_job()["id"])
    assert portfolio_service.list_portfolios(edm_id=edm_id) == []

    # Re-created in RM under the same name (a new RM id): the SAME row comes
    # back — resurrect clears deleted_at and the (edm_id, name) fallback
    # backfills the new irp_id — never a duplicate.
    fake_irp.add_portfolio(edm_exposure_id=exposure_id, irp_id="601",
                           name="Primary 2026", exposure=EXPOSURE_B)
    package_jobs._backfill_edm_detail_body(_backfill_job()["id"])

    rows = portfolio_service.list_portfolios(edm_id=edm_id)
    assert [(p.name, p.irp_id) for p in rows] == [("Primary 2026", "601")]
    assert rows[0].exposure_detail == {"metrics": EXPOSURE_B, "summary": None}
    assert len(_portfolio_rows(edm_id)) == 1  # resurrected in place, no dupe


def test_enumerated_portfolio_with_failed_exposure_read_is_not_pruned(
        iteration2_db, fake_irp, drive):
    # Existence comes from the enumeration, not the per-portfolio detail read: a
    # portfolio whose exposure read fails on a re-sync keeps its row AND its
    # prior good snapshot.
    edm_id = _edm_ready(drive, fake_irp, iteration2_db.user_a)
    exposure_id = fake_irp.edm_exposure_id("EDM")
    fake_irp.add_portfolio(edm_exposure_id=exposure_id, irp_id="501",
                           name="Primary 2026", exposure=EXPOSURE_A)
    fake_irp.add_portfolio(edm_exposure_id=exposure_id, irp_id="502",
                           name="Excess 2026", exposure=EXPOSURE_B)
    package_jobs.run_pending(worker_id="w1")

    fake_irp.fail_exposure_for = {"502"}
    result = package_jobs._backfill_edm_detail_body(_backfill_job()["id"])

    assert result.status == "succeeded"
    by_irp = {p.irp_id: p for p in portfolio_service.list_portfolios(edm_id=edm_id)}
    assert set(by_irp) == {"501", "502"}  # 502 enumerated → kept
    assert by_irp["502"].exposure_detail == {"metrics": EXPOSURE_B, "summary": None}


def test_failed_enumeration_never_prunes_existing_rows(
        iteration2_db, fake_irp, drive):
    edm_id = _edm_ready(drive, fake_irp, iteration2_db.user_a)
    exposure_id = fake_irp.edm_exposure_id("EDM")
    fake_irp.add_portfolio(edm_exposure_id=exposure_id, irp_id="501",
                           name="Primary 2026", exposure=EXPOSURE_A)
    fake_irp.add_treaty(edm_exposure_id=exposure_id, irp_id="1042",
                        name="Cat XoL", attributes=TREATY_CAT)
    package_jobs.run_pending(worker_id="w1")

    fake_irp.raise_on_list_portfolios = True
    package_jobs._backfill_edm_detail_body(_backfill_job()["id"])  # fails early
    assert len(portfolio_service.list_portfolios(edm_id=edm_id)) == 1
    assert len(treaty_service.list_treaties(edm_id=edm_id)) == 1

    fake_irp.raise_on_list_portfolios = False
    fake_irp.raise_on_search_treaties = True
    package_jobs._backfill_edm_detail_body(_backfill_job()["id"])  # treaty half fails
    assert len(treaty_service.list_treaties(edm_id=edm_id)) == 1


def test_missing_edm_and_no_irp_id_skip_gracefully(iteration2_db, fake_irp, drive):
    # An EDM that never finished importing has no irp_id (exposureId) — there is
    # nothing to fetch; the job succeeds as a skip and writes nothing (R7).
    res = edm_service.import_edm(name="EDM", source_file_path=str(drive / "edm1.bak"),
                                 actor_id=iteration2_db.user_a)
    job_id = rwb_job_service.enqueue_rwb_job(
        requestor_type="analyst_request", requestor_id=res.entity_id,
        rwb_job_type="backfill_edm_detail", input_data={"edm_id": res.entity_id})
    package_jobs.run_one(rwb_job_id=job_id, rwb_job_type="backfill_edm_detail")
    assert _portfolio_rows(res.entity_id) == []
    job = execute_one("SELECT status_code FROM rwb_job WHERE id=:i",
                      {"i": job_id}, connection="WORKBENCH")
    assert job["status_code"] == "succeeded"
