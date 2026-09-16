"""Unit tests for the treaty-level (TY) export (spec 016): the treaties the
results retrieval records, TY in the perspective intersection, the treaty
request, the split of a TY archive into one manifest row per treaty with the
P-11 combination, the per-row load, Retry by manifest row, and both screens.

Harness: the SQLite WORKBENCH and LOSS mirrors, ``fake_irp``, the fixture
archive of tests/unit/export_archive.py, and the TestClient of
tests/unit/export_client.py.
"""

from __future__ import annotations

import json
import uuid
from datetime import date

import pytest
from starlette.testclient import TestClient

from app.config import settings
from app.poller import run as poller
from app.services import export_service as svc
from app.workers import export_jobs
from db import elt, execute, execute_command, execute_one
from tests.unit.export_archive import TY_COLUMNS, build_archive, sqlite_upload_parquet
from tests.unit.export_client import csrf, make_client, make_deal
from tests.unit.export_rows import (
    manifest_row,
    seed_analysis,
    seed_client,
    seed_edm_for,
    seed_manifest,
    seed_submission,
)
from tests.unit.test_analysis_jobs_worker import (
    _run_retrieval,
    _seed_finished_analysis,
    _stored_extract,
)

TREATIES = (("33833", "PR1", "PR1"), ("33832", "PR2", "Layer two"))


# ── results retrieval records the applied treaties (T-04) ────────────────────


def test_retrieval_stores_the_applied_treaties(iteration2_db, fake_irp):
    analysis_id = _seed_finished_analysis()
    fake_irp.set_analysis_treaties("9001", [
        {"treatyId": 33833, "treatyNumber": "PR1", "treatyName": "PR1", "cedant": "x"},
        {"treatyId": 33832, "treatyNumber": "PR2", "treatyName": "Layer two"}])

    job = _run_retrieval(analysis_id)

    assert job["status_code"] == "succeeded"
    assert _stored_extract(analysis_id)["treaties"] == [
        {"treaty_id": "33833", "treaty_number": "PR1", "treaty_name": "PR1"},
        {"treaty_id": "33832", "treaty_number": "PR2", "treaty_name": "Layer two"}]
    assert fake_irp.treaty_calls == ["9001"]


def test_retrieval_without_treaties_stores_an_empty_list(iteration2_db, fake_irp):
    analysis_id = _seed_finished_analysis()
    _run_retrieval(analysis_id)
    assert _stored_extract(analysis_id)["treaties"] == []


def test_retrieval_fails_when_the_treaties_read_raises(iteration2_db, fake_irp):
    analysis_id = _seed_finished_analysis()
    fake_irp.raise_on_analysis_treaties = True

    job = _run_retrieval(analysis_id)

    assert job["status_code"] == "failed"
    assert job["error_detail"].startswith("treaties read failed:")
    assert _stored_extract(analysis_id) is None  # no partial write


# ── the form: TY in the intersection (FR-001, FR-002, FR-016) ────────────────


@pytest.fixture()
def deal(iteration2_db, loss_db):
    """Two own analyses: ``a`` run without treaties, ``c`` with two."""
    submission_id = seed_submission(iteration2_db.user_a)
    edm_id = seed_edm_for(submission_id)
    a = seed_analysis(edm_id=edm_id, name="A", full_name="A long", irp_id="41958",
                      irp_app_analysis_id="41958", perspectives=("GU", "GR", "RL"))
    c = seed_analysis(edm_id=edm_id, name="C", full_name="C long", irp_id="41960",
                      irp_app_analysis_id="41960", perspectives=("GU", "GR"), treaties=TREATIES)
    seed_client(1, "Example Re")
    return {"submission_id": submission_id, "edm_id": edm_id, "a": a, "c": c}


def _create(deal, analysis_ids, perspective="TY", **overrides):
    kwargs = dict(submission_id=deal["submission_id"], user_email="analyst.a@example.com",
                  analysis_ids=analysis_ids, perspective_code=perspective, client_id=1,
                  treaty_incept=date(2026, 4, 1), crm_id="CRM-1",
                  data_vintage=date(2025, 12, 31), data_names=None)
    kwargs.update(overrides)
    return svc.create_export(**kwargs)


def test_ty_is_offered_only_when_every_selected_analysis_ran_with_treaties(deal):
    rows = {r.id: r for r in svc.list_exportable_analyses(deal["submission_id"])}

    assert rows[deal["c"]].perspectives == ["GU", "GR", "TY"]
    assert "TY" not in rows[deal["a"]].perspectives
    assert svc.perspective_choices([rows[deal["c"]]]) == ["GU", "GR", "TY"]
    assert svc.perspective_choices([rows[deal["c"]], rows[deal["a"]]]) == ["GU", "GR"]


def test_create_export_refuses_ty_for_an_analysis_without_treaties(deal):
    with pytest.raises(svc.ExportValidationError) as exc:
        _create(deal, [deal["a"], deal["c"]])
    assert str(exc.value) == "A long was not run with treaties."

    export_id = _create(deal, [deal["c"]])
    rows = execute("SELECT perspective_code, treaty_number FROM stage.rwb_loss_result_manifest "
                   "WHERE export_id = :e", {"e": export_id}, connection="LOSS")
    assert [(r["perspective_code"], r["treaty_number"]) for r in rows] == [("TY", None)]


def test_detail_rows_carry_the_treaty_and_its_own_aal_at_ty(deal):
    export_id = str(uuid.uuid4())
    seed_manifest(export_id=export_id, submission_id=deal["submission_id"],
                  irp_analysis_id=deal["c"], perspective_code="TY", stage_status="staged",
                  load_status="loaded", data_id=7, treaty_number="PR2",
                  treaty_name="Layer two", treaty_ids="33832,44832", aal=0.61)
    seed_manifest(export_id=export_id, submission_id=deal["submission_id"],
                  irp_analysis_id=deal["c"], perspective_code="TY", stage_status="staged",
                  load_status="loaded", data_id=8, treaty_number="PR1", treaty_name="PR1")

    detail = svc.get_export_detail(deal["submission_id"], export_id)

    assert [a.treaty_label for a in detail.analyses] == ["PR1", "PR2 · Layer two"]
    second = detail.analyses[1]
    assert second.treaty_ids == ["33832", "44832"] and second.aal == 0.61
    assert detail.analyses[0].aal is None  # the row's own value, never the analysis's
    assert svc.list_exports(deal["submission_id"])[0].data_set_count == 2


def test_find_exported_counts_one_export_for_two_treaty_rows(deal):
    first = str(uuid.uuid4())
    for number in ("PR1", "PR2"):
        seed_manifest(export_id=first, submission_id=deal["submission_id"],
                      irp_analysis_id=deal["c"], irp_app_analysis_id=41960,
                      perspective_code="TY", treaty_number=number, treaty_name=number,
                      requested_at="2026-09-08 14:02:00")
    seed_manifest(submission_id=deal["submission_id"], irp_analysis_id=deal["c"],
                  irp_app_analysis_id=41960, perspective_code="TY",
                  requested_at="2026-09-10 08:00:00")

    mark = svc.find_exported([41960], "TY")[41960]

    assert mark.earlier_count == 2
    assert mark.requested_at == "2026-09-10 08:00:00"


# ── the submit worker (T-01) ─────────────────────────────────────────────────


def test_a_ty_row_requests_the_treaty_output_level_with_the_fixed_perspective(deal, fake_irp):
    _create(deal, [deal["c"]])

    export_jobs.run_pending(worker_id="w1")

    assert fake_irp.export_submits[0]["loss_details"] == [{
        "metricType": "LOSS_TABLES", "outputLevels": ["Treaty"],
        "perspectiveCodes": [export_jobs.TY_REQUEST_PERSPECTIVE_CODE]}]
    assert export_jobs.TY_REQUEST_PERSPECTIVE_CODE == "GR"


# ── the stage worker: split and combine (T-02, T-03, T-05, T-07, T-08) ───────


@pytest.fixture()
def ty_staging(deal, fake_irp, tmp_path, monkeypatch):
    """A TY export of ``c`` whose Risk Modeler job has FINISHED and whose stage
    job is pending; the archive is the two-treaty fixture table."""
    monkeypatch.setattr(elt, "upload_parquet", sqlite_upload_parquet)
    root = tmp_path / "archive"
    root.mkdir()
    monkeypatch.setattr(settings, "export_archive_dir", str(root))
    staging_root = tmp_path / "staging"
    staging_root.mkdir()
    monkeypatch.setattr(settings, "export_staging_dir", str(staging_root))
    fake_irp.export_archive_path = build_archive(tmp_path / "fixture", anls_id=41960,
                                                 output_level="Treaty")
    export_id = _create(deal, [deal["c"]], data_names={deal["c"]: "AmFam HU"})
    export_jobs.run_pending(worker_id="w1")
    irp_id = execute_one("SELECT irp_id FROM irp_job", {}, connection="WORKBENCH")["irp_id"]
    fake_irp.finish(irp_id)
    poller.poll_once()
    return {**deal, "export_id": export_id, "analysis_id": deal["c"], "root": root,
            "tmp": tmp_path}


def _rows(s):
    return [dict(r) for r in execute(
        "SELECT * FROM stage.rwb_loss_result_manifest WHERE export_id = :e "
        "AND irp_analysis_id = :a ORDER BY manifest_id",
        {"e": s["export_id"], "a": s["analysis_id"]}, connection="LOSS")]


def _elt_rows(manifest_id):
    return execute("SELECT * FROM stage.rwb_loss_result_elt_data WHERE manifest_id = :m "
                   "ORDER BY event_id", {"m": manifest_id}, connection="LOSS")


def _stage_job():
    return execute_one("SELECT * FROM rwb_job WHERE rwb_job_type = 'stage_results_export'",
                       {}, connection="WORKBENCH")


def _load_jobs():
    return execute("SELECT * FROM rwb_job WHERE rwb_job_type = 'load_results_export'", {},
                   connection="WORKBENCH")


def test_ty_archive_splits_into_one_staged_row_per_treaty(ty_staging):
    before = _rows(ty_staging)
    assert len(before) == 1 and before[0]["treaty_number"] is None

    assert export_jobs.run_pending(worker_id="w1") == 1

    rows = _rows(ty_staging)
    assert [r["treaty_number"] for r in rows] == ["PR1", "PR2"]
    first, second = rows
    assert first["manifest_id"] == before[0]["manifest_id"]  # the analysis row became PR1
    assert (first["treaty_name"], first["data_name"], first["treaty_ids"]) == (
        "PR1", "AmFam HU PR1", "33833")
    assert (second["treaty_name"], second["data_name"], second["treaty_ids"]) == (
        "Layer two", "AmFam HU PR2 Layer two", "33832")
    assert (first["staged_row_count"], second["staged_row_count"]) == (3, 2)
    assert first["aal"] == pytest.approx(0.001 * (100 + 10 + 500))
    assert second["aal"] == pytest.approx(0.001 * (40 + 60))
    for row in rows:
        assert row["stage_status"] == "staged" and row["load_status"] == "pending"
        assert row["perspective_code"] == "TY" and row["error_message"] is None
        assert (row["loss_table_type"], row["engine_type"], row["data_model_version"]) == (
            "ELT", "DLM", "25")
        assert row["zip_file"] == first["zip_file"] and row["irp_export_job_id"] == "1"
        assert row["client_id"] == 1 and row["data_vintage"] == "2025-12-31"

    files = execute("SELECT * FROM stage.rwb_loss_result_file ORDER BY result_file_id", {},
                    connection="LOSS")
    assert [(f["manifest_id"], f["output_level"], f["perspective_code"]) for f in files] == [
        (first["manifest_id"], "Treaty", "TY"), (second["manifest_id"], "Treaty", "TY")]
    assert files[0]["result_file"].endswith("ELT/Treaty/TY/25437617_CRE_Port_Template_ELT_Treaty_TY__1.parquet")
    assert files[1]["result_file"].endswith("_TY__2.parquet")

    staged = _elt_rows(second["manifest_id"])
    assert [(r["event_id"], r["loss"], r["port_info_num"], r["port_info_name"])
            for r in staged] == [(1001, 40.0, "PR2", "Layer two"), (3001, 60.0, "PR2", "Layer two")]
    assert staged[0]["port_info_id"] is None
    assert [r["loss"] for r in _elt_rows(first["manifest_id"])] == [100.0, 10.0, 500.0]

    load = _load_jobs()
    assert len(load) == 1
    assert json.loads(load[0]["input_data"]) == {
        "export_id": ty_staging["export_id"], "irp_analysis_id": ty_staging["analysis_id"]}
    assert _stage_job()["status_code"] == "succeeded"


def test_ty_rows_of_one_treaty_under_two_treaty_ids_are_combined_per_event(ty_staging, fake_irp):
    fake_irp.export_archive_path = build_archive(
        ty_staging["tmp"] / "group", anls_id=41960, output_level="Treaty", treaty_rows=[
            {"TreatyId": 33833, "TreatyNum": "PR1", "TreatyName": "PR1", "EventId": 1001,
             "Rate": 0.002, "Loss": 100.0, "StdDevI": 3.0, "StdDevC": 4.0, "ExpValue": 50.0},
            {"TreatyId": 44833, "TreatyNum": "PR1", "TreatyName": "PR1", "EventId": 1001,
             "Rate": 0.002, "Loss": 50.0, "StdDevI": 1.0, "StdDevC": 3.0, "ExpValue": 80.0},
            {"TreatyId": 33833, "TreatyNum": "PR1", "TreatyName": "PR1", "EventId": 1002,
             "Rate": 0.001, "Loss": 10.0, "StdDevI": 1.0, "StdDevC": 1.0, "ExpValue": 20.0},
        ])

    export_jobs.run_pending(worker_id="w1")

    rows = _rows(ty_staging)
    assert len(rows) == 1
    row = rows[0]
    assert (row["treaty_number"], row["treaty_ids"], row["staged_row_count"]) == (
        "PR1", "33833,44833", 2)
    assert row["aal"] == pytest.approx(0.002 * 150 + 0.001 * 10)
    staged = _elt_rows(row["manifest_id"])
    combined = staged[0]
    assert (combined["event_id"], combined["loss"], combined["std_dev_i"]) == (1001, 150.0, 4.0)
    assert combined["std_dev_c"] == pytest.approx(5.0)   # √(4² + 3²)
    assert combined["exp_value"] == 80.0                 # the largest (spec O-04)
    assert combined["rate"] == 0.002
    single = staged[1]
    assert (single["event_id"], single["loss"], single["std_dev_c"], single["exp_value"]) == (
        1002, 10.0, 1.0, 20.0)


def test_ty_archive_with_no_treaty_rows_fails_naming_ty(ty_staging, fake_irp):
    fake_irp.export_archive_path = build_archive(
        ty_staging["tmp"] / "empty", anls_id=41960, output_level="Treaty", treaty_rows=[])

    export_jobs.run_pending(worker_id="w1")

    rows = _rows(ty_staging)
    assert len(rows) == 1 and rows[0]["treaty_number"] is None
    assert rows[0]["stage_status"] == "failed"
    assert rows[0]["error_message"] == (
        "Risk Modeler returned no treaty (TY) loss rows for this analysis")
    assert _load_jobs() == []
    assert svc.derive_status(rows[0]) == svc.FAILED


def test_ty_file_missing_columns_fails_naming_them(ty_staging, fake_irp):
    fake_irp.export_archive_path = build_archive(
        ty_staging["tmp"] / "narrow", anls_id=41960, output_level="Treaty",
        columns=tuple(c for c in TY_COLUMNS if c != "TreatyName"))

    export_jobs.run_pending(worker_id="w1")

    row = _rows(ty_staging)[0]
    assert row["stage_status"] == "failed"
    assert row["error_message"].endswith("is missing columns TreatyName")


def test_retry_on_one_treaty_row_leaves_its_loaded_sibling_alone(ty_staging):
    export_jobs.run_pending(worker_id="w1")
    first, second = _rows(ty_staging)
    execute_command("UPDATE stage.rwb_loss_result_manifest SET load_status = 'loaded', "
                    "data_id = 7, updated_at = '2026-09-10 09:00:00' WHERE manifest_id = :m",
                    {"m": first["manifest_id"]}, connection="LOSS")
    execute_command("UPDATE stage.rwb_loss_result_manifest SET stage_status = 'failed', "
                    "error_message = 'x' WHERE manifest_id = :m",
                    {"m": second["manifest_id"]}, connection="LOSS")

    assert svc.apply_retry(ty_staging["submission_id"], ty_staging["export_id"],
                           second["manifest_id"]) == "stage"
    assert manifest_row(second["manifest_id"])["stage_status"] == "pending"
    assert export_jobs.run_one(rwb_job_id=_stage_job()["id"],
                               rwb_job_type="stage_results_export", worker_id="w1")

    first_after = manifest_row(first["manifest_id"])
    assert first_after["load_status"] == "loaded" and first_after["data_id"] == 7
    assert first_after["updated_at"] == "2026-09-10 09:00:00"
    assert len(_elt_rows(first["manifest_id"])) == 3  # never discarded
    second_after = manifest_row(second["manifest_id"])
    assert second_after["stage_status"] == "staged" and second_after["error_message"] is None
    assert len(_elt_rows(second["manifest_id"])) == 2
    assert len(_rows(ty_staging)) == 2  # no third row minted


def test_a_closed_treaty_row_is_never_re_staged(ty_staging):
    export_jobs.run_pending(worker_id="w1")
    first, second = _rows(ty_staging)
    execute_command("UPDATE stage.rwb_loss_result_manifest SET load_status = 'loaded', "
                    "data_id = 7 WHERE manifest_id = :m", {"m": first["manifest_id"]},
                    connection="LOSS")
    execute_command("UPDATE stage.rwb_loss_result_manifest SET stage_status = 'failed', "
                    "closed_at = '2026-09-10 09:00:00', closed_by = 'a@x' WHERE manifest_id = :m",
                    {"m": second["manifest_id"]}, connection="LOSS")

    export_jobs._stage_results_export_body(_stage_job()["id"])

    assert manifest_row(second["manifest_id"])["stage_status"] == "failed"
    assert len(_load_jobs()) == 1  # the one from the first run, none re-armed


# ── the load worker: every eligible row (T-05) ───────────────────────────────


@pytest.fixture()
def load_job(iteration2_db, loss_db):
    from app.services import rwb_job_service

    def make(**manifest):
        row = seed_manifest(perspective_code="TY", **manifest)
        job_id = rwb_job_service.enqueue_rwb_job(
            requestor_type="rwb_job", requestor_id="stage-job", rwb_job_type="load_results_export",
            link_type="not_applicable", link_id=None, context_type="irp_analysis",
            context_id=row["irp_analysis_id"],
            input_data={"export_id": row["export_id"], "irp_analysis_id": row["irp_analysis_id"]})
        return row, job_id
    return make


def _sibling(row, **columns):
    return seed_manifest(export_id=row["export_id"],
                         submission_id=row["requested_from_submission_id"],
                         irp_analysis_id=row["irp_analysis_id"], perspective_code="TY",
                         **columns)


def _job(job_id):
    return execute_one("SELECT status_code, output_data, error_detail FROM rwb_job WHERE id = :id",
                       {"id": job_id}, connection="WORKBENCH")


def test_every_staged_treaty_row_is_loaded_once_and_a_failure_stamps_only_its_row(
        load_job, monkeypatch):
    first, job_id = load_job(stage_status="staged", treaty_number="PR1", treaty_name="PR1")
    second = _sibling(first, stage_status="staged", treaty_number="PR2", treaty_name="Layer two")
    calls = []

    def procedure(name, params, connection):
        calls.append(params["manifest_id"])
        if params["manifest_id"] == second["manifest_id"]:
            raise RuntimeError("event 1001 matches 2 historical lookup rows")
        execute_command("UPDATE stage.rwb_loss_result_manifest SET load_status = 'loaded', "
                        "data_id = 4127 WHERE manifest_id = :m", {"m": params["manifest_id"]},
                        connection="LOSS")
    monkeypatch.setattr(export_jobs, "execute_procedure", procedure)

    export_jobs.run_pending(worker_id="w1")

    assert calls == [first["manifest_id"], second["manifest_id"]]
    job = _job(job_id)
    assert job["status_code"] == "failed"
    assert job["error_detail"] == "PR2: event 1001 matches 2 historical lookup rows"
    assert json.loads(job["output_data"]) == {"data_ids": [4127]}
    assert manifest_row(first["manifest_id"])["load_status"] == "loaded"
    after = manifest_row(second["manifest_id"])
    assert after["load_status"] == "failed"
    assert after["error_message"] == "event 1001 matches 2 historical lookup rows"


def test_loaded_and_closed_rows_are_skipped_by_the_load(load_job, monkeypatch):
    first, job_id = load_job(stage_status="staged", load_status="loaded", data_id=1,
                             treaty_number="PR1", treaty_name="PR1")
    _sibling(first, stage_status="staged", load_status="failed", treaty_number="PR2",
             treaty_name="PR2", closed_at="2026-09-10 09:00:00", closed_by="a@x")
    third = _sibling(first, stage_status="staged", treaty_number="PR3", treaty_name="PR3")
    calls = []
    monkeypatch.setattr(export_jobs, "execute_procedure",
                        lambda name, params, connection: calls.append(params["manifest_id"]))

    export_jobs.run_pending(worker_id="w1")

    assert calls == [third["manifest_id"]]
    assert _job(job_id)["status_code"] == "succeeded"


# ── the screens (FR-002, FR-011, FR-016) ─────────────────────────────────────


@pytest.fixture()
def client(iteration2_db, loss_db) -> TestClient:
    return make_client(iteration2_db, loss_db)


def test_fragment_at_ty_shows_the_note_and_no_aal(client):
    deal = make_deal(client)
    c = seed_analysis(edm_id=deal["edm_id"], name="C", full_name="C long", irp_id="41960",
                      irp_app_analysis_id="41960", perspectives=("GU", "GR"), treaties=TREATIES)
    url = f"/submissions/{deal['submission_id']}/exports/new/fields"

    alone = client.get(url, params={"analysis_ids": [c], "perspective": "TY"})
    assert alone.status_code == 200
    assert '<option value="TY" selected>TY</option>' in alone.text
    assert "one loss set per treaty, per analysis" in alone.text
    assert "AAL " not in alone.text

    both = client.get(url, params={"analysis_ids": [c, deal["a"]], "perspective": "TY"})
    assert 'value="TY"' not in both.text
    assert "one loss set per treaty" not in both.text
    assert '<option value="GR"' in both.text

    gr = client.get(url, params={"analysis_ids": [c], "perspective": "GR"})
    assert "AAL 100" in gr.text


def test_detail_rows_show_the_treaty_and_the_section_counts_data_sets(client):
    deal = make_deal(client)
    export_id = str(uuid.uuid4())
    for number, name, ids in (("PR1", "PR1", "33833"), ("PR2", "Layer two", "33832,44832")):
        seed_manifest(export_id=export_id, submission_id=deal["submission_id"],
                      irp_analysis_id=deal["a"], perspective_code="TY", stage_status="staged",
                      load_status="loaded", data_id=7, treaty_number=number, treaty_name=name,
                      treaty_ids=ids, aal=0.61)
    url = f"/submissions/{deal['submission_id']}/exports/{export_id}"

    page = client.get(url)
    assert page.status_code == 200
    assert "2 data sets" in page.text
    assert "<span class=\"l\">Treaty</span>" in page.text
    assert ">PR1</span>" in page.text
    assert 'title="Treaty IDs 33832, 44832">PR2 · Layer two</span>' in page.text
    assert page.text.count('title="0.61">1</span>') == 2  # the row's own AAL

    section = client.get(f"/submissions/{deal['submission_id']}/exports")
    assert "Data sets" in section.text and '<span class="l">2</span>' in section.text
    assert "CRE_Port_Template long · PR2 · Layer two" in section.text


def test_close_posts_by_manifest_row(client):
    deal = make_deal(client)
    export_id = str(uuid.uuid4())
    rows = [seed_manifest(export_id=export_id, submission_id=deal["submission_id"],
                          irp_analysis_id=deal["a"], perspective_code="TY", stage_status="failed",
                          error_message="x", treaty_number=n, treaty_name=n)
            for n in ("PR1", "PR2")]
    url = f"/submissions/{deal['submission_id']}/exports/{export_id}"

    response = client.post(f"{url}/manifests/{rows[1]['manifest_id']}/close",
                           data={"csrf_token": csrf()}, headers={"HX-Request": "true"})

    assert response.status_code == 200
    assert manifest_row(rows[1]["manifest_id"])["closed_at"] is not None
    assert manifest_row(rows[0]["manifest_id"])["closed_at"] is None
    assert f"manifests/{rows[0]['manifest_id']}/retry" in response.text
    assert f"manifests/{rows[1]['manifest_id']}/retry" not in response.text
