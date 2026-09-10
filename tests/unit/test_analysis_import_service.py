"""Import analyses by Risk Modeler id (issue #101): the per-id check and the
import itself, against the SQLite WORKBENCH mirror and the fake gateway."""

from __future__ import annotations

import json

import pytest

from app.services import analysis_import_service as svc
from app.services import analysis_service
from app.workers import analysis_jobs
from db import execute, execute_command, execute_one
from tests.unit.analysis_rows import seed_edm
from tests.unit.grouping_rows import (
    link_submission_edm,
    seed_broker_analysis,
    seed_group,
    seed_own_analysis,
    seed_submission,
)

APP_ID, PLATFORM_ID = "35774", "90001"
NAME = "CRE_WS_JP_COM_HD_JPWS_Stochastic"


def _seed_rm_analysis(fake_irp, *, app_id=APP_ID, platform_id=PLATFORM_ID,
                      name=NAME, is_group=False, engine="HD", currency="JPY",
                      app_id_in_payload=None) -> None:
    fake_irp.add_analysis(
        source_rdm_name="RDM", exposure_name="EDM", analysis_id=platform_id,
        name=name, app_analysis_id=app_id, is_group=is_group,
        exposure_resource_id="5",
        exposure_resource_type=("GROUP" if is_group else "PORTFOLIO"),
        metadata={"appAnalysisId": int(app_id_in_payload or app_id),
                  "analysisName": name, "engineType": engine,
                  "engineVersion": "23.0", "currencyCode": currency})


def _rows(submission_id: str) -> list[dict]:
    return [dict(r) for r in execute(
        "SELECT * FROM irp_analysis WHERE submission_id = :s ORDER BY name",
        {"s": submission_id}, connection="WORKBENCH")]


def _retrieval_jobs(analysis_id: str) -> list[dict]:
    return [dict(r) for r in execute(
        "SELECT link_type, link_id, status_code, input_data FROM rwb_job "
        "WHERE requestor_type = 'irp_analysis' AND requestor_id = :a "
        "AND rwb_job_type = 'retrieve_analysis_results'",
        {"a": analysis_id}, connection="WORKBENCH")]


def _candidate(app_id=APP_ID, platform_id=PLATFORM_ID) -> svc.ImportCandidate:
    return svc.ImportCandidate(app_analysis_id=app_id, analysis_id=platform_id,
                               name=None, is_group=False, engine=None,
                               currency=None)


# ── check_analysis ───────────────────────────────────────────────────────────────

def test_check_returns_the_candidate_for_a_known_app_id(iteration2_db, fake_irp):
    _seed_rm_analysis(fake_irp)
    submission = seed_submission()

    c = svc.check_analysis(submission_id=submission,
                           app_analysis_id=" 35774 ", existing=[])

    assert c == svc.ImportCandidate(
        app_analysis_id="35774", analysis_id="90001", name=NAME,
        is_group=False, engine="HD", currency="JPY")
    assert svc.ImportCandidate.from_form(c.form_value) == c


def test_check_reports_a_group(iteration2_db, fake_irp):
    _seed_rm_analysis(fake_irp, is_group=True, name="Gotham All Perils Rollup")

    c = svc.check_analysis(submission_id=seed_submission(),
                           app_analysis_id=APP_ID, existing=[])

    assert c.is_group is True and c.name == "Gotham All Perils Rollup"


def test_check_refuses_a_non_numeric_entry_without_a_risk_modeler_call(
        iteration2_db, fake_irp):
    fake_irp.raise_on_resolve_app_analysis_id = True  # would surface if called

    with pytest.raises(svc.ImportCheckError) as exc:
        svc.check_analysis(submission_id=seed_submission(),
                           app_analysis_id="abc", existing=[])
    assert "numeric id" in str(exc.value)


def test_check_names_the_id_to_use_when_risk_modeler_has_no_match(
        iteration2_db, fake_irp):
    with pytest.raises(svc.ImportCheckError) as exc:
        svc.check_analysis(submission_id=seed_submission(),
                           app_analysis_id="99999", existing=[])
    assert str(exc.value) == ("Risk Modeler has no analysis 99999 — use the id "
                              "shown on the analysis in Risk Modeler.")
    assert exc.value.kind == "error"


def test_check_reports_an_unreachable_risk_modeler(iteration2_db, fake_irp):
    fake_irp.raise_on_resolve_app_analysis_id = True

    with pytest.raises(svc.ImportCheckError) as exc:
        svc.check_analysis(submission_id=seed_submission(),
                           app_analysis_id=APP_ID, existing=[])
    assert str(exc.value) == "Risk Modeler did not answer. Try again in a moment."


def test_check_refuses_an_id_already_imported_into_the_deal(iteration2_db, fake_irp):
    _seed_rm_analysis(fake_irp)
    submission = seed_submission()
    svc.import_analyses(submission_id=submission, entries=[_candidate()],
                        actor_id=iteration2_db.user_a)

    with pytest.raises(svc.ImportCheckError) as exc:
        svc.check_analysis(submission_id=submission, app_analysis_id=APP_ID,
                           existing=[])
    assert str(exc.value) == f"35774 was already imported into this deal — {NAME}."
    assert exc.value.kind == "warning"


def test_check_refuses_an_id_captured_from_one_of_the_deals_rdms(
        iteration2_db, fake_irp):
    _seed_rm_analysis(fake_irp)
    submission = seed_submission()
    seed_broker_analysis(submission, "Broker EU Wind", irp_id=PLATFORM_ID,
                         rdm_name="GOTHAM_2026_RDM")

    with pytest.raises(svc.ImportCheckError) as exc:
        svc.check_analysis(submission_id=submission, app_analysis_id=APP_ID,
                           existing=[])
    assert str(exc.value) == ("35774 is already in this deal, captured from RDM "
                              "GOTHAM_2026_RDM — Broker EU Wind.")


def test_check_refuses_an_id_run_from_one_of_the_deals_edms(iteration2_db, fake_irp):
    _seed_rm_analysis(fake_irp)
    submission = seed_submission()
    edm = seed_edm("GOTHAM_2026_EDM")
    link_submission_edm(submission, edm)
    seed_own_analysis(edm, "CRE_P1_T1", irp_id=PLATFORM_ID)

    with pytest.raises(svc.ImportCheckError) as exc:
        svc.check_analysis(submission_id=submission, app_analysis_id=APP_ID,
                           existing=[])
    assert str(exc.value) == ("35774 is already in this deal, run from EDM "
                              "GOTHAM_2026_EDM — CRE_P1_T1.")


def test_check_refuses_an_id_the_deal_composed_as_a_group(iteration2_db, fake_irp):
    _seed_rm_analysis(fake_irp, is_group=True)
    submission = seed_submission()
    seed_group(submission, "CRE_Sub One_Group", irp_id=PLATFORM_ID)

    with pytest.raises(svc.ImportCheckError) as exc:
        svc.check_analysis(submission_id=submission, app_analysis_id=APP_ID,
                           existing=[])
    assert str(exc.value) == "35774 is already in this deal — CRE_Sub One_Group."


def test_check_allows_an_id_that_is_live_in_another_deal(iteration2_db, fake_irp):
    _seed_rm_analysis(fake_irp)
    mine, theirs = seed_submission("Mine"), seed_submission("Theirs")
    edm = seed_edm("Their EDM")
    link_submission_edm(theirs, edm)
    seed_own_analysis(edm, "CRE_P1_T1", irp_id=PLATFORM_ID)
    seed_broker_analysis(theirs, "Broker", irp_id=PLATFORM_ID)

    c = svc.check_analysis(submission_id=mine, app_analysis_id=APP_ID, existing=[])

    assert c.analysis_id == PLATFORM_ID


def test_check_ignores_a_deleted_row(iteration2_db, fake_irp):
    _seed_rm_analysis(fake_irp)
    submission = seed_submission()
    group = seed_group(submission, "Old group", irp_id=PLATFORM_ID)
    execute_command("UPDATE irp_analysis SET deleted_at = '2026-09-01' WHERE id = :i",
                    {"i": group}, connection="WORKBENCH")

    assert svc.check_analysis(
        submission_id=submission, app_analysis_id=APP_ID,
        existing=[]).analysis_id == PLATFORM_ID


# ── import_analyses ──────────────────────────────────────────────────────────────

def test_import_inserts_the_row_and_enqueues_its_results_retrieval(
        iteration2_db, fake_irp):
    _seed_rm_analysis(fake_irp)
    submission = seed_submission()

    outcome = svc.import_analyses(submission_id=submission,
                                  entries=[_candidate()],
                                  actor_id=iteration2_db.user_a)

    assert outcome == svc.ImportOutcome(imported=1, failed=[])
    [row] = _rows(submission)
    assert row["irp_id"] == PLATFORM_ID
    assert row["irp_app_analysis_id"] == APP_ID
    assert row["name"] == NAME and row["full_name"] == NAME
    assert row["status_code"] == "ready"
    assert row["is_group"] == 0
    assert row["edm_id"] is None and row["rdm_id"] is None
    assert row["imported_at"] is not None
    assert row["exposure_resource_id"] == "5"
    assert row["inserted_by"] == iteration2_db.user_a
    assert json.loads(row["settings_metadata"])["engineType"] == "HD"
    [job] = _retrieval_jobs(row["id"])
    assert job["link_type"] == "submission"
    assert job["link_id"].lower() == submission
    assert job["status_code"] == "pending"
    assert json.loads(job["input_data"]) == {"analysis_id": row["id"]}


def test_imported_row_gets_its_losses_from_the_retrieval_worker(
        iteration2_db, fake_irp):
    """The worker has no portfolio to read the pointer off, so it uses the
    exposure pointer the import stored — no second metadata read."""
    _seed_rm_analysis(fake_irp)
    submission = seed_submission()
    svc.import_analyses(submission_id=submission, entries=[_candidate()],
                        actor_id=iteration2_db.user_a)
    fake_irp.raise_on_analysis_metadata = True   # a re-read would fail the job

    [row_before] = analysis_service.list_submission_executed_analyses(
        submission_id=submission)
    assert row_before.is_live is True                # polls until the losses land
    assert analysis_jobs.run_pending(worker_id="w1") == 1
    [row] = analysis_service.list_submission_executed_analyses(
        submission_id=submission)

    assert row.results_state == "ready"
    assert row.is_live is False
    assert row.run_state == "imported"
    assert {c["exposure_resource_id"] for c in fake_irp.result_calls} == {"5"}


def test_import_of_a_group_marks_the_row(iteration2_db, fake_irp):
    _seed_rm_analysis(fake_irp, is_group=True, name="Gotham All Perils Rollup")
    submission = seed_submission()

    svc.import_analyses(submission_id=submission, entries=[_candidate()],
                        actor_id=iteration2_db.user_a)

    [row] = _rows(submission)
    assert row["is_group"] == 1
    assert row["exposure_resource_id"] is None  # not a PORTFOLIO pointer


def test_import_suffixes_a_name_already_live_in_the_deal(iteration2_db, fake_irp):
    _seed_rm_analysis(fake_irp)
    submission = seed_submission()
    seed_group(submission, NAME)

    svc.import_analyses(submission_id=submission, entries=[_candidate()],
                        actor_id=iteration2_db.user_a)

    imported = execute_one(
        "SELECT name, full_name FROM irp_analysis WHERE irp_app_analysis_id = :a",
        {"a": APP_ID}, connection="WORKBENCH")
    assert imported["name"] == f"{NAME}_2"
    assert imported["full_name"] == NAME  # Risk Modeler's real name, kept


def test_import_refuses_an_entry_whose_platform_id_no_longer_matches(
        iteration2_db, fake_irp):
    """A stale or edited form: the posted Platform id names an analysis whose
    appAnalysisId is not the one the analyst checked. The other entry still
    imports."""
    _seed_rm_analysis(fake_irp)
    _seed_rm_analysis(fake_irp, app_id="35810", platform_id="90002",
                      name="Other", app_id_in_payload="35811")
    submission = seed_submission()

    outcome = svc.import_analyses(
        submission_id=submission,
        entries=[_candidate(), _candidate("35810", "90002")],
        actor_id=iteration2_db.user_a)

    assert outcome.imported == 1
    assert outcome.failed == ["35810 no longer matches the analysis that was "
                              "checked — add it again."]
    assert [r["irp_app_analysis_id"] for r in _rows(submission)] == [APP_ID]


def test_import_rechecks_the_deal_and_reports_the_duplicate(iteration2_db, fake_irp):
    _seed_rm_analysis(fake_irp)
    submission = seed_submission()
    seed_group(submission, "Composed", irp_id=PLATFORM_ID)  # added since the check

    outcome = svc.import_analyses(submission_id=submission,
                                  entries=[_candidate()],
                                  actor_id=iteration2_db.user_a)

    assert outcome.imported == 0
    assert outcome.failed == ["35774 is already in this deal — Composed."]
    assert len(_rows(submission)) == 1  # only the group


def test_import_reports_an_unreachable_risk_modeler_per_entry(iteration2_db, fake_irp):
    _seed_rm_analysis(fake_irp)
    fake_irp.raise_on_analysis_metadata = True
    submission = seed_submission()

    outcome = svc.import_analyses(submission_id=submission,
                                  entries=[_candidate()],
                                  actor_id=iteration2_db.user_a)

    assert outcome.imported == 0
    assert outcome.failed == ["35774: Risk Modeler did not answer. Try again "
                              "in a moment."]
    assert _rows(submission) == []


def test_import_carries_on_past_an_unexpected_failure(
        iteration2_db, fake_irp, monkeypatch):
    """Entry 2 of 3 blows up after its row is written; the other two still land,
    the analyst is told which one did not, and the half-written row is taken
    back out so it neither polls forever nor blocks a retry."""
    for app_id, platform_id in (("35774", "90001"), ("35810", "90002"),
                                ("35820", "90003")):
        _seed_rm_analysis(fake_irp, app_id=app_id, platform_id=platform_id,
                          name=f"Analysis {app_id}")
    submission = seed_submission()
    real_enqueue = svc.rwb_job_service.enqueue_rwb_job

    def blow_up_on_the_second(**kwargs):
        row = execute_one("SELECT irp_app_analysis_id FROM irp_analysis "
                          "WHERE id = :i", {"i": kwargs["requestor_id"]},
                          connection="WORKBENCH")
        if row["irp_app_analysis_id"] == "35810":
            raise RuntimeError("queue is down")
        return real_enqueue(**kwargs)

    monkeypatch.setattr(svc.rwb_job_service, "enqueue_rwb_job",
                        blow_up_on_the_second)

    outcome = svc.import_analyses(
        submission_id=submission,
        entries=[_candidate("35774", "90001"), _candidate("35810", "90002"),
                 _candidate("35820", "90003")],
        actor_id=iteration2_db.user_a)

    assert outcome.imported == 2
    assert outcome.failed == ["35810 could not be imported — try again."]
    assert [r["irp_app_analysis_id"] for r in _rows(submission)
            if r["deleted_at"] is None] == ["35774", "35820"]
    assert svc.check_analysis(submission_id=submission,
                              app_analysis_id="35810",
                              existing=[]).analysis_id == "90002"


def test_check_names_an_ambiguous_app_id_rather_than_a_missing_one(
        iteration2_db, fake_irp):
    _seed_rm_analysis(fake_irp)
    _seed_rm_analysis(fake_irp, platform_id="90002", name="Same id, other row")

    with pytest.raises(svc.ImportCheckError) as exc:
        svc.check_analysis(submission_id=seed_submission(),
                           app_analysis_id=APP_ID, existing=[])
    assert str(exc.value) == ("Risk Modeler has more than one analysis with id "
                              "35774 — it cannot be imported by id.")


def test_check_refuses_an_app_id_already_on_the_dialogs_list(
        iteration2_db, fake_irp):
    """The typed value is normalized before the comparison, so a leading zero
    does not slip past the same id already listed."""
    _seed_rm_analysis(fake_irp)

    with pytest.raises(svc.ImportCheckError) as exc:
        svc.check_analysis(submission_id=seed_submission(),
                           app_analysis_id="035774", existing=[APP_ID])
    assert str(exc.value) == "35774 is already in the list."
    assert exc.value.kind == "warning"


def test_check_allows_an_id_whose_only_row_is_under_a_deleted_rdm(
        iteration2_db, fake_irp):
    """The Results grid and the group picker both hide an analysis under a
    soft-deleted RDM, so it is not in the deal any more."""
    _seed_rm_analysis(fake_irp)
    submission = seed_submission()
    seed_broker_analysis(submission, "Broker EU Wind", irp_id=PLATFORM_ID)
    execute_command("UPDATE irp_rdm SET deleted_at = '2026-09-01'", {},
                    connection="WORKBENCH")

    assert svc.check_analysis(
        submission_id=submission, app_analysis_id=APP_ID,
        existing=[]).analysis_id == PLATFORM_ID


# ── the imported row's run currency (#101) ───────────────────────────────────────

def test_import_writes_the_run_currency_into_the_submit_snapshot(
        iteration2_db, fake_irp):
    _seed_rm_analysis(fake_irp, currency="JPY")
    submission = seed_submission()

    svc.import_analyses(submission_id=submission, entries=[_candidate()],
                        actor_id=iteration2_db.user_a)

    [row] = _rows(submission)
    assert json.loads(row["submitted_settings"]) == {"currency": {"code": "JPY"}}


def test_import_of_an_analysis_with_no_currency_leaves_it_unpairable(
        iteration2_db, fake_irp):
    _seed_rm_analysis(fake_irp, currency=None)
    submission = seed_submission()

    svc.import_analyses(submission_id=submission, entries=[_candidate()],
                        actor_id=iteration2_db.user_a)

    [row] = _rows(submission)
    assert json.loads(row["submitted_settings"]) == {"currency": {"code": None}}
    [comparable] = analysis_service.list_comparable_analyses(
        submission_id=submission)
    assert comparable.run_currency is None


def test_imported_analysis_can_be_compared_with_an_own_analysis(
        iteration2_db, fake_irp):
    _seed_rm_analysis(fake_irp, currency="JPY")
    submission = seed_submission()
    edm = seed_edm()
    link_submission_edm(submission, edm)
    own = seed_own_analysis(edm, "Own JPY", currency="JPY")
    other = seed_own_analysis(edm, "Own USD", currency="USD")
    svc.import_analyses(submission_id=submission, entries=[_candidate()],
                        actor_id=iteration2_db.user_a)
    imported = _rows(submission)[0]["id"]

    by_id = {c.id: c for c in analysis_service.list_comparable_analyses(
        submission_id=submission)}
    assert by_id[imported].run_currency == "JPY"

    pairs, drops = analysis_service.list_comparison_pairs(
        pairs=f"{own}:{imported},{other}:{imported}", perspective="GR")

    assert [(p.base.name, p.second.name) for p in pairs] == [("Own JPY", NAME)]
    assert drops == [{"kind": "currency", "currencies": ("USD", "JPY")}]
