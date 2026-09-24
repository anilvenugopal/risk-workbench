"""Unit tests for the treaty-level (TY) export (spec 016): the treaties the
results retrieval records, TY in the perspective intersection, the treaty
selection the form writes as one manifest row per ticked treaty, the treaty
request, the staging of each ticked treaty with the P-11 combination, the
per-row load, Retry by manifest row, and both screens.

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
    seed_lookup_versions,
    seed_manifest,
    seed_submission,
)
from tests.unit.test_analysis_jobs_worker import (
    _run_retrieval,
    _seed_finished_analysis,
    _stored_extract,
)

# The treaties an analysis was run with, as the results retrieval records them:
# identity plus the four terms the cart shows (P-12). They match the two
# treaties of the fixture archive in tests/unit/export_archive.py.
TREATIES = (
    {"treaty_id": "33833", "treaty_number": "PR1", "treaty_name": "PR1",
     "treaty_type": "WORK", "risk_limit": 3_000_000.0, "attachment_point": 2_000_000.0,
     "occurrence_limit": 9_000_000.0, "has_loss": True},
    {"treaty_id": "33832", "treaty_number": "PR2", "treaty_name": "Layer two",
     "treaty_type": "CATA", "risk_limit": 5_000_000.0, "attachment_point": 250_000.0,
     "occurrence_limit": None, "has_loss": True},
)
BOTH_TREATIES = {"PR1": "", "PR2": ""}


# ── results retrieval records the applied treaties (T-04) ────────────────────


def test_retrieval_stores_the_applied_treaties(iteration2_db, fake_irp):
    analysis_id = _seed_finished_analysis()
    fake_irp.set_analysis_treaties("9001", [
        {"treatyId": 33833, "treatyNumber": "PR1", "treatyName": "PR1", "cedant": "x",
         "treatyType": "WORK", "attachmentPoint": 2_000_000.0,
         "occurrenceLimit": 9_000_000.0, "riskLimit": 3_000_000.0},
        {"treatyId": 33832, "treatyNumber": "PR2", "treatyName": "Layer two"}])
    fake_irp.set_treaty_stats("9001", "33833", [{"pure_premium": 1.0}])

    job = _run_retrieval(analysis_id)

    assert job["status_code"] == "succeeded"
    assert _stored_extract(analysis_id)["treaties"] == [
        {"treaty_id": "33833", "treaty_number": "PR1", "treaty_name": "PR1",
         "treaty_type": "WORK", "attachment_point": 2_000_000.0,
         "occurrence_limit": 9_000_000.0, "risk_limit": 3_000_000.0, "has_loss": True},
        {"treaty_id": "33832", "treaty_number": "PR2", "treaty_name": "Layer two",
         "treaty_type": None, "attachment_point": None, "occurrence_limit": None,
         "risk_limit": None, "has_loss": False}]
    assert fake_irp.treaty_calls == ["9001"]
    assert [(c["perspective_code"], c["exposure_resource_id"])
            for c in fake_irp.result_calls
            if c["exposure_resource_type"] == "TREATY"] == [("TY", "33833"), ("TY", "33832")]


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


def test_retrieval_fails_when_a_treaty_loss_read_raises(iteration2_db, fake_irp):
    analysis_id = _seed_finished_analysis()
    fake_irp.set_analysis_treaties("9001", [
        {"treatyId": 33833, "treatyNumber": "PR1", "treatyName": "PR1"},
        {"treatyId": 33832, "treatyNumber": "PR2", "treatyName": "Layer two"}])
    fake_irp.raise_on_treaty_stats_for = {"33832"}

    job = _run_retrieval(analysis_id)

    assert job["status_code"] == "failed"
    assert job["error_detail"].startswith("treaty loss read failed for PR2:")
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
    seed_lookup_versions("25.0")
    return {"submission_id": submission_id, "edm_id": edm_id, "a": a, "c": c}


def _create(deal, analysis_ids, perspective="TY", treaty_picks=None, **overrides):
    kwargs = dict(submission_id=deal["submission_id"], user_email="analyst.a@example.com",
                  analysis_ids=analysis_ids, perspective_code=perspective, client_id=1,
                  treaty_incept=date(2026, 4, 1), crm_id="CRM-1",
                  data_vintage=date(2025, 12, 31), model_version="25.0", data_names=None,
                  treaty_picks=treaty_picks)
    kwargs.update(overrides)
    return svc.create_export(**kwargs)


def _manifest_rows(export_id):
    return execute(
        "SELECT * FROM stage.rwb_loss_result_manifest WHERE export_id = :e "
        "ORDER BY manifest_id", {"e": export_id}, connection="LOSS")


def test_ty_is_offered_only_when_every_selected_analysis_ran_with_treaties(deal):
    rows = {r.id: r for r in svc.list_exportable_analyses(deal["submission_id"])}

    assert rows[deal["c"]].perspectives == ["GU", "GR", "TY"]
    assert "TY" not in rows[deal["a"]].perspectives
    assert svc.perspective_choices([rows[deal["c"]]]) == ["GU", "GR", "TY"]
    assert svc.perspective_choices([rows[deal["c"]], rows[deal["a"]]]) == ["GU", "GR"]


def test_the_cart_lists_each_treaty_once_with_its_terms(deal):
    """A group repeats a treaty once per member; the cart offers it once, with
    the type spelled out and the amounts in the display format (P-12, D26)."""
    repeated = seed_analysis(
        edm_id=deal["edm_id"], name="G", irp_id="41961", irp_app_analysis_id="41961",
        perspectives=("GR",), is_group=1,
        treaties=(*TREATIES, {**TREATIES[0], "treaty_id": "44833"}))
    rows = {r.id: r for r in svc.list_exportable_analyses(deal["submission_id"])}

    assert [(t.number, t.name) for t in rows[repeated].treaty_choices] == [
        ("PR1", "PR1"), ("PR2", "Layer two")]
    first, second = rows[deal["c"]].treaty_choices
    assert (first.number, first.name, first.type_label) == ("PR1", "PR1", "Working Excess")
    assert (first.risk_limit, first.attachment_point, first.occurrence_limit) == (
        "3.0M", "2.0M", "9.0M")
    assert (second.type_label, second.attachment_point, second.occurrence_limit) == (
        "Catastrophe", "250,000", "—")


def test_the_cart_hides_a_treaty_that_took_no_loss(deal):
    """Filtered out, not shown as zero (P-13); a group keeps a treaty when any
    of its copies took loss."""
    partial = seed_analysis(
        edm_id=deal["edm_id"], name="P", irp_id="41962", irp_app_analysis_id="41962",
        perspectives=("GR",), treaties=(TREATIES[0], {**TREATIES[1], "has_loss": False}))
    group = seed_analysis(
        edm_id=deal["edm_id"], name="G", irp_id="41963", irp_app_analysis_id="41963",
        perspectives=("GR",), is_group=1,
        treaties=(TREATIES[0], {**TREATIES[0], "treaty_id": "44833", "has_loss": False}))
    rows = {r.id: r for r in svc.list_exportable_analyses(deal["submission_id"])}

    assert [t.number for t in rows[partial].treaty_choices] == ["PR1"]
    assert "TY" in rows[partial].perspectives
    assert [t.number for t in rows[group].treaty_choices] == ["PR1"]


def test_ty_is_not_offered_when_no_treaty_took_loss(deal):
    none = seed_analysis(
        edm_id=deal["edm_id"], name="N", full_name="N long", irp_id="41964",
        irp_app_analysis_id="41964", perspectives=("GR",),
        treaties=tuple({**t, "has_loss": False} for t in TREATIES))
    rows = {r.id: r for r in svc.list_exportable_analyses(deal["submission_id"])}

    assert "TY" not in rows[none].perspectives
    assert rows[none].treaty_choices == []
    with pytest.raises(svc.ExportValidationError) as exc:
        _create(deal, [none], treaty_picks={none: BOTH_TREATIES})
    assert str(exc.value) == "N long has no treaty with TY loss."


def test_create_export_refuses_ty_for_an_analysis_without_treaties(deal):
    with pytest.raises(svc.ExportValidationError) as exc:
        _create(deal, [deal["a"], deal["c"]], treaty_picks={deal["c"]: BOTH_TREATIES})
    assert str(exc.value) == "A long has no treaty with TY loss."


def test_create_export_refuses_an_analysis_with_no_treaty_ticked(deal):
    with pytest.raises(svc.ExportValidationError) as exc:
        _create(deal, [deal["c"]], treaty_picks={})
    assert str(exc.value) == "Tick at least one treaty for C long."
    assert execute("SELECT 1 FROM stage.rwb_loss_result_manifest", {},
                   connection="LOSS") == []


def test_create_export_refuses_a_treaty_the_analysis_did_not_run_with(deal):
    with pytest.raises(svc.ExportValidationError) as exc:
        _create(deal, [deal["c"]], treaty_picks={deal["c"]: {"PR1": "", "FAC-9": ""}})
    assert str(exc.value) == "Treaty FAC-9 is not one of C long's treaties."


def test_one_manifest_row_per_ticked_treaty_with_the_typed_or_default_name(deal):
    export_id = _create(deal, [deal["c"]],
                        treaty_picks={deal["c"]: {"PR2": "AmFam HU 5x5 2026", "PR1": "  "}})

    rows = _manifest_rows(export_id)
    assert [(r["perspective_code"], r["treaty_number"], r["treaty_name"], r["data_name"])
            for r in rows] == [
        ("TY", "PR1", "PR1", "C PR1"),
        ("TY", "PR2", "Layer two", "AmFam HU 5x5 2026")]
    # Nothing the stage worker writes is known yet.
    assert all(r["treaty_ids"] is None and r["aal"] is None and r["stage_status"] == "pending"
               for r in rows)


def test_an_untouched_treaty_is_not_exported(deal):
    export_id = _create(deal, [deal["c"]], treaty_picks={deal["c"]: {"PR2": ""}})

    assert [r["treaty_number"] for r in _manifest_rows(export_id)] == ["PR2"]


def test_a_typed_treaty_data_name_over_the_limit_is_refused(deal):
    with pytest.raises(svc.ExportValidationError) as exc:
        _create(deal, [deal["c"]],
                treaty_picks={deal["c"]: {"PR1": "x" * (svc.DATA_NAME_MAX_LEN + 1)}})
    assert str(exc.value) == ("Data name for C long treaty PR1 is longer than 150 "
                              "characters.")


def test_export_rows_carry_the_treaty_and_its_own_aal_at_ty(deal):
    export_id = str(uuid.uuid4())
    seed_manifest(export_id=export_id, submission_id=deal["submission_id"],
                  irp_analysis_id=deal["c"], perspective_code="TY", stage_status="staged",
                  load_status="loaded", data_id=7, treaty_number="PR2",
                  treaty_name="Layer two", treaty_ids="33832,44832", aal=0.61)
    seed_manifest(export_id=export_id, submission_id=deal["submission_id"],
                  irp_analysis_id=deal["c"], perspective_code="TY", stage_status="staged",
                  load_status="loaded", data_id=8, treaty_number="PR1", treaty_name="PR1")

    rows = svc.list_export_rows(deal["submission_id"])

    assert [r.treaty_label for r in rows] == ["PR1", "PR2 · Layer two"]
    second = rows[1]
    assert second.treaty_ids == ["33832", "44832"] and second.aal == 0.61
    assert rows[0].aal is None  # the row's own value, never the analysis's


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
    _create(deal, [deal["c"]], treaty_picks={deal["c"]: BOTH_TREATIES})

    export_jobs.run_pending(worker_id="w1")

    assert fake_irp.export_submits[0]["loss_details"] == [{
        "metricType": "LOSS_TABLES", "outputLevels": ["Treaty"],
        "perspectiveCodes": ["GR"]}]


def test_the_ticked_treaty_rows_share_one_export_request(deal, fake_irp):
    """One loss table holds every treaty, so one request serves every row of the
    analysis and its job id is stamped on all of them."""
    export_id = _create(deal, [deal["c"]], treaty_picks={deal["c"]: BOTH_TREATIES})

    export_jobs.run_pending(worker_id="w1")

    assert [s["analysis_id"] for s in fake_irp.export_submits] == [41960]
    rows = _manifest_rows(export_id)
    assert [r["irp_export_job_id"] for r in rows] == ["1", "1"]
    jobs = execute("SELECT irp_id, irp_analysis_id FROM irp_job WHERE irp_job_type = 'export'",
                   {}, connection="WORKBENCH")
    assert [(j["irp_id"], j["irp_analysis_id"]) for j in jobs] == [("1", deal["c"])]


def test_a_rejected_request_fails_every_treaty_row_of_the_analysis(deal, fake_irp):
    fake_irp.raise_on_export_submit_for = {41960}
    export_id = _create(deal, [deal["c"]], treaty_picks={deal["c"]: BOTH_TREATIES})

    export_jobs.run_pending(worker_id="w1")

    rows = _manifest_rows(export_id)
    assert all(r["stage_status"] == "failed" and "41960 not found" in r["error_message"]
               for r in rows)
    assert all(r["irp_export_job_id"] is None for r in rows)


def test_a_job_recorded_by_a_crashed_run_is_stamped_on_every_treaty_row(deal, fake_irp):
    from tests.unit.export_rows import seed_export_job

    export_id = _create(deal, [deal["c"]], treaty_picks={deal["c"]: BOTH_TREATIES})
    seed_export_job(export_id=export_id, irp_analysis_id=deal["c"], irp_id="77",
                    edm_id=deal["edm_id"])

    export_jobs.run_pending(worker_id="w1")

    assert fake_irp.export_submits == []
    assert [r["irp_export_job_id"] for r in _manifest_rows(export_id)] == ["77", "77"]


# ── the stage worker: match and combine (T-02, T-03, T-05, T-08) ─────────────


@pytest.fixture()
def ty_env(deal, fake_irp, tmp_path, monkeypatch):
    """The archive and staging roots, the SQLite Parquet upload, and the
    two-treaty fixture table Risk Modeler hands back. ``_arm`` turns it into a
    TY export waiting to be staged."""
    monkeypatch.setattr(elt, "upload_parquet", sqlite_upload_parquet)
    root = tmp_path / "archive"
    root.mkdir()
    monkeypatch.setattr(settings, "export_archive_dir", str(root))
    staging_root = tmp_path / "staging"
    staging_root.mkdir()
    monkeypatch.setattr(settings, "export_staging_dir", str(staging_root))
    fake_irp.export_archive_path = build_archive(tmp_path / "fixture", anls_id=41960,
                                                 output_level="Treaty")
    return {**deal, "fake_irp": fake_irp, "root": root, "tmp": tmp_path}


def _arm(env, picks=None):
    """A TY export of ``c`` with the given ticks, whose Risk Modeler job has
    FINISHED and whose stage job is pending."""
    export_id = _create(env, [env["c"]],
                        treaty_picks={env["c"]: picks or {"PR1": "AmFam HU 3x2", "PR2": ""}})
    export_jobs.run_pending(worker_id="w1")
    irp_id = execute_one("SELECT irp_id FROM irp_job", {}, connection="WORKBENCH")["irp_id"]
    env["fake_irp"].finish(irp_id)
    poller.poll_once()
    return {**env, "export_id": export_id, "analysis_id": env["c"]}


@pytest.fixture()
def ty_staging(ty_env):
    """Both treaties ticked: the case every stage test but the selection ones
    starts from."""
    return _arm(ty_env)


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


def test_each_ticked_treaty_row_is_staged_from_the_one_loss_table(ty_staging):
    rows_before = _rows(ty_staging)
    assert [r["treaty_number"] for r in rows_before] == ["PR1", "PR2"]

    assert export_jobs.run_pending(worker_id="w1") == 1

    rows = _rows(ty_staging)
    assert [r["manifest_id"] for r in rows] == [r["manifest_id"] for r in rows_before]
    first, second = rows
    assert (first["treaty_name"], first["data_name"], first["treaty_ids"]) == (
        "PR1", "AmFam HU 3x2", "33833")
    assert (second["treaty_name"], second["data_name"], second["treaty_ids"]) == (
        "Layer two", "C PR2", "33832")
    assert (first["staged_row_count"], second["staged_row_count"]) == (3, 2)
    assert first["aal"] == pytest.approx(0.001 * (100 + 10 + 500))
    assert second["aal"] == pytest.approx(0.001 * (40 + 60))
    for row in rows:
        assert row["stage_status"] == "staged" and row["load_status"] == "pending"
        assert row["perspective_code"] == "TY" and row["error_message"] is None
        # the model version is the export's choice, not the archive's
        assert (row["loss_table_type"], row["engine_type"], row["data_model_version"]) == (
            "ELT", "DLM", "25.0")
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


def test_ty_rows_of_one_treaty_under_two_treaty_ids_are_combined_per_event(ty_env, fake_irp):
    ty_staging = _arm(ty_env, {"PR1": "AmFam HU 3x2"})
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


def test_a_treaty_the_analyst_left_unticked_is_skipped_and_logged(ty_env, caplog):
    ty_staging = _arm(ty_env, {"PR2": ""})

    with caplog.at_level("INFO", logger="app.workers.export_jobs"):
        export_jobs.run_pending(worker_id="w1")

    rows = _rows(ty_staging)
    assert len(rows) == 1
    assert (rows[0]["treaty_number"], rows[0]["stage_status"], rows[0]["staged_row_count"]) == (
        "PR2", "staged", 2)
    assert "1 treaties in the loss table have no row to stage" in caplog.text
    # PR1's derived file keeps its place in the table, so PR2's is still the second.
    files = execute("SELECT result_file FROM stage.rwb_loss_result_file", {}, connection="LOSS")
    assert len(files) == 1 and files[0]["result_file"].endswith("_TY__2.parquet")


def test_a_ticked_treaty_the_table_does_not_hold_fails_its_row_alone(ty_env, fake_irp):
    ty_staging = _arm(ty_env)
    fake_irp.export_archive_path = build_archive(
        ty_staging["tmp"] / "pr1only", anls_id=41960, output_level="Treaty",
        treaty_rows=[{"TreatyId": 33833, "TreatyNum": "PR1", "TreatyName": "PR1",
                      "EventId": 1001, "Rate": 0.001, "Loss": 100.0, "StdDevI": 1.0,
                      "StdDevC": 2.0, "ExpValue": 50.0}])

    export_jobs.run_pending(worker_id="w1")

    first, second = _rows(ty_staging)
    assert first["stage_status"] == "staged" and first["error_message"] is None
    assert second["stage_status"] == "failed"
    assert second["error_message"] == (
        "treaty PR2 Layer two is not in the loss table Risk Modeler returned")
    assert len(_load_jobs()) == 1  # PR1 still loads


def test_ty_archive_with_no_treaty_rows_fails_every_row_naming_ty(ty_staging, fake_irp):
    fake_irp.export_archive_path = build_archive(
        ty_staging["tmp"] / "empty", anls_id=41960, output_level="Treaty", treaty_rows=[])

    export_jobs.run_pending(worker_id="w1")

    rows = _rows(ty_staging)
    assert len(rows) == 2
    assert all(r["error_message"] == (
        "Risk Modeler returned no treaty (TY) loss rows for this analysis") for r in rows)
    assert all(svc.derive_status(r) == svc.FAILED for r in rows)
    assert _load_jobs() == []


def test_a_table_matching_no_ticked_treaty_fails_the_analysis_naming_what_it_held(
        ty_staging, fake_irp):
    fake_irp.export_archive_path = build_archive(
        ty_staging["tmp"] / "other", anls_id=41960, output_level="Treaty",
        treaty_rows=[{"TreatyId": 33831, "TreatyNum": "PR1.0", "TreatyName": "PR1",
                      "EventId": 1001, "Rate": 0.001, "Loss": 100.0, "StdDevI": 1.0,
                      "StdDevC": 2.0, "ExpValue": 50.0}])

    export_jobs.run_pending(worker_id="w1")

    rows = _rows(ty_staging)
    assert all(r["stage_status"] == "failed" for r in rows)
    assert all(r["error_message"] == (
        "no ticked treaty matches the loss table Risk Modeler returned, which holds "
        "PR1.0 PR1") for r in rows)
    assert _load_jobs() == []


def test_ty_file_missing_columns_fails_every_row_naming_them(ty_staging, fake_irp):
    fake_irp.export_archive_path = build_archive(
        ty_staging["tmp"] / "narrow", anls_id=41960, output_level="Treaty",
        columns=tuple(c for c in TY_COLUMNS if c != "TreatyName"))

    export_jobs.run_pending(worker_id="w1")

    rows = _rows(ty_staging)
    assert len(rows) == 2
    assert all(r["stage_status"] == "failed"
               and r["error_message"].endswith("is missing columns TreatyName") for r in rows)


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


def test_retry_after_a_rejection_stamps_the_new_job_on_the_row_that_rides_along(ty_env,
                                                                                 fake_irp):
    """Retry re-arms the analysis's request from one row; the sibling that
    failed with it is staged by the same job and must trace to it (FR-015)."""
    fake_irp.raise_on_export_submit_for = {41960}
    export_id = _create(ty_env, [ty_env["c"]], treaty_picks={ty_env["c"]: BOTH_TREATIES})
    export_jobs.run_pending(worker_id="w1")
    first, second = _manifest_rows(export_id)
    assert (first["stage_status"], second["stage_status"]) == ("failed", "failed")
    fake_irp.raise_on_export_submit_for = set()

    assert svc.apply_retry(ty_env["submission_id"], export_id, first["manifest_id"]) == "submit"
    export_jobs.run_pending(worker_id="w1")
    irp_id = execute_one("SELECT irp_id FROM irp_job", {}, connection="WORKBENCH")["irp_id"]
    assert [r["irp_export_job_id"] for r in _manifest_rows(export_id)] == [irp_id, None]
    fake_irp.finish(irp_id)
    poller.poll_once()
    export_jobs.run_pending(worker_id="w1")

    rows = _manifest_rows(export_id)
    assert [(r["stage_status"], r["irp_export_job_id"]) for r in rows] == [
        ("staged", irp_id), ("staged", irp_id)]
    assert len(_load_jobs()) == 1


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


def _treaty_analysis(deal):
    return seed_analysis(edm_id=deal["edm_id"], name="C", full_name="C long", irp_id="41960",
                         irp_app_analysis_id="41960", perspectives=("GU", "GR"),
                         treaties=TREATIES)


def test_fragment_at_ty_lists_the_treaties_with_their_terms_and_no_aal(client):
    deal = make_deal(client)
    c = _treaty_analysis(deal)
    url = f"/submissions/{deal['submission_id']}/exports/new/fields"

    alone = client.get(url, params={"analysis_ids": [c], "perspective": "TY"})
    assert alone.status_code == 200
    assert '<option value="TY" selected>TY</option>' in alone.text
    assert "one loss set per ticked treaty, per analysis" in alone.text
    assert "AAL " not in alone.text
    assert f'name="treaty[{c}]" value="PR1"' in alone.text
    assert f'name="treaty[{c}]" value="PR2"' in alone.text
    assert "checked" not in alone.text                      # nothing ticked by default
    assert ">Working Excess</span>" in alone.text
    assert "risk <b>3.0M</b> · att <b>2.0M</b> · occ <b>9.0M</b>" in alone.text
    assert "risk <b>5.0M</b> · att <b>250,000</b> · occ <b>—</b>" in alone.text
    assert f'name="treaty_data_name[{c}][PR2]"' in alone.text
    assert 'placeholder="C PR2"' in alone.text
    assert f'name="data_name[{c}]"' not in alone.text       # not at TY (P-06)

    both = client.get(url, params={"analysis_ids": [c, deal["a"]], "perspective": "TY"})
    assert 'value="TY"' not in both.text
    assert "one loss set per ticked treaty" not in both.text
    assert '<option value="GR"' in both.text

    gr = client.get(url, params={"analysis_ids": [c], "perspective": "GR"})
    assert "AAL 100" in gr.text
    assert f'name="data_name[{c}]"' in gr.text
    assert "treaty-pick" not in gr.text


def test_fragment_keeps_the_ticks_and_the_treaty_names_typed_before_the_next_change(client):
    deal = make_deal(client)
    c = _treaty_analysis(deal)

    frag = client.get(f"/submissions/{deal['submission_id']}/exports/new/fields",
                      params=[("analysis_ids", c), ("perspective", "TY"),
                              (f"treaty[{c}]", "PR2"),
                              (f"treaty_data_name[{c}][PR2]", "AmFam HU 5x5 2026"),
                              (f"treaty_data_name[{c}][PR1]", "not ticked, dropped")])

    ticked = frag.text.split(f'name="treaty[{c}]" value="PR2"')[1].split(">")[0]
    assert "checked" in ticked
    unticked = frag.text.split(f'name="treaty[{c}]" value="PR1"')[1].split(">")[0]
    assert "checked" not in unticked
    assert 'value="AmFam HU 5x5 2026"' in frag.text
    assert "not ticked, dropped" not in frag.text


def test_the_section_shows_one_row_per_treaty_with_its_own_aal(client):
    deal = make_deal(client)
    export_id = str(uuid.uuid4())
    for number, name, ids in (("PR1", "PR1", "33833"), ("PR2", "Layer two", "33832,44832")):
        seed_manifest(export_id=export_id, submission_id=deal["submission_id"],
                      irp_analysis_id=deal["a"], perspective_code="TY", stage_status="staged",
                      load_status="loaded", data_id=7, treaty_number=number, treaty_name=name,
                      treaty_ids=ids, aal=0.61)

    section = client.get(f"/submissions/{deal['submission_id']}/exports")

    assert section.status_code == 200
    assert '<span class="l">Treaty</span>' in section.text
    assert section.text.count('id="export-analysis-') == 2
    assert ">PR1</span>" in section.text
    assert 'title="Treaty IDs 33832, 44832">PR2 · Layer two</span>' in section.text
    assert section.text.count('title="0.61">1</span>') == 2  # the row's own AAL


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
