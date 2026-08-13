"""Unit tests for adopting EDMs that already live in Risk Modeler.

``list_adoptable_edms`` diffs Risk Modeler's exposures list against ``irp_edm``;
``adopt_edms`` inserts a ``ready`` row per selected exposureId — no
``source_file_path``, no package, no import submit — and enqueues one
``backfill_edm_detail`` head each, which is what fetches the portfolios, their
exposure figures, and the treaties.

The diff has two arms because a live ``irp_edm`` row does not always carry an
exposureId: exposureId when it has one, name when it does not (still importing,
or the poller's by-name resolution missed and the row reached ``ready`` anyway).
"""

from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError

from app.services import edm_service
from app.workers import dispatch
from db import execute, execute_command, execute_one


def _local_edm(*, name: str, status: str, irp_id=None, deleted_at=None) -> str:
    """An ``irp_edm`` row in an arbitrary state — the diff's left-hand side."""
    eid = str(uuid.uuid4())
    execute_command(
        "INSERT INTO irp_edm (id, name, status, irp_id, deleted_at, "
        "inserted_at, updated_at) "
        "VALUES (:i, :n, :s, :x, :d, '2026-01-01', '2026-01-01')",
        {"i": eid, "n": name, "s": status, "x": irp_id, "d": deleted_at},
        connection="WORKBENCH")
    return eid


def _adoptable_names(fake) -> list[str]:
    return [a.name for a in edm_service.list_adoptable_edms().rows]


def _backfill_heads(edm_id: str) -> list[dict]:
    return execute(
        "SELECT id, status_code FROM rwb_job WHERE requestor_type='analyst_request' "
        "AND requestor_id=:r AND rwb_job_type='backfill_edm_detail'",
        {"r": edm_id}, connection="WORKBENCH")


# ── the diff predicate ────────────────────────────────────────────────────────────

def test_lists_every_rm_edm_the_workbench_does_not_have(iteration2_db, fake_irp):
    fake_irp.add_catalog_edm(name="alpha", irp_id=501)
    fake_irp.add_catalog_edm(name="beta", irp_id=502)

    rows = edm_service.list_adoptable_edms().rows

    assert [(r.name, r.irp_id) for r in rows] == [("alpha", 501), ("beta", 502)]


def test_carries_the_display_fields_from_risk_modeler(iteration2_db, fake_irp):
    fake_irp.add_catalog_edm(
        name="alpha", irp_id=501, status="READY", server_name="databridge-2",
        portfolio_count=4, treaty_count=2, updated_at="2026-08-01T00:00:00Z")

    row = edm_service.list_adoptable_edms().rows[0]

    assert (row.status, row.server_name) == ("READY", "databridge-2")
    assert (row.portfolio_count, row.treaty_count) == (4, 2)
    assert row.updated_at == "2026-08-01T00:00:00Z"


def test_links_each_row_to_its_risk_modeler_portfolios_screen(
        iteration2_db, fake_irp, monkeypatch):
    monkeypatch.setattr(edm_service.settings, "risk_modeler_base_url",
                        "https://api-euw1.rms-ppe.com")
    monkeypatch.setattr(edm_service.settings, "risk_modeler_tenant_name", "acme")
    fake_irp.add_catalog_edm(name="a name/with slash", irp_id=501)

    assert edm_service.list_adoptable_edms().rows[0].rm_url == (
        "https://acme.rms-ppe.com/riskmodeler/datasources/"
        "a%20name%2Fwith%20slash/portfolios")


def test_excludes_an_edm_whose_exposure_id_a_local_row_holds(
        iteration2_db, fake_irp):
    fake_irp.add_catalog_edm(name="alpha", irp_id=501)
    fake_irp.add_catalog_edm(name="beta", irp_id=502)
    _local_edm(name="alpha", status=edm_service.READY, irp_id=501)

    assert _adoptable_names(fake_irp) == ["beta"]


def test_excludes_an_edm_matching_an_in_flight_import_by_name(
        iteration2_db, fake_irp):
    # pending_import / importing rows have irp_id NULL until the poller
    # backfills the exposureId, so only the name arm can hide them.
    fake_irp.add_catalog_edm(name="alpha", irp_id=501)
    fake_irp.add_catalog_edm(name="beta", irp_id=502)
    _local_edm(name="alpha", status=edm_service.PENDING)
    _local_edm(name="beta", status=edm_service.IMPORTING)

    assert _adoptable_names(fake_irp) == []


def test_excludes_a_ready_row_whose_exposure_id_resolution_missed(
        iteration2_db, fake_irp):
    # The poller passes resolved.get("edm_exposure_id"), which can be None while
    # the status still goes ready — the row exists, it just has no exposureId.
    fake_irp.add_catalog_edm(name="alpha", irp_id=501)
    _local_edm(name="alpha", status=edm_service.READY, irp_id=None)

    assert _adoptable_names(fake_irp) == []


def test_includes_an_edm_whose_only_local_row_failed_to_import(
        iteration2_db, fake_irp):
    # A failed import created nothing in Risk Modeler, so an EDM there under the
    # same name is a different one and stays adoptable.
    fake_irp.add_catalog_edm(name="alpha", irp_id=501)
    _local_edm(name="alpha", status=edm_service.ERROR)

    assert _adoptable_names(fake_irp) == ["alpha"]


def test_includes_an_edm_whose_only_local_row_is_soft_deleted(
        iteration2_db, fake_irp):
    fake_irp.add_catalog_edm(name="alpha", irp_id=501)
    _local_edm(name="alpha", status=edm_service.READY, irp_id=501,
               deleted_at="2026-02-01")

    assert _adoptable_names(fake_irp) == ["alpha"]


def test_a_duplicate_rm_name_stays_adoptable_once_its_twin_is_adopted(
        iteration2_db, fake_irp):
    # EDM names are not unique in Risk Modeler. The name arm applies only to rows
    # with no exposureId, so adopting one "alpha" must not hide the other.
    fake_irp.add_catalog_edm(name="alpha", irp_id=501)
    fake_irp.add_catalog_edm(name="alpha", irp_id=502)
    _local_edm(name="alpha", status=edm_service.READY, irp_id=501)

    assert [(r.name, r.irp_id) for r in edm_service.list_adoptable_edms().rows] == [
        ("alpha", 502)]


def test_a_gateway_failure_lists_none_not_an_empty_page(iteration2_db, fake_irp):
    # None is what the page renders as "Risk Modeler unavailable". An empty page
    # would read as "everything is already synced".
    fake_irp.add_catalog_edm(name="alpha", irp_id=501)
    fake_irp.raise_on_list_edms = True

    assert edm_service.list_adoptable_edms() is None


# ── paging and the name search ───────────────────────────────────────────────────

def _seed_catalog(fake, count: int) -> None:
    for i in range(count):
        fake.add_catalog_edm(name=f"edm_{i:03d}", irp_id=1000 + i)


def test_first_page_holds_page_size_rows_and_reports_the_full_total(
        iteration2_db, fake_irp):
    _seed_catalog(fake_irp, edm_service.ADOPTABLE_PAGE_SIZE + 10)

    result = edm_service.list_adoptable_edms()

    assert len(result.rows) == edm_service.ADOPTABLE_PAGE_SIZE
    assert result.rows[0].name == "edm_000"
    assert (result.page, result.has_next) == (1, True)
    # total counts everything the diff left, not just this page — the pager and
    # the "N EDMs are not in the workbench" line both read it.
    assert result.total == edm_service.ADOPTABLE_PAGE_SIZE + 10


def test_last_page_holds_the_remainder_and_ends_the_pager(
        iteration2_db, fake_irp):
    _seed_catalog(fake_irp, edm_service.ADOPTABLE_PAGE_SIZE + 10)

    result = edm_service.list_adoptable_edms(page=2)

    assert len(result.rows) == 10
    assert result.rows[0].name == f"edm_{edm_service.ADOPTABLE_PAGE_SIZE:03d}"
    assert result.has_next is False


def test_a_page_past_the_end_reads_the_last_page_with_rows(
        iteration2_db, fake_irp):
    # Another analyst syncing the tail of the list must not leave the analyst on
    # page 2 staring at an empty table.
    _seed_catalog(fake_irp, edm_service.ADOPTABLE_PAGE_SIZE + 10)

    result = edm_service.list_adoptable_edms(page=9)

    assert (result.page, result.has_next) == (2, False)
    assert len(result.rows) == 10


def test_a_page_below_one_reads_the_first_page(iteration2_db, fake_irp):
    # A hand-typed ?page=0 must not slice from a negative offset.
    _seed_catalog(fake_irp, 3)

    assert edm_service.list_adoptable_edms(page=0).page == 1
    assert edm_service.list_adoptable_edms(page=-5).rows[0].name == "edm_000"


def test_the_name_search_narrows_the_list_before_it_is_paged(
        iteration2_db, fake_irp):
    fake_irp.add_catalog_edm(name="Coastal_Wind_Study", irp_id=501)
    fake_irp.add_catalog_edm(name="Midwest_Hail_2025", irp_id=502)
    fake_irp.add_catalog_edm(name="coastal_flood", irp_id=503)

    result = edm_service.list_adoptable_edms(name="COASTAL")

    # Case-insensitive substring, and the case-insensitive sort interleaves the
    # two spellings. total reflects the filtered list, so the pager does not
    # offer pages the search already emptied.
    assert [r.name for r in result.rows] == ["coastal_flood", "Coastal_Wind_Study"]
    assert result.total == 2


# ── adopt_edms ───────────────────────────────────────────────────────────────────

def test_adopt_inserts_a_ready_row_with_no_source_file_or_package(
        iteration2_db, fake_irp):
    fake_irp.add_catalog_edm(name="alpha", irp_id=501, server_name="databridge-2")

    result = edm_service.adopt_edms(irp_ids=[501], actor_id=iteration2_db.user_a)

    assert len(result.adopted) == 1
    row = execute_one(
        "SELECT name, irp_id, status, server_name, source_file_path, package_id, "
        "as_of, created_by_irp_job_irp_id, inserted_by FROM irp_edm WHERE id = :i",
        {"i": result.adopted[0]}, connection="WORKBENCH")
    assert row["name"] == "alpha"
    assert row["irp_id"] == 501
    assert row["status"] == edm_service.READY
    assert row["server_name"] == "databridge-2"
    assert row["source_file_path"] is None
    assert row["package_id"] is None
    # backfill_edm_detail stamps as_of; there is no creating import job.
    assert row["as_of"] is None
    assert row["created_by_irp_job_irp_id"] is None
    assert str(row["inserted_by"]) == str(iteration2_db.user_a)


def test_adopt_enqueues_and_dispatches_one_backfill_head_per_edm(
        iteration2_db, fake_irp):
    fake_irp.add_catalog_edm(name="alpha", irp_id=501)
    fake_irp.add_catalog_edm(name="beta", irp_id=502)

    sent: list[tuple[str, str]] = []
    dispatch.configure(lambda *, rwb_job_id, rwb_job_type: sent.append(
        (rwb_job_id, rwb_job_type)))
    try:
        result = edm_service.adopt_edms(irp_ids=[501, 502],
                                        actor_id=iteration2_db.user_a)
    finally:
        dispatch.reset()

    assert len(result.adopted) == 2
    assert [t for _, t in sent] == ["backfill_edm_detail"] * 2
    for edm_id in result.adopted:
        heads = _backfill_heads(edm_id)
        assert len(heads) == 1
        assert heads[0]["status_code"] == "pending"


def test_adopting_the_same_exposure_id_twice_creates_one_row(
        iteration2_db, fake_irp):
    fake_irp.add_catalog_edm(name="alpha", irp_id=501)

    first = edm_service.adopt_edms(irp_ids=[501], actor_id=iteration2_db.user_a)
    second = edm_service.adopt_edms(irp_ids=[501], actor_id=iteration2_db.user_b)

    assert len(first.adopted) == 1
    assert second.adopted == []
    assert second.skipped == [501]
    rows = execute("SELECT id FROM irp_edm WHERE irp_id = 501",
                   connection="WORKBENCH")
    assert len(rows) == 1


def test_adopt_treats_a_unique_race_as_skipped(
        iteration2_db, fake_irp, monkeypatch):
    fake_irp.add_catalog_edm(name="alpha", irp_id=501)
    execute_real = edm_service.execute_command

    def lose_insert(sql, *args, **kwargs):
        if "INSERT INTO irp_edm" in sql:
            raise IntegrityError("duplicate", {}, Exception("duplicate"))
        return execute_real(sql, *args, **kwargs)

    monkeypatch.setattr(edm_service, "execute_command", lose_insert)

    result = edm_service.adopt_edms(
        irp_ids=[501], actor_id=iteration2_db.user_a)

    assert result.adopted == []
    assert result.skipped == [501]


def test_adopt_skips_an_edm_an_in_flight_import_already_covers(
        iteration2_db, fake_irp):
    # The list hides this EDM by name, but a page loaded before the import started
    # can still POST it. Without the name arm on the insert guard the workbench
    # ends up with two rows for exposureId 501 once the poller resolves the id.
    fake_irp.add_catalog_edm(name="alpha", irp_id=501)
    _local_edm(name="alpha", status=edm_service.IMPORTING)

    result = edm_service.adopt_edms(irp_ids=[501], actor_id=iteration2_db.user_a)

    assert (result.adopted, result.skipped) == ([], [501])
    rows = execute("SELECT id FROM irp_edm WHERE name = 'alpha'",
                   connection="WORKBENCH")
    assert len(rows) == 1


def test_adopt_skips_an_exposure_id_risk_modeler_no_longer_lists(
        iteration2_db, fake_irp):
    fake_irp.add_catalog_edm(name="alpha", irp_id=501)

    result = edm_service.adopt_edms(irp_ids=[501, 999],
                                    actor_id=iteration2_db.user_a)

    assert len(result.adopted) == 1
    assert result.skipped == [999]


def test_an_adopted_edm_drops_off_the_adoptable_list(iteration2_db, fake_irp):
    fake_irp.add_catalog_edm(name="alpha", irp_id=501)
    fake_irp.add_catalog_edm(name="beta", irp_id=502)

    edm_service.adopt_edms(irp_ids=[501], actor_id=iteration2_db.user_a)

    assert _adoptable_names(fake_irp) == ["beta"]
