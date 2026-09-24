"""Unit tests for app.services.export_service (spec 014 T017–T022, T035):
the exportable-analysis list, the perspective intersection, the duplicate check
over the loss mirror, the manifest insert, the exports table rows, and status
derivation. Workers, routes, and Retry have their own modules."""

from __future__ import annotations

import json
import re
import uuid
from datetime import date

import pytest

from app.config import settings
from app.services import export_service as svc
from db import execute, execute_one
from tests.unit.export_rows import (
    seed_analysis,
    seed_client,
    seed_edm_for,
    seed_export_job,
    seed_lookup_versions,
    seed_manifest,
    seed_rdm_for,
    seed_submission,
)


@pytest.fixture()
def deal(iteration2_db, loss_db):
    """A submission with one EDM, two exportable own analyses, and one client."""
    submission_id = seed_submission(iteration2_db.user_a)
    edm_id = seed_edm_for(submission_id)
    a = seed_analysis(edm_id=edm_id, name="A", full_name="A long", irp_id="41958",
                      irp_app_analysis_id="41958", perspectives=("GU", "GR", "RL"))
    b = seed_analysis(edm_id=edm_id, name="B", full_name="B long", irp_id="41959",
                      irp_app_analysis_id="41959", perspectives=("GR", "RL", "RP"),
                      inserted_at="2026-09-10 07:00:00")
    seed_client(1, "Example Re")
    seed_lookup_versions("25.0", "23.0")
    return {"submission_id": submission_id, "edm_id": edm_id, "a": a, "b": b,
            "user_a": iteration2_db.user_a}


def _create(deal, analysis_ids, perspective="GR", **overrides):
    kwargs = dict(submission_id=deal["submission_id"], user_email="analyst.a@example.com",
                  analysis_ids=analysis_ids, perspective_code=perspective, client_id=1,
                  treaty_incept=date(2026, 4, 1), crm_id="CRM-1",
                  data_vintage=date(2025, 12, 31), model_version="25.0", data_names=None)
    kwargs.update(overrides)
    return svc.create_export(**kwargs)


# ── list_exportable_analyses ─────────────────────────────────────────────────

def test_own_rows_come_first_then_rdm_rows_by_rdm(deal):
    rdm_id = seed_rdm_for(deal["submission_id"], "GC RDM")
    seed_analysis(rdm_id=rdm_id, name="GC_HU", full_name=None, irp_id="38812",
                  irp_app_analysis_id="38812", perspectives=("GR",))
    group = seed_analysis(edm_id=deal["edm_id"], name="Group", irp_id="41990",
                          irp_app_analysis_id="41990", perspectives=("GR",), is_group=1)

    rows = svc.list_exportable_analyses(deal["submission_id"])

    # Origin is the source system (P-27); the Engine column tells a group apart
    assert [r.origin for r in rows[:3]] == ["RMS", "RMS", "RMS"]
    assert rows[-1].origin == "RDM" and rows[-1].rdm_name == "GC RDM"
    assert next(r for r in rows if r.id == group).engine == "Group"
    a = next(r for r in rows if r.analysis_name == "A")
    assert a.engine == "DLM · RL25"
    assert a.exportable and a.irp_app_analysis_id == 41958 and a.irp_id == "41958"
    assert a.name == "A long" and a.analysis_description == "A long"
    assert a.perspectives == ["GU", "GR", "RL"]
    assert (a.peril_code, a.region_code, a.currency) == ("EQ", "NAEQ", "USD")
    rdm_row = rows[-1]
    assert rdm_row.exportable and rdm_row.name == "GC_HU" and rdm_row.analysis_description is None
    assert rdm_row.engine == "DLM · RL25"


def test_non_integer_app_id_and_missing_results_are_disabled_with_a_reason(deal):
    bad = seed_analysis(edm_id=deal["edm_id"], name="Bad", irp_app_analysis_id="A-388")
    pending = seed_analysis(edm_id=deal["edm_id"], name="Pending", irp_app_analysis_id="5",
                            perspectives=None)
    rows = {r.id: r for r in svc.list_exportable_analyses(deal["submission_id"])}

    assert not rows[bad].exportable
    assert "'A-388' is not a whole number" in rows[bad].disabled_reason
    assert rows[pending].disabled_reason == "results not retrieved yet"


def test_a_broker_row_takes_its_app_analysis_id_from_the_metadata_snapshot(deal):
    rdm_id = seed_rdm_for(deal["submission_id"], "GC RDM")
    broker = seed_analysis(rdm_id=rdm_id, name="GC_HU", irp_id="38812",
                           irp_app_analysis_id="38812", perspectives=("GR",),
                           write_app_column=False)
    no_id = seed_analysis(rdm_id=rdm_id, name="GC_EQ", irp_id="38813",
                          irp_app_analysis_id=None, perspectives=("GR",),
                          write_app_column=False)

    rows = {r.id: r for r in svc.list_exportable_analyses(deal["submission_id"])}

    assert rows[broker].exportable and rows[broker].irp_app_analysis_id == 38812
    assert rows[no_id].disabled_reason == (
        "the analysis has no Risk Modeler application analysis ID")


def test_an_hd_analysis_is_disabled_and_refused(deal):
    hd = seed_analysis(edm_id=deal["edm_id"], name="HD", full_name=None,
                       irp_app_analysis_id="9", perspectives=None, engine_type="HD")

    rows = {r.id: r for r in svc.list_exportable_analyses(deal["submission_id"])}

    # HD wins over every other reason, so the row never reads "not retrieved yet"
    assert rows[hd].disabled_reason == "PLT results are not exportable yet"
    with pytest.raises(svc.ExportValidationError, match=re.escape(
            "HD cannot be exported: PLT results are not exportable yet.")):
        _create(deal, [hd])


def test_a_plt_group_is_disabled_and_refused(deal):
    # Risk Modeler reports a group as engineType "Group"; only analysisFramework
    # says whether its members' losses are a PLT.
    group = seed_analysis(edm_id=deal["edm_id"], name="PLT group", full_name=None,
                          is_group=1, engine_type="Group", framework="PLT")

    rows = {r.id: r for r in svc.list_exportable_analyses(deal["submission_id"])}

    assert rows[group].disabled_reason == "PLT results are not exportable yet"
    with pytest.raises(svc.ExportValidationError, match=re.escape(
            "PLT group cannot be exported: PLT results are not exportable yet.")):
        _create(deal, [group])


def test_unknown_submission_is_none(deal):
    assert svc.list_exportable_analyses(str(uuid.uuid4())) is None


# ── perspective_choices ──────────────────────────────────────────────────────

def test_perspective_choices_is_the_configured_order_intersection(deal):
    rows = {r.analysis_name: r for r in svc.list_exportable_analyses(deal["submission_id"])}
    assert svc.perspective_choices([rows["A"]]) == ["GU", "GR", "RL"]
    assert svc.perspective_choices([rows["A"], rows["B"]]) == ["GR", "RL"]
    assert svc.perspective_choices([]) == []


def test_perspective_choices_empty_when_nothing_is_shared(deal):
    only_gu = seed_analysis(edm_id=deal["edm_id"], name="GU only", irp_app_analysis_id="7",
                            perspectives=("GU",))
    rows = {r.id: r for r in svc.list_exportable_analyses(deal["submission_id"])}
    assert svc.perspective_choices([rows[only_gu], rows[deal["b"]]]) == []


# ── find_exported / clients / defaults ────────────────────────────────────────

def test_find_exported_names_the_newest_export_and_counts_the_earlier_ones(deal):
    other_submission = str(uuid.uuid4())
    seed_manifest(submission_id=other_submission, irp_app_analysis_id=41958,
                  perspective_code="GR", requested_at="2026-08-01 08:00:00",
                  requested_by_email="d.owens@example.com")
    newest = seed_manifest(submission_id=other_submission, irp_app_analysis_id=41958,
                           perspective_code="GR", requested_at="2026-09-01 08:00:00",
                           requested_by_email="r.patel@example.com")

    marks = svc.find_exported([41958, 41959], "GR")

    assert set(marks) == {41958}
    mark = marks[41958]
    assert mark.requested_by_email == "r.patel@example.com"
    assert mark.earlier_count == 2
    assert mark.status == svc.QUEUED
    assert mark.exports_url == f"/submissions/{other_submission}#submission-exports"
    assert mark.export_id == newest["export_id"]
    assert svc.find_exported([41958], "RL") == {}


def test_list_clients_is_every_client_by_name(deal):
    seed_client(2, "Alpha Mutual", "Y")
    seed_client(3, "Retired", "N")
    assert [c.name for c in svc.list_clients()] == ["Alpha Mutual", "Example Re", "Retired"]


def test_model_version_choices_newest_first_from_the_lookup(deal):
    seed_lookup_versions("HDv2.1")
    assert svc.model_version_choices() == ["25.0", "23.0", "HDv2.1"]
    from db import execute_command
    execute_command("DELETE FROM dbo.Lookup_RMS_HistoricalRDS", {}, connection="LOSS")
    assert svc.model_version_choices() == []


# ── create_export ────────────────────────────────────────────────────────────

def test_create_export_records_the_approved_values_and_enqueues_submit(deal, monkeypatch):
    monkeypatch.setattr(settings, "risk_modeler_base_url", "https://api-euw1.rms-ppe.com/")
    monkeypatch.setattr(settings, "risk_modeler_tenant_name", "acme")
    export_id = _create(deal, [deal["a"], deal["b"]], crm_id="CRM-9",
                        treaty_incept=date(2026, 5, 1), data_vintage=date(2025, 12, 31),
                        data_names={deal["a"]: "Named A"})

    rows = execute("SELECT * FROM stage.rwb_loss_result_manifest ORDER BY analysis_name",
                   {}, connection="LOSS")
    assert [r["analysis_name"] for r in rows] == ["A", "B"]
    a = rows[0]
    assert a["export_id"] == export_id
    assert a["requested_by_email"] == "analyst.a@example.com"
    assert a["requested_from_submission_id"] == deal["submission_id"]
    assert a["irp_analysis_id"] == deal["a"] and a["irp_analysis_irp_id"] == "41958"
    assert a["irp_app_analysis_id"] == 41958 and a["analysis_description"] == "A long"
    assert a["perspective_code"] == "GR" and a["client_id"] == 1
    assert a["treaty_incept"] == "2026-05-01" and a["treaty_year"] == 2026
    assert a["crm_id"] == "CRM-9" and a["data_name"] == "Named A"
    assert a["data_vintage"] == "2025-12-31" and a["data_currency"] == "USD"
    assert a["data_model_vendor"] == "RMS" and a["data_model_version"] == "25.0"
    # what dbo.Data records the results came from: the RM web UI, this Workbench
    assert a["server"] == "https://acme.rms-ppe.com" and a["database"] == "rwb_workbench"
    assert (a["peril_code"], a["region_code"]) == ("EQ", "NAEQ")
    assert (a["stage_status"], a["load_status"]) == ("pending", "pending")
    assert rows[1]["data_name"] is None

    job = execute_one("SELECT * FROM rwb_job WHERE rwb_job_type = 'submit_results_export'",
                      {}, connection="WORKBENCH")
    assert job["requestor_type"] == "analyst_request" and job["requestor_id"] == export_id
    assert job["context_type"] == "result_export" and job["context_id"] == export_id
    assert job["link_type"] == "not_applicable" and job["status_code"] == "pending"
    assert json.loads(job["input_data"]) == {"export_id": export_id,
                                             "submission_id": deal["submission_id"]}


def test_create_export_never_writes_back_to_the_submission(deal):
    _create(deal, [deal["a"]], crm_id="CRM-changed", treaty_incept=date(2027, 1, 1))
    sub = execute_one("SELECT inception_date FROM submission WHERE id = :s",
                      {"s": deal["submission_id"]}, connection="WORKBENCH")
    tags = execute("SELECT crm_id FROM submission_crm_id WHERE submission_id = :s",
                   {"s": deal["submission_id"]}, connection="WORKBENCH")
    assert sub["inception_date"] == "2026-04-01"
    assert [t["crm_id"] for t in tags] == ["CRM-1"]


@pytest.mark.parametrize("overrides, message", [
    ({"analysis_ids": []}, "Select at least one analysis"),
    ({"perspective_code": "RP"}, "A long has no RP results"),
    ({"perspective_code": "XX"}, "Choose a perspective"),
    ({"client_id": 99}, "Choose a client"),
    ({"treaty_incept": None}, "Treaty inception is required"),
    ({"data_vintage": None}, "Data vintage is required"),
    ({"model_version": None}, "Choose a model version"),
    ({"model_version": "24.0"}, "Choose a model version"),
    ({"crm_id": "x" * 31}, "longer than 30"),
])
def test_create_export_validation_messages(deal, overrides, message):
    kwargs = {"analysis_ids": [deal["a"]]}
    kwargs.update(overrides)
    with pytest.raises(svc.ExportValidationError) as exc:
        _create(deal, **kwargs)
    assert message in str(exc.value)
    assert execute("SELECT 1 FROM stage.rwb_loss_result_manifest", {}, connection="LOSS") == []


def test_create_export_dedupes_a_repeated_analysis_id(deal):
    _create(deal, [deal["a"], deal["a"]])
    assert len(execute("SELECT 1 FROM stage.rwb_loss_result_manifest", {},
                       connection="LOSS")) == 1


def test_create_export_refuses_a_disabled_and_an_unknown_analysis(deal):
    bad = seed_analysis(edm_id=deal["edm_id"], name="Bad", full_name="Bad long",
                        irp_app_analysis_id="A-388")
    with pytest.raises(svc.ExportValidationError) as exc:
        _create(deal, [deal["a"], bad])
    assert "Bad long cannot be exported" in str(exc.value)
    with pytest.raises(svc.ExportValidationError) as exc:
        _create(deal, [str(uuid.uuid4())])
    assert "is not part of this submission" in str(exc.value)


def test_a_repeat_export_is_created_alongside_the_earlier_one(deal):
    seed_manifest(submission_id=str(uuid.uuid4()), irp_app_analysis_id=41959,
                  perspective_code="GR", requested_by_email="r.patel@example.com")

    _create(deal, [deal["a"], deal["b"]])

    assert len(execute("SELECT 1 FROM stage.rwb_loss_result_manifest", {},
                       connection="LOSS")) == 3
    assert len(execute("SELECT 1 FROM rwb_job WHERE rwb_job_type = "
                       "'submit_results_export'", {}, connection="WORKBENCH")) == 1


def test_enqueue_failure_after_commit_fails_the_rows_so_retry_applies(deal, monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("queue down")
    monkeypatch.setattr(svc.rwb_job_service, "enqueue_rwb_job", boom)

    export_id = _create(deal, [deal["a"], deal["b"]])

    rows = execute("SELECT * FROM stage.rwb_loss_result_manifest", {}, connection="LOSS")
    assert [r["export_id"] for r in rows] == [export_id, export_id]
    assert all(r["stage_status"] == "failed" for r in rows)
    assert all(r["error_message"] == "could not queue the Risk Modeler request: queue down"
               for r in rows)
    assert all(svc.derive_status(r) == svc.FAILED for r in rows)
    assert all(svc.retry_decision(r, None, "", svc._utcnow()) == "submit" for r in rows)


# ── derive_status ────────────────────────────────────────────────────────────

def _m(**kw):
    base = {"stage_status": "pending", "load_status": "pending", "irp_export_job_id": None,
            "closed_at": None}
    base.update(kw)
    return base


@pytest.mark.parametrize("manifest, expected", [
    (_m(load_status="loaded"), svc.LOADED),
    (_m(stage_status="failed"), svc.FAILED),
    (_m(stage_status="staged", load_status="failed"), svc.FAILED),
    (_m(), svc.QUEUED),
    (_m(irp_export_job_id="1"), svc.IN_PROGRESS),
    (_m(irp_export_job_id="1", stage_status="staged"), svc.IN_PROGRESS),
    (_m(irp_export_job_id="1", stage_status="staged", load_status="loading"),
     svc.IN_PROGRESS),
    # closed wins over the failure it was set on (P-22)
    (_m(stage_status="failed", closed_at="2026-09-11 09:00:00"), svc.CLOSED),
])
def test_derive_status(manifest, expected):
    assert svc.derive_status(manifest) == expected


# ── list_export_rows ─────────────────────────────────────────────────────────

def test_list_export_rows_carries_the_export_columns_and_the_origin_on_every_row(deal):
    group = seed_analysis(edm_id=deal["edm_id"], name="Group", full_name="Group long",
                          irp_id="41990", irp_app_analysis_id="41990", perspectives=("GR",),
                          is_group=1)
    rdm_id = seed_rdm_for(deal["submission_id"], "GC RDM")
    rdm_row = seed_analysis(rdm_id=rdm_id, name="GC_HU", full_name="Z last", irp_id="38812",
                            irp_app_analysis_id="38812", perspectives=("GR",))
    export_id = _create(deal, [deal["a"], deal["b"], group, rdm_row],
                        data_vintage=date(2025, 12, 31))
    seed_export_job(export_id=export_id, irp_analysis_id=deal["b"], irp_id="500",
                    status="RUNNING")
    from db import execute_command
    execute_command("UPDATE stage.rwb_loss_result_manifest SET irp_export_job_id = '500' "
                    "WHERE irp_analysis_id = :a", {"a": deal["b"]}, connection="LOSS")

    rows = svc.list_export_rows(deal["submission_id"])

    assert [(r.export_id, r.export_ordinal) for r in rows] == [(export_id, 1)] * 4
    assert [r.analysis_name for r in rows] == ["A long", "B long", "Group long", "Z last"]
    assert [r.status for r in rows[:2]] == [svc.QUEUED, svc.IN_PROGRESS]
    # origin and engine are read from irp_analysis at render time, by the same
    # rule as the form's picker (rdm_id; is_group else the settings engine)
    assert [r.origin for r in rows] == ["RMS", "RMS", "RMS", "RDM"]
    assert [r.engine for r in rows] == ["DLM · RL25", "DLM · RL25", "Group", "DLM · RL25"]
    a = rows[0]
    assert a.client_name == "Example Re" and a.perspective_code == "GR" and a.crm_id == "CRM-1"
    assert str(a.data_vintage) == "2025-12-31" and str(a.treaty_incept) == "2026-04-01"
    assert a.requested_by_email == "analyst.a@example.com" and a.data_model_version == "25.0"
    # AAL is read from irp_analysis.loss_results at the export's perspective (P-20)
    assert [r.aal for r in rows] == [100.0] * 4 and a.aal_display == "100"
    assert not a.is_terminal and not a.can_retry


def test_list_export_rows_orders_exports_newest_first_and_numbers_them(deal):
    seed_manifest(submission_id=str(uuid.uuid4()), irp_app_analysis_id=41958,
                  perspective_code="GU")
    older = str(uuid.uuid4())
    seed_manifest(export_id=older, submission_id=deal["submission_id"],
                  irp_analysis_id=deal["a"], irp_app_analysis_id=41958, perspective_code="GR",
                  requested_at="2026-09-08 14:02:00", load_status="loaded", data_id=5)
    seed_manifest(export_id=older, submission_id=deal["submission_id"],
                  irp_analysis_id=deal["b"], irp_app_analysis_id=41959, perspective_code="GR",
                  requested_at="2026-09-08 14:02:00", stage_status="failed")
    newer = str(uuid.uuid4())
    seed_manifest(export_id=newer, submission_id=deal["submission_id"],
                  irp_analysis_id=deal["a"], irp_app_analysis_id=41958, perspective_code="RL",
                  requested_at="2026-09-10 08:00:00")

    rows = svc.list_export_rows(deal["submission_id"])

    assert [(r.export_ordinal, r.perspective_code) for r in rows] == [
        (1, "RL"), (2, "GR"), (2, "GR")]
    assert [r.export_id for r in rows] == [newer, older, older]
    assert [r.status for r in rows] == [svc.QUEUED, svc.LOADED, svc.FAILED]
    assert rows[1].data_id == 5 and rows[2].can_retry
    assert svc.list_export_rows(str(uuid.uuid4())) == []


def test_a_closed_row_is_terminal_and_offers_no_retry(deal):
    export_id = str(uuid.uuid4())
    seed_manifest(export_id=export_id, submission_id=deal["submission_id"],
                  irp_analysis_id=deal["a"], stage_status="staged", load_status="loaded",
                  data_id=5)
    seed_manifest(export_id=export_id, submission_id=deal["submission_id"],
                  irp_analysis_id=deal["b"], stage_status="failed",
                  closed_at="2026-09-11 09:00:00", closed_by="b.bailey@premiumiq.com")

    rows = svc.list_export_rows(deal["submission_id"])

    assert [r.status for r in rows] == [svc.LOADED, svc.CLOSED]
    assert all(r.is_terminal for r in rows) and not rows[1].can_retry
    assert rows[1].closed_by == "b.bailey@premiumiq.com"
